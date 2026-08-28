"""Core vocabulary shared by every stage of the reconciliation.

The three source names are NOT arbitrary labels: they are exactly the three
values of the postgres enum `dictionary_source_enum`. Anything this program
puts in `expected_source` has to be writable back into
`public.dictionary.source`, so the vocabulary is pinned to the database.
"""

# public.dictionary.source -- the values the column can hold.
WEBSITE = "website"
MOBILE = "mobile"
BACKOFFICE = "backoffice"

# The only two things that RENDER a translation. The backend renders none:
# it reads keys out of `key` jsonb columns, which is what the DB sources
# are, so a key found there is DYNAMIC usage by whichever front end draws
# that content -- never "backoffice usage", and never a reason to want a
# backoffice row.
PLATFORMS = (WEBSITE, MOBILE)

SOURCES = (WEBSITE, MOBILE, BACKOFFICE)

# Applications that own no key at all still deserve a row in SUMMARY, and
# hardcoded keys that belong to no application need somewhere to live.
UNMAPPED = "(none)"


class Status(object):
    """Outcome for one (application_code, key) pair.

    `source` answers "which front end renders this", so it has three
    meaningful values: website, mobile, and NULL meaning BOTH. NULL is a
    real answer, not an absence -- a key both platforms use is correct with
    a single NULL row, and correct again with one website row and one mobile
    row, which the unique index on (key, application_code, source) allows.

    Precedence: usage decides first. A key nothing references is UNUSED
    whatever its source says, and a key reached only through DB content is
    DYNAMIC_ONLY -- used, but with no platform readable from the code, so
    no source can be recommended for it.
    """

    OK = "OK"
    UNUSED = "UNUSED"
    DYNAMIC_ONLY = "DYNAMIC_ONLY"
    SOURCE_MISSING = "SOURCE_MISSING"
    SOURCE_MISMATCH = "SOURCE_MISMATCH"
    MULTIPLE_SOURCES = "MULTIPLE_SOURCES"
    MISSING_IN_DATABASE = "MISSING_IN_DATABASE"


def order(sources):
    """Render a set of sources in the fixed spec order, not alphabetically.

    `actual_usage`, `db_source` and `expected_source` all pass through here
    so the three columns line up when read side by side. Comparison is
    always setwise, so ordering is purely presentational.
    """
    return ",".join(s for s in SOURCES if s in sources)


def usage_label(used, dynamic=False):
    """Render where a key is actually referenced from.

    `used` maps platform name -> bool. Order is fixed so that two equal sets
    always render to the same string and can be compared to `db_source`
    textually as well as setwise.
    """
    hits = [s for s in PLATFORMS if used.get(s)]
    if hits:
        return ",".join(hits)
    return "dynamic" if dynamic else "none"
