import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

STATUS_URL = "https://smartpass.instatus.com/summary.json"

try:
    with urlopen(STATUS_URL, timeout=15) as response:
        data = json.load(response)
except (URLError, TimeoutError) as error:
    raise SystemExit(f"Could not retrieve SmartPass status: {error}")
else:
    status_map = {
        "UP": "none",
        "HASISSUES": "issues",
        "UNDERMAINTENANCE": "maintenance",
    }

    vendor_status = data["page"]["status"]

    if vendor_status not in status_map:
        raise SystemExit(
            f"Unrecognized SmartPass status: {vendor_status}"
        )

    status_indicator = status_map[vendor_status]

    status_description = {
        "UP": "All Systems Operational",
        "HASISSUES": "Vendor reports service issues",
        "UNDERMAINTENANCE": "Vendor reports maintenance",
    }[vendor_status]

    observed_at = datetime.now(timezone.utc)

    record = {
        "vendor": "SmartPass",
        "source_type": "vendor_reported",
        "observed_at": observed_at.isoformat(),
        "vendor_status": vendor_status,
        "status_indicator": status_indicator,
        "status_description": status_description,
    }

    data_dir = Path(__file__).resolve().parent.parent / "data"
    data_dir.mkdir(exist_ok=True)

    output_file = data_dir / "status_checks.jsonl"

    with output_file.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record) + "\n")

    print("Saved observation to:", output_file)
    print("Vendor:", data["page"]["name"])
    print("Vendor-reported status:", vendor_status)
    print("Standardized indicator:", status_indicator)
