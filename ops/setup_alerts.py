"""Create or update Cloud Monitoring alerts for the district status collector (Cloud Run job).

Policies (re-running updates them in place, nothing is duplicated):
  1. "run failed": 2 or more executions finished with result=failed within 45 minutes (a collector,
     the BigQuery load, or the archive step failed). One isolated failure, usually a vendor page
     briefly refusing or timing out, does not alert.
  2. "no successful run in 1 hour": no execution finished with result=succeeded for 60 minutes
     (scheduler paused, every run failing, or the job stopped).
Both notify one email channel. Uses only the Python standard library and the Monitoring REST API.

    ACCESS_TOKEN="$(gcloud auth print-access-token)" python3 ops/setup_alerts.py \
        --project PROJECT --region us-east1 --job district-status-collector --email you@example.com
"""

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
import sys
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

API = "https://monitoring.googleapis.com/v3/"
METRIC = "run.googleapis.com/job/completed_execution_count"
FAILED_NAME = "District status collector - run failed"
ABSENT_NAME = "District status collector - no successful run in 1 hour"


class Monitoring:
    def __init__(self, project, token):
        self.project = project
        self.headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                        "x-goog-user-project": project}

    def call(self, method, path, body=None, params=None):
        url = API + path + (f"?{urlencode(params)}" if params else "")
        data = json.dumps(body).encode() if body is not None else None
        try:
            with urlopen(Request(url, data=data, method=method, headers=self.headers), timeout=30) as response:
                return json.load(response)
        except HTTPError as error:
            try:
                message = json.load(error).get("error", {}).get("message", "")
            except ValueError:
                message = ""
            raise SystemExit(f"STOP: Monitoring API {method} {path.split('?')[0]} returned {error.code}: {message}")

    def list_all(self, path, key, params=None):
        items, token = [], None
        while True:
            page = self.call("GET", path, params={**(params or {}), "pageSize": 100, **({"pageToken": token} if token else {})})
            items += page.get(key, [])
            token = page.get("nextPageToken")
            if not token:
                return items


def recent_results(api, base_filter, days=3):
    end = datetime.now(timezone.utc)
    params = {
        "filter": base_filter,
        "interval.startTime": (end - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "interval.endTime": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "aggregation.alignmentPeriod": f"{days * 86400}s",
        "aggregation.perSeriesAligner": "ALIGN_SUM",
        "aggregation.crossSeriesReducer": "REDUCE_SUM",
        "aggregation.groupByFields": "metric.label.result",
    }
    counts = {}
    for series in api.list_all(f"projects/{api.project}/timeSeries", "timeSeries", params):
        result = series.get("metric", {}).get("labels", {}).get("result", "?")
        counts[result] = counts.get(result, 0) + sum(int(p["value"].get("int64Value", 0)) for p in series.get("points", []))
    return counts


def policies(base_filter, channel, region, job, bucket):
    logs = f"gs://{bucket}/logs/" if bucket else "the history bucket's logs/ folder"
    check = (f"Check in Cloud Shell: `gcloud run jobs executions list --job {job} --region {region} --limit 5`, "
             f"or Cloud Run > Jobs > {job} > Executions > Logs. Each run's pipeline log is also archived in {logs}")
    aggregation = {"perSeriesAligner": "ALIGN_SUM", "crossSeriesReducer": "REDUCE_SUM"}
    common = {"combiner": "OR", "enabled": True, "notificationChannels": [channel],
              "userLabels": {"app": "district-status"}, "alertStrategy": {"autoClose": "1800s"}}
    failed = {
        **common,
        "displayName": FAILED_NAME,
        "severity": "WARNING",
        "documentation": {"mimeType": "text/markdown", "content": (
            f"At least 2 runs of the Cloud Run job `{job}` ({region}) failed within 45 minutes: a vendor feed could not "
            "be read or validated, or the BigQuery load or history archive failed. A single failed run (usually one "
            "vendor page briefly refusing or timing out) does not alert. Collectors that succeeded were still loaded; "
            "the dashboard shows the last good data and marks scopes stale after 30 minutes.\n\n" + check)},
        "conditions": [{"displayName": "2 or more failed runs within 45 minutes", "conditionThreshold": {
            "filter": base_filter + ' AND metric.labels.result = "failed"',
            "aggregations": [{**aggregation, "alignmentPeriod": "2700s"}],
            "comparison": "COMPARISON_GT", "thresholdValue": 1, "duration": "60s", "trigger": {"count": 1},
            "evaluationMissingData": "EVALUATION_MISSING_DATA_INACTIVE"}}],
    }
    absent = {
        **common,
        "displayName": ABSENT_NAME,
        "severity": "ERROR",
        "documentation": {"mimeType": "text/markdown", "content": (
            f"No run of `{job}` has finished successfully for 60 minutes (it normally runs every 15 minutes). "
            "Cloud Scheduler may be paused, every run may be failing, or the project's billing or trial may have ended. "
            "The dashboard will show Stale data until runs succeed again.\n\n"
            f"Check the scheduler: `gcloud scheduler jobs describe district-status-every-15-min --location {region}`. " + check)},
        "conditions": [{"displayName": "No execution with result = succeeded for 60 minutes", "conditionAbsent": {
            "filter": base_filter + ' AND metric.labels.result = "succeeded"',
            "aggregations": [{**aggregation, "alignmentPeriod": "900s"}],
            "duration": "3600s", "trigger": {"count": 1}}}],
    }
    return failed, absent


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--job", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--bucket", default="")
    args = parser.parse_args(argv)
    token = os.environ.get("ACCESS_TOKEN", "").strip()
    if not token:
        raise SystemExit("STOP: set ACCESS_TOKEN from `gcloud auth print-access-token`.")
    if "@" not in args.email or " " in args.email:
        raise SystemExit("STOP: --email does not look like an email address.")
    api = Monitoring(args.project, token)
    base_filter = (f'resource.type = "cloud_run_job" AND resource.labels.job_name = "{args.job}" '
                   f'AND resource.labels.location = "{args.region}" AND metric.type = "{METRIC}"')

    descriptor = api.call("GET", f"projects/{args.project}/metricDescriptors/{quote(METRIC, safe='/.')}")
    labels = [label["key"] for label in descriptor.get("labels", [])]
    print(f"Metric {METRIC}: kind={descriptor.get('metricKind')} labels={labels}")
    if "result" not in labels:
        raise SystemExit("STOP: the metric has no 'result' label; alert filters would not match.")
    counts = recent_results(api, base_filter)
    print("Executions finished in the last 3 days by result:", counts or "none recorded")
    if "succeeded" not in counts:
        raise SystemExit("STOP: no 'succeeded' results recorded for this job; check the job name and region first.")

    wanted = args.email.lower()
    channels = api.list_all(f"projects/{args.project}/notificationChannels", "notificationChannels", {"filter": 'type = "email"'})
    channel = next((c["name"] for c in channels if c.get("labels", {}).get("email_address", "").lower() == wanted), None)
    if channel:
        print("Email channel already exists:", channel)
    else:
        channel = api.call("POST", f"projects/{args.project}/notificationChannels", {
            "type": "email", "displayName": "District status alerts (email)",
            "labels": {"email_address": args.email}, "enabled": True})["name"]
        print("Created email channel:", channel)

    existing = {p["displayName"]: p["name"] for p in api.list_all(f"projects/{args.project}/alertPolicies", "alertPolicies")}
    for policy in policies(base_filter, channel, args.region, args.job, args.bucket):
        name = existing.get(policy["displayName"])
        if name:
            api.call("PATCH", name, policy)
            print("Updated alert policy:", policy["displayName"])
        else:
            name = api.call("POST", f"projects/{args.project}/alertPolicies", policy)["name"]
            print("Created alert policy:", policy["displayName"])
    print(f"Review: https://console.cloud.google.com/monitoring/alerting?project={args.project}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
