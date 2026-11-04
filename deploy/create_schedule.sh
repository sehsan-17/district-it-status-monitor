#!/usr/bin/env bash
# Create the Cloud Scheduler job that starts the Cloud Run job every 15 minutes (US Eastern).
#   PROJECT_ID=your-gcp-project-id bash deploy/create_schedule.sh
set -euo pipefail

: "${PROJECT_ID:?Set PROJECT_ID to your Google Cloud project ID}"
REGION="${REGION:-us-east1}"
JOB="${JOB:-district-status-collector}"
SA="${SA:-district-collector@${PROJECT_ID}.iam.gserviceaccount.com}"

gcloud scheduler jobs create http district-status-every-15-min \
  --project "$PROJECT_ID" --location "$REGION" \
  --schedule "*/15 * * * *" --time-zone "America/New_York" \
  --uri "https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${JOB}:run" \
  --http-method POST --oauth-service-account-email "$SA" \
  --description "District status collector every 15 minutes"
