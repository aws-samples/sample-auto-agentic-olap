# Unified field conventions for the two Pharma fact tables

Raw fields come from the seed tables in `sim_business`; what the agent actually
queries is the `pharma_dm` layer, where some fields are renamed. The "Semantic
layer name" column gives the corresponding MetricFlow dimension, entity or metric.

## 1. Actual sales: pharma_dm.pharma_sales_detail (from dm_fact_sales_detail)

| Raw field | pharma_dm field | Semantic layer name | Meaning | Convention | Joinable from a dimension table |
| --- | --- | --- | --- | --- | --- |
| `yearmonth` | `yearmonth` / `sales_month` | `sales_month` (time dimension) | Sales month | `sales_month` is the first-of-month date; aggregated monthly. | |
| `quarter` | `quarter` | `sales_quarter` | Sales quarter | Used directly. | |
| `country_code` | `country_code` | `pharma_country` (entity) | Sales country code | Join `pharma_dim_country` for country and region; shows `country_code` when the country is unmatched, blank region when the region is unmatched. | Yes: `pharma_dim_country` |
| `brand_code` | `brand_code` | `pharma_brand` (entity) | Brand code | Takes `brand_name` from `pharma_dim_product` by `brand_code`; shows the name only when the code maps to a unique name, otherwise (unmatched or multiple names, e.g. `PX14`) shows `brand_code`. | Yes: brand mapping from `pharma_dim_product` |
| `package_code` | `package_code` | `pharma_package` (entity) | Package / SKU code | Join `pharma_dim_product` for the package name; shows `package_code` when unmatched. | Yes: `pharma_dim_product` |
| `dist_code` | `distributor_code` | `pharma_sales_detail__distributor_code` | Distributor code | Used directly; blanks display as "not provided". | No |
| `control_flag` | `control_flag` | `pharma_sales_detail__control_flag` | Comparator flag | Used directly; records are not excluded by it by default. | No |
| `sales_type` | `sales_type` | `pharma_sales_type` (entity) | Sales business type | Used directly; blanks display as "unclassified". | No |
| `sales_qty` | `sales_quantity` | `pharma_sales_quantity` | Sales quantity | Measure, summed directly; negatives kept. | |
| `sales_value` | `sales_value` | `pharma_sales_value` | Sales value | Measure, in CNY yuan, shown in 10k-yuan in reports; negatives kept. | |
| `bu_code` | `bu_code` | `pharma_sales_detail__bu_code` | Business-unit code | Uses the fact-table field directly, not the product BU. | No: the current dimension only has the product BU |
| `un_split_sales_qty` | `unsplit_sales_quantity` | `pharma_unsplit_sales_quantity` | Unsplit sales quantity | Reconciliation measure, summed directly, not used as the primary volume. | |

## 2. Budget/forecast: pharma_dm.pharma_sales_forecast (from dm_fact_sales_forecast)

| Raw field | pharma_dm field | Semantic layer name | Meaning | Convention | Joinable from a dimension table |
| --- | --- | --- | --- | --- | --- |
| `yearmonth` | `yearmonth` / `forecast_month` | `forecast_month` (time dimension) | Plan target month | `forecast_month` is the first-of-month date; aggregated monthly. | |
| `brand_code` | `brand_code` | `pharma_brand` (entity) | Brand code | Same rule as the actuals table: shows the name when unique, otherwise `brand_code`. | Yes: brand mapping from `pharma_dim_product` |
| `package_name` | `package_name` | `pharma_sales_forecast__package_name` | Plan product / spec label | Used directly; sometimes a spec, sometimes a brand name. | No: cannot be matched uniquely to the product dimension |
| `country_code` | `country_code` | `pharma_country` (entity) | Plan sales country code | Join `pharma_dim_country` for country and region; shows `country_code` when the country is unmatched, blank region when the region is unmatched. | Yes: `pharma_dim_country` |
| `fcst_qty` | `planned_quantity` | `pharma_budget_quantity` / `pharma_forecast_quantity` | Plan quantity | Measure, split into budget vs forecast by `data_type`. | |
| `fcst_value` | `planned_value` | `pharma_budget_value` / `pharma_forecast_value` | Plan value | Measure, split into budget vs forecast by `data_type`; in CNY yuan. Budget and forecast are not added together. | |
| `sales_type` | `sales_type` | `pharma_sales_type` (entity) | Plan sales business type | Used directly; blanks display as "unclassified". | No |
| `data_type` | `data_type` | `pharma_sales_forecast__data_type` | Plan scenario | Used directly; `bgt` is budget, `fcst` is forecast. | No |

## 3. Analysis requirements (can be used directly as the agent's prompt)

1. You must fetch data through the MetricFlow semantic layer; the LLM must not
   generate any computation logic itself (neither Python nor SQL).

2. Data is served by the semantic layer; negatives and outliers are not cleaned
   or subjectively judged - they are reported faithfully per the metric
   definitions and raw values.

3. MCP tool: Medical Data Service. First use `list_metrics` and `list_dimensions`
   to find valid metric and dimension names, then `run_query` to fetch data, and
   `preview_sql` to see the SQL only.

4. Data scope:

   1. schemas: pharma_dim, pharma_dm
   2. metrics: semantic-layer metrics with the `pharma_` prefix

5. Besides the normal output, append the metrics hit and the generation logic at
   the end, for example:

   Built-in metrics and SQL

   **Built-in metrics used:**

   - `pharma_sales_value` - actual sales value (net, including negative adjustments)
   - `pharma_sales_ytd` - year-to-date actual sales value
   - `pharma_sales_ytd_yoy_rate` / `_change` - YTD YoY rate / change (computed in the semantic layer)
   - `pharma_negative_sales_value` - absolute value of negative sales adjustments (not returns; returned as a positive number)

   **SQL generated by the semantic layer** (YTD uses the `metricflow_time_spine`
   time axis for the cumulative window; core logic excerpted):

   ```sql
   WITH sma_cte AS (
     SELECT DATE_TRUNC('month', sales_month) AS metric_time__month,
            brand_code AS pharma_brand,
            sales_value AS __pharma_sales_value,
            CASE WHEN sales_value < 0 THEN -sales_value ELSE 0 END AS __pharma_negative_sales_value
     FROM "medical_dw"."pharma_dm"."pharma_sales_detail"
   )
   -- brand name: take the unique name by brand_code, fall back to the code when there are multiple
   , brand_names AS (
     SELECT brand_code,
            CASE WHEN COUNT(DISTINCT NULLIF(TRIM(brand_name), '')) = 1
                 THEN MIN(NULLIF(TRIM(brand_name), '')) ELSE brand_code END AS brand_name
     FROM "medical_dw"."pharma_dim"."pharma_dim_product"
     GROUP BY brand_code
   )
   -- YTD: accumulate from January of the current year to the current month via time_spine
   -- YoY: when prior-year same-period YTD > 0, (current YTD - prior-year YTD) / prior-year YTD; otherwise return null
   ```

6. After receiving the requirements above, do not start analyzing yet; wait for
   the specific analysis request.
