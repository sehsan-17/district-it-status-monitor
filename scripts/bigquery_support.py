"""BigQuery configuration and ADC helpers; no credentials are stored in this repo."""

import json
from dataclasses import dataclass
from pathlib import Path
import re

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "bigquery.json"
FIELDS = ("vendor", "source_type", "observed_at", "status_indicator", "status_description")


class BigQueryConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True)
class BigQuerySettings:
    project_id: str
    dataset_id: str
    location: str
    maximum_bytes_billed: int
    staging_expiration_hours: int

    @property
    def dataset(self):
        return f"{self.project_id}.{self.dataset_id}"

    @property
    def target(self):
        return f"{self.dataset}.status_observations"


def load_settings(path=DEFAULT_CONFIG):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        settings = BigQuerySettings(**data)
    except (OSError, ValueError, TypeError):
        raise BigQueryConfigurationError("Invalid BigQuery config. Check config/bigquery.json.") from None
    patterns = (
        (settings.project_id, r"[a-z][a-z0-9-]{4,61}[a-z0-9]"),
        (settings.dataset_id, r"[A-Za-z_][A-Za-z0-9_]{0,1023}"),
        (settings.location, r"[A-Za-z][A-Za-z0-9-]{0,62}"),
    )
    if any(not isinstance(value, str) or not re.fullmatch(pattern, value) for value, pattern in patterns):
        raise BigQueryConfigurationError("Invalid BigQuery project, dataset, or location identifier.")
    if type(settings.maximum_bytes_billed) is not int or not 20_000_000 <= settings.maximum_bytes_billed <= 10**12:
        raise BigQueryConfigurationError("Query byte limit must be an integer between 20 MB and 1 TB.")
    if type(settings.staging_expiration_hours) is not int or not 1 <= settings.staging_expiration_hours <= 168:
        raise BigQueryConfigurationError("Staging expiration must be an integer from 1 to 168 hours.")
    return settings


def render_sql(filename, settings, staging_table=None):
    text = (PROJECT_ROOT / "sql" / "bigquery" / filename).read_text(encoding="utf-8")
    text = text.replace("{{DATASET}}", settings.dataset).replace("{{LOCATION}}", settings.location)
    if staging_table is not None:
        if not re.fullmatch(re.escape(settings.dataset) + r"\.status_observations_stage_[a-f0-9]{32}", staging_table):
            raise BigQueryConfigurationError("Invalid per-run staging table identifier.")
        text = text.replace("{{STAGING_TABLE}}", staging_table)
    if "{{" in text:
        raise BigQueryConfigurationError("SQL template has an unresolved placeholder.")
    return text


def bigquery_module():
    try:
        from google.cloud import bigquery
    except ImportError:
        raise BigQueryConfigurationError("Run scripts/setup_bigquery_environment.ps1 and use its local cloud Python environment.") from None
    return bigquery


def get_client(settings):
    bigquery = bigquery_module()
    from google.auth.exceptions import DefaultCredentialsError
    try:
        return bigquery.Client(project=settings.project_id, location=settings.location)
    except DefaultCredentialsError:
        raise BigQueryConfigurationError("No Google Application Default Credentials. Follow docs/data_studio.md to sign in privately with gcloud auth application-default login.") from None


def query_config(settings, operation):
    return bigquery_module().QueryJobConfig(
        maximum_bytes_billed=settings.maximum_bytes_billed,
        use_legacy_sql=False,
        labels={"application": "district-it-monitoring", "operation": operation},
    )


def verify_bigquery_access(settings=None):
    settings = settings or load_settings()
    client = get_client(settings)
    from google.api_core.exceptions import GoogleAPIError
    from google.auth.exceptions import GoogleAuthError
    try:
        dataset = client.get_dataset(settings.dataset, timeout=20)
        if dataset.location.upper() != settings.location.upper():
            raise BigQueryConfigurationError("Dataset location differs from config/bigquery.json.")
        target = client.get_table(settings.target, timeout=20)
        actual = [(field.name, field.field_type, field.mode) for field in target.schema]
        expected = [(name, "TIMESTAMP" if name == "observed_at" else "STRING", "REQUIRED") for name in FIELDS]
        if actual != expected:
            raise BigQueryConfigurationError("BigQuery target schema differs from the five expected observation fields.")
        if target.expires is not None or (target.time_partitioning and target.time_partitioning.expiration_ms is not None):
            raise BigQueryConfigurationError("History still has automatic expiration. Run setup_bigquery.py --apply to remove inherited sandbox retention.")
        for name in ("ingestion_control", "v_latest_status", "v_observations_hourly"):
            table = client.get_table(settings.dataset + "." + name, timeout=20)
            if table.expires is not None:
                raise BigQueryConfigurationError("A required BigQuery table or view expires automatically. Run setup_bigquery.py --apply.")
    except (GoogleAPIError, GoogleAuthError):
        raise BigQueryConfigurationError("Cannot access BigQuery dataset or tables. Check sign-in, project, location, API, billing, and permissions; run setup_bigquery.py --apply after sign-in.") from None
    finally:
        client.close()
    return settings
