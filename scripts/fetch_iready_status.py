"""Collect production i-Ready component statuses from the public Status.io API."""

import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

STATUS_URL = "https://status.io/1.0/status/64d25bf4adb04b052efa180e"
# Production scope from i-ready.status.io; corporate/other products are excluded.
COMPONENTS = {
    "657a05d83736b40535788453": "General",
    "64d25bf4adb04b052efa181e": "Reports",
    "64d289945983aa0534501489": "Rosters",
    "64ee3fdc5983aa0534501d67": "Logins",
    "68e56665bb28ac0523813261": "Secure Browser",
    "699368f7a9387506045e8ceb": "Fluency Flight",
    "64ee3fea94d27b05355389e2": "Classroom",
    "64ee400ef65cc40530c920cf": "Success Central",
    "64ee3fffe88a000536314969": "Teacher Toolbox",
}
# These are Status.io service-status codes, not HTTP response/incident-state codes.
STATUSES = {
    100: "operational",
    200: "under_maintenance",
    300: "degraded_performance",
    400: "partial_outage",
    500: "major_outage",
    600: "security_event",
}


def status_code(item):
    code = item.get("status_code")
    if type(code) is not int or code not in STATUSES:
        raise ValueError("Unrecognized i-Ready component status code.")
    return code


def build_record(data, observed_at):
    result = data.get("result") if isinstance(data, dict) else None
    if not isinstance(result, dict) or not isinstance(result.get("status"), list):
        raise ValueError("Expected a public Status.io response with component statuses.")
    if observed_at.utcoffset() is None:
        raise ValueError("Observation timestamp must include a timezone.")
    selected = {}
    for component in result["status"]:
        if not isinstance(component, dict) or not isinstance(component.get("id"), str):
            raise ValueError("Invalid i-Ready component.")
        component_id = component["id"]
        if component_id not in COMPONENTS:
            continue
        if component_id in selected:
            raise ValueError("Duplicate monitored i-Ready component ID.")
        code = status_code(component)
        containers = component.get("containers")
        if not isinstance(containers, list) or not containers:
            raise ValueError("A monitored i-Ready component has no container status.")
        container_codes = []
        for container in containers:
            if not isinstance(container, dict):
                raise ValueError("Invalid i-Ready container status.")
            container_codes.append(status_code(container))
        # A container disruption must remain visible even if its parent differs.
        effective_code = max(code, *container_codes)
        snapshot = {
            "id": component_id,
            "name": COMPONENTS[component_id],
            "status": STATUSES[effective_code],
            "vendor_status_code": code,
            "effective_status_code": effective_code,
        }
        updated = component.get("updated")
        if updated is not None:
            if not isinstance(updated, str):
                raise ValueError("Invalid i-Ready component update timestamp.")
            timestamp = datetime.fromisoformat(updated.replace("Z", "+00:00"))
            if timestamp.utcoffset() is None:
                raise ValueError("Component update timestamp must include a timezone.")
            snapshot["vendor_updated_at"] = timestamp.astimezone(timezone.utc).isoformat()
        selected[component_id] = snapshot
    if set(selected) != set(COMPONENTS):
        raise ValueError("A monitored i-Ready component is missing; status is unknown.")

    components = [selected[component_id] for component_id in COMPONENTS]
    issues = [c for c in components if c["effective_status_code"] >= 300]
    maintenance = [c for c in components if c["effective_status_code"] == 200]
    if issues:
        indicator = "issues"
        detail = ", ".join(f"{c['name']} ({c['status']})" for c in issues)
        description = "i-Ready reports production component issues: " + detail
        if maintenance:
            description += "; maintenance also reported for " + ", ".join(c["name"] for c in maintenance)
    elif maintenance:
        indicator = "maintenance"
        description = "i-Ready reports maintenance for " + ", ".join(c["name"] for c in maintenance)
    else:
        indicator = "none"
        description = "i-Ready reports monitored production components operational"
    # No free-text incident updates, account data, or student information is saved.
    return {
        "vendor": "i-Ready",
        "source_type": "vendor_reported",
        "observed_at": observed_at.astimezone(timezone.utc).isoformat(),
        "status_indicator": indicator,
        "status_description": description,
        "status_basis": "selected_vendor_components",
        "source_url": "https://i-ready.status.io/",
        "components": components,
    }


def main():
    try:
        with urlopen(STATUS_URL, timeout=15) as response:
            data = json.load(response)
    except (URLError, TimeoutError) as error:
        raise SystemExit(f"Could not retrieve i-Ready status: {error}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise SystemExit("i-Ready status response was not valid JSON.")
    try:
        record = build_record(data, datetime.now(timezone.utc))
    except ValueError as error:
        raise SystemExit(f"Invalid i-Ready status data: {error}")
    data_dir = Path(__file__).resolve().parent.parent / "data"
    data_dir.mkdir(exist_ok=True)
    with (data_dir / "status_checks.jsonl").open("a", encoding="utf-8") as file:
        file.write(json.dumps(record) + "\n")
    print("Vendor:", record["vendor"])
    print("Vendor-reported status:", record["status_description"])
    print("Saved i-Ready observation.")


if __name__ == "__main__":
    main()
