"""Per-application reconciliation.

The unit of comparison is (application_code, key) and never `key` alone.
Each application is reduced to its own indexes up front and reconciled from
those alone, so there is no code path by which a kz row can influence an am
row -- the am pass simply cannot see kz data.
"""

from collections import OrderedDict, defaultdict

from .model import (
    BACKOFFICE, MOBILE, SOURCES, UNMAPPED, WEBSITE, Status, order, usage_label,
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


def _classify(used, db_sources, expected):
    """Decide the status for one key.

    `db_sources` is the SET of non-null sources on the key's dictionary rows,
    `expected` the set implied by real usage.
    """
    if not any(used.values()):
        # Nothing references it. There is no "right" source for a key that
        # is not used, so usage always outranks source problems here.
        return Status.UNUSED

    if db_sources == expected:
        # Includes the multi-source case that is already fully represented
        # by one dictionary row per source.
        return Status.OK

    # Multi-source usage outranks SOURCE_MISSING: spec section 8 example 3
    # is a web+mobile key with no source recorded, and it is MULTIPLE_SOURCES
    # rather than SOURCE_MISSING. The distinction matters because the fix is
    # different -- these need a row per source, not one value filled in, and
    # picking a single source for them would be the arbitrary choice the
    # spec forbids.
    if len(expected) > 1:
        return Status.MULTIPLE_SOURCES

    if not db_sources:
        return Status.SOURCE_MISSING

    return Status.SOURCE_MISMATCH


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

        used = {
            WEBSITE: key in usage[WEBSITE],
            MOBILE: key in usage[MOBILE],
            # Backoffice does not hardcode keys; it authors the DB content
            # that references them. An explicit backoffice key file, if one
            # was supplied, is unioned in.
            BACKOFFICE: exists_in_db or key in usage[BACKOFFICE],
        }

        # Aggregate every dictionary row's source; never pick one.
        db_sources = set(
            d["source"] for d in dict_hits if d.get("source")
        )
        expected = set(s for s in SOURCES if used[s])

        status = _classify(used, db_sources, expected)
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
        if len(db_sources) > 1:
            details.append("dictionary already carries multiple sources")
        if status == Status.MULTIPLE_SOURCES and not db_sources:
            details.append("no source recorded either")
        missing_srcs = expected - db_sources
        if status == Status.MULTIPLE_SOURCES and db_sources:
            details.append("needs dictionary rows for: " + order(missing_srcs))
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
            ("used_in_backend", "YES" if used[BACKOFFICE] else "NO"),
            ("actual_usage", usage_label(used)),
            ("exists_in_dictionary", "YES" if exists_in_dict else "NO"),
            ("exists_in_db_sources", "YES" if exists_in_db else "NO"),
            ("db_source", order(db_sources)),
            ("db_source_table", ",".join(tables)),
            ("db_source_column", ",".join(columns)),
            ("expected_source", order(expected)),
            ("status", status),
            ("details", " | ".join(details)),
        ]))
    return rows


def unmapped_keys(usage, db_idx, dict_idx):
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

    rows = []
    every = set()
    for s in (WEBSITE, MOBILE, BACKOFFICE):
        every.update(usage[s].keys())

    for key in sorted(every - known):
        used = dict((s, key in usage[s]) for s in SOURCES)
        expected = set(s for s in SOURCES if used[s])
        locs = []
        for s in SOURCES:
            for where in (usage[s].get(key) or [])[:1]:
                locs.append("%s:%s" % (s, where))
        rows.append(OrderedDict([
            ("application_code", UNMAPPED),
            ("key", key),
            ("used_in_web", "YES" if used[WEBSITE] else "NO"),
            ("used_in_mobile", "YES" if used[MOBILE] else "NO"),
            ("used_in_backend", "YES" if used[BACKOFFICE] else "NO"),
            ("actual_usage", usage_label(used)),
            ("exists_in_dictionary", "NO"),
            ("exists_in_db_sources", "NO"),
            ("db_source", ""),
            ("db_source_table", ""),
            ("db_source_column", ""),
            ("expected_source", order(expected)),
            ("status", Status.MISSING_IN_DATABASE),
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
    else:
        apps = sorted(set(db_idx) | set(dict_idx))

    rows = []
    for app in apps:
        rows.extend(
            reconcile_application(app, db_idx, dict_idx, usage, scoping)
        )
    if scoping != "global":
        # Under `global` these keys already appear inside every application,
        # so emitting them again here would double count them.
        rows.extend(unmapped_keys(usage, db_idx, dict_idx))
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
            ("backend_keys", count(lambda r: r["used_in_backend"] == "YES")),
            ("multi_application_keys", count(lambda r: r["key"] in multi)),
            ("unused_keys", count(lambda r: r["status"] == Status.UNUSED)),
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
