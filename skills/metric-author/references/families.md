# Metric families

Each family states its enabling conditions, outputs, and what you must judge after
derivation. All families are expanded by `derive.py`; you draft the config,
rewrite labels and make the judgments.

## F1 additive measures

- **Enable**: measure columns under `facts.<sm>.measures` in the config that can
  be summed directly at the fact-table grain.
- **Output**: a simple `SUM(column)` metric; when the fact has a `filter` (e.g.
  `order_status = COMPLETED`), carry that filter.
- **Judge**: snapshot/balance columns go to F7; quantities with different units of
  measure cannot be summed across products - warn the user they can only be viewed
  within a single product. A column marked `repeats_per_entity` in the profile is
  fan-out duplication (e.g. promotion cost on the attribution bridge); summing it
  on this table inflates it, and the script marks it "cannot do".

## F2 scenario split

- **Enable**: the config has a `scenario`, or a column's `parallel_versions` in
  the profile roughly equals its number of values (the same record appears once
  per value).
- **Output**: one filtered simple metric per value, e.g. budget, forecast.
- **Judge**: different values cannot be summed. When the profile finds a suspected
  scenario column absent from the config, confirm with the user and add it.

## F3 counts

- **Enable**: `facts.<sm>.counts` in the config. Row IDs on a detail table use
  `count`; IDs coarser than the fact grain (order, customer) use `count_distinct`.
- **Judge**: on an aggregated table you can only count codes (e.g. package-code
  count), not documents; the summary row count is not the order count.

## F4 time

- **Enable**: the semantic model has an `agg_time_dimension`, and the measure
  config has `time: full` or `to_date`. Variants are trimmed by time grain: a
  monthly table does not produce MTD.
- **Output**: `full` produces prior-year same period, YoY, MoM, full-quarter QoQ,
  rolling three months, QTD, YTD and their YoY; `to_date` produces only QTD, YTD.
- **Judge**: a ratio returns null when the denominator is not greater than 0. Plan
  data usually needs only to_date.

## F5 ratio

- **Enable**: `ratios` in the config. Numerator and denominator are two existing
  or candidate metrics.
- **Output**: without `guards`, a MetricFlow `ratio` where numerator and
  denominator each aggregate at their own grain and then divide; with `guards`, a
  derived metric with preconditions that returns null when unmet.
- **Judge**: when drafting the config, you judge which ratios hold up in business
  terms and whether units match; a unit-price ratio must add single-product,
  complete-input guards.

## F6 cross-fact comparison

- **Enable**: the config `comparisons` declares the common grain
  `comparable_entity` and the comparison `pairs`.
- **Output**: each pair first produces both sides' comparable amounts at the
  common grain (`{one_side}_value_with_{other_side}`, keeping only cells where both
  sides have records and complete amounts), then produces achievement, ratio,
  variance and gap per `outputs`, each in single-month, QTD and YTD versions;
  `flags` and `consecutive_below_months` produce "growing but below budget" and
  "below budget for N consecutive months" flags.
- **Judge**: both sides must use comparable amounts; you cannot divide the two
  tables' own totals directly. When one side lacks the other's grouping dimension
  (e.g. the plan table has no package), comparison on that dimension is marked
  "cannot do".

## F7 semi-additive

- **Enable**: a measure config `kind: snapshot` (inventory, balance, headcount and
  other snapshot values).
- **Output**: a simple metric with `non_additive_dimension`, taking the
  period-end (`window_agg: max`) or period-start (`min`) value by the time
  dimension.
- **Judge**: snapshot values are not QTD/YTD-accumulated or YoY-summed; configure
  `group_by` when you need the latest value per entity.

## F8 reconciliation

- **Enable**: a measure has negatives or may be null; a foreign key has no
  `not_null` test; a non-primary-convention measure exists.
- **Output**: negative absolute value and row count, null row count, missing-key
  row count and missing-key amount; an aggregated table (`grain: aggregate`) also
  gets a summary row count.
- **Judge**: the summary row count is not a document count - make the label clear.
  For missing-key checks with no nulls in the sample data, explain to the user and
  let them decide whether to keep them.

## F9a conversion

- **Enable**: `conversions` in the config, with two event tables sharing a common
  entity and timestamp.
- **Output**: a MetricFlow `conversion` metric, one per window.
- **Judge**: both inputs must be count metrics (count / count_distinct), otherwise
  dbt validation rejects them and the script marks it "cannot do". Inputs also
  **cannot carry a filter**: a filtered input fails to compile; putting the filter
  on the conversion metric's input reference compiles but MetricFlow drops the
  filter from the SQL, so the result is inflated with no error. In that case the
  script marks it "needs a model first": build a dbt model with the filter already
  applied (e.g. a view with only completed orders), define an unfiltered count on
  it, and use that as the conversion input. The conversion window must be confirmed
  by the user.

## F9b attribution

- **Enable**: a fact with `role: attribution`, whose table has an
  attribution-model column and already-allocated amounts and weights.
- **Output**: split by attribution model (like F2), one metric per model;
  attribution ROI goes through F5.
- **Judge**: an existing attribution metric with no per-attribution-model filter is
  marked "convention mismatch": it double-counts once a second attribution model
  appears in the table. The default attribution model is confirmed by the user. A
  dbt test checking that each detail's weights sum to 1 per attribution model is
  recommended. When there is no attribution table it is marked "needs an
  attribution model first"; do not write the allocation logic in the semantic
  layer.
