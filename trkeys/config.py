"""Configuration loading.

Plain JSON rather than TOML/YAML: this has to run on the system python
(3.9 here), where `tomllib` does not exist and PyYAML is not installed.
Every path may use `~`, and relative paths resolve against the config file's
own directory so a config can be moved around with its sql/ folder.
"""

import json
import os

DEFAULTS = {
    # null/omitted => discover every application_code present in the data.
    "applications": None,
    "db": {
        # Any argv that accepts SQL on stdin and speaks psql flags. Using the
        # container directly avoids needing libpq or a python driver locally.
        "command": [
            "docker", "exec", "-i", "movato_postgres",
            "psql", "-U", "movato", "-d", "movato",
        ],
        "db_keys_sql": "sql/db_keys.sql",
        "dictionary_sql": "sql/dictionary.sql",
    },
    "sources": {
        "website": {
            "kind": "code",
            "repo": "https://github.com/Movato/front.git",
            "ref": "main",
            "root": "~/Desktop/front",
            "include": ["app"],
            "extensions": [".ts", ".tsx", ".js", ".jsx"],
        },
        "mobile": {
            "kind": "code",
            "repo": "https://github.com/Movato/mobile.git",
            "ref": "main",
            "root": "~/Desktop/mobile",
            "include": ["src"],
            "extensions": [".ts", ".tsx", ".js", ".jsx"],
        },
        # The backend is scanned like the others, but expect close to zero
        # hits: it does not hardcode translation keys, it reads them out of
        # the `key` jsonb columns at runtime. Backoffice usage is therefore
        # driven mainly by DB content -- reconcile.py credits a key to
        # backoffice when the non-dictionary DB sources reference it,
        # whatever the scan finds. Anything the scan does find is unioned on
        # top. See README.
        "backoffice": {
            "kind": "code",
            "repo": "https://github.com/Movato/app.git",
            "ref": "main",
            "root": "~/Desktop/app",
            "include": ["libs", "apps"],
            "extensions": [".ts"],
            # The backend renders no translations -- it reads them from `key`
            # jsonb columns, so its usage comes from the DB sources. Its enums
            # are snake_case strings the dictionary keys were named after
            # ("approved", "buyforme_fee"), and its export maps pair DB column
            # paths with hardcoded English ('buyforme_request.created_at':
            # 'Request Created'). Matching those literals credited 658 keys as
            # used on no evidence, so the second pass is off here.
            "literal_keys": False,
        },
    },
    # Applied to `kind: code` sources. Group 1 must be the key.
    "patterns": [
        r"""\bt\(\s*['"`]([^'"`\n]+)['"`]""",
        r"""\bi18n(?:ext)?\.t\(\s*['"`]([^'"`\n]+)['"`]""",
        r"""i18nKey\s*=\s*['"{]+\s*['"`]?([^'"`}\n]+)['"`]?""",
    ],
    # Count a bare string literal as usage when the dictionary or the DB
    # sources already hold that exact key. Codebases keep keys in data files
    # and pass them to t() by variable, which no call pattern can see; the
    # membership test is what stops those being reported as UNUSED and
    # deleted. Set false to match calls only.
    "literal_keys": True,
    "exclude_dirs": [
        "node_modules", ".git", "dist", "build", ".next", "ios", "android",
        "__tests__", "coverage", ".expo",
    ],
    # "existing" -> hardcoded usage counts for an application only where the
    #               key exists in that application's DB/Dictionary rows.
    # "global"   -> hardcoded usage counts for every application.
    "scoping": "existing",
    # Clones live here; `repo` wins over `root` when both are present.
    "repo_cache": ".cache/repos",
    "output": "out/translation-reconciliation.xlsx",
}


def _merge(base, override):
    """Recursive dict merge; override wins, absent keys keep the default."""
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            out[k] = _merge(base[k], v)
        else:
            out[k] = v
    return out


def load(path=None):
    cfg = dict(DEFAULTS)
    base_dir = os.getcwd()
    if path:
        with open(path) as fh:
            cfg = _merge(DEFAULTS, json.load(fh))
        base_dir = os.path.dirname(os.path.abspath(path)) or os.getcwd()
    cfg["_base_dir"] = base_dir
    return cfg


def resolve(cfg, path):
    """Expand `~` and anchor relative paths to the config's directory."""
    path = os.path.expanduser(path)
    if os.path.isabs(path):
        return path
    return os.path.join(cfg.get("_base_dir", os.getcwd()), path)
