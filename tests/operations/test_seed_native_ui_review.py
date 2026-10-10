"""Pure guard and transaction controls; no application import or database."""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch


SOURCE = Path(__file__).resolve().parents[2] / "scripts/operations/seed_native_ui_review.py"
SPEC = importlib.util.spec_from_file_location("seed_native_ui_review", SOURCE)
assert SPEC is not None and SPEC.loader is not None
SEED = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SEED)


def environment():
    return {
        "REVIEW_DISPOSABLE_DATABASE": "1", "REVIEW_SOURCE_SHA": SEED.SOURCE_REVISION,
        "REVIEW_PUBLISHER_ORIGIN": "http://review-source:8765",
        "ADMIN_EMAIL": "fresh-review@example.invalid", "AI_ENABLED": "true",
        "ALLOW_PRIVATE_NETWORK_FETCH": "true",
    }


def counts():
    result = {name: limits[0] for name, limits in SEED.BASELINE_COUNTS.items()}
    result.update({"feeds": 0, "teams": 0, "auth_sessions": 0, "api_tokens": 0})
    return result


class SeedGuardTests(unittest.TestCase):
    def test_owned_environment_is_normalized(self):
        values = environment()
        values["ADMIN_EMAIL"] = " FRESH-REVIEW@example.invalid "
        values["REVIEW_PUBLISHER_ORIGIN"] += "/"
        self.assertEqual(SEED.validate_environment(values), {
            "admin_email": "fresh-review@example.invalid",
            "publisher_origin": "http://review-source:8765",
        })

    def test_required_environment_guards_fail_before_seed_or_output(self):
        for name, value in (
            ("REVIEW_DISPOSABLE_DATABASE", "0"), ("REVIEW_SOURCE_SHA", "0" * 40),
            ("AI_ENABLED", "false"), ("ALLOW_PRIVATE_NETWORK_FETCH", "false"),
            ("ADMIN_EMAIL", ""), ("ADMIN_EMAIL", "invalid"),
            ("ADMIN_EMAIL", "two@@example.invalid"), ("ADMIN_EMAIL", "bad\n@example.invalid"),
        ):
            with self.subTest(name=name, value=value):
                values, callback, output, errors = environment(), Mock(), io.StringIO(), io.StringIO()
                values[name] = value
                self.assertEqual(SEED.main(values, seed=callback, stdout=output, stderr=errors), 1)
                callback.assert_not_called()
                self.assertEqual(output.getvalue(), "")
                self.assertEqual(errors.getvalue(), "native_ui_seed_failed error_type=ValueError\n")

    def test_only_exact_owned_publisher_is_allowed(self):
        for origin in (
            "https://review-source:8765", "http://example.invalid:8765",
            "http://review-source:8766", "http://review-source:8765/article.html",
            "http://review-source:8765?query=1", "http://review-source:8765#fragment",
            "http://user:secret@review-source:8765", "http://127.0.0.1:8765",
            "http://review-source:invalid", "http://review-source:8765\n",
        ):
            with self.subTest(origin=origin):
                values = environment()
                values["REVIEW_PUBLISHER_ORIGIN"] = origin
                with self.assertRaises(ValueError):
                    SEED.validate_environment(values)

    def test_exact_migration_catalog_and_empty_domain_rows_are_accepted(self):
        SEED.validate_counts(counts())

    def test_missing_catalog_or_changed_catalog_count_is_rejected(self):
        for name in SEED.BASELINE_COUNTS:
            with self.subTest(name=name):
                values = counts()
                del values[name]
                with self.assertRaises(ValueError):
                    SEED.validate_counts(values)
                values = counts()
                values[name] = SEED.BASELINE_COUNTS[name][1] + 1
                with self.assertRaises(ValueError):
                    SEED.validate_counts(values)

    def test_any_preexisting_domain_token_or_session_is_rejected(self):
        for name in ("feeds", "teams", "auth_sessions", "api_tokens", "unexpected_table"):
            with self.subTest(name=name):
                values = counts()
                values[name] = 1
                with self.assertRaises(ValueError):
                    SEED.validate_counts(values)

    def test_malformed_counts_are_rejected(self):
        for value in (True, -1, 1.0, "0", None):
            with self.subTest(value=value):
                values = counts()
                values["feeds"] = value
                with self.assertRaises(ValueError):
                    SEED.validate_counts(values)

    def test_stdout_is_json_only_and_contains_no_admin_credential(self):
        output, errors = io.StringIO(), io.StringIO()
        payload = json.dumps({"synthetic": True, "team_id": "synthetic-owned-id"})
        callback = Mock(return_value=payload)
        self.assertEqual(SEED.main(environment(), seed=callback, stdout=output, stderr=errors), 0)
        self.assertEqual(json.loads(output.getvalue()), json.loads(payload))
        self.assertEqual(output.getvalue(), payload + "\n")
        self.assertEqual(errors.getvalue(), "")
        self.assertNotIn(environment()["ADMIN_EMAIL"], output.getvalue())

    def test_failure_reports_type_without_exception_body_or_partial_json(self):
        output, errors = io.StringIO(), io.StringIO()
        callback = Mock(side_effect=RuntimeError("secret SQL password fixture body"))
        self.assertEqual(SEED.main(environment(), seed=callback, stdout=output, stderr=errors), 1)
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(errors.getvalue(), "native_ui_seed_failed error_type=RuntimeError\n")

    def test_fixture_text_has_real_long_reader_content(self):
        self.assertGreater(len(SEED.SUMMARY), 10000)
        self.assertGreater(len(SEED.ARTICLE_TEXT), 18000)
        self.assertIn("Synthetic defensive intelligence", SEED.TITLE)


class SeedTransactionTests(unittest.TestCase):
    def test_success_serializes_before_single_commit_then_closes(self):
        db, events = Mock(), []
        db.commit.side_effect = lambda: events.append("commit")
        db.close.side_effect = lambda: events.append("close")

        def operation(session):
            self.assertIs(session, db)
            events.append("operation")
            return {"synthetic": True}

        self.assertEqual(SEED.run_transaction(lambda: db, operation), '{"synthetic":true}')
        self.assertEqual(events, ["operation", "commit", "close"])
        db.commit.assert_called_once_with()
        db.rollback.assert_not_called()

    def test_write_failure_rolls_back_and_closes_without_commit(self):
        db = Mock()
        with self.assertRaises(RuntimeError):
            SEED.run_transaction(lambda: db, Mock(side_effect=RuntimeError("write failure")))
        db.commit.assert_not_called()
        db.rollback.assert_called_once_with()
        db.close.assert_called_once_with()

    def test_serialization_failure_rolls_back_uncommitted_fixture(self):
        db = Mock()
        with self.assertRaises(TypeError):
            SEED.run_transaction(lambda: db, lambda _: {"invalid": object()})
        db.commit.assert_not_called()
        db.rollback.assert_called_once_with()
        db.close.assert_called_once_with()

    def test_commit_failure_preserves_failure_and_rolls_back(self):
        db = Mock()
        db.commit.side_effect = RuntimeError("commit failure")
        with self.assertRaises(RuntimeError):
            SEED.run_transaction(lambda: db, lambda _: {"synthetic": True})
        db.commit.assert_called_once_with()
        db.rollback.assert_called_once_with()
        db.close.assert_called_once_with()

    def test_cancellation_also_rolls_back_and_closes(self):
        db = Mock()
        with self.assertRaises(KeyboardInterrupt):
            SEED.run_transaction(lambda: db, Mock(side_effect=KeyboardInterrupt()))
        db.commit.assert_not_called()
        db.rollback.assert_called_once_with()
        db.close.assert_called_once_with()


class SeedLoginCredentialTests(unittest.TestCase):
    def test_uses_the_actual_request_model_contract_without_returning_credentials(self):
        email, password = "native-review@example.com", "synthetic-validation-only-password"
        model = Mock(return_value=SimpleNamespace(email=email))
        self.assertIsNone(SEED.validate_login_credentials(email, password, request_model=model))
        model.assert_called_once_with(email=email, password=password)

    def test_request_schema_failure_is_preserved(self):
        model = Mock(side_effect=ValueError("private validation details"))
        with self.assertRaises(ValueError):
            SEED.validate_login_credentials("native-review@example.test", "synthetic-password", request_model=model)

    def test_schema_normalization_cannot_change_the_seeded_identity(self):
        model = Mock(return_value=SimpleNamespace(email="other@example.com"))
        with self.assertRaises(ValueError):
            SEED.validate_login_credentials("native-review@example.com", "synthetic-password", request_model=model)

    def test_invalid_login_credentials_fail_before_opening_database_session(self):
        config = ModuleType("app.core.config")
        config.get_settings = Mock(return_value=SimpleNamespace(admin_password="synthetic-password"))
        session = ModuleType("app.db.session")
        session.SessionLocal = Mock()
        with patch.dict(sys.modules, {"app.core.config": config, "app.db.session": session}), \
                patch.object(SEED, "validate_login_credentials", side_effect=ValueError("private validation details")) as validate:
            with self.assertRaises(ValueError):
                SEED.seed_database({"admin_email": "native-review@example.test"})
        validate.assert_called_once_with("native-review@example.test", "synthetic-password")
        session.SessionLocal.assert_not_called()


if __name__ == "__main__":
    unittest.main()
