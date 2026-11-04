"""Preview or apply the project's BigQuery schema and Data Studio views."""

import argparse

from bigquery_support import (
    BigQueryConfigurationError, DEFAULT_CONFIG, get_client, load_settings,
    query_config, render_sql, verify_bigquery_access,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--apply", action="store_true", help="Create dataset, tables and views in the configured Google Cloud project.")
    parser.add_argument("--validation-sql", action="store_true", help="Print validation SQL instead of setup SQL; never executes it.")
    args = parser.parse_args()
    try:
        settings = load_settings(args.config)
        filename = "003_validation.sql" if args.validation_sql else "001_setup.sql"
        sql = render_sql(filename, settings)
        if not args.apply or args.validation_sql:
            print(sql)
            return 0
        client = get_client(settings)
        from google.api_core.exceptions import GoogleAPIError, NotFound
        from google.auth.exceptions import GoogleAuthError
        try:
            try:
                existing = client.get_dataset(settings.dataset, timeout=20)
            except NotFound:
                existing = None
            if existing and existing.location.upper() != settings.location.upper():
                raise BigQueryConfigurationError("Existing dataset has a different location. Review config before setup.")
            job = client.query(sql, job_config=query_config(settings, "setup"), location=settings.location, timeout=20)
            print("Setup job:", job.job_id)
            job.result(timeout=180)
        except (GoogleAPIError, GoogleAuthError, TimeoutError):
            raise BigQueryConfigurationError("BigQuery setup did not finish successfully. Check the job in Cloud Console, API/billing access and permissions before retrying.") from None
        finally:
            client.close()
        verify_bigquery_access(settings)
        print("BigQuery schema ready:", settings.dataset, "location:", settings.location)
        return 0
    except BigQueryConfigurationError as error:
        raise SystemExit(str(error)) from None


if __name__ == "__main__":
    raise SystemExit(main())
