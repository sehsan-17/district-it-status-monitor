import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

STATUS_URL = "https://status.openai.com/api/v2/summary.json"
# ChatGPT component IDs, distinct from the separate API Login component.
CHATGPT_COMPONENTS = {
    "01JMXBNJXGV1T5GT2M9XA83XNG": "Conversations",
    "01JMXBNJXG1S2D9V65P1ZZTD94": "Login",
}


def build_record(data, observed_at):
    if not isinstance(data, dict) or not isinstance(data.get("components"), list):
        raise ValueError("Expected an OpenAI status summary with components.")
    if observed_at.utcoffset() is None:
        raise ValueError("Observation timestamp must include a timezone.")

    selected = {}
    allowed_statuses = {
        "operational", "degraded_performance", "partial_outage",
        "major_outage", "under_maintenance",
    }
    for component in data["components"]:
        if not isinstance(component, dict):
            raise ValueError("Invalid status component.")
        component_id = component.get("id")
        if component_id not in CHATGPT_COMPONENTS:
            continue
        status = component.get("status")
        if status not in allowed_statuses:
            raise ValueError(f"Unrecognized ChatGPT component status: {status}")
        if component_id in selected:
            raise ValueError("Duplicate ChatGPT component ID.")
        selected[component_id] = {
            "id": component_id,
            "name": CHATGPT_COMPONENTS[component_id],
            "status": status,
        }

    if set(selected) != set(CHATGPT_COMPONENTS):
        raise ValueError("A monitored ChatGPT component is missing; status is unknown.")

    components = [selected[component_id] for component_id in CHATGPT_COMPONENTS]
    if any(component["status"] in {"degraded_performance", "partial_outage", "major_outage"} for component in components):
        indicator = "issues"
        description = "OpenAI reports an issue with ChatGPT Conversations or Login"
    elif any(component["status"] == "under_maintenance" for component in components):
        indicator = "maintenance"
        description = "OpenAI reports maintenance affecting ChatGPT Conversations or Login"
    else:
        indicator = "none"
        description = "OpenAI reports ChatGPT Conversations and Login operational"

    return {
        "vendor": "ChatGPT",
        "source_type": "vendor_reported",
        "observed_at": observed_at.astimezone(timezone.utc).isoformat(),
        "status_indicator": indicator,
        "status_description": description,
        "status_basis": "selected_vendor_components",
        "components": components,
    }


def main():
    try:
        with urlopen(STATUS_URL, timeout=15) as response:
            data = json.load(response)
    except (URLError, TimeoutError) as error:
        raise SystemExit(f"Could not retrieve ChatGPT status: {error}")
    except json.JSONDecodeError:
        raise SystemExit("OpenAI status response was not valid JSON.")

    try:
        record = build_record(data, datetime.now(timezone.utc))
    except ValueError as error:
        raise SystemExit(f"Invalid ChatGPT status data: {error}")

    data_dir = Path(__file__).resolve().parent.parent / "data"
    data_dir.mkdir(exist_ok=True)
    output_file = data_dir / "status_checks.jsonl"
    with output_file.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record) + "\n")

    print("Vendor:", record["vendor"])
    print("Vendor-reported status:", record["status_description"])
    print("Saved observation to:", output_file)


if __name__ == "__main__":
    main()
