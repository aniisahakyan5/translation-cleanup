"""Command line entry point."""

import argparse
import glob
import os
import sys

from . import config as config_mod
from . import extract, inputs, reconcile, report
from .model import BACKOFFICE, MOBILE, SOURCES, UNMAPPED, WEBSITE


def build_parser():
    p = argparse.ArgumentParser(
        prog="trkeys",
        description="Reconcile translation keys per application_code across "
                    "web/mobile/backoffice usage, DB sources and the "
                    "Dictionary.",
    )
    p.add_argument("-c", "--config", help="path to config.json")
    p.add_argument(
        "-a", "--application", action="append", dest="applications",
        metavar="CODE",
        help="limit to this application_code (repeatable). "
             "Default: every application found.",
    )
    p.add_argument("-o", "--output", help="output .xlsx path")
    p.add_argument(
        "--scoping", choices=["existing", "global"],
        help="existing: credit hardcoded usage only where the key exists for "
             "that application (default). global: credit it everywhere.",
    )
    # File overrides -- any input can come from files instead of live sources.
    # Repeatable and glob-aware, because the per-application query produces
    # one export per application rather than a single combined file.
    p.add_argument(
        "--db-keys", action="append", metavar="FILE",
        help="CSV/TSV of the DB query; repeat or glob for per-application "
             "exports (dictionary rows are dropped automatically)",
    )
    p.add_argument(
        "--dictionary", action="append", metavar="FILE",
        help="CSV/TSV of the Dictionary; repeatable/globbable",
    )
    p.add_argument("--web-keys", help="file of web hardcoded keys")
    p.add_argument("--mobile-keys", help="file of mobile hardcoded keys")
    p.add_argument("--backend-keys", help="file of backend hardcoded keys")
    p.add_argument(
        "--dump-inputs", metavar="DIR",
        help="also write the resolved inputs as CSV, for auditing",
    )
    p.add_argument(
        "--split", action="store_true",
        help="write one workbook per application_code instead of a single "
             "combined one (report-kz.xlsx, report-am.xlsx, ...)",
    )
    return p


def _expand(patterns):
    """Expand each argument as a glob, keeping literal paths that match none.

    Lets `--db-keys 'exports/*.tsv'` work even when the shell did not expand
    it, and keeps a plain filename working unchanged.
    """
    out = []
    for pat in patterns:
        hits = sorted(glob.glob(os.path.expanduser(pat)))
        if hits:
            out.extend(hits)
        else:
            out.append(os.path.expanduser(pat))
    seen, uniq = set(), []
    for p in out:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def _write_split(output, rows, summary, notes):
    """One workbook per application_code.

    Each workbook holds only that application's rows. The SUMMARY row is
    taken from the combined pass rather than recomputed, so
    multi_application_keys still means "also present in another
    application" -- a per-application recount could only ever report 0.
    """
    stem, ext = os.path.splitext(output)
    by_app = {}
    for r in rows:
        by_app.setdefault(r["application_code"], []).append(r)
    summary_by_app = dict((s["application_code"], s) for s in summary)

    written = []
    for app in sorted(by_app):
        # "(none)" is not an application; give it a name that reads as one.
        tag = "unmapped" if app == UNMAPPED else app
        path = "%s-%s%s" % (stem, tag, ext)
        app_notes = list(notes) + ["this workbook contains only: %s" % app]
        if app == UNMAPPED:
            app_notes.append(
                "these keys are referenced in code but belong to no "
                "application; they are listed once, not per application"
            )
        written.append(
            report.write(path, by_app[app], [summary_by_app[app]], app_notes)
        )
    return written


def _coverage(db_rows, dict_rows, usage):
    """Warn when the inputs do not cover the same applications.

    An application present in one input but missing from another still
    yields a full set of rows, all of them UNUSED, which looks exactly like
    a real cleanup finding. Loud is better than plausible.
    """
    db_apps = set(r["application_code"] for r in db_rows if r["application_code"])
    dict_apps = set(r["application_code"] for r in dict_rows if r["application_code"])
    warn = []

    only_dict = sorted(dict_apps - db_apps)
    if only_dict:
        warn.append(
            "%s in the dictionary but NOT in the DB sources -- every key for "
            "%s will read as unused. Run the query for %s too."
            % (", ".join(only_dict),
               "it" if len(only_dict) == 1 else "them",
               ", ".join(only_dict))
        )
    only_db = sorted(db_apps - dict_apps)
    if only_db:
        warn.append(
            "%s in the DB sources but NOT in the dictionary -- source status "
            "cannot be judged for %s."
            % (", ".join(only_db), "it" if len(only_db) == 1 else "them")
        )
    for name, label in ((WEBSITE, "web"), (MOBILE, "mobile")):
        if not usage[name]:
            warn.append(
                "no %s keys loaded -- nothing can be marked used in %s"
                % (label, label)
            )
    return warn


def _apps_in(rows, field):
    codes = sorted(set(r[field] for r in rows if r.get(field)))
    return ",".join(codes) if codes else "no application_code"


def _usage_for(name, cfg, override, notes):
    """Resolve one hardcoded-key source to {key: [locations]}."""
    if override:
        hits = inputs.load_code_keys(override)
        notes.append("%s: %d keys from file %s" % (name, len(hits), override))
        return hits

    spec = cfg["sources"].get(name, {})
    kind = spec.get("kind")

    if kind == "db_tables":
        # Derived during reconciliation from the DB rows themselves.
        notes.append(
            "%s: derived from non-dictionary DB source tables (the backend "
            "hardcodes no keys; it reads them from `key` jsonb columns)" % name
        )
        return {}

    if kind == "code":
        root = os.path.expanduser(spec.get("root", ""))
        if not os.path.isdir(root):
            notes.append(
                "%s: repository %s NOT FOUND -- treated as zero keys" % (name, root)
            )
            return {}
        hits = extract.code_keys(spec, cfg["patterns"], cfg["exclude_dirs"])
        notes.append("%s: %d unique keys scanned from %s" % (name, len(hits), root))
        return hits

    notes.append("%s: no source configured -- treated as zero keys" % name)
    return {}


def _dump(directory, db_rows, dict_rows, usage):
    import csv
    os.makedirs(directory, exist_ok=True)

    def w(name, cols, rows):
        with open(os.path.join(directory, name), "w", newline="",
                  encoding="utf-8") as fh:
            wr = csv.DictWriter(fh, fieldnames=cols)
            wr.writeheader()
            for r in rows:
                wr.writerow(dict((c, r.get(c, "")) for c in cols))

    w("db_keys.csv",
      ["application_code", "source_table", "source_column", "key_value", "scope"],
      db_rows)
    w("dictionary.csv",
      ["application_code", "key", "source", "type", "is_generic", "description"],
      dict_rows)
    for name in SOURCES:
        with open(os.path.join(directory, "%s_keys.csv" % name), "w",
                  newline="", encoding="utf-8") as fh:
            wr = csv.writer(fh)
            wr.writerow(["key", "locations"])
            for k in sorted(usage[name]):
                wr.writerow([k, ";".join(usage[name][k][:5])])


def main(argv=None):
    args = build_parser().parse_args(argv)
    cfg = config_mod.load(args.config)
    notes = []

    if args.scoping:
        cfg["scoping"] = args.scoping
    apps = args.applications or cfg.get("applications")
    output = args.output or cfg["output"]

    known = set(a.strip().lower() for a in (apps or []))

    # --- inputs D and E -------------------------------------------------
    try:
        if args.db_keys:
            paths = _expand(args.db_keys)
            db_rows = []
            for p in paths:
                rows = inputs.load_db_keys(p, known)
                db_rows.extend(rows)
                notes.append("db keys: %5d rows <- %s (%s)" % (
                    len(rows), os.path.basename(p), _apps_in(rows, "application_code")))
            notes.append("db keys: %d rows total from %d file(s); dictionary "
                         "rows dropped" % (len(db_rows), len(paths)))
        else:
            db_rows = extract.db_keys(cfg)
            notes.append("db keys: %d rows from postgres (dictionary branch "
                         "excluded at the SQL level)" % len(db_rows))

        if args.dictionary:
            paths = _expand(args.dictionary)
            dict_rows = []
            for p in paths:
                rows = inputs.load_dictionary(p, known)
                dict_rows.extend(rows)
                notes.append("dictionary: %5d rows <- %s (%s)" % (
                    len(rows), os.path.basename(p), _apps_in(rows, "application_code")))
            notes.append("dictionary: %d rows total from %d file(s)"
                         % (len(dict_rows), len(paths)))
        else:
            dict_rows = extract.dictionary(cfg)
            notes.append("dictionary: %d rows from public.dictionary "
                         "(the authoritative dictionary input)" % len(dict_rows))
    except (RuntimeError, OSError, ValueError) as exc:
        sys.stderr.write("error: %s\n" % exc)
        return 2

    # --- inputs A, B, C -------------------------------------------------
    usage = {
        WEBSITE: _usage_for(WEBSITE, cfg, args.web_keys, notes),
        MOBILE: _usage_for(MOBILE, cfg, args.mobile_keys, notes),
        BACKOFFICE: _usage_for(BACKOFFICE, cfg, args.backend_keys, notes),
    }

    notes.append("scoping=%s" % cfg["scoping"])

    warnings = _coverage(db_rows, dict_rows, usage)
    for w in warnings:
        notes.append("WARNING: " + w)

    rows, resolved = reconcile.run(
        db_rows, dict_rows, usage,
        applications=apps, scoping=cfg["scoping"],
    )
    summary = reconcile.summarise(rows, resolved)

    if args.dump_inputs:
        _dump(args.dump_inputs, db_rows, dict_rows, usage)
        notes.append("resolved inputs written to %s" % args.dump_inputs)

    if args.split:
        written = _write_split(output, rows, summary, notes)
        notes.append("split: %d workbooks, one per application" % len(written))
    else:
        written = [report.write(output, rows, summary, notes)]

    for n in notes:
        print("  " + n)
    print("")
    hdr = "%-10s %8s %8s %8s %8s %8s %8s" % (
        "app", "total", "unused", "src_miss", "mismatch", "multi", "missing")
    print(hdr)
    print("-" * len(hdr))
    for s in summary:
        print("%-10s %8d %8d %8d %8d %8d %8d" % (
            s["application_code"], s["total_keys"], s["unused_keys"],
            s["source_missing"], s["source_mismatch"],
            s["multiple_source_conflicts"], s["missing_keys"]))
    if warnings:
        print("\n  !! CHECK YOUR INPUTS -- these results are probably misleading")
        for w in warnings:
            print("  !! " + w)

    print("")
    for p in written:
        print("wrote %s" % p)
    return 0
