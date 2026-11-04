-- Data Studio custom query for the "Latest status" data source (BigQuery connector).
-- Inner query: readable status labels, sort keys and flags for the scorecards.
-- Outer query: grouping fields for the expandable Google Workspace row.
WITH base AS (
SELECT
  v.*,
  CASE display_status
    WHEN 'NONE' THEN 'No issue reported'
    WHEN 'ISSUES' THEN 'Reported issue'
    WHEN 'MINOR' THEN 'Minor issue'
    WHEN 'MAJOR' THEN 'Major issue'
    WHEN 'CRITICAL' THEN 'Critical issue'
    WHEN 'MAINTENANCE' THEN 'Maintenance'
    WHEN 'INFORMATION' THEN 'Service information'
    WHEN 'UNKNOWN' THEN 'Unknown'
    WHEN 'STALE' THEN 'Stale data'
    ELSE CONCAT('Review: ', COALESCE(display_status, 'missing status'))
  END AS service_status,
  CASE
    WHEN display_status IN ('ISSUES', 'MINOR', 'MAJOR', 'CRITICAL') THEN 0
    WHEN display_status IN ('UNKNOWN', 'STALE') THEN 1
    WHEN display_status = 'MAINTENANCE' THEN 2
    WHEN display_status = 'INFORMATION' THEN 3
    WHEN display_status = 'NONE' THEN 4
    ELSE 1
  END AS status_sort,
  CASE WHEN display_status = 'NONE' THEN 1 ELSE 0 END AS no_issue_flag,
  CASE WHEN display_status IN ('ISSUES', 'MINOR', 'MAJOR', 'CRITICAL') THEN 1 ELSE 0 END AS reported_issue_flag,
  CASE
    WHEN display_status IN ('NONE', 'ISSUES', 'MINOR', 'MAJOR', 'CRITICAL', 'MAINTENANCE', 'INFORMATION') THEN 0
    ELSE 1
  END AS evidence_attention_flag,
  CASE source_type
    WHEN 'vendor_reported' THEN 'Vendor reported'
    WHEN 'external_monitor' THEN 'External HTTP check'
    ELSE source_type
  END AS evidence_source,
  FORMAT_TIMESTAMP('%b %d, %Y %I:%M %p %Z', observed_at, 'America/New_York') AS last_checked_eastern,
  DATETIME(observed_at, 'America/New_York') AS observed_at_eastern
FROM `your-gcp-project-id.district_monitoring.v_latest_status` AS v
)
SELECT
  base.*,
  IF(STARTS_WITH(vendor, 'Google Workspace'), 'Google Workspace', vendor) AS service_group,
  IF(STARTS_WITH(vendor, 'Google Workspace - '), 'product', 'main') AS row_kind,
  IF(STARTS_WITH(vendor, 'Google Workspace - '),
     CONCAT('\u00A0\u00A0\u00A0\u00A0\u21B3 ', SUBSTR(vendor, 20)),
     vendor) AS service_display,
  CONCAT(CAST(MIN(status_sort) OVER (PARTITION BY IF(STARTS_WITH(vendor, 'Google Workspace'), 'Google Workspace', vendor)) AS STRING), '|',
         LOWER(IF(STARTS_WITH(vendor, 'Google Workspace'), 'Google Workspace', vendor)), '|',
         IF(STARTS_WITH(vendor, 'Google Workspace - '), '1', '0'), '|', LOWER(vendor)) AS table_order
FROM base
