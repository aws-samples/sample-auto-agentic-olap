# metric_scenario.yml format

```yaml
version: 1
manifest: [target/semantic_manifest.json]   # searched in order, the first that exists wins

naming:
  prefix: pharma_        # name prefix for all generated metrics, may be ""
  language: zh           # label language: zh / en

targets:                 # where generated YAML is written (relative to the dbt root)
  simple: model_yaml     # into the fact model's own schema.yml
  time: <path>           # F4, and derived / cumulative with no dedicated target
  comparison: <path>     # F6
  ratio: <path>          # F5, defaults to time
  conversion: <path>     # F9a, defaults to time

facts:                   # key is the semantic model name
  <semantic_model>:
    role: actual | target | event | attribution
    key: sales                      # reconciliation-metric name prefix; required when a role has multiple tables
    label: Actuals                  # reconciliation-metric label
    grain: aggregate | detail       # only an aggregated table generates the "summary row count" check
    filter: {dimension: order_status, value: COMPLETED}   # optional: shared by all metrics on this table
    scenario:                       # optional: F2 scenario column
      column: data_type
      values:
        bgt: {key: budget, label: Budget}
    measures:                       # key is the column name
      <column>:
        name: sales_value           # metric name (no prefix), may use {scenario}
        stem: sales                 # name prefix for time variants, may use {scenario}
        label: Actual sales value   # may use {scenario_label}
        kind: value | quantity | weight | snapshot
        time: full | to_date | none
        role: check                 # optional: non-primary convention, generates reconciliation metrics only
        window_agg: max | min       # snapshot only: period-end / period-start
    counts:                         # F3
      <column>: {name: order_count, label: Order count, agg: count_distinct, expr: <optional>}

comparisons:             # F6
  comparable_entity: <entity>       # the comparable grain shared across fact tables
  pairs:
    - subject: actual               # a side's key: the fact's key/role, or a scenario value's key
      reference: budget
      outputs: [achievement, variance, gap, rate]
      flags: [growing_below]        # optional: YoY growth but below target
      consecutive_below_months: 3   # optional: below target for N consecutive months

ratios:                  # F5, metric names without prefix
  - {name: payment_rate, label: Payment rate, numerator: payment_amount, denominator: net_sales_amount}
  - name: same_package_average_value
    label: Same-package net shipment average price
    numerator: sales_value
    denominator: sales_quantity
    guards:                         # optional: compute only when all are satisfied
      - {metric: actual_package_count, alias: package_count, condition: "= 1"}

conversions:             # F9a
  - name: touch_to_rx_conversion_rate
    label: Prescription conversion rate within {window} after a promotion touch
    entity: customer
    base_metric: touchpoint_count
    conversion_metric: prescription_event_count
    windows: [30 days]

conventions_docs: [<path>]          # docs read/written when judging "cannot do" or adding metric notes
validate:
  parse: <cmd>                      # regenerate semantic_manifest.json
  metricflow_python: <path>         # python with the server's same MetricFlow version
  dialect: redshift
  offline: [<cmd>]                  # checks that do not connect to the warehouse
  warehouse: [<cmd>]                # run only when the user asks
  refresh: <cmd>                    # package the manifest for the service
```

## Drafting rules

- `measures` takes columns with `proposed_role: measure` in the profile. Numeric
  columns that are IDs or sequence numbers (e.g. `region_sequence`), and
  non-additive columns like unit price and discount, are not measures;
  `period_label` columns (e.g. `yearmonth`) are not measures; columns marked
  `repeats_per_entity` are not put in this table's measures.
- `counts` takes the detail table's row IDs (`proposed_role: row_id`) and business
  IDs coarser than the grain (order number, customer).
- A column with only a few values where the same record appears once per value
  (`parallel_versions` ~ number of values) is handled as a `scenario`; a status
  column with only one valid value (e.g. `COMPLETED`) is handled as a `filter`.
- When a fact has multiple quantity conventions, ask the user which is primary.
- `ratios` and `conversions` are proposed by you per business meaning; every one
  must be shown to the user in the confirm table.
- `derive.py` lists "measure columns present in the profile but absent from the
  config" at the end of the confirm table; each column must either be added to the
  config or explained to the user as to why it is excluded.
```
