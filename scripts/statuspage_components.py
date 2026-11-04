"""Shared logic for public Atlassian Statuspage feeds (GET /api/v2/summary.json, no login).

Status is computed only from the selected components, plus any open incident or
in-progress maintenance that names one of them. Anything unexpected raises ValueError,
so the collector exits non-zero and saves nothing: unknown is never shown as healthy.
Only fixed identifiers and statuses are kept, never free-text incident updates.
"""

import json
from datetime import timezone
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

COMPONENT_STATUSES = {"operational", "degraded_performance", "partial_outage", "major_outage", "under_maintenance"}
ISSUE_STATUSES = {"degraded_performance", "partial_outage", "major_outage"}
OPEN_INCIDENT_STATUSES = {"investigating", "identified", "monitoring"}
INCIDENT_IMPACTS = {"none", "minor", "major", "critical"}
ACTIVE_MAINTENANCE_STATUSES = {"in_progress", "verifying"}
MAINTENANCE_STATUSES = ACTIVE_MAINTENANCE_STATUSES | {"scheduled", "completed"}
OUTPUT_FILE = Path(__file__).resolve().parent.parent / "data" / "status_checks.jsonl"


def _components(data):
    if not isinstance(data, dict) or not isinstance(data.get("components"), list):
        raise ValueError("Expected a Statuspage summary with a components list.")
    for component in data["components"]:
        if not isinstance(component, dict) or not isinstance(component.get("id"), str) or not component["id"]:
            raise ValueError("Invalid status component.")
        if not isinstance(component.get("name"), str) or not component["name"]:
            raise ValueError("A status component has no name.")
        if component.get("status") not in COMPONENT_STATUSES:
            raise ValueError(f"Unrecognized component status: {component.get('status')}")
    return data["components"]


def select_group(data, group_name):
    """Children of the one component group with this exact name."""
    components = _components(data)
    groups = [c for c in components if c.get("group") is True and c["name"] == group_name]
    if len(groups) != 1:
        raise ValueError(f"Expected exactly one component group named {group_name!r}; found {len(groups)}.")
    children = [c for c in components if c.get("group_id") == groups[0]["id"] and c.get("group") is not True]
    if not children:
        raise ValueError(f"Component group {group_name!r} has no components; status is unknown.")
    return children


def select_named(data, names):
    """The one component with each exact name, in the order given."""
    components = _components(data)
    selected = []
    for name in names:
        matches = [c for c in components if c["name"] == name]
        if len(matches) != 1:
            raise ValueError(f"Expected exactly one component named {name!r}; found {len(matches)}.")
        selected.append(matches[0])
    return selected


def _affected_ids(item, kind):
    affected = item.get("components", [])
    if not isinstance(affected, list) or not all(isinstance(c, dict) and isinstance(c.get("id"), str) for c in affected):
        raise ValueError(f"Invalid {kind} component list.")
    return {c["id"] for c in affected}


def _readable(names):
    return ", ".join(names[:3]) + (f" and {len(names) - 3} more" if len(names) > 3 else "")


def build_selected_record(data, observed_at, vendor, scope_label, selected):
    if observed_at.utcoffset() is None:
        raise ValueError("Observation timestamp must include a timezone.")
    selected_ids = {c["id"] for c in selected}
    if len(selected_ids) != len(selected):
        raise ValueError("Duplicate selected component ID.")

    incidents = data.get("incidents", [])
    maintenances = data.get("scheduled_maintenances", [])
    if not isinstance(incidents, list) or not isinstance(maintenances, list):
        raise ValueError("Incidents and maintenances must be lists.")

    open_incidents = []
    for incident in incidents:
        if not isinstance(incident, dict) or not isinstance(incident.get("id"), str):
            raise ValueError("Invalid incident.")
        if incident.get("status") in ("resolved", "postmortem"):
            continue
        if incident.get("status") not in OPEN_INCIDENT_STATUSES or incident.get("impact") not in INCIDENT_IMPACTS:
            raise ValueError("Unrecognized incident status or impact.")
        affected = _affected_ids(incident, "incident")
        open_incidents.append({
            "id": incident["id"], "status": incident["status"], "impact": incident["impact"],
            "tied_to_components": bool(affected), "affects_scope": bool(affected & selected_ids),
        })

    active_maintenances = []
    for maintenance in maintenances:
        if not isinstance(maintenance, dict) or not isinstance(maintenance.get("id"), str):
            raise ValueError("Invalid scheduled maintenance.")
        if maintenance.get("status") not in MAINTENANCE_STATUSES:
            raise ValueError("Unrecognized maintenance status.")
        if maintenance["status"] not in ACTIVE_MAINTENANCE_STATUSES:
            continue
        affected = _affected_ids(maintenance, "maintenance")
        active_maintenances.append({
            "id": maintenance["id"], "status": maintenance["status"], "affects_scope": bool(affected & selected_ids),
        })

    components = [{"id": c["id"], "name": c["name"], "status": c["status"]} for c in selected]
    impaired = [f"{c['name']} ({c['status'].replace('_', ' ')})" for c in components if c["status"] in ISSUE_STATUSES]
    scoped_incidents = [i for i in open_incidents if i["affects_scope"] and i["impact"] != "none"]
    if impaired:
        indicator, description = "issues", f"{vendor} reports an issue in {scope_label}: {_readable(impaired)}"
    elif scoped_incidents:
        indicator, description = "issues", f"{vendor} reports an open incident affecting {scope_label}"
    elif any(c["status"] == "under_maintenance" for c in components) or any(m["affects_scope"] for m in active_maintenances):
        indicator, description = "maintenance", f"{vendor} reports maintenance affecting {scope_label}"
    elif any(i["affects_scope"] or not i["tied_to_components"] for i in open_incidents):
        indicator, description = "information", f"{vendor} has an open notice that may involve {scope_label}"
    else:
        indicator, description = "none", f"{vendor} reports {scope_label} operational ({len(components)} components)"

    return {
        "vendor": vendor,
        "source_type": "vendor_reported",
        "observed_at": observed_at.astimezone(timezone.utc).isoformat(),
        "status_indicator": indicator,
        "status_description": description,
        "status_basis": "selected_vendor_components",
        "scope": scope_label,
        "components": components,
        "open_incidents": open_incidents,
        "active_maintenances": active_maintenances,
    }


def run_collector(vendor, url, build_record, argv, now):
    """Fetch, validate, then append one JSONL record (or only print it with --check)."""
    check_only = "--check" in argv
    try:
        with urlopen(url, timeout=15) as response:
            data = json.load(response)
    except (URLError, TimeoutError) as error:
        raise SystemExit(f"Could not retrieve {vendor} status: {error}")
    except json.JSONDecodeError:
        raise SystemExit(f"{vendor} status response was not valid JSON.")
    try:
        record = build_record(data, now())
    except (ValueError, TypeError, KeyError) as error:
        raise SystemExit(f"Invalid {vendor} status data: {error}")

    if check_only:
        print(json.dumps(record, indent=2))
        return 0
    OUTPUT_FILE.parent.mkdir(exist_ok=True)
    with OUTPUT_FILE.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record) + "\n")
    print("Vendor:", record["vendor"])
    print("Vendor-reported status:", record["status_description"])
    print("Saved observation to:", OUTPUT_FILE)
    return 0
