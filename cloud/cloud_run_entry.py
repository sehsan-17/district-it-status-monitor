"""Cloud Run entry point: run the unchanged BigQuery pipeline, then archive this run's JSONL and log.

The pipeline (collectors, validation, staging load, guarded MERGE) is scripts/run_pipeline.py,
unchanged. This wrapper only copies the run's local files to Cloud Storage afterwards, so
component/incident/probe detail and any batch whose load failed can be replayed later with:
    python scripts/load_status_bigquery.py --input <downloaded .jsonl>
Objects are create-only (ifGenerationMatch=0); nothing is overwritten or deleted.
"""

from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PIPELINE = [sys.executable, str(PROJECT_ROOT / "scripts" / "run_pipeline.py"),
            "--backend", "bigquery", "--non-interactive"]


def archive(session, bucket, source, object_name, content_type):
    url = f"https://storage.googleapis.com/upload/storage/v1/b/{bucket}/o"
    with source.open("rb") as file:
        response = session.post(
            url, params={"uploadType": "media", "name": object_name, "ifGenerationMatch": "0"},
            data=file, headers={"Content-Type": content_type}, timeout=60,
        )
    response.raise_for_status()


def main():
    bucket = os.environ.get("HISTORY_BUCKET", "").strip()
    if not bucket:
        print("HISTORY_BUCKET is not set; refusing to run without durable history.", file=sys.stderr)
        return 2

    started = datetime.now(timezone.utc)
    exit_code = subprocess.run(PIPELINE, cwd=PROJECT_ROOT).returncode

    stamp = started.strftime("%Y%m%dT%H%M%SZ")
    day = started.strftime("%Y/%m/%d")
    execution = os.environ.get("CLOUD_RUN_EXECUTION", "manual")
    suffix = f"{stamp}_{execution}_exit{exit_code}"
    files = (
        (PROJECT_ROOT / "data" / "status_checks.jsonl", f"history/{day}/{suffix}.jsonl", "application/x-ndjson"),
        (PROJECT_ROOT / "data" / "logs" / "pipeline.log", f"logs/{day}/{suffix}.log", "text/plain"),
    )
    try:
        import google.auth
        from google.auth.transport.requests import AuthorizedSession

        credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/devstorage.read_write"])
        session = AuthorizedSession(credentials)
        for source, object_name, content_type in files:
            if source.is_file() and source.stat().st_size:
                archive(session, bucket, source, object_name, content_type)
                print(f"Archived gs://{bucket}/{object_name}")
    except Exception as error:  # Report archive failure without hiding the pipeline result.
        print(f"History archive failed ({type(error).__name__}): {error}", file=sys.stderr)
        return exit_code or 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
