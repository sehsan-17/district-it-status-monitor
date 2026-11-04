#!/usr/bin/env bash
# Build the image from this repository, deploy the Cloud Run job, and run it once.
# Run from Cloud Shell (or any machine with gcloud) after the one-time setup in docs/DEPLOY.md.
#   PROJECT_ID=your-gcp-project-id bash deploy/deploy.sh
set -euo pipefail

: "${PROJECT_ID:?Set PROJECT_ID to your Google Cloud project ID}"
REGION="${REGION:-us-east1}"
JOB="${JOB:-district-status-collector}"
SA="${SA:-district-collector@${PROJECT_ID}.iam.gserviceaccount.com}"
BUCKET="${BUCKET:-${PROJECT_ID}-district-history}"

cd "$(dirname "$0")/.."

CONFIGURED="$(python3 -c 'import json; print(json.load(open("config/bigquery.json"))["project_id"])')"
if [ "$CONFIGURED" != "$PROJECT_ID" ]; then
  echo "STOP: config/bigquery.json has project_id \"$CONFIGURED\"; set it to \"$PROJECT_ID\" first." >&2
  exit 1
fi

echo "== Checking two live feeds (nothing is saved)"
python3 scripts/fetch_cisco_meraki_status.py --check > /dev/null
python3 scripts/fetch_incident_iq_status.py --check > /dev/null
rm -rf scripts/__pycache__

echo "== Building and deploying $JOB in $REGION"
gcloud run jobs deploy "$JOB" --source . --project "$PROJECT_ID" --region "$REGION" \
  --service-account "$SA" --set-env-vars "HISTORY_BUCKET=$BUCKET" \
  --task-timeout 10m --max-retries 0 --tasks 1 --cpu 1 --memory 512Mi --quiet

# Lets Cloud Scheduler, acting as the same service account, start this job.
gcloud run jobs add-iam-policy-binding "$JOB" --project "$PROJECT_ID" --region "$REGION" \
  --member "serviceAccount:$SA" --role roles/run.invoker > /dev/null

echo "== Test run"
gcloud run jobs execute "$JOB" --project "$PROJECT_ID" --region "$REGION" --wait

echo "== Latest status per scope"
bq --project_id="$PROJECT_ID" query --nouse_legacy_sql --format=pretty --max_rows=50 \
  "SELECT vendor, display_status, ROUND(observation_age_minutes, 1) AS age_min FROM \`${PROJECT_ID}.district_monitoring.v_latest_status\` ORDER BY vendor"
