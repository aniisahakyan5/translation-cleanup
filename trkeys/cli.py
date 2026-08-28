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
        "--no-fetch", action="store_true",
        help="scan the cached clones as they are, without contacting GitHub",
    )
    p.add_argument("--repo-cache", metavar="DIR", help="where clones are kept")
    p.add_argument(
        "--ref", action="append", metavar="SOURCE=BRANCH",
        help="branch to scan for one source, e.g. --ref website=develop "
             "(default: the repository's default branch)",
    )
    p.add_argument(
        "--export-keys", metavar="DIR",
        help="only scan the repositories and write the three hardcoded-key "
             "files to DIR, then exit; no database needed",
    )
    p.add_argument(
        "--serve", action="store_true",
        help="open the browser app with a working Update button: the page "
             "loads the last scan instantly and only re-scans when asked",
    )
    p.add_argument("--port", type=int, default=8765, help="port for --serve")
    p.add_argument(
        "--no-open", action="store_true",
        help="with --serve, do not open a browser window",
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
    # (kind, text): "info" is a deliberate consequence of what was supplied,
    # "problem" means the results are likely to mislead.
    warn = []

    only_dict = sorted(dict_apps - db_apps)
    if only_dict:
        warn.append(
            ("info", "%s SKIPPED -- dictionary rows but no DB sources. Only the "
            "applications present in the DB files are reconciled. To include "
            "%s, run the query for %s too."
            % (", ".join(only_dict),
               "it" if len(only_dict) == 1 else "them",
               ", ".join(only_dict))))
    only_db = sorted(db_apps - dict_apps)
    if only_db:
        warn.append(
            ("problem",
             "%s in the DB sources but NOT in the dictionary -- source status "
             "cannot be judged for %s."
             % (", ".join(only_db), "it" if len(only_db) == 1 else "them")))
    for name, label in ((WEBSITE, "web"), (MOBILE, "mobile")):
        if not usage[name]:
            warn.append(
                ("problem",
                 "no %s keys loaded -- nothing can be marked used in %s"
                 % (label, label)))
    return warn


def _apps_in(rows, field):
    codes = sorted(set(r[field] for r in rows if r.get(field)))
    return ",".join(codes) if codes else "no application_code"


def _usage_for(name, cfg, override, notes, fetch=True, known=None):
    """Resolve one hardcoded-key source to {key: [locations]}.

    `known` is the set of keys the dictionary and DB sources already carry.
    It lets the scanner count a key held in a data file and passed to t()
    through a variable -- see extract.code_keys.
    """
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
        try:
            root = extract.ensure_repo(
                dict(spec, name=name),
                config_mod.resolve(cfg, cfg.get("repo_cache", ".cache/repos")),
                update=fetch,
            )
        except (RuntimeError, OSError) as exc:
            notes.append("%s: could not fetch %s -- %s"
                         % (name, spec.get("repo") or spec.get("root"), exc))
            return {}
        if not os.path.isdir(root):
            notes.append(
                "%s: repository %s NOT FOUND -- treated as zero keys" % (name, root)
            )
            return {}
        # Per-source, falling back to the global setting. The backoffice is
        # off: it is a backend that renders no translations, and its enums
        # are snake_case strings the dictionary keys were named after, so
        # every literal it matches is a collision -- see config.py.
        on = spec.get("literal_keys", cfg.get("literal_keys", True))
        lit = known if on else None
        stats = {}
        hits = extract.code_keys(dict(spec, root=root), cfg["patterns"],
                                 cfg["exclude_dirs"], known=lit, stats=stats)
        head = extract.repo_head(root)
        notes.append("%s: %d unique keys scanned from %s%s"
                     % (name, len(hits), root, (" @ " + head) if head else ""))
        if stats.get("literal_only"):
            notes.append("%s: %d of those are known keys held in data files "
                         "rather than in a t() call -- matched as literals"
                         % (name, stats["literal_only"]))
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


def _serve(args, cfg):
    """Run the page with a local backend so Update can do real work."""
    from . import server

    cached = server.read_cache(cfg)
    if cached and cached.get("scanned_at"):
        counts = ", ".join(
            "%s %d" % (n, len(cached["sources"].get(n, {}).get("keys", {})))
            for n in SOURCES
        )
        print("  cached scan from %s -- %s" % (cached["scanned_at"], counts))
    else:
        print("  no cached scan yet (nothing reads it: the page scans ZIPs itself)")

    try:
        httpd = server.serve(cfg, port=args.port)
    except OSError as exc:
        sys.stderr.write(
            "error: could not listen on port %d (%s). Try --port <other>.\n"
            % (args.port, exc)
        )
        return 2

    url = "http://127.0.0.1:%d/" % args.port
    print("\n  %s" % url)
    print("  loopback only -- this serves your repository contents, "
          "so do not expose it.\n  Ctrl-C to stop.\n")
    if not args.no_open:
        import webbrowser
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
    return 0


def _export_keys(args, cfg, notes, fetch):
    """Scan the repositories and write the three key files, nothing else.

    No database is touched, so this is the step to run when the reconciling
    is going to happen in the browser app: it turns "go and find the
    hardcoded keys" into three files you can drop straight into the
    matching slots.
    """
    import csv

    # Keys held in data files are only recognisable against a known set, and
    # here there is no database step to supply one. --dictionary does.
    known = set()
    if args.dictionary or args.db_keys:
        for path in _expand(args.dictionary or []):
            known.update(r["key"] for r in inputs.load_dictionary(path, set())
                         if r.get("key"))
        for path in _expand(args.db_keys or []):
            known.update(r["key_value"] for r in inputs.load_db_keys(path, set())
                         if r.get("key_value"))
        notes.append("known keys: %d from --dictionary/--db-keys, used to "
                     "recognise keys held in data files" % len(known))
    elif cfg.get("literal_keys", True):
        notes.append("note: no --dictionary given, so keys held in data files "
                     "and passed to t() by variable cannot be recognised and "
                     "will look unused")

    usage = {}
    for name in SOURCES:
        usage[name] = _usage_for(name, cfg, None, notes, fetch, known)

    out = os.path.expanduser(args.export_keys)
    os.makedirs(out, exist_ok=True)
    written = []
    for name in SOURCES:
        path = os.path.join(out, "%s_keys.csv" % name)
        with open(path, "w", newline="", encoding="utf-8") as fh:
            wr = csv.writer(fh)
            wr.writerow(["key", "locations"])
            for k in sorted(usage[name]):
                wr.writerow([k, ";".join(usage[name][k][:5])])
        written.append((path, len(usage[name])))

    for n in notes:
        print("  " + n)
    print("")
    for path, n in written:
        print("wrote %-44s %5d keys" % (path, n))
    if not usage[BACKOFFICE]:
        print("\n  note: the backend hardcodes no translation keys -- it reads "
              "them from `key`\n        jsonb columns at runtime, so backoffice "
              "usage comes from the DB sources.")
    return 0


def main(argv=None):
    args = build_parser().parse_args(argv)
    cfg = config_mod.load(args.config)
    notes = []

    if args.scoping:
        cfg["scoping"] = args.scoping
    apps = args.applications or cfg.get("applications")
    output = args.output or cfg["output"]

    # Repository options must be settled before anything scans, because
    # both --export-keys and a full run go through the same scanner.
    if args.repo_cache:
        cfg["repo_cache"] = args.repo_cache
    for pair in args.ref or []:
        if "=" not in pair:
            sys.stderr.write("error: --ref needs SOURCE=BRANCH, got %r\n" % pair)
            return 2
        src, branch = pair.split("=", 1)
        if src not in cfg["sources"]:
            sys.stderr.write("error: unknown source %r; expected one of %s\n"
                             % (src, ", ".join(sorted(cfg["sources"]))))
            return 2
        cfg["sources"][src]["ref"] = branch
    fetch = not args.no_fetch

    if args.serve:
        return _serve(args, cfg)

    if args.export_keys:
        return _export_keys(args, cfg, notes, fetch)

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
    # Every key any application already knows about. The scanner uses it to
    # recognise keys that live in a data file rather than inside a t() call;
    # it is a membership test, so it can only ever move a key out of UNUSED.
    known_keys = set(r["key"] for r in dict_rows if r.get("key"))
    known_keys.update(r["key_value"] for r in db_rows if r.get("key_value"))

    usage = {
        WEBSITE: _usage_for(WEBSITE, cfg, args.web_keys, notes, fetch, known_keys),
        MOBILE: _usage_for(MOBILE, cfg, args.mobile_keys, notes, fetch, known_keys),
        BACKOFFICE: _usage_for(BACKOFFICE, cfg, args.backend_keys, notes, fetch,
                               known_keys),
    }

    notes.append("scoping=%s" % cfg["scoping"])

    warnings = _coverage(db_rows, dict_rows, usage)
    for kind, text in warnings:
        notes.append(("WARNING: " if kind == "problem" else "note: ") + text)

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
        problems = [t for k, t in warnings if k == "problem"]
        print("\n  %s" % ("!! CHECK YOUR INPUTS -- these results are probably misleading"
                          if problems else "note:"))
        for kind, text in warnings:
            print("  %s %s" % ("!!" if kind == "problem" else "  ", text))

    print("")
    for p in written:
        print("wrote %s" % p)
    return 0
