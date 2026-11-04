-- These reports must return no duplicates and no missing expected services
-- after a full successful collection and import. An empty latest view is not healthy.
SELECT vendor, source_type, observed_at, COUNT(*) AS copies
FROM `{{DATASET}}.status_observations`
GROUP BY vendor, source_type, observed_at
HAVING COUNT(*) > 1;

SELECT source_type, COUNT(*) AS observations
FROM `{{DATASET}}.status_observations`
GROUP BY source_type;

SELECT * FROM `{{DATASET}}.v_latest_status` ORDER BY vendor, source_type;

WITH expected AS (
    SELECT service FROM UNNEST([
        'GoGuardian', 'SmartPass', 'ClassLink', 'Frontline Education',
        'Google Workspace', 'Google Workspace - Gmail',
        'Google Workspace - Classroom', 'Google Workspace - Drive',
        'ChatGPT', 'Realtime SIS', 'Realtime - Parent Portal',
        'Realtime - Student Portal', 'i-Ready',
        'Google Workspace - Calendar', 'Google Workspace - Meet', 'Google Workspace - Docs',
        'Google Workspace - Sheets', 'Google Workspace - Slides', 'Google Workspace - Forms',
        'Google Workspace - Chat', 'Google Workspace - Sites', 'Google Workspace - Admin Console',
        'Google Workspace - Sign In',
        'Cisco Meraki', 'Incident IQ'
    ]) AS service
)
SELECT service AS missing_service
FROM expected
LEFT JOIN `{{DATASET}}.v_latest_status` latest ON latest.vendor = service
WHERE latest.vendor IS NULL;
