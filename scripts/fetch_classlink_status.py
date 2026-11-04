import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

STATUS_URL = "https://status.classlink.com/api/v2/summary.json"

try:
    with urlopen(STATUS_URL, timeout=15) as response:
        data = json.load(response)
except (URLError, TimeoutError) as error:
    raise SystemExit(f"Could not retrieve ClassLink status: {error}")
except json.JSONDecodeError:
    raise SystemExit("ClassLink status response was not valid JSON.")
else:
    observed_at = datetime.now(timezone.utc)

    record = {
        "vendor": "ClassLink",
        "source_type": "vendor_reported",
        "observed_at": observed_at.isoformat(),
        "status_indicator": data["status"]["indicator"],
        "status_description": data["status"]["description"],
        "components": [
            {
                "id": component["id"],
                "name": component["name"],
                "status": component["status"],
            }
            for component in data["components"]
        ],
    }

    data_dir = Path(__file__).resolve().parent.parent / "data"
    data_dir.mkdir(exist_ok=True)

    output_file = data_dir / "status_checks.jsonl"

    with output_file.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record) + "\n")

    print("Vendor:", record["vendor"])
    print("Checked at:", record["observed_at"])
    print("Vendor-reported status:", record["status_description"])
    print("Saved observation to:", output_file)

    for component in record["components"]:
        if component["status"] != "operational":
            print(component["name"], "-", component["status"])
