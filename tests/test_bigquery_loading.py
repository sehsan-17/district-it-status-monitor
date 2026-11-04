import contextlib
from datetime import datetime, timedelta, timezone
import importlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parent.parent
with patch.object(sys, "path", [str(ROOT / "scripts"), *sys.path]):
    SUPPORT = importlib.import_module("bigquery_support")
    LOADER = importlib.import_module("load_status_bigquery")
try:
    from google.cloud import bigquery
    from google.api_core.exceptions import BadRequest, Forbidden
    from google.auth.exceptions import DefaultCredentialsError, RefreshError
except ImportError:
    bigquery = None


class BigQueryHistoryTests(unittest.TestCase):
    def record(self, **changes):
        row = {"vendor": "Synthetic Example", "source_type": "synthetic",
               "observed_at": "2026-11-02T14:00:00Z", "status_indicator": "none",
               "status_description": "Synthetic healthy example", "components": [{"private": "not uploaded"}]}
        row.update(changes)
        return row

    def read(self, lines):
        with tempfile.TemporaryDirectory(prefix="district-bq-test-") as directory:
            path = Path(directory) / "history.jsonl"
            path.write_text("\n".join(json.dumps(line) if not isinstance(line, str) else line for line in lines), encoding="utf-8")
            return LOADER.read_history(path)

    def test_timestamp_normalization_collapses_equivalent_duplicate_keys(self):
        rows, duplicates = self.read([self.record(), self.record(observed_at="2026-11-02T09:00:00-05:00")])
        self.assertEqual(len(rows), 1)
        self.assertEqual(duplicates, 1)
        self.assertEqual(rows[0]["observed_at"], "2026-11-02T14:00:00.000000+00:00")
        self.assertEqual(set(rows[0]), set(SUPPORT.FIELDS))
        self.assertNotIn("not uploaded", json.dumps(rows))

    def test_later_collection_and_different_source_remain_distinct(self):
        rows, duplicates = self.read([self.record(), self.record(observed_at="2026-11-02T14:15:00Z"), self.record(source_type="external_monitor")])
        self.assertEqual(len(rows), 3)
        self.assertEqual(duplicates, 0)

    def test_conflicting_duplicate_fails_instead_of_silently_rewriting(self):
        with self.assertRaisesRegex(LOADER.HistoryValidationError, "Conflicting duplicate"):
            self.read([self.record(), self.record(status_indicator="issues")])

    def test_bad_lines_missing_values_and_naive_times_fail_without_payload_echo(self):
        for row in ("not json", [], self.record(vendor=None), self.record(status_description=""), self.record(observed_at="2026-11-02T14:00:00")):
            with self.subTest(row=row):
                with self.assertRaises(LOADER.HistoryValidationError) as raised:
                    self.read([row])
                self.assertNotIn("not uploaded", str(raised.exception))

    def test_blank_file_has_no_uploadable_rows(self):
        self.assertEqual(self.read(["", " "]), ([], 0))

    def test_configuration_rejects_sql_injection_and_invalid_limits(self):
        base = json.loads(SUPPORT.DEFAULT_CONFIG.read_text())
        for change in ({"project_id": "x`; DROP TABLE x;--"}, {"dataset_id": "dataset.with.dot"},
                       {"location": "US'; DROP SCHEMA x;--"}, {"maximum_bytes_billed": True},
                       {"staging_expiration_hours": 0}):
            with self.subTest(change=change), tempfile.TemporaryDirectory(prefix="district-bq-config-") as directory:
                path = Path(directory) / "config.json"
                path.write_text(json.dumps({**base, **change}), encoding="utf-8")
                with self.assertRaises(SUPPORT.BigQueryConfigurationError):
                    SUPPORT.load_settings(path)

    def test_templates_have_correct_scope_and_strict_freshness_boundary(self):
        settings = SUPPORT.load_settings()
        setup = SUPPORT.render_sql("001_setup.sql", settings)
        self.assertIn("source_type IN ('vendor_reported', 'external_monitor')", setup)
        self.assertIn("observed_at < TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 MINUTE)", setup)
        self.assertIn("PARTITION BY vendor, source_type", setup)
        self.assertNotIn("{{", setup)
        self.assertIn("partition_expiration_days = NULL", setup)
        stage = settings.dataset + ".status_observations_stage_" + "a" * 32
        merge = SUPPORT.render_sql("002_merge.sql", settings, stage)
        self.assertLess(merge.index("UPDATE"), merge.index("MERGE"))
        self.assertIn("ASSERT @@row_count = 1", merge)
        self.assertIn("PARTITION BY vendor, source_type, observed_at", merge)
        self.assertIn("COMMIT TRANSACTION", merge)
        with self.assertRaises(SUPPORT.BigQueryConfigurationError):
            SUPPORT.render_sql("002_merge.sql", settings, "untrusted`table")

    def test_validate_only_makes_no_cloud_calls(self):
        with tempfile.TemporaryDirectory(prefix="district-bq-local-") as directory:
            path = Path(directory) / "history.jsonl"
            path.write_text(json.dumps(self.record()) + "\n", encoding="utf-8")
            with patch.object(sys, "argv", ["loader", "--validate-only", "--input", str(path)]), patch.object(LOADER, "get_client") as client, patch.object(LOADER, "verify_bigquery_access") as verify, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(LOADER.main(), 0)
            client.assert_not_called()
            verify.assert_not_called()


@unittest.skipIf(bigquery is None, "Optional BigQuery library is not installed")
class BigQueryCloudContractTests(unittest.TestCase):
    def setUp(self):
        self.settings = SUPPORT.load_settings()
        self.records = [{"vendor": "Synthetic Example", "source_type": "synthetic",
                         "observed_at": "2026-11-02T14:00:00Z", "status_indicator": "none",
                         "status_description": "Synthetic example"}]
        self.client = Mock()
        self.client.load_table_from_json.return_value.job_id = "synthetic-load-job"
        self.client.query.return_value.job_id = "synthetic-merge-job"
        self.client.query.return_value.result.return_value = [{"inserted_rows": 1}]

    def run_batch(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return LOADER.load_batch(self.client, self.settings, self.records)

    def test_upload_is_atomic_batch_with_expiring_isolated_stage(self):
        self.assertEqual(self.run_batch(), (1, 0))
        table = self.client.create_table.call_args.args[0]
        self.assertGreater(table.expires, datetime.now(timezone.utc) + timedelta(hours=23))
        self.assertEqual([f.name for f in table.schema], list(SUPPORT.FIELDS))
        args = self.client.load_table_from_json.call_args
        self.assertEqual(args.kwargs["job_config"].write_disposition, "WRITE_EMPTY")
        self.assertEqual(args.kwargs["job_config"].create_disposition, "CREATE_NEVER")
        self.assertEqual(args.kwargs["location"], self.settings.location)
        self.client.insert_rows_json.assert_not_called()
        self.assertNotIn("TRUNCATE", self.client.query.call_args.args[0])
        self.client.delete_table.assert_called_once()

    def test_each_run_has_a_distinct_stage(self):
        self.run_batch(); self.run_batch()
        names = [str(call.args[0].reference) for call in self.client.create_table.call_args_list]
        self.assertNotEqual(names[0], names[1])

    def test_failed_load_never_submits_merge_or_deletes_running_stage(self):
        self.client.load_table_from_json.return_value.result.side_effect = TimeoutError()
        with self.assertRaises(TimeoutError):
            self.run_batch()
        self.client.query.assert_not_called()
        self.client.delete_table.assert_not_called()

    def test_merge_conflict_or_timeout_keeps_expiring_stage_for_safe_retry(self):
        for error in (BadRequest("synthetic concurrent update"), TimeoutError()):
            with self.subTest(error=error):
                self.client.query.return_value.result.side_effect = error
                with self.assertRaises(type(error)):
                    self.run_batch()
                self.client.delete_table.assert_not_called()

    def test_no_new_rows_reports_skip_count(self):
        self.client.query.return_value.result.return_value = [{"inserted_rows": 0}]
        self.assertEqual(self.run_batch(), (0, 1))

    def test_invalid_merge_count_is_not_reported_as_success(self):
        self.client.query.return_value.result.return_value = [{"inserted_rows": 2}]
        with self.assertRaises(SUPPORT.BigQueryConfigurationError):
            self.run_batch()

    def test_cleanup_error_does_not_invalidate_committed_import(self):
        self.client.delete_table.side_effect = Forbidden("synthetic cleanup denied")
        self.assertEqual(self.run_batch(), (1, 0))

    def test_query_cost_limit_is_applied(self):
        self.run_batch()
        config = self.client.query.call_args.kwargs["job_config"]
        self.assertEqual(config.maximum_bytes_billed, 104857600)
        self.assertFalse(config.use_legacy_sql)

    def test_missing_adc_message_does_not_echo_private_exception(self):
        with patch.object(bigquery, "Client", side_effect=DefaultCredentialsError("SYNTHETIC_SECRET")):
            with self.assertRaises(SUPPORT.BigQueryConfigurationError) as raised:
                SUPPORT.get_client(self.settings)
        self.assertNotIn("SYNTHETIC_SECRET", str(raised.exception))

    def test_preflight_auth_errors_are_sanitized_and_client_closed(self):
        for error in (Forbidden("SYNTHETIC_SECRET"), RefreshError("SYNTHETIC_SECRET")):
            with self.subTest(error=error):
                self.client.get_dataset.side_effect = error
                with patch.object(SUPPORT, "get_client", return_value=self.client):
                    with self.assertRaises(SUPPORT.BigQueryConfigurationError) as raised:
                        SUPPORT.verify_bigquery_access(self.settings)
                self.assertNotIn("SYNTHETIC_SECRET", str(raised.exception))
                self.client.close.assert_called()

    def test_preflight_rejects_sandbox_expiration_on_history_and_control(self):
        dataset = bigquery.Dataset(self.settings.dataset)
        dataset.location = self.settings.location
        target = bigquery.Table(self.settings.target, schema=[
            bigquery.SchemaField(name, "TIMESTAMP" if name == "observed_at" else "STRING", mode="REQUIRED")
            for name in SUPPORT.FIELDS
        ])
        target.time_partitioning = bigquery.TimePartitioning()
        tables = {self.settings.target: target}
        for name in ("ingestion_control", "v_latest_status", "v_observations_hourly"):
            tables[self.settings.dataset + "." + name] = bigquery.Table(self.settings.dataset + "." + name)
        self.client.get_dataset.return_value = dataset
        self.client.get_table.side_effect = lambda name, **kwargs: tables[name]
        with patch.object(SUPPORT, "get_client", return_value=self.client):
            self.assertEqual(SUPPORT.verify_bigquery_access(self.settings), self.settings)
            for name in tables:
                with self.subTest(table=name):
                    tables[name].expires = datetime.now(timezone.utc) + timedelta(days=60)
                    with self.assertRaisesRegex(SUPPORT.BigQueryConfigurationError, "expir"):
                        SUPPORT.verify_bigquery_access(self.settings)
                    tables[name].expires = None
            target.time_partitioning.expiration_ms = 60 * 24 * 60 * 60 * 1000
            with self.assertRaisesRegex(SUPPORT.BigQueryConfigurationError, "expiration"):
                SUPPORT.verify_bigquery_access(self.settings)
        self.client.close.assert_called()

    def test_preflight_rejects_wrong_region_or_schema(self):
        self.client.get_dataset.return_value.location = "EU"
        with patch.object(SUPPORT, "get_client", return_value=self.client):
            with self.assertRaises(SUPPORT.BigQueryConfigurationError):
                SUPPORT.verify_bigquery_access(self.settings)
        self.client.get_dataset.return_value.location = self.settings.location
        self.client.get_table.return_value.schema = []
        with patch.object(SUPPORT, "get_client", return_value=self.client):
            with self.assertRaises(SUPPORT.BigQueryConfigurationError):
                SUPPORT.verify_bigquery_access(self.settings)


if __name__ == "__main__":
    unittest.main()
