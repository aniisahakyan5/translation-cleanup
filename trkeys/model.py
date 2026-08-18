"""Core vocabulary shared by every stage of the reconciliation.

The three source names are NOT arbitrary labels: they are exactly the three
values of the postgres enum `dictionary_source_enum`. Anything this program
puts in `expected_source` has to be writable back into
`public.dictionary.source`, so the vocabulary is pinned to the database.
"""

# public.dictionary.source -- the only three values the column can hold.
WEBSITE = "website"
MOBILE = "mobile"
BACKOFFICE = "backoffice"

SOURCES = (WEBSITE, MOBILE, BACKOFFICE)

# Applications that own no key at all still deserve a row in SUMMARY, and
# hardcoded keys that belong to no application need somewhere to live.
UNMAPPED = "(none)"


class Status(object):
    """Outcome for one (application_code, key) pair.

    Precedence matters and is asserted in tests: a key with no usage is
    UNUSED regardless of what its source column says, because there is no
    "correct" source for something nothing references.
    """

    OK = "OK"
    UNUSED = "UNUSED"
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


def usage_label(used):
    """Render the set of sources a key is actually referenced from.

    `used` maps source name -> bool. Order is fixed (website, mobile,
    backoffice) so that two equal sets always render to the same string and
    can be compared to `db_source` textually as well as setwise.
    """
    hits = [s for s in SOURCES if used.get(s)]
    return ",".join(hits) if hits else "none"
