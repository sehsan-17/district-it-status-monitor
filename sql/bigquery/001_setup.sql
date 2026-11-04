-- Rendered by setup_bigquery.py with validated identifiers from config/bigquery.json.
-- Main history has no automatic expiration. Only per-run staging tables expire.
CREATE SCHEMA IF NOT EXISTS `{{DATASET}}`
OPTIONS (location = '{{LOCATION}}');

-- Linking billing does not clear expiration defaults inherited from the sandbox.
ALTER SCHEMA `{{DATASET}}` SET OPTIONS (
    default_table_expiration_days = NULL,
    default_partition_expiration_days = NULL
);

CREATE TABLE IF NOT EXISTS `{{DATASET}}.status_observations` (
    vendor STRING NOT NULL,
    source_type STRING NOT NULL,
    observed_at TIMESTAMP NOT NULL,
    status_indicator STRING NOT NULL,
    status_description STRING NOT NULL
)
PARTITION BY DATE(observed_at)
CLUSTER BY vendor, source_type;
ALTER TABLE `{{DATASET}}.status_observations` SET OPTIONS (
    expiration_timestamp = NULL,
    partition_expiration_days = NULL
);

-- Every loader transaction updates this row first. Conflicting simultaneous
-- transactions fail rather than both inserting the same observation.
CREATE TABLE IF NOT EXISTS `{{DATASET}}.ingestion_control` (
    pipeline STRING NOT NULL,
    revision INT64 NOT NULL
);
ALTER TABLE `{{DATASET}}.ingestion_control`
SET OPTIONS (expiration_timestamp = NULL);
MERGE `{{DATASET}}.ingestion_control` T
USING (SELECT 'status_observations' AS pipeline) S
ON T.pipeline = S.pipeline
WHEN NOT MATCHED THEN INSERT (pipeline, revision) VALUES (S.pipeline, 0);

CREATE OR REPLACE VIEW `{{DATASET}}.v_latest_status` AS
SELECT
    vendor,
    source_type,
    observed_at,
    TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), observed_at, SECOND) / 60.0
        AS observation_age_minutes,
    status_indicator,
    status_description,
    CASE
        WHEN observed_at < TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 MINUTE)
            THEN 'STALE'
        ELSE UPPER(status_indicator)
    END AS display_status
FROM `{{DATASET}}.status_observations`
WHERE source_type IN ('vendor_reported', 'external_monitor')
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY vendor, source_type
    ORDER BY observed_at DESC, status_indicator, status_description
) = 1;

CREATE OR REPLACE VIEW `{{DATASET}}.v_observations_hourly` AS
SELECT
    TIMESTAMP_TRUNC(observed_at, HOUR) AS observation_hour,
    vendor,
    source_type,
    COUNT(*) AS observation_count
FROM `{{DATASET}}.status_observations`
WHERE source_type IN ('vendor_reported', 'external_monitor')
GROUP BY observation_hour, vendor, source_type;
