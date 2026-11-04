"""Collect the three public Realtime NodePing HTTP availability checks."""

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

REPORT_URL = "https://nodeping.com/reports/statusevents/UWVWG8OSC0CKEBWK"
STATUS_URL = REPORT_URL + "/listchecks"
# Stable check IDs taken from the public report; labels below define our scope.
CHECKS = {
    "2014051312262UQ8L-67EOIJ0F": "Realtime SIS",
    "2014051312262UQ8L-QLIAYSFJ": "Realtime - Parent Portal",
    "2014051312262UQ8L-419M6GFA": "Realtime - Student Portal",
}
MAX_PROBE_AGE = timedelta(minutes=15)
MAX_CLOCK_SKEW = timedelta(minutes=5)


def probe_time(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        return None
    try:
        return datetime.fromtimestamp(value / 1000, timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None


def build_records(data, observed_at):
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
        raise ValueError("Expected a public NodePing report with a check list.")
    if observed_at.utcoffset() is None:
        raise ValueError("Observation timestamp must include a timezone.")
    observed_at = observed_at.astimezone(timezone.utc)
    selected = {}
    for job in data["jobs"]:
        if not isinstance(job, dict) or not isinstance(job.get("_id"), str):
            raise ValueError("Invalid NodePing check.")
        check_id = job["_id"]
        if check_id not in CHECKS:
            continue
        if check_id in selected:
            raise ValueError("Duplicate monitored NodePing check.")
        selected[check_id] = job
    if set(selected) != set(CHECKS):
        raise ValueError("A monitored Realtime check is missing; report coverage is incomplete.")

    records = []
    for check_id, service in CHECKS.items():
        job = selected[check_id]
        result = job.get("result")
        result = result if isinstance(result, dict) else {}
        checked_at = probe_time(result.get("s"))
        state = job.get("state")
        indicator = "unknown"
        component_status = "unknown"
        if job.get("enable") not in ("active", "true"):
            detail = "check is disabled or its enabled state is unknown"
        elif job.get("type") != "HTTPADV":
            detail = "check type changed; review monitoring scope"
        elif type(state) is not int or state not in (0, 1):
            detail = "current check state is unavailable or unrecognized"
        elif checked_at is None:
            detail = "latest probe timestamp is unavailable or invalid"
        elif checked_at > observed_at + MAX_CLOCK_SKEW:
            detail = "latest probe timestamp is unexpectedly in the future"
        elif observed_at - checked_at > MAX_PROBE_AGE:
            detail = "latest probe is older than the 15-minute freshness limit"
        elif state == 1:
            indicator, component_status = "none", "operational"
            detail = "HTTP check is up"
        else:
            indicator, component_status = "issues", "check_failed"
            detail = "HTTP check is down"
        description = f"NodePing: {service} {detail}"
        if checked_at is not None:
            description += f"; last probe at {checked_at.isoformat()}"
        # Keep operational metadata only: no target URLs, HTML, notes, or diagnostics.
        records.append({
            "vendor": service,
            "source_type": "external_monitor",
            "observed_at": observed_at.isoformat(),
            "status_indicator": indicator,
            "status_description": description,
            "status_basis": "nodeping_public_http_check",
            "source_url": REPORT_URL,
            "probe_observed_at": checked_at.isoformat() if checked_at else None,
            "probe_state": state if type(state) is int and state in (0, 1) else None,
            "components": [{"id": check_id, "name": service, "status": component_status}],
        })
    return records


def main():
    # This is the public page's read-only lookup, despite using HTTP POST.
    request = Request(STATUS_URL, data=b'{"offset":0}',
                      headers={"Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=15) as response:
            data = json.load(response)
    except (URLError, TimeoutError) as error:
        raise SystemExit(f"Could not retrieve Realtime NodePing status: {error}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise SystemExit("Realtime NodePing response was not valid JSON.")
    try:
        records = build_records(data, datetime.now(timezone.utc))
    except ValueError as error:
        raise SystemExit(f"Invalid Realtime NodePing data: {error}")

    data_dir = Path(__file__).resolve().parent.parent / "data"
    data_dir.mkdir(exist_ok=True)
    with (data_dir / "status_checks.jsonl").open("a", encoding="utf-8") as file:
        file.write("".join(json.dumps(record) + "\n" for record in records))
    for record in records:
        print(record["vendor"], "-", record["status_indicator"])
        print(record["status_description"])
    print("Saved Realtime observations:", len(records))


if __name__ == "__main__":
    main()
