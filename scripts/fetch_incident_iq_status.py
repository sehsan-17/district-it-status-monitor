"""Incident IQ: public Statuspage API, core Platform and API components only.

Third-party integrations listed on the same page (Jamf, Mosyle, PowerSchool, mailgun,
and others) are excluded so an integration the district may not use does not show as
an Incident IQ outage. Run with --check to fetch and validate without saving.
"""

from datetime import datetime, timezone
import sys

from statuspage_components import build_selected_record, run_collector, select_named

STATUS_URL = "https://status.incidentiq.com/api/v2/summary.json"
CORE_COMPONENTS = ("Platform", "API")


def build_record(data, observed_at):
    return build_selected_record(data, observed_at, "Incident IQ", "Platform and API", select_named(data, CORE_COMPONENTS))


if __name__ == "__main__":
    raise SystemExit(run_collector("Incident IQ", STATUS_URL, build_record, sys.argv[1:],
                                   lambda: datetime.now(timezone.utc)))
