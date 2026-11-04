"""Collect public status and load PostgreSQL or BigQuery."""

import argparse
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import subprocess
import sys
import time

PROJECT_ROOT = Path(__file__).resolve().parent.parent
COLLECTORS = (
    "fetch_goguardian_status.py",
    "fetch_smartpass_status.py",
    "fetch_classlink_status.py",
    "fetch_frontline_status.py",
    "fetch_google_workspace_status.py",
    "fetch_chatgpt_status.py",
    "fetch_realtime_status.py",
    "fetch_iready_status.py",
    "fetch_cisco_meraki_status.py",
    "fetch_incident_iq_status.py",
)


def make_logger():
    log_dir = PROJECT_ROOT / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("district_pipeline")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        formatter = logging.Formatter("%(asctime)sZ %(levelname)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%S")
        formatter.converter = time.gmtime
        handlers = [RotatingFileHandler(log_dir / "pipeline.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")]
        if sys.stdout is not None:
            handlers.append(logging.StreamHandler(sys.stdout))
        for handler in handlers:
            handler.setFormatter(formatter)
            logger.addHandler(handler)
    return logger


def run_pipeline(logger, collect_only=False, non_interactive=False, backend="postgres", bigquery_config=None):
    if backend not in ("postgres", "bigquery"):
        raise ValueError("Unrecognized database backend.")
    if backend == "bigquery" and not collect_only:
        from bigquery_support import BigQueryConfigurationError, DEFAULT_CONFIG, load_settings, verify_bigquery_access
        try:
            verify_bigquery_access(load_settings(bigquery_config or DEFAULT_CONFIG))
        except BigQueryConfigurationError as error:
            logger.error("BigQuery preflight failed: %s", error)
            return 1
        logger.info("Google credentials and BigQuery schema verified.")
    if backend == "postgres" and non_interactive and not collect_only:
        from database_auth import CredentialError, verify_saved_login
        try:
            verify_saved_login()
        except CredentialError as error:
            logger.error("Unattended login preflight failed: %s", error)
            return 1
        logger.info("Saved Windows database login verified.")

    child_env = os.environ.copy()
    child_env["PYTHONIOENCODING"] = "utf-8"
    child_python = Path(sys.executable)
    if child_python.name.lower() == "pythonw.exe":
        child_python = child_python.with_name("python.exe")
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if non_interactive else 0
    failed = []
    succeeded = 0
    for name in COLLECTORS:
        logger.info("Starting %s", name)
        try:
            result = subprocess.run(
                [str(child_python), str(PROJECT_ROOT / "scripts" / name)],
                cwd=PROJECT_ROOT, env=child_env, timeout=45,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                creationflags=creation_flags,
            )
        except (subprocess.TimeoutExpired, OSError) as error:
            logger.error("Collector failed: %s (%s)", name, error)
            failed.append(name)
            continue
        if result.stdout:
            logger.info("%s output:\n%s", name, result.stdout.rstrip())
        if result.returncode != 0:
            logger.error("Collector failed: %s (exit %s)", name, result.returncode)
            failed.append(name)
        else:
            succeeded += 1
            logger.info("Collector succeeded: %s", name)

    logger.info("Collection finished: %s succeeded, %s failed", succeeded, len(failed))
    if not succeeded:
        logger.error("No collector succeeded; database import was not started.")
        return 1
    if collect_only:
        logger.info("Collection-only run: database import was not requested.")
        return 1 if failed else 0

    loader_name = "load_status_bigquery.py" if backend == "bigquery" else "load_status_history.py"
    loader_command = [str(child_python), str(PROJECT_ROOT / "scripts" / loader_name)]
    if backend == "bigquery" and bigquery_config:
        loader_command.extend(["--config", str(bigquery_config)])
    loader_options = {"cwd": PROJECT_ROOT, "env": child_env, "creationflags": creation_flags}
    capture_loader = non_interactive or backend == "bigquery"
    if capture_loader:
        if backend == "postgres":
            logger.info("Starting database import with the saved Windows credential.")
            loader_command.append("--non-interactive")
        else:
            logger.info("Starting BigQuery batch import with Google Application Default Credentials.")
        loader_options.update(
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=360 if backend == "bigquery" else 120, text=True, encoding="utf-8", errors="replace",
        )
    else:
        logger.info("Starting database import. Enter the district_monitor password at its prompt.")
        # Inherit the user's terminal so the password prompt is never logged.
    try:
        result = subprocess.run(loader_command, **loader_options)
    except (subprocess.TimeoutExpired, OSError) as error:
        logger.error("Database import could not complete: %s", error)
        return 1
    if capture_loader and result.stdout:
        logger.info("Database import output:\n%s", result.stdout.rstrip())
    if result.returncode != 0:
        logger.error("Database import failed (exit %s). Saved history can be imported again.", result.returncode)
        return 1

    logger.info("Database import completed.")
    if failed:
        logger.error("Pipeline finished with failed collectors: %s", ", ".join(failed))
        return 1
    logger.info("Pipeline completed successfully.")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collect-only", action="store_true", help="Save observations without opening a database connection.")
    parser.add_argument("--non-interactive", action="store_true", help="Never prompt; PostgreSQL uses Windows credentials and BigQuery uses ADC.")
    parser.add_argument("--backend", choices=("postgres", "bigquery"), default="postgres", help="Database destination; defaults to the existing PostgreSQL workflow.")
    parser.add_argument("--bigquery-config", type=Path, help="Optional BigQuery config path.")
    args = parser.parse_args()
    return run_pipeline(make_logger(), collect_only=args.collect_only, non_interactive=args.non_interactive,
                        backend=args.backend, bigquery_config=args.bigquery_config)


if __name__ == "__main__":
    raise SystemExit(main())
