# Deploying to Google Cloud

These are the steps used for the live deployment, with the project-specific names replaced by variables. Run them in Cloud Shell from the repository root.

```bash
export PROJECT_ID=your-gcp-project-id
export REGION=us-east1
export SA=district-collector@${PROJECT_ID}.iam.gserviceaccount.com
export BUCKET=${PROJECT_ID}-district-history
gcloud config set project "$PROJECT_ID"
```

## 1. Configure the project ID

Set `project_id` in `config/bigquery.json` to your project. The other settings (dataset `district_monitoring`, location `US`, 100 MiB query limit, 24-hour staging expiry) can stay as they are.

## 2. Create the BigQuery dataset, table and views

```bash
python3 -m pip install --user -r requirements.txt
python3 scripts/setup_bigquery.py            # prints the SQL only
python3 scripts/setup_bigquery.py --apply    # creates dataset, history table, control table and views
python3 scripts/check_bigquery_connection.py
```

`sql/bigquery/001_setup.sql` creates `status_observations` (partitioned by day, clustered by vendor and source type, no expiration), the `ingestion_control` row used by the merge guard, and the views `v_latest_status` and `v_observations_hourly`.

## 3. Enable APIs, create the service account and the history bucket

```bash
gcloud services enable bigquery.googleapis.com run.googleapis.com cloudscheduler.googleapis.com \
  cloudbuild.googleapis.com artifactregistry.googleapis.com

gcloud iam service-accounts create district-collector --display-name "District status collector"

# Run BigQuery jobs in the project
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member "serviceAccount:$SA" --role roles/bigquery.jobUser

# Private history bucket; the job may create objects but not read, overwrite or delete them
gcloud storage buckets create "gs://$BUCKET" --location "$REGION" \
  --uniform-bucket-level-access --public-access-prevention
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" \
  --member "serviceAccount:$SA" --role roles/storage.objectCreator
```

Grant **BigQuery Data Editor** on the `district_monitoring` dataset only (not the project): BigQuery console → the dataset → **Sharing** → **Permissions** → **Add principal** → the service account → *BigQuery Data Editor*.

## 4. Deploy the Cloud Run job and run it once

```bash
bash deploy/deploy.sh
```

The script checks that `config/bigquery.json` matches `PROJECT_ID`, reads two live feeds as a smoke test, builds the image with Cloud Build, deploys the job (1 CPU, 512 MiB, 10-minute timeout, no retries), lets the service account invoke it, runs it once, and prints the latest status per scope.

## 5. Schedule it every 15 minutes

```bash
bash deploy/create_schedule.sh
```

## 6. Add failure alerts

```bash
gcloud services enable monitoring.googleapis.com
ACCESS_TOKEN="$(gcloud auth print-access-token)" python3 ops/setup_alerts.py \
  --project "$PROJECT_ID" --region "$REGION" --job district-status-collector \
  --email you@example.com --bucket "$BUCKET"
```

This creates one email notification channel and two alert policies on `run.googleapis.com/job/completed_execution_count`:

| Alert policy | Fires when |
| --- | --- |
| District status collector - run failed | 2 or more runs end as failed within 45 minutes |
| District status collector - no successful run in 1 hour | No run ends as succeeded for 60 minutes |

Re-running the script updates both policies in place. Before writing anything it checks that the metric exists and that the job has recorded successful runs, so run it after the job has run a few times.

## Updating the code

Edit, run the tests, then run `bash deploy/deploy.sh` again. The Cloud Scheduler job keeps pointing at the same Cloud Run job, so nothing else changes.
