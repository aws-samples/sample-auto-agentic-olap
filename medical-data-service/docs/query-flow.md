# How a question becomes data: natural language → MetricFlow query → Redshift

This document explains: when a user asks a business question in natural language,
how it is broken down into a MetricFlow query, generates SQL, executes on Redshift
and returns, and what each link in the call chain is responsible for.

## The short answer

The "natural language → MetricFlow query parameters" step is done by the LLM in
the agent, not by this service's code.

The MCP service only receives structured parameters (`metrics` + `group_by`),
hands them to MetricFlow to compile into SQL, and executes on Redshift. The
conventions live in the dbt semantic layer; the LLM can only reference valid
metric and dimension names and cannot change the conventions.

## Example

The user asks in Amazon Quick / Kiro / Claude:

> By region, show YTD sales value and YoY by month for 2026.

## Call chain

```text
┌─ 1. User asks in natural language
│
├─ 2. Agent queries the catalog first (common but optional)
│      list_metrics() / list_dimensions()
│      the LLM maps "YTD sales value" → pharma_sales_ytd
│                   "YoY"             → pharma_sales_ytd_yoy_rate
│                   "region"          → pharma_country__region_name
│                   "by month"        → metric_time__month
│
├─ 3. Agent translates the question into MetricFlow params   ★ the breakdown happens here
│      run_query(
│        metrics=["pharma_sales_ytd", "pharma_sales_ytd_yoy_rate"],
│        group_by=["metric_time__month", "pharma_country__region_name"],
│        limit=1000)
│      via MCP over HTTP → https://<domain>/mcp
│
├─ 4. The MCP server receives the call
│      app/mcp/server.py :: run_query()
│           → app/service.py :: DataService.run_query()
│
├─ 5. MetricFlow compiles SQL
│      MetricFlowClient.explain()                   (app/metricflow/client.py)
│      renders to Redshift-dialect SQL
│
├─ 6. Execute SQL
│      RedshiftDataApi.query_records(sql)           (app/redshift/client.py)
│      via the Redshift Data API, returns {columns, rows, row_count}
│
├─ 7. Results returned to the agent
│      {metrics, group_by, sql, columns, rows, row_count}
│
└─ 8. The agent reads the results and answers in natural language
```

## What the "breakdown" in step 3 really is

The LLM does a constrained translation: mapping colloquial phrasing to valid names
in the semantic layer.

| What the user says | Maps to (from list_metrics / list_dimensions) |
| --- | --- |
| actual sales value | `pharma_sales_value` |
| YTD sales value / YTD YoY | `pharma_sales_ytd` / `pharma_sales_ytd_yoy_rate` |
| YTD budget achievement rate | `pharma_budget_achievement_ytd` |
| by region | `pharma_country__region_name` |
| by brand / country / sales type | `pharma_brand` / `pharma_country` / `pharma_sales_type` |
| by month | `metric_time__month` |

A MetricFlow query is just these fields: `metrics`, `group_by`, `limit`.

## What MetricFlow does for you

- fetches from the `pharma_dm` fact tables, aggregating per the aggregation in the
  metric definition;
- auto-JOINs `pharma_dim_country`, `pharma_dim_product` to get region, brand,
  package and other dimensions;
- YTD, QTD use `metricflow_time_spine` for the cumulative window; YoY and MoM are
  computed via time offsets;
- cross-fact comparisons like budget achievement are computed only within the same
  "month x country x brand x sales type" comparable cell, never comparing local
  actuals against a global budget; budget (`bgt`) and forecast (`fcst`) are kept
  separate by metric filters and are not added together.

## Responsibilities of each link

| Link | Component | Responsibility |
| --- | --- | --- |
| semantic understanding, filling params | the agent's LLM | map natural language to valid metrics / group_by |
| catalog | `list_metrics` / `list_dimensions` | tell the LLM which valid names exist and their meaning |
| conventions and SQL | dbt semantic layer + MetricFlow | decide aggregation, filters, which table to query, how to JOIN |
| execution | Redshift Data API | execute SQL, return columns and rows |
| interpretation | the agent's LLM | turn results into a natural-language answer |

## Why it is designed this way

- **Unified conventions**: aggregation, filters and comparable scope are all
  defined in dbt YAML, so the same metric always computes the same way.
- **The LLM does not write SQL**: it only fills `metrics` / `group_by`, so it
  cannot write SQL that queries the wrong table or double-counts.
- **Adding a metric needs no Python code change**: define it in dbt YAML with the
  `pharma_` prefix, run `dbt parse`, then `backend/refresh-manifest.sh`, rebuild
  and redeploy the image (the manifest is baked into it); `list_metrics` then
  lists it (see [../README.md](../README.md)).

## Related code

```text
app/mcp/server.py        MCP tools: list_metrics / list_dimensions / preview_sql / run_query
app/service.py           DataService: catalog + generate_sql + run_query
app/metricflow/client.py MetricFlowClient: read manifest, build the query, render SQL
app/redshift/client.py   RedshiftDataApi: execute SQL, return columns and rows
```
