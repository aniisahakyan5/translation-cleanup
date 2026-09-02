"""Per-application reconciliation.

The unit of comparison is (application_code, key) and never `key` alone.
Each application is reduced to its own indexes up front and reconciled from
those alone, so there is no code path by which a kz row can influence an am
row -- the am pass simply cannot see kz data.
"""

from collections import OrderedDict, defaultdict

from .model import (
    BACKOFFICE, MOBILE, PLATFORMS, SOURCES, UNMAPPED, WEBSITE, Status, order,
    usage_label,
)


def _index_db(db_rows):
    """application_code -> key -> [row]. Applications never share a bucket."""
    idx = defaultdict(lambda: defaultdict(list))
    for r in db_rows:
        app = r["application_code"]
        if app:
            idx[app][r["key_value"]].append(r)
    return idx


def _index_dict(dict_rows):
    """application_code -> key -> [row].

    A key may hold several dictionary rows in one application, because
    uidx_dictionary_key_app_source is unique on (key, application_code,
    source): website, mobile and NULL can all coexist. They are kept as a
    list and aggregated later -- never collapsed by picking one.
    """
    idx = defaultdict(lambda: defaultdict(list))
    for r in dict_rows:
        app = r["application_code"]
        if app:
            idx[app][r["key"]].append(r)
    return idx


def _classify(used, dynamic, recorded, expected):
    """Decide the status for one key.

    `recorded` is what the dictionary rows say, with None standing for a
    NULL row -- NULL is a value here ("both platforms"), not an absence.
    `expected` is what the code actually does, in the same vocabulary:
    {WEBSITE}, {MOBILE}, or {None} for a key both platforms render.
    """
    if not any(used.values()):
        # Nothing in either front end references it.
        if dynamic:
            # But DB content does, so it IS used -- reached at runtime
            # through t(record.key.name). Which platform draws that content
            # cannot be read from the code, so no source can be recommended
            # and the key is certainly not unused.
            return Status.DYNAMIC_ONLY
        return Status.UNUSED

    if recorded == expected:
        return Status.OK

    # A key both platforms use is equally correct as one NULL row or as one
    # website row plus one mobile row; the unique index allows either.
    if expected == set([None]) and recorded == set(PLATFORMS):
        return Status.OK

    if recorded == set([None]):
        # Recorded as "both", but only one platform uses it.
        return Status.SOURCE_MISSING

    if len(recorded) > 1:
        return Status.MULTIPLE_SOURCES

    return Status.SOURCE_MISMATCH


def _label(sources):
    """Render a recorded/expected set. None is a NULL row, meaning both.

    A NULL sitting beside a named source is kept visible. Dropping it made
    a MULTIPLE_SOURCES row read as one source -- the same value it was
    already expected to have -- so the row looked flagged for nothing.
    """
    if sources == set([None]):
        return "null"
    named = order(set(s for s in sources if s))
    if named and None in sources:
        return named + ",null"
    return named or "null"


def action_for(status, recorded, expected):
    """The concrete remediation for one row.

    Phrased as the edit to make against public.dictionary, because that is
    the only table any of these verdicts can be fixed in.
    """
    if status == Status.OK:
        return ""

    if status == Status.UNUSED:
        # Neither front end references it and no DB content points at it.
        return "delete from dictionary"

    if status == Status.DYNAMIC_ONLY:
        # It is used -- DB content references it -- but nothing in the code
        # says which platform renders that content. Recommending a source
        # here would be inventing evidence.
        return ""

    if status == Status.MISSING_IN_DATABASE:
        return "add to dictionary with source = %s" % _label(expected)

    if status == Status.MULTIPLE_SOURCES:
        # More than one row recorded, and the set is wrong for the usage.
        keep = _label(expected)
        drop = order(set(s for s in recorded - expected if s))
        fix = "set source = %s" % keep
        if drop:
            fix += "; remove row for %s" % drop
        # A NULL row beside a named one is a duplicate, not a second source.
        # Without naming it the action reads "set it to what it already is".
        if None in recorded and None not in expected:
            fix += "; remove the row with no source"
        return fix

    # SOURCE_MISSING and SOURCE_MISMATCH are the same edit, one value.
    return u"set source = %s (was %s)" % (_label(expected), _label(recorded))


def reconcile_application(app, db_idx, dict_idx, usage, scoping):
    """Reconcile a single application_code. Returns a list of row dicts."""
    db_keys = db_idx.get(app, {})
    dict_keys = dict_idx.get(app, {})

    # The key universe for this application: what the DB says belongs to it,
    # plus what the Dictionary says belongs to it. Nothing else.
    universe = set(db_keys) | set(dict_keys)

    if scoping == "global":
        # Opt-out mode: every hardcoded key is treated as belonging to every
        # application, so a key used only by kz also appears -- and counts as
        # used -- under am. Literal reading of the spec, far noisier.
        for s in (WEBSITE, MOBILE, BACKOFFICE):
            universe |= set(usage[s])

    rows = []
    for key in sorted(universe):
        db_hits = db_keys.get(key, [])
        dict_hits = dict_keys.get(key, [])
        exists_in_db = bool(db_hits)
        exists_in_dict = bool(dict_hits)

        # Only the two front ends render a translation. The backend reads
        # keys out of `key` jsonb columns at runtime, so its involvement is
        # DYNAMIC usage, not a platform -- treating it as one asked for a
        # backoffice row on keys the website plainly draws.
        used = {
            WEBSITE: key in usage[WEBSITE],
            MOBILE: key in usage[MOBILE],
        }
        dynamic = exists_in_db or key in usage[BACKOFFICE]

        # Every dictionary row's source, never just one. None is kept as a
        # value: a NULL row says "both platforms", which is not the same as
        # having no row at all.
        recorded = set(
            (d.get("source") or "").strip().lower() or None for d in dict_hits
        )
        hits = [s for s in PLATFORMS if used[s]]
        expected = set([None]) if len(hits) == 2 else set(hits)

        status = _classify(used, dynamic, recorded, expected)
        if not exists_in_db and not exists_in_dict:
            # Only reachable under `global` scoping, where the universe is
            # widened past what the application actually owns.
            status = Status.MISSING_IN_DATABASE

        tables = sorted(set(d["source_table"] for d in db_hits if d["source_table"]))
        columns = sorted(set(d["source_column"] for d in db_hits if d["source_column"]))
        scopes = sorted(set(d["scope"] for d in db_hits if d["scope"]))

        details = []
        if len(dict_hits) > 1:
            details.append("%d dictionary rows" % len(dict_hits))
        if len(recorded) > 1:
            details.append("dictionary already carries several sources")
        if status == Status.DYNAMIC_ONLY:
            details.append("referenced by DB content, so used; which platform "
                           "renders it cannot be read from the code")
        if status == Status.UNUSED and exists_in_dict and not exists_in_db:
            details.append("dictionary-only; no DB content references it")
        if scopes:
            details.append("scope=" + ",".join(scopes))
        locs = []
        for s in (WEBSITE, MOBILE):
            if used[s]:
                where = usage[s].get(key) or []
                if where:
                    locs.append("%s:%s" % (s, where[0]))
        if locs:
            details.append("; ".join(locs))

        rows.append(OrderedDict([
            ("application_code", app),
            ("key", key),
            ("used_in_web", "YES" if used[WEBSITE] else "NO"),
            ("used_in_mobile", "YES" if used[MOBILE] else "NO"),
            ("dynamic", "YES" if dynamic else "NO"),
            ("actual_usage", usage_label(used, dynamic)),
            ("exists_in_dictionary", "YES" if exists_in_dict else "NO"),
            ("exists_in_db_sources", "YES" if exists_in_db else "NO"),
            ("db_source", _label(recorded) if exists_in_dict else ""),
            ("db_source_table", ",".join(tables)),
            ("db_source_column", ",".join(columns)),
            ("expected_source", "" if status in (Status.UNUSED, Status.DYNAMIC_ONLY)
                                else _label(expected)),
            ("status", status),
            ("action", action_for(status, recorded, expected)),
            ("details", " | ".join(details)),
        ]))
    return rows


def unmapped_keys(usage, db_idx, dict_idx, apps=None):
    """Hardcoded keys that belong to no application at all.

    Under `existing` scoping a key inside an application's universe is by
    construction present in its DB or Dictionary, so MISSING_IN_DATABASE
    cannot arise per-application. It arises here: keys the code references
    that no application has ever heard of. Those are the genuinely dangling
    references, and they are reported once rather than six times.
    """
    known = set()
    for app_map in list(db_idx.values()) + list(dict_idx.values()):
        known.update(app_map.keys())
    if apps is not None:
        # Only count a key as "known" if it belongs to an application that
        # was actually in scope; otherwise a key living solely in a skipped
        # application would silently vanish from MISSING_KEYS too.
        known = set()
        for idx in (db_idx, dict_idx):
            for app in apps:
                known.update(idx.get(app, {}).keys())

    rows = []
    every = set()
    for s in (WEBSITE, MOBILE, BACKOFFICE):
        every.update(usage[s].keys())

    for key in sorted(every - known):
        used = dict((s, key in usage[s]) for s in PLATFORMS)
        dynamic = key in usage[BACKOFFICE]
        hits = [s for s in PLATFORMS if used[s]]
        expected = set([None]) if len(hits) == 2 else set(hits)
        locs = []
        for s in SOURCES:
            for where in (usage[s].get(key) or [])[:1]:
                locs.append("%s:%s" % (s, where))
        rows.append(OrderedDict([
            ("application_code", UNMAPPED),
            ("key", key),
            ("used_in_web", "YES" if used[WEBSITE] else "NO"),
            ("used_in_mobile", "YES" if used[MOBILE] else "NO"),
            ("dynamic", "YES" if dynamic else "NO"),
            ("actual_usage", usage_label(used, dynamic)),
            ("exists_in_dictionary", "NO"),
            ("exists_in_db_sources", "NO"),
            ("db_source", ""),
            ("db_source_table", ""),
            ("db_source_column", ""),
            ("expected_source", _label(expected)),
            ("status", Status.MISSING_IN_DATABASE),
            ("action", action_for(Status.MISSING_IN_DATABASE, set(), expected)),
            ("details", "referenced in code, absent from every application"
                        + (" | " + "; ".join(locs) if locs else "")),
        ]))
    return rows


def run(db_rows, dict_rows, usage, applications=None, scoping="existing"):
    """Reconcile every application independently and return all rows."""
    db_idx = _index_db(db_rows)
    dict_idx = _index_dict(dict_rows)

    if applications:
        apps = [a.strip().lower() for a in applications]
    elif db_idx:
        # The DB exports decide the scope. Upload kz.tsv and only kz is
        # reconciled; upload kz and am and both are. A dictionary covering
        # applications you did not export is not evidence that those
        # applications are dead -- it is evidence you did not export them,
        # so they are left out rather than reported as entirely unused.
        apps = sorted(db_idx)
    else:
        # Nothing to scope by; fall back to the dictionary so a
        # dictionary-only run still produces something.
        apps = sorted(dict_idx)

    rows = []
    for app in apps:
        rows.extend(
            reconcile_application(app, db_idx, dict_idx, usage, scoping)
        )
    if scoping != "global":
        # Under `global` these keys already appear inside every application,
        # so emitting them again here would double count them.
        rows.extend(unmapped_keys(usage, db_idx, dict_idx, apps))
    return rows, apps


def summarise(rows, apps):
    """Per-application counts for the SUMMARY sheet."""
    # A key is multi-application when it appears under more than one code.
    seen = defaultdict(set)
    for r in rows:
        if r["application_code"] != UNMAPPED:
            seen[r["key"]].add(r["application_code"])
    multi = set(k for k, a in seen.items() if len(a) > 1)

    by_app = defaultdict(list)
    for r in rows:
        by_app[r["application_code"]].append(r)

    out = []
    order = list(apps) + ([UNMAPPED] if by_app.get(UNMAPPED) else [])
    for app in order:
        rs = by_app.get(app, [])

        def count(pred):
            return sum(1 for r in rs if pred(r))

        out.append(OrderedDict([
            ("application_code", app),
            ("total_keys", len(rs)),
            ("web_keys", count(lambda r: r["used_in_web"] == "YES")),
            ("mobile_keys", count(lambda r: r["used_in_mobile"] == "YES")),
            ("dynamic_keys", count(lambda r: r["dynamic"] == "YES")),
            ("multi_application_keys", count(lambda r: r["key"] in multi)),
            ("unused_keys", count(lambda r: r["status"] == Status.UNUSED)),
            ("dynamic_only_keys",
             count(lambda r: r["status"] == Status.DYNAMIC_ONLY)),
            ("missing_keys",
             count(lambda r: r["status"] == Status.MISSING_IN_DATABASE)),
            ("source_missing",
             count(lambda r: r["status"] == Status.SOURCE_MISSING)),
            ("source_mismatch",
             count(lambda r: r["status"] == Status.SOURCE_MISMATCH)),
            ("multiple_source_conflicts",
             count(lambda r: r["status"] == Status.MULTIPLE_SOURCES)),
            ("dictionary_keys",
             count(lambda r: r["exists_in_dictionary"] == "YES")),
            ("db_source_keys",
             count(lambda r: r["exists_in_db_sources"] == "YES")),
        ]))
    return out
