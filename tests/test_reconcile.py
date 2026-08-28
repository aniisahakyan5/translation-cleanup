"""Tests for the reconciliation rules.

The isolation tests are the important ones: they encode the requirement that
a result for kz can never leak into a result for am.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trkeys import reconcile  # noqa: E402
from trkeys.model import BACKOFFICE, MOBILE, UNMAPPED, WEBSITE, Status  # noqa: E402


def db(app, key, table="page", column="title", scope="direct"):
    return {"application_code": app, "source_table": table,
            "source_column": column, "key_value": key, "scope": scope}


def dct(app, key, source=None):
    return {"application_code": app, "key": key, "source": source,
            "type": "text", "is_generic": "t", "description": ""}


def usage(web=(), mobile=(), backoffice=()):
    return {WEBSITE: dict((k, []) for k in web),
            MOBILE: dict((k, []) for k in mobile),
            BACKOFFICE: dict((k, []) for k in backoffice)}


def index(rows):
    return dict(((r["application_code"], r["key"]), r) for r in rows)


class TestApplicationIsolation(unittest.TestCase):
    """Section 6 of the spec: the unit of comparison is (app, key)."""

    def test_usage_does_not_leak_between_applications(self):
        # customer.balance exists for both, but only kz's is web-hardcoded...
        # which is untrue by construction: the web bundle is shared. Under
        # `existing` scoping the key must still resolve per application.
        rows, _ = reconcile.run(
            # am needs a DB row of its own to be in scope at all -- the DB
            # exports decide which applications are reconciled.
            db_rows=[db("kz", "customer.balance"), db("am", "am.thing")],
            dict_rows=[dct("kz", "customer.balance"), dct("am", "other.key")],
            usage=usage(web=["customer.balance"]),
        )
        by = index(rows)
        self.assertEqual(by[("kz", "customer.balance")]["used_in_web"], "YES")
        # am never had the key, so it gets no am row at all.
        self.assertNotIn(("am", "customer.balance"), by)
        self.assertEqual(by[("am", "other.key")]["status"], Status.UNUSED)

    def test_same_key_independent_status_per_application(self):
        # kz has it in a DB table (backoffice content), am only in the
        # dictionary. Same key, two different verdicts.
        rows, _ = reconcile.run(
            db_rows=[db("kz", "shared.key"), db("am", "am.thing")],
            dict_rows=[dct("kz", "shared.key", "backoffice"),
                       dct("am", "shared.key")],
            usage=usage(),
        )
        by = index(rows)
        self.assertEqual(by[("kz", "shared.key")]["status"], Status.OK)
        self.assertEqual(by[("kz", "shared.key")]["actual_usage"], "backoffice")
        self.assertEqual(by[("am", "shared.key")]["status"], Status.UNUSED)
        self.assertEqual(by[("am", "shared.key")]["actual_usage"], "none")

    def test_dictionary_is_filtered_by_application(self):
        rows, _ = reconcile.run(
            db_rows=[db("kz", "kz.seed"), db("am", "am.seed")],
            dict_rows=[dct("kz", "kz.only"), dct("am", "am.only")],
            usage=usage(),
        )
        by = index(rows)
        self.assertIn(("kz", "kz.only"), by)
        self.assertIn(("am", "am.only"), by)
        self.assertNotIn(("am", "kz.only"), by)
        self.assertNotIn(("kz", "am.only"), by)

    def test_single_application_run_is_unaffected_by_others(self):
        args = dict(
            db_rows=[db("kz", "a"), db("am", "a")],
            dict_rows=[dct("kz", "a", "website"), dct("am", "a")],
            usage=usage(web=["a"]),
        )
        only_kz, _ = reconcile.run(applications=["kz"], **args)
        both, _ = reconcile.run(**args)
        self.assertEqual(
            index(only_kz)[("kz", "a")], index(both)[("kz", "a")]
        )


class TestStatusRules(unittest.TestCase):

    def test_source_missing(self):
        # Spec section 8, example 1.
        rows, _ = reconcile.run(
            db_rows=[], dict_rows=[dct("kz", "customer.balance")],
            usage=usage(web=["customer.balance"]),
        )
        r = index(rows)[("kz", "customer.balance")]
        self.assertEqual(r["actual_usage"], "website")
        self.assertEqual(r["db_source"], "")
        self.assertEqual(r["expected_source"], "website")
        self.assertEqual(r["status"], Status.SOURCE_MISSING)

    def test_source_mismatch(self):
        # Spec section 8, example 2: mobile usage, website recorded.
        rows, _ = reconcile.run(
            db_rows=[], dict_rows=[dct("kz", "customer.balance", "website")],
            usage=usage(mobile=["customer.balance"]),
        )
        r = index(rows)[("kz", "customer.balance")]
        self.assertEqual(r["actual_usage"], "mobile")
        self.assertEqual(r["db_source"], "website")
        self.assertEqual(r["expected_source"], "mobile")
        self.assertEqual(r["status"], Status.SOURCE_MISMATCH)

    def test_multiple_sources_not_arbitrarily_resolved(self):
        # Spec section 8, example 3.
        rows, _ = reconcile.run(
            db_rows=[], dict_rows=[dct("kz", "k", "website")],
            usage=usage(web=["k"], mobile=["k"]),
        )
        r = index(rows)[("kz", "k")]
        self.assertEqual(r["actual_usage"], "website,mobile")
        self.assertEqual(r["expected_source"], "website,mobile")
        self.assertEqual(r["status"], Status.MULTIPLE_SOURCES)

    def test_multi_source_outranks_source_missing(self):
        # Spec section 8 example 3 has no source recorded and is still
        # MULTIPLE_SOURCES, not SOURCE_MISSING: the remediation differs.
        rows, _ = reconcile.run(
            db_rows=[], dict_rows=[dct("kz", "k")],
            usage=usage(web=["k"], mobile=["k"]),
        )
        r = index(rows)[("kz", "k")]
        self.assertEqual(r["status"], Status.MULTIPLE_SOURCES)
        self.assertEqual(r["db_source"], "")
        self.assertEqual(r["expected_source"], "website,mobile")

    def test_multi_source_already_represented_is_ok(self):
        # The dictionary CAN hold several rows per key (unique on
        # key+app+source), so a fully represented multi-source key is not a
        # conflict and must not be reported as one.
        rows, _ = reconcile.run(
            db_rows=[],
            dict_rows=[dct("kz", "k", "website"), dct("kz", "k", "mobile")],
            usage=usage(web=["k"], mobile=["k"]),
        )
        r = index(rows)[("kz", "k")]
        self.assertEqual(r["status"], Status.OK)
        self.assertEqual(r["db_source"], "website,mobile")

    def test_unused_requires_no_usage_at_all(self):
        # Spec section 9.
        rows, _ = reconcile.run(
            db_rows=[], dict_rows=[dct("kz", "old.key", "website")],
            usage=usage(),
        )
        r = index(rows)[("kz", "old.key")]
        self.assertEqual(r["status"], Status.UNUSED)
        self.assertEqual(r["actual_usage"], "none")

    def test_null_source_on_unused_key_is_still_unused(self):
        # Spec section 15: a NULL source must not be read as "unused", and
        # usage must outrank source problems.
        rows, _ = reconcile.run(
            db_rows=[], dict_rows=[dct("kz", "k")], usage=usage(),
        )
        self.assertEqual(index(rows)[("kz", "k")]["status"], Status.UNUSED)

    def test_db_presence_counts_as_backoffice_usage(self):
        # A key referenced by backoffice-managed content is not dead.
        rows, _ = reconcile.run(
            db_rows=[db("kz", "k", table="notification_template")],
            dict_rows=[dct("kz", "k", "backoffice")], usage=usage(),
        )
        r = index(rows)[("kz", "k")]
        self.assertEqual(r["used_in_backend"], "YES")
        self.assertEqual(r["status"], Status.OK)
        self.assertNotEqual(r["status"], Status.UNUSED)

    def test_dictionary_only_key_is_not_missing(self):
        # Spec section 11.
        rows, _ = reconcile.run(
            db_rows=[], dict_rows=[dct("kz", "customer.name", "website")],
            usage=usage(web=["customer.name"]),
        )
        r = index(rows)[("kz", "customer.name")]
        self.assertEqual(r["exists_in_dictionary"], "YES")
        self.assertEqual(r["exists_in_db_sources"], "NO")
        self.assertNotEqual(r["status"], Status.MISSING_IN_DATABASE)


class TestSourcePreservation(unittest.TestCase):
    """Spec section 12: never collapse multiple DB rows to one."""

    def test_all_source_tables_and_columns_preserved(self):
        rows, _ = reconcile.run(
            db_rows=[
                db("kz", "customer.name", "notification_template", "title"),
                db("kz", "customer.name", "page", "name"),
            ],
            dict_rows=[], usage=usage(),
        )
        r = index(rows)[("kz", "customer.name")]
        self.assertEqual(r["db_source_table"], "notification_template,page")
        self.assertEqual(r["db_source_column"], "name,title")

    def test_relation_and_global_scopes_are_kept(self):
        rows, _ = reconcile.run(
            db_rows=[db("kz", "k", "city", "name", "relation"),
                     db("kz", "k", "country", "name", "global")],
            dict_rows=[], usage=usage(),
        )
        self.assertIn("scope=global,relation", index(rows)[("kz", "k")]["details"])


class TestMissingKeys(unittest.TestCase):
    """Spec section 10: used but absent is MISSING, never UNUSED."""

    def test_key_in_no_application_is_missing(self):
        rows, _ = reconcile.run(
            db_rows=[db("kz", "known")], dict_rows=[], usage=usage(web=["ghost"]),
        )
        by = index(rows)
        self.assertEqual(by[(UNMAPPED, "ghost")]["status"],
                         Status.MISSING_IN_DATABASE)
        self.assertNotEqual(by[(UNMAPPED, "ghost")]["status"], Status.UNUSED)

    def test_missing_key_reported_once_not_per_application(self):
        rows, _ = reconcile.run(
            db_rows=[db("kz", "a"), db("am", "b")], dict_rows=[],
            usage=usage(web=["ghost"]),
        )
        ghosts = [r for r in rows if r["key"] == "ghost"]
        self.assertEqual(len(ghosts), 1)


class TestGlobalScoping(unittest.TestCase):

    def test_global_scoping_credits_every_application(self):
        rows, _ = reconcile.run(
            db_rows=[db("kz", "x"), db("am", "y")], dict_rows=[dct("am", "y")],
            usage=usage(web=["shared"]), scoping="global",
        )
        by = index(rows)
        self.assertEqual(by[("kz", "shared")]["used_in_web"], "YES")
        self.assertEqual(by[("am", "shared")]["used_in_web"], "YES")
        self.assertEqual(by[("kz", "shared")]["status"],
                         Status.MISSING_IN_DATABASE)

    def test_global_scoping_does_not_double_count(self):
        rows, _ = reconcile.run(
            db_rows=[db("kz", "x")], dict_rows=[], usage=usage(web=["ghost"]),
            scoping="global",
        )
        self.assertEqual([r["application_code"] for r in rows
                          if r["key"] == "ghost"], ["kz"])


class TestSummary(unittest.TestCase):

    def test_multi_application_keys_counted(self):
        rows, apps = reconcile.run(
            db_rows=[db("kz", "shared"), db("am", "shared"), db("kz", "kzonly")],
            dict_rows=[], usage=usage(),
        )
        s = dict((x["application_code"], x) for x in reconcile.summarise(rows, apps))
        self.assertEqual(s["kz"]["multi_application_keys"], 1)
        self.assertEqual(s["am"]["multi_application_keys"], 1)
        self.assertEqual(s["kz"]["total_keys"], 2)

    def test_counts_are_per_application(self):
        rows, apps = reconcile.run(
            db_rows=[], dict_rows=[dct("kz", "a"), dct("am", "b"), dct("am", "c")],
            usage=usage(),
        )
        s = dict((x["application_code"], x) for x in reconcile.summarise(rows, apps))
        self.assertEqual(s["kz"]["unused_keys"], 1)
        self.assertEqual(s["am"]["unused_keys"], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestActions(unittest.TestCase):
    """Each status carries the concrete edit that resolves it."""

    def test_unused_says_delete(self):
        rows, _ = reconcile.run(
            db_rows=[], dict_rows=[dct("kz", "old.key", "website")], usage=usage())
        r = index(rows)[("kz", "old.key")]
        self.assertEqual(r["status"], Status.UNUSED)
        self.assertEqual(r["action"], "delete from dictionary")

    def test_source_missing_names_the_source_to_set(self):
        rows, _ = reconcile.run(
            db_rows=[], dict_rows=[dct("kz", "k")], usage=usage(web=["k"]))
        self.assertEqual(index(rows)[("kz", "k")]["action"], "set source = website")

    def test_source_missing_for_mobile(self):
        rows, _ = reconcile.run(
            db_rows=[], dict_rows=[dct("kz", "k")], usage=usage(mobile=["k"]))
        self.assertEqual(index(rows)[("kz", "k")]["action"], "set source = mobile")

    def test_mismatch_shows_both_ends(self):
        rows, _ = reconcile.run(
            db_rows=[], dict_rows=[dct("kz", "k", "website")], usage=usage(mobile=["k"]))
        self.assertEqual(index(rows)[("kz", "k")]["action"],
                         u"change source website \u2192 mobile")

    def test_multiple_sources_adds_a_row_per_missing_source(self):
        rows, _ = reconcile.run(
            db_rows=[], dict_rows=[dct("kz", "k")], usage=usage(web=["k"], mobile=["k"]))
        self.assertEqual(index(rows)[("kz", "k")]["action"],
                         "add row for website,mobile")

    def test_multiple_sources_only_names_what_is_missing(self):
        rows, _ = reconcile.run(
            db_rows=[], dict_rows=[dct("kz", "k", "website")],
            usage=usage(web=["k"], mobile=["k"]))
        self.assertEqual(index(rows)[("kz", "k")]["action"], "add row for mobile")

    def test_multiple_sources_removes_a_stale_row(self):
        rows, _ = reconcile.run(
            db_rows=[], dict_rows=[dct("kz", "k", "backoffice")],
            usage=usage(web=["k"], mobile=["k"]))
        self.assertEqual(index(rows)[("kz", "k")]["action"],
                         "add row for website,mobile; remove row for backoffice")

    def test_missing_in_database_says_add(self):
        rows, _ = reconcile.run(
            db_rows=[db("kz", "known")], dict_rows=[], usage=usage(web=["ghost"]))
        self.assertEqual(index(rows)[(UNMAPPED, "ghost")]["action"],
                         "add to dictionary with source = website")

    def test_ok_has_no_action(self):
        rows, _ = reconcile.run(
            db_rows=[], dict_rows=[dct("kz", "k", "website")], usage=usage(web=["k"]))
        r = index(rows)[("kz", "k")]
        self.assertEqual(r["status"], Status.OK)
        self.assertEqual(r["action"], "")

    def test_backoffice_key_with_no_source(self):
        # The dominant real case: DB content references it, source is NULL.
        rows, _ = reconcile.run(
            db_rows=[db("kz", "k", "notification_template")],
            dict_rows=[dct("kz", "k")], usage=usage())
        self.assertEqual(index(rows)[("kz", "k")]["action"],
                         "set source = backoffice")


class TestScopeFromDbFiles(unittest.TestCase):
    """The uploaded DB exports decide which applications are reconciled."""

    def _dict_all(self):
        return [dct(a, "a") for a in ("am", "cy", "kz", "ru", "uz")]

    def test_one_db_file_reconciles_one_application(self):
        rows, apps = reconcile.run(
            db_rows=[db("kz", "a")], dict_rows=self._dict_all(), usage=usage())
        self.assertEqual(apps, ["kz"])
        self.assertEqual(
            sorted(set(r["application_code"] for r in rows)), ["kz"])

    def test_two_db_files_reconcile_two_applications(self):
        rows, apps = reconcile.run(
            db_rows=[db("kz", "a"), db("am", "a")],
            dict_rows=self._dict_all(), usage=usage())
        self.assertEqual(apps, ["am", "kz"])
        self.assertEqual(
            sorted(set(r["application_code"] for r in rows)), ["am", "kz"])

    def test_dictionary_only_applications_emit_no_rows(self):
        # The old behaviour reported these as entirely UNUSED, which reads
        # as a real finding rather than as a missing export.
        rows, _ = reconcile.run(
            db_rows=[db("kz", "a")], dict_rows=self._dict_all(), usage=usage())
        for skipped in ("am", "cy", "ru", "uz"):
            self.assertEqual(
                [r for r in rows if r["application_code"] == skipped], [])

    def test_explicit_application_still_wins(self):
        _, apps = reconcile.run(
            db_rows=[db("kz", "a"), db("am", "a")], dict_rows=self._dict_all(),
            usage=usage(), applications=["am"])
        self.assertEqual(apps, ["am"])

    def test_dictionary_only_run_still_works(self):
        # No DB export at all: fall back to the dictionary rather than
        # producing nothing.
        _, apps = reconcile.run(
            db_rows=[], dict_rows=[dct("kz", "a"), dct("am", "a")], usage=usage())
        self.assertEqual(apps, ["am", "kz"])

    def test_missing_keys_ignore_out_of_scope_applications(self):
        # `ghost` exists only in a skipped application, so from the point of
        # view of this run it is genuinely unaccounted for.
        rows, _ = reconcile.run(
            db_rows=[db("kz", "a")],
            dict_rows=[dct("kz", "a"), dct("am", "ghost")],
            usage=usage(web=["ghost"]))
        by = index(rows)
        self.assertEqual(by[(UNMAPPED, "ghost")]["status"],
                         Status.MISSING_IN_DATABASE)


class TestKeysHeldInDataFiles(unittest.TestCase):
    """A key is not always a literal inside the t() call.

    Real code keeps keys in a data file and maps over them:

        export const points = ['about_us.our_way.point_2']
        {points.map((k) => <p>{t(k)}</p>)}

    No call pattern can see that. Left unmatched the key scans as unused and
    the report asks for it to be deleted, while the page is rendering it.
    """

    PATTERNS = [
        r"""\bt\(\s*['"`]([^'"`\n]+)['"`]""",
    ]

    def _repo(self):
        import shutil
        import tempfile

        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, True)
        app = os.path.join(root, "app")
        os.makedirs(app)
        with open(os.path.join(app, "data.tsx"), "w") as fh:
            fh.write("export const points = [\n"
                     "  'about_us.our_way.point_2',\n"
                     "  'not.in.the.dictionary'\n"
                     "];\n")
        with open(os.path.join(app, "view.tsx"), "w") as fh:
            fh.write("const V = () => points.map((k) => t(k));\n"
                     "const W = () => t('called.directly');\n")
        return root

    def _scan(self, root, known, stats=None):
        from trkeys import extract
        return extract.code_keys(
            {"root": root, "include": ["app"], "extensions": [".tsx"]},
            self.PATTERNS, [], known=known, stats=stats,
        )

    def test_call_scan_alone_misses_the_data_file_key(self):
        hits = self._scan(self._repo(), None)
        self.assertIn("called.directly", hits)
        self.assertNotIn("about_us.our_way.point_2", hits)

    def test_a_known_key_in_a_data_file_counts_as_used(self):
        root = self._repo()
        hits = self._scan(root, {"about_us.our_way.point_2"})
        self.assertIn("about_us.our_way.point_2", hits)
        self.assertEqual(hits["about_us.our_way.point_2"], ["app/data.tsx:2"])

    def test_an_unknown_literal_is_never_invented(self):
        """The membership test is the whole safety property: no literal that
        the dictionary does not already carry can enter the key set, so
        MISSING_IN_DATABASE cannot grow."""
        hits = self._scan(self._repo(), {"about_us.our_way.point_2"})
        self.assertNotIn("not.in.the.dictionary", hits)
        self.assertNotIn("export const points = [", hits)

    def test_stats_separate_calls_from_literals(self):
        stats = {}
        self._scan(self._repo(), {"about_us.our_way.point_2"}, stats)
        self.assertEqual(stats["calls"], 1)          # called.directly
        self.assertEqual(stats["literal_only"], 1)   # the data-file key

    def test_the_rescued_key_is_no_longer_deleted(self):
        """End to end: the status and the action both change."""
        key = "about_us.our_way.point_2"
        dict_rows = [dct("kz", key, "website")]

        dead, _ = reconcile.run([], dict_rows, usage(), scoping="existing")
        self.assertEqual(dead[0]["status"], Status.UNUSED)
        self.assertEqual(dead[0]["action"], "delete from dictionary")

        alive, _ = reconcile.run([], dict_rows, usage(web=[key]),
                                 scoping="existing")
        self.assertEqual(alive[0]["status"], Status.OK)
        self.assertEqual(alive[0]["action"], "")


class TestLiteralShape(unittest.TestCase):
    """A literal has to look like a key path before membership is tested.

    The dictionary genuinely holds bare words -- "approved", "arrived",
    "auth", "Addresses", "-". Every codebase also holds those strings as
    enum members, union types and route names, and matching them credited
    ~30 keys per repository as used on no evidence at all.
    """

    def _scan(self, text, known):
        import shutil
        import tempfile

        from trkeys import extract
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, True)
        os.makedirs(os.path.join(root, "app"))
        with open(os.path.join(root, "app", "a.ts"), "w") as fh:
            fh.write(text)
        return extract.code_keys(
            {"root": root, "include": ["app"], "extensions": [".ts"]},
            [r"""\bt\(\s*['"`]([^'"`\n]+)['"`]"""], [], known=known,
        )

    KNOWN = {"approved", "arrived", "Addresses", "-", "a.b", "a_b", "a.b_c",
             "home.how-it-works.image", "profile.balance.fill-balance",
             "\u0448\u0435\u0439\u043a\u0435\u0440.name", "^[0-9]+$"}

    def test_bare_words_are_not_matched_as_literals(self):
        hits = self._scan("type S = 'approved' | 'arrived';\n"
                          "const r = 'Addresses'; const d = '-';\n", self.KNOWN)
        self.assertEqual(hits, {})

    def test_separated_keys_are_matched(self):
        hits = self._scan("const xs = ['a.b', 'a_b', 'a.b_c'];\n", self.KNOWN)
        self.assertEqual(sorted(hits), ["a.b", "a.b_c", "a_b"])

    def test_the_shape_filter_does_not_touch_call_matches(self):
        """A key spelled as a bare word is still a key when the call says so.

        The filter exists to judge an ambiguous literal. t('approved') is
        not ambiguous.
        """
        hits = self._scan("t('approved');\n", self.KNOWN)
        self.assertIn("approved", hits)

    def test_hyphens_separate_too(self):
        """14 hyphenated keys are used across the two front ends --
        home.how-it-works.image, profile.balance.fill-balance. Admitting the
        hyphen is safe: the only known keys that are a single hyphenated word
        are "-" and two stray regex strings, and none can satisfy the shape."""
        hits = self._scan("const xs = ['home.how-it-works.image',\n"
                          "  'profile.balance.fill-balance'];\n", self.KNOWN)
        self.assertEqual(sorted(hits),
                         ["home.how-it-works.image", "profile.balance.fill-balance"])

    def test_a_non_ascii_key_is_not_dropped(self):
        """Keys are generated from DB content, so they carry whatever the
        content says -- product-category.<cyrillic>.name is a real one."""
        key = "\u0448\u0435\u0439\u043a\u0435\u0440.name"
        hits = self._scan("const xs = ['%s'];\n" % key, self.KNOWN)
        self.assertIn(key, hits)

    def test_a_regex_string_is_not_a_key(self):
        hits = self._scan("const re = '^[0-9]+$';\n", self.KNOWN)
        self.assertEqual(hits, {})

    def test_a_source_can_opt_out_entirely(self):
        """config.json sets literal_keys=false on backoffice: its enums are
        snake_case strings the dictionary keys were named after, so even
        key-shaped matches there are collisions."""
        from trkeys import config as config_mod
        cfg = config_mod.load(os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "config.json"))
        self.assertFalse(cfg["sources"]["backoffice"].get("literal_keys", True))
        self.assertTrue(cfg["sources"]["website"].get("literal_keys", True))
        self.assertTrue(cfg["sources"]["mobile"].get("literal_keys", True))
