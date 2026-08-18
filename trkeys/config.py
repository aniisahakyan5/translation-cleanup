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
            "root": "~/Desktop/front",
            "include": ["app"],
            "extensions": [".ts", ".tsx", ".js", ".jsx"],
        },
        "mobile": {
            "kind": "code",
            "root": "~/Desktop/mobile",
            "include": ["src"],
            "extensions": [".ts", ".tsx", ".js", ".jsx"],
        },
        # The backend never hardcodes translation keys -- it reads them out of
        # the `key` jsonb columns at runtime. A key is therefore "used by
        # backoffice" exactly when backoffice-managed DB content references it,
        # which is what db_keys.sql already returns. See README.
        "backoffice": {"kind": "db_tables"},
    },
    # Applied to `kind: code` sources. Group 1 must be the key.
    "patterns": [
        r"""\bt\(\s*['"`]([^'"`\n]+)['"`]""",
        r"""\bi18n(?:ext)?\.t\(\s*['"`]([^'"`\n]+)['"`]""",
        r"""i18nKey\s*=\s*['"{]+\s*['"`]?([^'"`}\n]+)['"`]?""",
    ],
    "exclude_dirs": [
        "node_modules", ".git", "dist", "build", ".next", "ios", "android",
        "__tests__", "coverage", ".expo",
    ],
    # "existing" -> hardcoded usage counts for an application only where the
    #               key exists in that application's DB/Dictionary rows.
    # "global"   -> hardcoded usage counts for every application.
    "scoping": "existing",
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
