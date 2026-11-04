"""Validate JSONL history, batch-load an isolated stage, and MERGE transactionally."""

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from uuid import uuid4

from bigquery_support import (
    BigQueryConfigurationError, DEFAULT_CONFIG, FIELDS, PROJECT_ROOT,
    bigquery_module, get_client, load_settings, query_config, render_sql,
    verify_bigquery_access,
)

DATA_FILE = PROJECT_ROOT / "data" / "status_checks.jsonl"


class HistoryValidationError(ValueError):
    pass


def read_history(path=DATA_FILE):
    selected = {}
    total = 0
    with Path(path).open("r", encoding="utf-8") as file:
        for number, line in enumerate(file, 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except (ValueError, UnicodeError):
                raise HistoryValidationError(f"Invalid JSON on history line {number}.") from None
            if not isinstance(item, dict) or any(not isinstance(item.get(key), str) or not item[key].strip() for key in FIELDS):
                raise HistoryValidationError(f"Missing or invalid observation field on line {number}.")
            try:
                observed_at = datetime.fromisoformat(item["observed_at"].replace("Z", "+00:00"))
                if observed_at.utcoffset() is None:
                    raise ValueError()
                timestamp = observed_at.astimezone(timezone.utc).isoformat(timespec="microseconds")
            except (ValueError, OverflowError):
                raise HistoryValidationError(f"Invalid or timezone-free observation timestamp on line {number}.") from None
            # Only the same five fields as the PostgreSQL loader leave the machine.
            record = {key: item[key] for key in FIELDS}
            record["observed_at"] = timestamp
            key = (record["vendor"], record["source_type"], timestamp)
            if key in selected and selected[key] != record:
                raise HistoryValidationError(f"Conflicting duplicate observation key on line {number}; review history before importing.")
            selected[key] = record
            total += 1
    records = list(selected.values())
    return records, total - len(records)


def load_batch(client, settings, records):
    bigquery = bigquery_module()
    schema = [bigquery.SchemaField(key, "TIMESTAMP" if key == "observed_at" else "STRING", mode="REQUIRED") for key in FIELDS]
    stage = f"{settings.dataset}.status_observations_stage_{uuid4().hex}"
    table = bigquery.Table(stage, schema=schema)
    table.expires = datetime.now(timezone.utc) + timedelta(hours=settings.staging_expiration_hours)
    table.labels = {"application": "district-it-monitoring", "purpose": "batch-staging"}
    client.create_table(table, timeout=20)
    # Never stream or TRUNCATE a shared stage. Wait for the atomic batch load.
    load_job = client.load_table_from_json(
        records, stage, location=settings.location, timeout=20,
        job_config=bigquery.LoadJobConfig(
            schema=schema, write_disposition=bigquery.WriteDisposition.WRITE_EMPTY,
            create_disposition=bigquery.CreateDisposition.CREATE_NEVER,
            max_bad_records=0, ignore_unknown_values=False,
            labels={"application": "district-it-monitoring", "operation": "batch-load"},
        ),
    )
    print("Batch load job:", load_job.job_id)
    load_job.result(timeout=120)
    merge_job = client.query(
        render_sql("002_merge.sql", settings, stage),
        job_config=query_config(settings, "history-merge"),
        location=settings.location, timeout=20,
    )
    print("Merge job:", merge_job.job_id)
    rows = list(merge_job.result(timeout=120))
    if len(rows) != 1 or type(rows[0]["inserted_rows"]) is not int:
        raise BigQueryConfigurationError("BigQuery merge finished without the expected insert count. Check its job before retrying.")
    inserted = rows[0]["inserted_rows"]
    if not 0 <= inserted <= len(records):
        raise BigQueryConfigurationError("BigQuery merge returned an invalid insert count.")
    # Delete only after confirmed job completion. On failure/timeout, leave the
    # stage to expire so a still-running cloud job is not disrupted by cleanup.
    from google.api_core.exceptions import GoogleAPIError
    try:
        client.delete_table(stage, not_found_ok=True, timeout=20)
    except GoogleAPIError:
        print("Stage cleanup delayed; its configured expiration is still in place.")
    return inserted, len(records) - inserted


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--input", type=Path, default=DATA_FILE)
    parser.add_argument("--validate-only", action="store_true", help="Validate and deduplicate local history without authentication or cloud calls.")
    args = parser.parse_args()
    try:
        settings = load_settings(args.config)
        records, duplicates = read_history(args.input)
        print("Distinct observations ready to load:", len(records))
        print("Identical duplicate input rows collapsed:", duplicates)
        if not records:
            raise HistoryValidationError("No saved observations found.")
        if args.validate_only:
            print("Local validation passed. No Google Cloud calls were made.")
            return 0
        verify_bigquery_access(settings)
        client = get_client(settings)
        from google.api_core.exceptions import GoogleAPIError
        from google.auth.exceptions import GoogleAuthError
        try:
            inserted, skipped = load_batch(client, settings, records)
        except (GoogleAPIError, GoogleAuthError, TimeoutError):
            raise BigQueryConfigurationError("BigQuery import did not finish successfully. Check the printed cloud job IDs and log in again if needed. Conflicting concurrent transactions can be retried after the other run finishes. Saved history is intact; abandoned staging tables expire automatically.") from None
        finally:
            client.close()
        print("Inserted:", inserted)
        print("Already loaded; skipped:", skipped)
        return 0
    except (BigQueryConfigurationError, HistoryValidationError) as error:
        raise SystemExit(str(error)) from None
    except OSError:
        raise SystemExit("Could not read the history file. Run a collector or check --input.") from None


if __name__ == "__main__":
    raise SystemExit(main())
