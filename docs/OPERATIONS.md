# Operating the monitor

Commands assume `PROJECT_ID`, `REGION` (`us-east1`) and `BUCKET` are set as in [DEPLOY.md](DEPLOY.md).

## Daily health check

- Dashboard: **Oldest saved check (min)** is green (under 15).
- Cloud Console → **Cloud Run** → Jobs → `district-status-collector` → **History**: a green check every 15 minutes.
- Cloud Console → **Cloud Scheduler**: `district-status-every-15-min` is Enabled with a recent successful run.
- Email: no Cloud Monitoring alert.

BigQuery should gain about 100 rows per hour (4 runs of 25 scopes).

## What an alert means

| Alert | Usual cause | First check |
| --- | --- | --- |
| Run failed (2+ in 45 minutes) | A vendor page refusing requests or timing out repeatedly, or a BigQuery load error | The run's log in `gs://$BUCKET/logs/YYYY/MM/DD/`; its file name ends in `_exit1` |
| No successful run in 1 hour | Scheduler paused, every run failing, or billing stopped | `gcloud scheduler jobs describe district-status-every-15-min --location "$REGION"` |

A failed run still loads every collector that succeeded. The dashboard keeps the last good data and marks a scope **Stale data** after 30 minutes.

```bash
# Recent runs and their results
gcloud run jobs executions list --job district-status-collector --region "$REGION" --limit 5

# Error lines from the newest failed runs
gcloud storage ls "gs://$BUCKET/logs/**" | grep -v '_exit0\.log$' | tail -5 |
  while read -r f; do echo "--- $f"; gcloud storage cat "$f" | grep -E "ERROR|Could not|Invalid"; done
```

## Pause, resume, run once

```bash
gcloud scheduler jobs pause  district-status-every-15-min --location "$REGION"
gcloud scheduler jobs resume district-status-every-15-min --location "$REGION"
gcloud run jobs execute district-status-collector --region "$REGION" --wait
```

## Replay a saved batch

Every run's raw JSON Lines are archived, so a batch whose load failed can be loaded later. The merge skips rows that already exist, so replaying is safe.

```bash
gcloud storage cp "gs://$BUCKET/history/YYYY/MM/DD/<file>.jsonl" .
python3 scripts/load_status_bigquery.py --validate-only --input <file>.jsonl
python3 scripts/load_status_bigquery.py --input <file>.jsonl
```

## Validation queries

```bash
python3 scripts/setup_bigquery.py --validation-sql
```

prints read-only checks for duplicate keys, row counts by evidence type, the latest status per scope, and any expected scope missing from the latest view.
