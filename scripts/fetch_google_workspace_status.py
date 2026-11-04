import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

STATUS_URL = "https://www.google.com/appsstatus/dashboard/incidents.json"
# Stable product IDs from Google's public products.json catalog.
PRODUCTS = {
    "Google Workspace - Gmail": "prPt2Yra2CbGsbEm9cpC",
    "Google Workspace - Classroom": "3GoNVrVz48jeNZSyLJMV",
    "Google Workspace - Drive": "VHNA7p3Z5p3iakj5sA8V",
    "Google Workspace - Calendar": "n5hVexuq1PM9onY9qrmo",
    "Google Workspace - Meet": "sUH4BQXzYXma7NiS94Hi",
    "Google Workspace - Docs": "wNHuVFtZEWU5Mmj2eRCK",
    "Google Workspace - Sheets": "GsNjeYHsogeRgTiC5coU",
    "Google Workspace - Slides": "DvWBgkXVhodA3WYwgZoB",
    "Google Workspace - Forms": "3mtqLXm6QxeM6bAVkVez",
    "Google Workspace - Chat": "Ht9Vx5PFzDPHYvrnrvSk",
    "Google Workspace - Sites": "RxkDBi3TRJ5uf3dhPYiT",
    "Google Workspace - Admin Console": "xrkLpNMHAvPQczcU2uSb",
    "Google Workspace - Sign In": "TKCGxL5V9653G1XwH6nw",
}


def parse_timestamp(value):
    if not isinstance(value, str) or not value:
        raise ValueError("Incident timestamp is missing or invalid.")
    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if timestamp.utcoffset() is None:
        raise ValueError("Incident timestamp must include a timezone.")
    return timestamp


def build_records(incidents, observed_at):
    """Create vendor-reported snapshots, without claiming measured availability."""
    if not isinstance(incidents, list):
        raise ValueError("Expected a list of Google Workspace incidents.")
    if observed_at.utcoffset() is None:
        raise ValueError("Observation timestamp must include a timezone.")

    ongoing = []
    for incident in incidents:
        if not isinstance(incident, dict):
            raise ValueError("An incident is not a JSON object.")
        begin = parse_timestamp(incident.get("begin"))
        end_value = incident.get("end")
        end = parse_timestamp(end_value) if end_value not in (None, "") else None
        if end is not None and end < begin:
            raise ValueError("An incident ends before it begins.")
        if begin > observed_at or (end is not None and end <= observed_at):
            continue

        update = incident.get("most_recent_update", {})
        if not isinstance(update, dict):
            raise ValueError("Invalid most recent incident update.")
        impact = update.get("status") or incident.get("status_impact")
        if impact == "AVAILABLE":
            continue
        if impact not in {"SERVICE_INFORMATION", "SERVICE_DISRUPTION", "SERVICE_OUTAGE"}:
            raise ValueError(f"Unrecognized ongoing Google incident status: {impact}")
        if not isinstance(incident.get("id"), str) or not incident["id"]:
            raise ValueError("An ongoing incident has no valid ID.")

        products = incident.get("affected_products")
        if not isinstance(products, list) or not products:
            raise ValueError("An ongoing incident has no affected product IDs.")
        affected = []
        for product in products:
            if not isinstance(product, dict):
                raise ValueError("Invalid affected product.")
            product_id = product.get("id")
            title = product.get("current_title") or product.get("title")
            if not isinstance(product_id, str) or not product_id:
                raise ValueError("An affected product has no valid ID.")
            if not isinstance(title, str) or not title:
                raise ValueError("An affected product has no valid title.")
            affected.append({"id": product_id, "name": title})

        # Keep operational metadata only, not free-text incident updates.
        ongoing.append({
            "id": incident["id"],
            "begin": begin.astimezone(timezone.utc).isoformat(),
            "status": impact,
            "affected_products": affected,
        })

    records = []
    scopes = {"Google Workspace": None, **PRODUCTS}
    for vendor, product_id in scopes.items():
        relevant = [
            incident for incident in ongoing
            if product_id is None or any(
                product["id"] == product_id
                for product in incident["affected_products"]
            )
        ]
        if not relevant:
            indicator = "none"
            description = f"No ongoing incidents reported for {vendor} in Google's public feed"
        elif any(incident["status"] != "SERVICE_INFORMATION" for incident in relevant):
            indicator = "issues"
            description = f"{len(relevant)} ongoing incident(s) reported for {vendor} in Google's public feed"
            if product_id is None:
                # Name the affected catalog products so a red overall row can be traced.
                names = sorted({product["name"] for incident in relevant for product in incident["affected_products"]})
                description += f" ({', '.join(names[:3])}{f' and {len(names) - 3} more' if len(names) > 3 else ''})"
        else:
            indicator = "information"
            description = f"{len(relevant)} ongoing service-information notice(s) reported for {vendor}"

        records.append({
            "vendor": vendor,
            "source_type": "vendor_reported",
            "observed_at": observed_at.astimezone(timezone.utc).isoformat(),
            "status_indicator": indicator,
            "status_description": description,
            "status_basis": "public_incident_feed",
            "ongoing_incident_count": len(relevant),
            "incidents": relevant,
        })
    return records


def main():
    try:
        with urlopen(STATUS_URL, timeout=15) as response:
            incidents = json.load(response)
    except (URLError, TimeoutError) as error:
        raise SystemExit(f"Could not retrieve Google Workspace incidents: {error}")
    except json.JSONDecodeError:
        raise SystemExit("Google Workspace response was not valid JSON.")

    try:
        records = build_records(incidents, datetime.now(timezone.utc))
    except (ValueError, TypeError) as error:
        raise SystemExit(f"Invalid Google Workspace incident data: {error}")

    data_dir = Path(__file__).resolve().parent.parent / "data"
    data_dir.mkdir(exist_ok=True)
    output_file = data_dir / "status_checks.jsonl"
    with output_file.open("a", encoding="utf-8") as file:
        for record in records:
            file.write(json.dumps(record) + "\n")

    for record in records:
        print(record["vendor"], "-", record["status_description"])
    print("Saved observations:", len(records))
    print("Saved to:", output_file)


if __name__ == "__main__":
    main()
