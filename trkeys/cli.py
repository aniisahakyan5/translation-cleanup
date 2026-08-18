"""Command line entry point."""

import argparse
import os
import sys

from . import config as config_mod
from . import extract, inputs, reconcile, report
from .model import BACKOFFICE, MOBILE, SOURCES, WEBSITE


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
    # File overrides -- any input can come from a file instead of live sources.
    p.add_argument("--db-keys", help="CSV of the non-dictionary DB query")
    p.add_argument("--dictionary", help="CSV of the Dictionary")
    p.add_argument("--web-keys", help="file of web hardcoded keys")
    p.add_argument("--mobile-keys", help="file of mobile hardcoded keys")
    p.add_argument("--backend-keys", help="file of backend hardcoded keys")
    p.add_argument(
        "--dump-inputs", metavar="DIR",
        help="also write the resolved inputs as CSV, for auditing",
    )
    return p


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

    # --- inputs D and E -------------------------------------------------
    try:
        if args.db_keys:
            db_rows = inputs.load_db_keys(args.db_keys)
            notes.append("db keys: %d rows from %s" % (len(db_rows), args.db_keys))
        else:
            db_rows = extract.db_keys(cfg)
            notes.append("db keys: %d rows from postgres (dictionary branch "
                         "excluded at the SQL level)" % len(db_rows))

        if args.dictionary:
            dict_rows = inputs.load_dictionary(args.dictionary)
            notes.append("dictionary: %d rows from %s"
                         % (len(dict_rows), args.dictionary))
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

    rows, resolved = reconcile.run(
        db_rows, dict_rows, usage,
        applications=apps, scoping=cfg["scoping"],
    )
    summary = reconcile.summarise(rows, resolved)

    if args.dump_inputs:
        _dump(args.dump_inputs, db_rows, dict_rows, usage)
        notes.append("resolved inputs written to %s" % args.dump_inputs)

    path = report.write(output, rows, summary, notes)

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
    print("\nwrote %s" % path)
    return 0
