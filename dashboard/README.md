# Data Studio report

The report reads BigQuery only. It has two data sources and two pages.

| Data source | Type | Used for |
| --- | --- | --- |
| Latest status | BigQuery custom query: [`latest_status_query.sql`](latest_status_query.sql) | Page 1 (scorecards, controls, status table) |
| Hourly observations | BigQuery view `district_monitoring.v_observations_hourly` | Page 2 (collection history) |

Data freshness is 15 minutes, matching the collection schedule.

## Page 1: Service status

**Scorecards** (always all 25 scopes; the dropdowns filter only the table):

| Card | Metric | Colour rule |
| --- | --- | --- |
| Services reporting | Count of scopes, "of 25 configured scopes" underneath | Amber below 25 |
| No issue reported | Sum of `no_issue_flag` | Green |
| Reported issues | Sum of `reported_issue_flag` | Red above 0 |
| Stale / unknown | Sum of `evidence_attention_flag` | Amber above 0 |
| Oldest saved check (min) | Max of `observation_age_minutes` | Green under 15, amber from 15, red above 30 |

Missing data shows as "No data", never as 0.

**Status table** columns: Service (`service_display`), Status (`service_status`), Last checked (Eastern), Evidence source, Details (`status_description`), Age (min). It is sorted by `table_order`, so a group with a reported issue rises to the top and each group's main row comes before its products.

Status colours (conditional formatting on `service_status`):

| Pattern | Text / fill |
| --- | --- |
| `^(Reported\|Minor\|Major\|Critical) issue$` | `#991B1B` / `#FEE2E2` |
| `^(Maintenance\|Service information)$` | `#1E40AF` / `#DBEAFE` |
| `^(Unknown\|Stale data\|Review:.*)$` | `#92400E` / `#FEF3C7` |
| `No issue reported` | `#166534` / `#DCFCE7` |

**Dropdowns:** Service (`service_group`), Status, Evidence source. They are grouped with the table so they filter only the table.

## Expandable Google Workspace row

Data Studio tables cannot collapse rows, so the expand behaviour uses a parameter:

1. Data source parameter **Show all Google Workspace products** (Boolean, default false).
2. Calculated field `table_row_visibility`:

   ```
   CASE
     WHEN Show all Google Workspace products = true OR row_kind = "main" THEN "Show"
     ELSE "Hide"
   END
   ```

3. Table filter: Include `table_row_visibility` Equal to `Show`.
4. A checkbox control above the table bound to the parameter.

Unticked, the table shows one Google Workspace row (the whole Google feed). Ticked, the 13 product rows appear indented beneath it. The scorecards ignore the toggle.

## Page 2: Collection history

Hourly observation counts from `v_observations_hourly`, to show gaps in collection.

## Presentation rules

- No district logo; the header says "Portfolio project prototype - not an official district service".
- Missing or old data is never coloured green.
