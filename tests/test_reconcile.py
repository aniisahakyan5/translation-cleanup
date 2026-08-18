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
            db_rows=[db("kz", "customer.balance")],
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
            db_rows=[db("kz", "shared.key")],
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
            db_rows=[],
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
            db_rows=[db("kz", "x")], dict_rows=[dct("am", "y")],
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
