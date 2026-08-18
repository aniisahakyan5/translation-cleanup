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
