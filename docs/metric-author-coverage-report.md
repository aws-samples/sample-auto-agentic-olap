# metric-author coverage evaluation report

This report evaluates the derivation rules of [`skills/metric-author`](../skills/metric-author/README.md):
take two projects that already have a semantic layer, and see whether the
candidate metrics the skill derives can cover all the metrics already manually
defined in each project.

A gap against an existing metric means a derivation rule is missing. A full match
by definition means the convention the rules produce agrees with the manual
definition.

## 1. Evaluation method

1. `profile.py` profiling: read the semantic structure from
   `semantic_manifest.json`, run the model SQL on local DuckDB over the seed
   CSVs, and compute statistics per column. No warehouse connection.
2. `derive.py` derivation: expand the candidates for all metric families per the
   scenario config, then compare against the existing manifest.
3. Comparison is by **definition signature**, so different names do not affect the
   result. The signature includes:
   - the owning semantic model, aggregation, expression;
   - filter conditions (multiple conditions are order-independent);
   - time offset, cumulative grain, `non_additive_dimension`;
   - the input metrics of derived / ratio / conversion, expanded recursively.
4. Each candidate gets a status:

| Status | Rule |
| --- | --- |
| Existing | An existing metric has an identical signature |
| Convention mismatch | Same name but different signature |
| New | No corresponding item among existing metrics |
| Needs a model first | Writing it straight into the semantic layer would error; a dbt model must be built first, with the how-to attached |
| Cannot do | Not supported by the data or MetricFlow rules, with the reason attached |

**Coverage** = (Existing + Convention mismatch) / number of existing metrics.
Convention mismatch also counts as covered: the rules derived the metric, only
with a different convention than the existing definition, requiring human
judgment on which side is right.

## 2. Evaluation subjects

The two scenarios come from two different repositories. They look alike: the dbt
directory is called `medical-olap-dbt` in both, both are about pharma sales, and
this repo was originally copied from sample-agentic-olap and then changed. But the
data models are entirely different: sample-agentic-olap uses row-per-transaction
detail data; this repo switches the data to the month-aggregated mart layer common
in enterprises, and rewrote all the metrics.

| | Scenario A: aggregated mart (this repo) | Scenario B: detail facts (sample-agentic-olap) |
| --- | --- | --- |
| Repo | this repo `sample-auto-agentic-olap` | `sample-agentic-olap` (a separate detail-fact sample) |
| dbt dir | `medical-olap-dbt` | `medical-olap-dbt` (same name, different content) |
| Data shape | month-aggregated mart tables | row-per-transaction detail tables |
| Fact tables | actual sales, budget/forecast (2) | order items, payments, promotion touchpoints, prescriptions, attribution bridge (5) |
| A row represents | the total for a month x country x brand x package x distributor x sales type | one order item, one payment, one promotion touch |
| Typical metrics | budget achievement, YTD YoY | order count, payment rate, ROI, attributed revenue |
| Existing metrics | 83 | 15 |
| Scenario config | [`medical-olap-dbt/metric_scenario.yml`](../medical-olap-dbt/metric_scenario.yml) | written ad hoc for the evaluation, see appendix |

The two scenarios test different skill abilities: A mainly tests scenario
splitting, time variants and cross-table comparison; B mainly tests counting,
ratios, conversion and attribution. Covering both shows the rules fit more than
one data shape.

## 3. Overall results

### Coverage: were the existing metrics derived

| Scenario | Existing metrics | Existing | Convention mismatch | Uncovered | Coverage |
| --- | --- | --- | --- | --- | --- |
| A aggregated mart | 83 | 83 | 0 | 0 | **100%** |
| B detail facts | 15 | 12 | 3 | 0 | **100%** |

### Composition of derived results: all candidates the skill listed

Total candidates = Existing + Convention mismatch + New + Needs a model first +
Cannot do. The part beyond the existing metrics is what the skill additionally
found and needs the user to confirm.

| Scenario | Total candidates | Existing | Convention mismatch | New | Needs a model first | Cannot do |
| --- | --- | --- | --- | --- | --- | --- |
| A aggregated mart | 98 | 83 | 0 | 15 | 0 | 0 |
| B detail facts | 54 | 12 | 3 | 37 | 2 | 0 |

Other checks:

| Check | A | B |
| --- | --- | --- |
| Deriving the same input twice yields identical `candidates.json` | OK | OK |
| All existing metrics compile offline with the server's same MetricFlow version | 83/83 pass | 15/15 pass |
| End-to-end new metric (generate -> `dbt parse` -> compile -> back-end tests) | OK `pharma_missing_sales_type_rows` | OK test metric, see section 5 |
| After generation, `git diff` only has added lines | OK | OK |

## 4. Results by metric family

### Scenario A: aggregated mart (this repo)

| Metric family | Existing | New | Convention mismatch | Cannot do |
| --- | --- | --- | --- | --- |
| F1 additive measures | 2 | 0 | 0 | 0 |
| F2 scenario split (budget/forecast) | 4 | 0 | 0 | 0 |
| F3 counts | 1 | 0 | 0 | 0 |
| F4 time | 19 | 0 | 0 | 0 |
| F5 ratio | 1 | 0 | 0 | 0 |
| F6 cross-fact comparison | 43 | 0 | 0 | 0 |
| F8 reconciliation | 13 | 15 | 0 | 0 |
| **Total** | **83** | **15** | **0** | **0** |

- **F6** covers all 43 comparison metrics: 6 single-month comparable amounts, 12
  QTD/YTD cumulative comparable amounts, 21 achievement/variance/gap/ratio, and 4
  trend flags. These are the easiest to get wrong, e.g. dividing the two tables'
  totals directly; the derived result agrees exactly with the manual definition.
- **F5**'s same-package average carries 4 preconditions: single package, complete
  package code, complete quantity, complete amount - and matches exactly.
- The **15 new** are all reconciliation metrics:
  - negative volume: 91 rows have negative volume in the data;
  - missing brand: 6 rows have a blank brand;
  - missing-key checks for the budget and forecast tables: no nulls in the sample
    data for now.

  These are gaps the rules found; whether to adopt them is the user's call.

### Scenario B: detail facts (sample-agentic-olap)

| Metric family | Existing | New | Convention mismatch | Needs a model first | Cannot do |
| --- | --- | --- | --- | --- | --- |
| F1 additive measures | 6 | 0 | 0 | 0 | 0 |
| F3 counts | 4 | 0 | 0 | 0 | 0 |
| F5 ratio | 2 | 0 | 1 | 0 | 0 |
| F8 reconciliation | 0 | 37 | 0 | 0 | 0 |
| F9a conversion | 0 | 0 | 0 | 2 | 0 |
| F9b attribution | 0 | 0 | 2 | 0 | 0 |
| **Total** | **12** | **37** | **3** | **2** | **0** |

- **F1** carries the fact table's business filter (`order_status = COMPLETED`).
  **F3** distinguishes `count` (order-item count, touch count) from
  `count_distinct` (order count, active-customer count).
- **F5**'s payment rate and average order value match exactly. ROI is a
  convention mismatch; see the next section.
- The **37 new** are all missing-key reconciliation metrics. Most foreign keys in
  this project lack a `not_null` test, and the sample data has no nulls for now,
  so their value is limited; the user is advised to pick as needed.

## 5. Issues found during evaluation

### 1. Scenario B's attribution metrics have a convention hazard (3 convention mismatches)

| Metric | Existing convention | Derived convention |
| --- | --- | --- |
| `attributed_revenue` | `SUM(attributed_revenue)` | Same, plus `attribution_model = 'linear'` |
| `attribution_weight` | `SUM(attribution_weight)` | Same, plus `attribution_model = 'linear'` |
| `roi` | Based on the unfiltered attributed revenue above | Based on the filtered attributed revenue |

The attribution table allocates each order once per attribution model. Currently
the table has only `linear`, so the existing numbers are not wrong. But as soon as
another attribution model (e.g. `first_touch`) is added, all three metrics would
double-count revenue. The derived result adds the filter per the F2 scenario-split
rule; adopting the derived convention is recommended here.

### 2. Scenario B's fan-out duplication is correctly identified

The attribution bridge table's grain is "order item x touchpoint", and
`cost_amount` repeats on every touchpoint. Profiling detects this, and the confirm
table marks the column "repeats on each touchpoint, cannot be summed". The
existing project does the same: promotion cost is defined only on the touchpoint
table.

Profiling also distinguishes "duplicated" from "already allocated".
`attributed_revenue` on the bridge table is an allocated result, can be summed
normally, and is not misjudged. The test is whether the column exists in the
entity's own fact table.

### 3. Conversion metric inputs cannot carry a filter (2 need a model first)

MetricFlow supports conversion rate (`type: conversion`), but imposes two limits
on both input metrics:

- they must be count metrics (count / count_distinct). A SUM metric like
  prescription volume is rejected outright by dbt's semantic validation;
- they cannot carry a filter.

The config proposes "order conversion within 30 / 90 days after a promotion
touch". "Placing an order" should count completed orders, so `order_count` carries
an `order_status = COMPLETED` filter. Three forms were tested with the server's
pinned MetricFlow version:

| Form | Result |
| --- | --- |
| Use the filtered `order_count` directly as the conversion endpoint | `dbt parse` passes, **compile fails**: cannot find the `order_status` dimension |
| Use an unfiltered order count and put the filter on the conversion metric's input reference | parse and compile pass, but **the generated SQL has no such filter**; cancelled orders count as conversions, so the result is inflated with no error |
| Use an unfiltered order count with no filter | Computes, but the convention becomes "ordered after a touch, including later cancellations" |

The correct approach is to build a dbt model with the filter already applied (e.g.
a view with only completed orders), build a semantic model for it and define an
unfiltered order count, then use that as the conversion endpoint. This step
requires a new dbt model, beyond the skill's "write semantic-layer YAML only"
scope, so the derivation marks it "needs a model first" and the confirm table
states the how-to and the risk of the second form.

An early version broadly marked this "cannot do" and wrote "MetricFlow does not
support it", which is inaccurate and has been corrected.

### 4. Server manifest-reading bug (fixed)

Offline compilation of scenario B's `touchpoint_count` errored. The cause: the
dbt-generated manifest had not yet gone through MetricFlow's own transforms (e.g.
COUNT to SUM), and the server parsed it directly, so any `agg: count` metric
failed when queried over MCP. Scenario A has no count metrics, so it never
surfaced.

See commit `fix: apply MetricFlow transforms when loading the dbt manifest`.
After the fix:

- 10 back-end tests pass;
- 8 saved queries compile;
- all existing metrics in both scenarios A and B compile.

## 6. Parts not sufficiently covered by the evaluation

| Metric family | Situation |
| --- | --- |
| F7 semi-additive | Neither scenario has snapshot data. A test metric (latest covered-patient count by date) verified that generation, `dbt parse` and compile pass; numeric correctness not verified |
| F9a conversion | Neither scenario has a ready conversion metric to compare. A test metric "prescription conversion within 30 days after a touch" verified that generation, parse and compile pass; numeric correctness not verified. The "needs a model first" how-to (build a completed-orders model first) has not been run end-to-end |
| F2 scenario split | Only verified on budget/forecast and a single attribution model, not on a scenario column with three or more values |
| Profiling | Only verified on seed CSV + DuckDB. Warehouse sampling is not implemented; models using macros other than `ref`, `var`, `config` cannot be profiled for now |
| `dbt test` | Requires a warehouse connection and was not run in this evaluation |

The test metrics were written only in scenario B's temporary clone and reverted
after testing; they did not enter any repository.

## 7. How to reproduce

Prerequisites: a local dbt 1.12 environment (`medical-olap-dbt/.venv`), and a
Python environment with the server's same MetricFlow version
(`medical-data-service/backend/.venv`).

```bash
cd medical-olap-dbt                    # for scenario B, switch to that project's dbt root
S=../skills/metric-author/scripts
M=target/metric-author
DEPS="--with pyyaml==6.0.3 --with duckdb==1.5.6 --with sqlglot==30.24.0 --with jinja2==3.1.6"

# dbt parse needs only the profile, no warehouse
mkdir -p $M/profiles && cp profiles.yml.example $M/profiles/profiles.yml
DBT_PROFILES_DIR=$M/profiles REDSHIFT_HOST=localhost REDSHIFT_USER=x REDSHIFT_PASSWORD=x \
  .venv/bin/python scripts/dbt_parse.py

uv run $DEPS python $S/profile.py --project . --config metric_scenario.yml --out $M/profile.json
uv run --with pyyaml==6.0.3 python $S/derive.py --profile $M/profile.json --config metric_scenario.yml
# output: candidates / existing matched / conflict / uncovered; details in $M/review.md

# compile all existing metrics offline
../medical-data-service/backend/.venv/bin/python $S/check_metrics.py \
  --manifest target/semantic_manifest.json \
  --metrics "$(python3 -c "import json;print(','.join(m['name'] for m in json.load(open('target/semantic_manifest.json'))['metrics']))")"
```

The `candidates.json` MD5 for this run: scenario A is
`6ab5e5a2e671a07901fe8a031eadd833`, scenario B is
`6161e88d18c1f7e5bca72f9a762d876d`. These change after the derivation rules or
scenario config change.

## Appendix: scenario B's scenario config

```yaml
version: 1
manifest: [target/semantic_manifest.json]
naming: {prefix: "", language: en}
targets:
  simple: model_yaml
  time: models/semantic/time_metrics.yml
  ratio: models/semantic/cross_model_metrics.yml
  conversion: models/semantic/cross_model_metrics.yml

facts:
  sales_order_items:
    role: actual
    key: sales
    label: Sales
    grain: detail
    filter: {dimension: order_status, value: COMPLETED}
    measures:
      net_amount: {name: net_sales_amount, stem: net_sales, label: Net drug sales, kind: value, time: none}
      quantity:   {name: quantity, label: Sales quantity, kind: quantity, time: none}
    counts:
      order_id:      {name: order_count, label: Order count, agg: count_distinct}
      order_item_id: {name: order_item_count, label: Order item count, agg: count}
      customer_id:   {name: active_customer_count, label: Active customer count, agg: count_distinct}
  payments:
    role: event
    key: payment
    label: Payments
    grain: detail
    filter: {dimension: order_status, value: COMPLETED}
    measures:
      payment_amount: {name: payment_amount, label: Payment amount, kind: value, time: none}
  touchpoints:
    role: event
    key: touchpoint
    label: Promotion touchpoints
    grain: detail
    measures:
      cost_amount: {name: campaign_cost, label: Promotion cost, kind: value, time: none}
    counts:
      touchpoint_id: {name: touchpoint_count, label: Promotion touch count, agg: count}
  prescriptions:
    role: event
    key: prescription
    label: Prescriptions
    grain: detail
    measures:
      prescription_count: {name: prescription_count, label: Prescription volume, kind: quantity, time: none}
      patient_count:      {name: patient_count, label: Covered patient count, kind: quantity, time: none}
  campaign_attribution:
    role: attribution
    key: attribution
    label: Attribution
    grain: detail
    scenario:
      column: attribution_model
      values:
        linear: {key: linear, label: Linear attribution}
    measures:
      attributed_revenue: {name: attributed_revenue, label: Marketing attributed revenue, kind: value, time: none}
      attribution_weight: {name: attribution_weight, label: Attribution weight, kind: weight, time: none}

ratios:
  - {name: payment_rate, label: Payment rate, numerator: payment_amount, denominator: net_sales_amount}
  - {name: roi, label: Marketing ROI, numerator: attributed_revenue, denominator: campaign_cost}
  - {name: average_order_value, label: Average order value, numerator: net_sales_amount, denominator: order_count}

conversions:
  - name: touch_to_order_conversion_rate
    label: Order conversion rate within {window} after a promotion touch
    entity: customer
    base_metric: touchpoint_count
    conversion_metric: order_count
    windows: [30 days, 90 days]
```
