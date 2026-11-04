DECLARE inserted_count INT64 DEFAULT 0;
BEGIN TRANSACTION;

UPDATE `{{DATASET}}.ingestion_control`
SET revision = revision + 1
WHERE pipeline = 'status_observations';
ASSERT @@row_count = 1 AS 'Expected exactly one ingestion control row';

MERGE `{{DATASET}}.status_observations` T
USING (
    SELECT vendor, source_type, observed_at, status_indicator, status_description
    FROM `{{STAGING_TABLE}}`
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY vendor, source_type, observed_at
        ORDER BY status_indicator, status_description
    ) = 1
) S
ON T.vendor = S.vendor
    AND T.source_type = S.source_type
    AND T.observed_at = S.observed_at
WHEN NOT MATCHED THEN
    INSERT (vendor, source_type, observed_at, status_indicator, status_description)
    VALUES (S.vendor, S.source_type, S.observed_at,
            S.status_indicator, S.status_description);

SET inserted_count = @@row_count;
COMMIT TRANSACTION;
SELECT inserted_count AS inserted_rows;
