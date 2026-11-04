"""Offline tests for the Statuspage collectors (Cisco Meraki, Incident IQ) and the scope inventory."""

import contextlib
from datetime import datetime, timedelta, timezone
import importlib
import io
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import URLError

ROOT = Path(__file__).resolve().parent.parent
with patch.object(sys, "path", [str(ROOT / "scripts"), *sys.path]):
    SUPPORT = importlib.import_module("statuspage_components")
    MERAKI = importlib.import_module("fetch_cisco_meraki_status")
    IIQ = importlib.import_module("fetch_incident_iq_status")
    GOOGLE = importlib.import_module("fetch_google_workspace_status")
    PIPELINE = importlib.import_module("run_pipeline")
    LOADER = importlib.import_module("load_status_bigquery")

NOW = datetime(2026, 11, 4, 18, 0, tzinfo=timezone.utc)
SERVICES = ["Dashboard", "API", "Network Device Connectivity", "End User Device Connectivity",
            "Access Point", "Switch", "WAN appliance", "Camera"]


def meraki_summary(regions=("North and South America", "Europe", "India")):
    components = []
    for r, region in enumerate(regions):
        group_id = f"group{r}"
        children = [f"{group_id}-{s}" for s in range(len(SERVICES))]
        components.append({"id": group_id, "name": region, "status": "operational", "group": True,
                           "group_id": None, "components": children})
        components += [{"id": child, "name": name, "status": "operational", "group": False, "group_id": group_id}
                       for child, name in zip(children, SERVICES)]
    return {"page": {"id": "page"}, "status": {"indicator": "none", "description": "All Systems Operational"},
            "components": components, "incidents": [], "scheduled_maintenances": []}


def iiq_summary():
    names = ["API", "Platform", "User Integrations", "Jamf Pro", "Mosyle"]
    return {"status": {"indicator": "none"}, "incidents": [], "scheduled_maintenances": [], "components": [
        {"id": f"c{i}", "name": name, "status": "operational", "group": name == "User Integrations",
         "group_id": "c2" if name in ("Jamf Pro", "Mosyle") else None} for i, name in enumerate(names)]}


def set_status(data, component_id, status):
    next(c for c in data["components"] if c["id"] == component_id)["status"] = status
    return data


class MerakiTests(unittest.TestCase):
    def test_all_operational_uses_only_the_americas_group(self):
        record = MERAKI.build_record(meraki_summary(), NOW)
        self.assertEqual(record["status_indicator"], "none")
        self.assertEqual(len(record["components"]), 8)
        self.assertTrue(all(c["id"].startswith("group0-") for c in record["components"]))
        self.assertIn("North and South America operational (8 components)", record["status_description"])
        self.assertEqual(record["observed_at"], "2026-11-04T18:00:00+00:00")

    def test_americas_component_issue_is_reported_with_its_name(self):
        record = MERAKI.build_record(set_status(meraki_summary(), "group0-5", "partial_outage"), NOW)
        self.assertEqual(record["status_indicator"], "issues")
        self.assertIn("Switch (partial outage)", record["status_description"])

    def test_other_region_outage_is_out_of_scope(self):
        data = set_status(meraki_summary(), "group2-0", "major_outage")
        data["incidents"] = [{"id": "i1", "status": "investigating", "impact": "major", "components": [{"id": "group2-0"}]}]
        self.assertEqual(MERAKI.build_record(data, NOW)["status_indicator"], "none")

    def test_open_incident_on_scoped_component_is_an_issue_even_if_component_is_green(self):
        data = meraki_summary()
        data["incidents"] = [{"id": "i1", "status": "identified", "impact": "minor", "components": [{"id": "group0-0"}]}]
        record = MERAKI.build_record(data, NOW)
        self.assertEqual(record["status_indicator"], "issues")
        self.assertEqual(record["open_incidents"], [{"id": "i1", "status": "identified", "impact": "minor",
                                                     "tied_to_components": True, "affects_scope": True}])

    def test_untied_open_incident_is_information_not_healthy(self):
        data = meraki_summary()
        data["incidents"] = [{"id": "i1", "status": "investigating", "impact": "minor", "components": []}]
        self.assertEqual(MERAKI.build_record(data, NOW)["status_indicator"], "information")

    def test_resolved_incidents_are_ignored(self):
        data = meraki_summary()
        data["incidents"] = [{"id": "i1", "status": "resolved", "impact": "major", "components": [{"id": "group0-0"}]}]
        self.assertEqual(MERAKI.build_record(data, NOW)["status_indicator"], "none")

    def test_maintenance_component_or_active_window_is_maintenance(self):
        record = MERAKI.build_record(set_status(meraki_summary(), "group0-0", "under_maintenance"), NOW)
        self.assertEqual(record["status_indicator"], "maintenance")
        data = meraki_summary()
        data["scheduled_maintenances"] = [{"id": "m1", "status": "in_progress", "components": [{"id": "group0-1"}]}]
        self.assertEqual(MERAKI.build_record(data, NOW)["status_indicator"], "maintenance")
        data["scheduled_maintenances"][0]["status"] = "scheduled"
        self.assertEqual(MERAKI.build_record(data, NOW)["status_indicator"], "none")

    def test_issue_outranks_maintenance(self):
        data = set_status(set_status(meraki_summary(), "group0-0", "under_maintenance"), "group0-1", "major_outage")
        self.assertEqual(MERAKI.build_record(data, NOW)["status_indicator"], "issues")

    def test_structure_changes_fail_instead_of_guessing(self):
        missing = meraki_summary(regions=("Europe",))
        duplicate = meraki_summary(regions=("North and South America", "North and South America"))
        empty = meraki_summary()
        empty["components"] = [c for c in empty["components"] if c.get("group_id") != "group0"]
        unknown = set_status(meraki_summary(), "group0-0", "sort_of_ok")
        bad_incident = meraki_summary()
        bad_incident["incidents"] = [{"id": "i1", "status": "pondering", "impact": "minor", "components": []}]
        bad_maintenance = meraki_summary()
        bad_maintenance["scheduled_maintenances"] = [{"id": "m1", "status": "maybe", "components": []}]
        for data in (missing, duplicate, empty, unknown, bad_incident, bad_maintenance, {}, [], {"components": "x"}):
            with self.subTest(data=str(data)[:60]), self.assertRaises(ValueError):
                MERAKI.build_record(data, NOW)

    def test_naive_timestamp_is_rejected(self):
        with self.assertRaises(ValueError):
            MERAKI.build_record(meraki_summary(), datetime(2026, 11, 4, 18, 0))


class IncidentIQTests(unittest.TestCase):
    def test_core_components_only(self):
        record = IIQ.build_record(set_status(iiq_summary(), "c3", "major_outage"), NOW)
        self.assertEqual(record["status_indicator"], "none")
        self.assertEqual([c["name"] for c in record["components"]], ["Platform", "API"])
        self.assertEqual(record["vendor"], "Incident IQ")

    def test_core_outage_is_an_issue(self):
        record = IIQ.build_record(set_status(iiq_summary(), "c0", "major_outage"), NOW)
        self.assertEqual(record["status_indicator"], "issues")
        self.assertIn("API (major outage)", record["status_description"])

    def test_missing_or_duplicate_core_component_fails(self):
        missing = iiq_summary()
        missing["components"] = [c for c in missing["components"] if c["name"] != "Platform"]
        duplicate = iiq_summary()
        duplicate["components"].append({"id": "c9", "name": "API", "status": "operational"})
        for data in (missing, duplicate):
            with self.subTest(), self.assertRaises(ValueError):
                IIQ.build_record(data, NOW)


class RunCollectorTests(unittest.TestCase):
    def fake_response(self, payload):
        return contextlib.nullcontext(io.BytesIO(json.dumps(payload).encode()))

    def run_collector(self, argv, payload=None, error=None):
        with tempfile.TemporaryDirectory(prefix="district-collector-") as directory:
            output = Path(directory) / "data" / "status_checks.jsonl"
            side_effect = error or (lambda *args, **kwargs: self.fake_response(payload))
            with patch.object(SUPPORT, "OUTPUT_FILE", output), patch.object(SUPPORT, "urlopen", side_effect=side_effect), \
                    contextlib.redirect_stdout(io.StringIO()) as stdout:
                code = SUPPORT.run_collector("Cisco Meraki", MERAKI.STATUS_URL, MERAKI.build_record, argv, lambda: NOW)
            lines = output.read_text().splitlines() if output.exists() else []
            return code, lines, stdout.getvalue()

    def test_check_mode_prints_and_saves_nothing(self):
        code, lines, out = self.run_collector(["--check"], meraki_summary())
        self.assertEqual((code, lines), (0, []))
        self.assertEqual(json.loads(out)["vendor"], "Cisco Meraki")

    def test_normal_run_appends_one_loader_compatible_record(self):
        code, lines, _ = self.run_collector([], meraki_summary())
        self.assertEqual((code, len(lines)), (0, 1))
        with tempfile.TemporaryDirectory(prefix="district-collector-") as directory:
            path = Path(directory) / "history.jsonl"
            path.write_text(lines[0] + "\n", encoding="utf-8")
            rows, duplicates = LOADER.read_history(path)
        self.assertEqual((len(rows), duplicates), (1, 0))
        self.assertEqual(rows[0]["vendor"], "Cisco Meraki")

    def test_network_or_data_errors_exit_without_saving(self):
        for kwargs in ({"error": URLError("offline")}, {"payload": {"components": []}}):
            with self.subTest(kwargs=kwargs), self.assertRaises(SystemExit):
                self.run_collector([], **kwargs)


class ScopeInventoryTests(unittest.TestCase):
    def test_google_products_are_unique_and_prefixed(self):
        self.assertEqual(len(GOOGLE.PRODUCTS), 13)
        self.assertEqual(len(set(GOOGLE.PRODUCTS.values())), 13)
        self.assertTrue(all(name.startswith("Google Workspace - ") for name in GOOGLE.PRODUCTS))
        self.assertTrue(all(re.fullmatch(r"[A-Za-z0-9]{20}", pid) for pid in GOOGLE.PRODUCTS.values()))

    def test_google_overall_row_names_affected_products(self):
        incident = {"id": "g1", "begin": "2026-11-04T17:00:00Z", "end": None, "most_recent_update": {"status": "SERVICE_DISRUPTION"},
                    "affected_products": [{"id": "6cvoSQjvSMrEEWswEm6b", "title": "Google Vids"}]}
        records = {r["vendor"]: r for r in GOOGLE.build_records([incident], NOW)}
        self.assertEqual(len(records), 14)
        self.assertIn("(Google Vids)", records["Google Workspace"]["status_description"])
        self.assertEqual(records["Google Workspace - Gmail"]["status_indicator"], "none")

    def test_pipeline_sources_and_validation_agree(self):
        scripts = sorted(p.name for p in (ROOT / "scripts").glob("fetch_*.py"))
        self.assertEqual(sorted(PIPELINE.COLLECTORS), scripts)
        sources = json.loads((ROOT / "config" / "sources.json").read_text())
        self.assertEqual(sorted(Path(s["collector"]).name for s in sources["sources"]), scripts)
        scopes = [scope for s in sources["sources"] for scope in s["output_scopes"]]
        self.assertEqual(len(scopes), len(set(scopes)))
        self.assertEqual(sources["expected_scoped_observations_per_complete_run"], len(scopes))
        self.assertEqual(len(scopes), 25)
        validation = (ROOT / "sql" / "bigquery" / "003_validation.sql").read_text()
        self.assertEqual(sorted(re.findall(r"'([^']+)'", validation.split("UNNEST([")[1].split("])")[0])), sorted(scopes))


if __name__ == "__main__":
    unittest.main()
