"""Cisco Meraki: public Statuspage API, North and South America region components only.

Meraki's status page lists the same services (Dashboard, API, device connectivity,
Access Point, Switch, WAN appliance, Camera) once per region; the district is in New Jersey.
Meraki posts only critical, broad outages there, so "operational" is not proof of local health.
Run with --check to fetch and validate without saving.
"""

from datetime import datetime, timezone
import sys

from statuspage_components import build_selected_record, run_collector, select_group

STATUS_URL = "https://status.meraki.net/api/v2/summary.json"
REGION_GROUP = "North and South America"


def build_record(data, observed_at):
    return build_selected_record(data, observed_at, "Cisco Meraki", REGION_GROUP, select_group(data, REGION_GROUP))


if __name__ == "__main__":
    raise SystemExit(run_collector("Cisco Meraki", STATUS_URL, build_record, sys.argv[1:],
                                   lambda: datetime.now(timezone.utc)))
