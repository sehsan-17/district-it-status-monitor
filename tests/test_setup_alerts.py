"""Offline tests for ops/setup_alerts.py: request flow, idempotency, and policy shape."""

import contextlib
import importlib
import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
with patch.object(sys, "path", [str(ROOT / "ops"), *sys.path]):
    ALERTS = importlib.import_module("setup_alerts")

ARGS = ["--project", "demo-project", "--region", "us-east1", "--job", "district-status-collector",
        "--email", "alerts@example.com", "--bucket", "demo-project-district-history"]


class FakeMonitoring:
    def __init__(self, channels=(), policies=(), results=("succeeded",), labels=("result",)):
        self.channels, self.policies, self.results, self.labels = list(channels), list(policies), results, labels
        self.calls = []

    def __call__(self, request, timeout):
        url = urlparse(request.full_url)
        path = url.path.removeprefix("/v3/")
        body = json.loads(request.data) if request.data else None
        self.calls.append((request.get_method(), path, parse_qs(url.query), body))
        assert request.headers["Authorization"] == "Bearer test-token"
        method = request.get_method()
        if "/metricDescriptors/" in path:
            payload = {"metricKind": "DELTA", "labels": [{"key": key} for key in self.labels]}
        elif path.endswith("/timeSeries"):
            payload = {"timeSeries": [{"metric": {"labels": {"result": r}}, "points": [{"value": {"int64Value": "3"}}]}
                                      for r in self.results]}
        elif path.endswith("/notificationChannels") and method == "GET":
            payload = {"notificationChannels": self.channels}
        elif path.endswith("/notificationChannels"):
            payload = {"name": "projects/demo-project/notificationChannels/new"}
        elif path.endswith("/alertPolicies") and method == "GET":
            payload = {"alertPolicies": self.policies}
        else:
            payload = {"name": path if method == "PATCH" else "projects/demo-project/alertPolicies/new"}
        return contextlib.nullcontext(io.BytesIO(json.dumps(payload).encode()))

    def writes(self):
        return [(method, path, body) for method, path, _, body in self.calls if method in ("POST", "PATCH")]


class SetupAlertsTests(unittest.TestCase):
    def run_setup(self, fake, args=ARGS):
        with patch.dict(os.environ, {"ACCESS_TOKEN": "test-token"}), patch.object(ALERTS, "urlopen", side_effect=fake), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            code = ALERTS.main(args)
        self.assertNotIn("test-token", out.getvalue())
        return code

    def test_first_run_creates_channel_and_two_policies(self):
        fake = FakeMonitoring(results=("succeeded", "failed"))
        self.assertEqual(self.run_setup(fake), 0)
        writes = fake.writes()
        self.assertEqual([w[0] for w in writes], ["POST", "POST", "POST"])
        self.assertEqual(writes[0][2]["labels"], {"email_address": "alerts@example.com"})
        failed, absent = writes[1][2], writes[2][2]
        self.assertEqual(failed["displayName"], ALERTS.FAILED_NAME)
        threshold = failed["conditions"][0]["conditionThreshold"]
        self.assertIn('metric.labels.result = "failed"', threshold["filter"])
        self.assertIn('resource.labels.job_name = "district-status-collector"', threshold["filter"])
        self.assertEqual((threshold["comparison"], threshold["thresholdValue"]), ("COMPARISON_GT", 1))
        self.assertEqual(threshold["aggregations"][0]["alignmentPeriod"], "2700s")
        # The API rejects evaluationMissingData on a condition with a zero duration.
        self.assertEqual(threshold["evaluationMissingData"], "EVALUATION_MISSING_DATA_INACTIVE")
        self.assertNotEqual(threshold["duration"], "0s")
        condition = absent["conditions"][0]["conditionAbsent"]
        self.assertIn('metric.labels.result = "succeeded"', condition["filter"])
        self.assertEqual(condition["duration"], "3600s")
        for policy in (failed, absent):
            self.assertEqual(policy["notificationChannels"], ["projects/demo-project/notificationChannels/new"])
            self.assertIn("gs://demo-project-district-history/logs/", policy["documentation"]["content"])

    def test_rerun_reuses_channel_and_updates_policies_in_place(self):
        fake = FakeMonitoring(
            channels=[{"name": "projects/demo-project/notificationChannels/7", "labels": {"email_address": "Alerts@Example.com"}}],
            policies=[{"displayName": ALERTS.FAILED_NAME, "name": "projects/demo-project/alertPolicies/1"},
                      {"displayName": ALERTS.ABSENT_NAME, "name": "projects/demo-project/alertPolicies/2"}])
        self.assertEqual(self.run_setup(fake), 0)
        self.assertEqual([(m, p) for m, p, _ in fake.writes()], [
            ("PATCH", "projects/demo-project/alertPolicies/1"), ("PATCH", "projects/demo-project/alertPolicies/2")])

    def test_stops_before_writing_when_metric_or_history_does_not_match(self):
        for fake in (FakeMonitoring(labels=("outcome",)), FakeMonitoring(results=("failed",)), FakeMonitoring(results=())):
            with self.subTest(), self.assertRaises(SystemExit):
                self.run_setup(fake)
            self.assertEqual(fake.writes(), [])

    def test_requires_token_and_plausible_email(self):
        with patch.dict(os.environ, {"ACCESS_TOKEN": ""}), self.assertRaises(SystemExit):
            ALERTS.main(ARGS)
        bad = ARGS[:7] + ["not-an-email"] + ARGS[8:]
        with patch.dict(os.environ, {"ACCESS_TOKEN": "test-token"}), self.assertRaises(SystemExit):
            ALERTS.main(bad)


if __name__ == "__main__":
    unittest.main()
