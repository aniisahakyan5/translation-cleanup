"""CSV overrides for any of the five inputs.

Column names are detected, never assumed: a dictionary export might call its
key column `key`, `key_value` or `dictionary_key` depending on who produced
it. Anything genuinely unresolvable raises rather than guessing, so a
mislabelled file fails loudly instead of silently reconciling nothing.
"""

import csv

# Candidate spellings, most specific first.
ALIASES = {
    "application_code": [
        "application_code", "applicationcode", "app_code", "application", "app",
    ],
    "key": ["key_value", "key", "dictionary_key", "translation_key", "name"],
    "source": ["source", "db_source", "dictionary_source"],
    "source_table": ["source_table", "table", "table_name"],
    "source_column": ["source_column", "column", "column_name"],
    "scope": ["scope"],
    "type": ["type", "dictionary_type"],
    "is_generic": ["is_generic", "generic"],
    "description": ["description", "descr", "comment"],
}


def _norm(name):
    return (name or "").strip().lower().replace(" ", "_").replace("-", "_")


def _resolve(fieldnames, logical, required=True):
    present = dict((_norm(f), f) for f in fieldnames or [])
    for cand in ALIASES[logical]:
        if cand in present:
            return present[cand]
    if required:
        raise ValueError(
            "could not find a %r column; saw: %s"
            % (logical, ", ".join(fieldnames or []))
        )
    return None


def _sniff(path):
    """Accept comma, semicolon or tab separated files."""
    with open(path, newline="", encoding="utf-8-sig") as fh:
        sample = fh.read(8192)
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


def load_db_keys(path):
    """-> [{application_code, source_table, source_column, key_value, scope}]"""
    out = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rd = csv.DictReader(fh, delimiter=_sniff(path))
        c_app = _resolve(rd.fieldnames, "application_code")
        c_key = _resolve(rd.fieldnames, "key")
        c_tab = _resolve(rd.fieldnames, "source_table", required=False)
        c_col = _resolve(rd.fieldnames, "source_column", required=False)
        c_scope = _resolve(rd.fieldnames, "scope", required=False)
        for r in rd:
            key = (r.get(c_key) or "").strip()
            if not key:
                continue
            table = (r.get(c_tab) or "").strip() if c_tab else ""
            # A dictionary row here would double count the separate
            # Dictionary input, so drop it no matter what the file contains.
            if table.lower() == "dictionary":
                continue
            out.append({
                "application_code": (r.get(c_app) or "").strip().lower(),
                "source_table": table,
                "source_column": (r.get(c_col) or "").strip() if c_col else "",
                "key_value": key,
                "scope": (r.get(c_scope) or "").strip() if c_scope else "",
            })
    return out


def load_dictionary(path):
    """-> [{application_code, key, source, type, is_generic, description}]"""
    out = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rd = csv.DictReader(fh, delimiter=_sniff(path))
        c_app = _resolve(rd.fieldnames, "application_code")
        c_key = _resolve(rd.fieldnames, "key")
        c_src = _resolve(rd.fieldnames, "source", required=False)
        c_type = _resolve(rd.fieldnames, "type", required=False)
        c_gen = _resolve(rd.fieldnames, "is_generic", required=False)
        c_desc = _resolve(rd.fieldnames, "description", required=False)
        for r in rd:
            key = (r.get(c_key) or "").strip()
            if not key:
                continue
            src = (r.get(c_src) or "").strip().lower() if c_src else ""
            out.append({
                "application_code": (r.get(c_app) or "").strip().lower(),
                "key": key,
                "source": src or None,
                "type": (r.get(c_type) or "").strip() if c_type else "",
                "is_generic": (r.get(c_gen) or "").strip() if c_gen else "",
                "description": (r.get(c_desc) or "").strip() if c_desc else "",
            })
    return out


def load_code_keys(path):
    """Hardcoded key list -> {key: [locations]}.

    Accepts a bare one-key-per-line list or a CSV with a key column; an
    optional location/file column is carried through when present.
    """
    with open(path, newline="", encoding="utf-8-sig") as fh:
        first = fh.readline()
    has_header = any(
        a in _norm(first) for a in ("key", "source", "file", "location")
    )
    hits = {}
    if not has_header:
        with open(path, encoding="utf-8-sig") as fh:
            for line in fh:
                key = line.strip()
                if key:
                    hits.setdefault(key, [])
        return hits

    with open(path, newline="", encoding="utf-8-sig") as fh:
        rd = csv.DictReader(fh, delimiter=_sniff(path))
        c_key = _resolve(rd.fieldnames, "key")
        c_loc = None
        for cand in ("location", "file", "path", "locations"):
            for f in rd.fieldnames or []:
                if _norm(f) == cand:
                    c_loc = f
                    break
            if c_loc:
                break
        for r in rd:
            key = (r.get(c_key) or "").strip()
            if not key:
                continue
            loc = (r.get(c_loc) or "").strip() if c_loc else ""
            hits.setdefault(key, [])
            if loc:
                hits[key].append(loc)
    return hits
