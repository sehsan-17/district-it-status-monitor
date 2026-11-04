"""Verify ADC and the configured BigQuery dataset/schema without printing secrets."""

import argparse
from bigquery_support import BigQueryConfigurationError, DEFAULT_CONFIG, load_settings, verify_bigquery_access


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    args = parser.parse_args()
    try:
        settings = verify_bigquery_access(load_settings(args.config))
    except BigQueryConfigurationError as error:
        raise SystemExit(str(error)) from None
    print("BigQuery dataset and schema verified:", settings.dataset)
    print("Location:", settings.location)


if __name__ == "__main__":
    main()
