"""Getting the five inputs, from postgres or from the source repositories.

Everything here returns the same shape the CSV loaders in `inputs.py`
return, so `--db-keys foo.csv` and a live database are interchangeable.
"""

import csv
import io
import os
import re
import subprocess

from . import config as config_mod


def _psql(cfg, sql_path):
    """Run a .sql file and return a list of field-lists.

    Uses psql's real CSV writer rather than -A -F<sep>. Translation values
    genuinely contain newlines (HTML blocks in `description`, multi-line
    page copy), and any delimiter-and-split scheme silently tears one such
    record into two -- which shows up as an HTML fragment appearing where an
    application_code should be. CSV quoting is the only safe option, and
    python's csv module honours embedded newlines inside quotes.
    """
    path = config_mod.resolve(cfg, sql_path)
    with open(path) as fh:
        sql = fh.read()
    cmd = list(cfg["db"]["command"]) + ["-q", "--csv", "-v", "ON_ERROR_STOP=1"]
    proc = subprocess.run(cmd, input=sql, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            "query failed: %s\n%s" % (os.path.basename(path), proc.stderr.strip())
        )
    reader = csv.reader(io.StringIO(proc.stdout))
    rows = list(reader)
    return rows[1:] if rows else []  # drop the header --csv always emits


def db_keys(cfg):
    """Non-dictionary DB sources.

    -> [{application_code, source_table, source_column, key_value, scope}]
    """
    out = []
    for f in _psql(cfg, cfg["db"]["db_keys_sql"]):
        if len(f) < 5:
            continue
        out.append({
            "application_code": f[0].strip(),
            "source_table": f[1].strip(),
            "source_column": f[2].strip(),
            "key_value": f[3].strip(),
            "scope": f[4].strip(),
        })
    return out


def dictionary(cfg):
    """public.dictionary -- the authoritative dictionary input.

    -> [{application_code, key, source, type, is_generic, description}]
    """
    out = []
    for f in _psql(cfg, cfg["db"]["dictionary_sql"]):
        if len(f) < 6:
            f = f + [""] * (6 - len(f))
        out.append({
            "application_code": f[0].strip(),
            "key": f[1].strip(),
            # psql prints NULL as the empty string under -A -t.
            "source": f[2].strip() or None,
            "type": f[3].strip(),
            "is_generic": f[4].strip(),
            "description": f[5].strip(),
        })
    return out


def ensure_repo(spec, cache_dir, update=True):
    """Clone or refresh a source repository, returning its local path.

    Shallow single-branch clones: the scan only ever reads the checked-out
    tree, so history is dead weight on repositories this size. An existing
    clone is fetched and hard-reset rather than pulled, so a rewritten
    branch cannot leave the tree in a conflicted state.
    """
    url, ref = spec.get("repo"), spec.get("ref") or "HEAD"
    if not url:
        return os.path.expanduser(spec.get("root", ""))

    name = re.sub(r"[^A-Za-z0-9_.-]", "-", spec.get("name") or url.rstrip("/").split("/")[-1])
    if name.endswith(".git"):
        name = name[:-4]
    path = os.path.join(os.path.expanduser(cache_dir), name)
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")

    def run(args, cwd=None):
        p = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True)
        if p.returncode != 0:
            raise RuntimeError((p.stderr or p.stdout).strip().splitlines()[-1])
        return p.stdout

    if not os.path.isdir(os.path.join(path, ".git")):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        args = ["git", "clone", "--depth", "1", "--single-branch"]
        if spec.get("ref"):
            args += ["--branch", spec["ref"]]
        run(args + [url, path])
    elif update:
        run(["git", "fetch", "--depth", "1", "origin", ref], cwd=path)
        run(["git", "reset", "--hard", "FETCH_HEAD"], cwd=path)
        run(["git", "clean", "-qfd"], cwd=path)
    return path


def repo_head(path):
    """Short commit and date of the checked-out tree, for the run notes."""
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%h %cs"], cwd=path,
            capture_output=True, text=True,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except OSError:
        return ""


def code_keys(spec, patterns, exclude_dirs):
    """Scan a repository for hardcoded translation keys.

    Returns a dict of key -> sorted list of "path:line" so the report can
    say *where* a key is used, not merely that it is.
    """
    root = os.path.expanduser(spec["root"])
    exts = tuple(spec.get("extensions", [".ts", ".tsx"]))
    regexes = [re.compile(p) for p in patterns]
    excluded = set(exclude_dirs)
    hits = {}

    roots = [os.path.join(root, sub) for sub in spec.get("include", ["."])]
    for base in roots:
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            # Prune in place so os.walk never descends into node_modules.
            dirnames[:] = [d for d in dirnames if d not in excluded]
            for fn in filenames:
                if not fn.endswith(exts):
                    continue
                full = os.path.join(dirpath, fn)
                try:
                    with open(full, encoding="utf-8", errors="ignore") as fh:
                        lines = fh.readlines()
                except OSError:
                    continue
                rel = os.path.relpath(full, root)
                for lineno, line in enumerate(lines, 1):
                    for rx in regexes:
                        for m in rx.finditer(line):
                            key = m.group(1).strip()
                            if key:
                                hits.setdefault(key, set()).add(
                                    "%s:%d" % (rel, lineno)
                                )
    return dict((k, sorted(v)) for k, v in hits.items())
