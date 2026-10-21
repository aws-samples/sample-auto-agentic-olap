# Pharma metrics guide

This document follows [current fact-table state](pharma-fact-tables-current.md)
and [unified conventions](pharma-metrics-conventions.md), and describes the
actually supported scope per the current dbt semantic layer. "Implemented" here
does not mean it is live, nor that real business data has been reconciled.

## 1. Usage rules

- The analysis is of monthly shipment sales, not daily sales or recognized
  financial revenue; amounts are in CNY yuan, shown in 10k-yuan.
- Sales value is summed as the fact-table net amount; negative rows are not
  auto-deleted; all `sales_type` values are included by default, and records are
  not auto-excluded by `control_flag`. Filtering happens only when a query
  explicitly specifies it and confirms the business meaning.
  - For example, when sales values are 1,000k and -200k, the net is 800k.
    Negatives may be returns, reversals, allowances or other adjustments; you
    cannot infer the cause just from a negative amount.
  - This does not mean every record is correct; it means that, without a
    confirmed business rule, the raw convention is kept and anomalies are exposed
    through reconciliation metrics.
- Country, region and package prefer existing dimension names; the code is used
  when no name exists. Sales BU uses the fact-table field, not the product BU.
- The two fact tables are aligned by brand code; brand names come from the unique
  mapping in the product dimension. `PX14` maps to multiple names, so the code is
  shown until confirmed; you must not join multiple package rows directly by brand.
- Actuals, budget and forecast are each summed first, then compared over the same
  period and scope. Budget and forecast are not added together as a target.
  - For example, when comparing "Jan-Aug 2026, China, brand A, 自营 type", compute
    actuals, budget and forecast separately, then compare "actual vs budget",
    "actual vs forecast", "forecast vs budget". You cannot compare local actuals
    against a global budget.
  - Budget is the originally set target; forecast is the amount expected to be
    achieved given current information - they are different scenarios and cannot
    be added into a new target.
- Missing records are not filled with zero; a percentage returns null when its
  final denominator is zero, negative or missing. Growth rate and change,
  achievement and gap, share and amount are shown in pairs.
  - No budget record only means "budget missing", not budget = 0; you cannot
    compute an achievement rate from it.
  - If the budget or prior-year amount is 0, negative or missing, the
    corresponding achievement or growth rate returns null, to avoid infinities or
    misleading ratios.
  - A percentage must be read alongside the amount, e.g. "achievement 80%, actual
    8,000k, budget gap 2,000k", not just 80%.
- Data is already aggregated across dimensions; the summary row count is not the
  order or transaction count, so do not use `AVG(sales_value)` to represent a
  business average; nor should overall achievement or growth rate be derived by
  simply averaging per-item percentages.
  - Wrong example: product A is split into two summary records with sales values
    1,000k and 200k; product B has one with 600k. Computing
    `AVG(sales_value)=(100+20+60)/3=60` (in 10k-yuan) yields only the average per
    summary record and is distorted by the split row count. The correct method is
    to aggregate by product first, then compute `(120+60)/2=90` (in 10k-yuan) as
    the average per product.
  - Per-item percentages also cannot be simply averaged, because the denominators
    differ. E.g. product A actual/budget is `100/100=100%`, product B is
    `450/900=50%`; a simple average gives 75%, but the overall achievement should
    re-aggregate numerator and denominator: `(100+450)/(100+900)=55%`. Overall
    growth rate likewise uses re-aggregated current and prior amounts.
- In this project, MTD is simply the selected month's aggregate. QTD for August
  2026 is Jul-Aug, with YoY against Jul-Aug of the prior year; these two months
  are not treated as a routine QoQ against a full three-month prior quarter.

## 2. Natively defined semantic metrics

Across the two fact tables there are **83 MetricFlow metrics** defined: 40 base,
time and reconciliation metrics, plus 43 comparable
budget/forecast metrics. The additions include 6 comparable-amount inputs, 12
QTD/YTD comparable amounts, 21 achievement/variance/gap metrics and 4 trend
flags; do not read "83" as 83 mutually independent business conventions. No new
DM table or dimension table was added.

### Base metrics

| Metric | Description |
| --- | --- |
| `pharma_sales_value` | Actual sales value. |
| `pharma_sales_quantity` | Actual sales quantity, used only within a unit-comparable scope. |
| `pharma_unsplit_sales_quantity` | Unsplit quantity, used only for reconciliation. |
| `pharma_budget_value` / `pharma_budget_quantity` | Budget value/quantity for `data_type=bgt`. |
| `pharma_forecast_value` / `pharma_forecast_quantity` | Forecast value/quantity for `data_type=fcst`. |

### Added time and derived metrics

The names below all carry the `pharma_` prefix. YoY (year over year) compares the
same period of the prior year; MoM (month over month) compares the immediately
preceding month; QoQ (quarter over quarter) compares the immediately preceding
quarter.

MoM is computed per single month only. When querying multiple months you may
return each month's MoM, but you cannot sum those months and compare against an
overlapping window shifted one month back. For example, Jun-Aug 2026 compared
against a non-overlapping prior three months should use Mar-May; that is an
"equal-length period-over-period" that is not yet defined, not the current
`sales_mom_*` metrics.

QoQ is used only for full quarters. **"Full quarter" is a precondition: the
current code only specifies the quarter grain and offset and does not check
whether the quarter's months are complete; the querying side must confirm this.**
Example amounts below are shown in 10k-yuan.

| Metric name (prefix omitted) | Name | Simple example |
| --- | --- | --- |
| `sales_prior_year` | Prior-year same-period actual sales value | Querying Aug 2026 maps to Aug 2025; Jun-Aug 2026 maps to Jun-Aug 2025. If last year those three months were 80, 100, 120, the same-period total is 300; shown by month it returns the three corresponding months. |
| `sales_prior_month` | Prior-month actual sales value | Querying Aug 2026, if Jul 2026 actual sales was 90, this metric is 90. Used only for single-month MoM. |
| `sales_yoy_change` | Actual sales YoY change | This month 120, prior-year same period 100, so YoY change is `120-100=20`. |
| `sales_yoy_rate` | Actual sales YoY growth rate | This month 120, prior-year same period 100, so YoY rate is `20/100=20%`. |
| `sales_mom_change` | Actual sales MoM change | This month 120, last month 90, so MoM change is `120-90=30`. |
| `sales_mom_rate` | Actual sales MoM growth rate | This month 120, last month 90, so MoM rate is `30/90=33.3%`. |
| `sales_qoq_change` | Full-quarter actual sales QoQ change | This quarter 330, last quarter 300, so QoQ change is `330-300=30`. |
| `sales_qoq_rate` | Full-quarter actual sales QoQ growth rate | This quarter 330, last quarter 300, so QoQ rate is `30/300=10%`. |
| `sales_qtd` | Quarter-to-date actual sales value | Querying Aug, Jul 100 and Aug 120, so QTD is 220. |
| `sales_ytd` | Year-to-date actual sales value | Jan-Aug total 800, so Aug YTD is 800. |
| `budget_qtd` | Quarter-to-date budget value | Jul-Aug budget total 240, so Aug budget QTD is 240. |
| `budget_ytd` | Year-to-date budget value | Jan-Aug budget total 1,000, so Aug budget YTD is 1,000. |
| `forecast_qtd` | Quarter-to-date forecast value | Jul-Aug forecast total 230, so Aug forecast QTD is 230. |
| `forecast_ytd` | Year-to-date forecast value | Jan-Aug forecast total 950, so Aug forecast YTD is 950. |
| `sales_qtd_yoy_change` | QTD actual sales YoY change | This year QTD 220, prior-year QTD 200, so YoY change is 20. |
| `sales_qtd_yoy_rate` | QTD actual sales YoY growth rate | This year QTD 220, prior-year QTD 200, so YoY rate is `20/200=10%`. |
| `sales_ytd_yoy_change` | YTD actual sales YoY change | This year YTD 800, prior-year YTD 700, so YoY change is 100. |
| `sales_ytd_yoy_rate` | YTD actual sales YoY growth rate | This year YTD 800, prior-year YTD 700, so YoY rate is about `100/700=14.3%`. |
| `sales_rolling_3m` | Rolling 3-month actual sales value | Jun-Aug 80, 90, 120, so the rolling three-month sales is 290. |
| `same_package_average_value` | Same-package net shipment average price (raw quantity unit) | Same-package net sales 1,000k and net quantity 1,000, so the average price is 1,000 yuan per raw quantity unit. |

A rate (derived) metric returns null when its denominator is zero, negative or missing;
the rolling three months returns null when any month is missing; the same-package
average also requires a single package, complete amount and quantity, and net
quantity greater than zero.

### Comparable budget and forecast metrics

Actuals, budget and forecast are compared only within a common "month + country +
brand + sales type" cell. If any identity field is missing, an amount is null, or
the other side has no record, that cell does not enter the achievement rate.
Common cells are aligned through the nullable `pharma_comparable_cell` entity on
the two existing fact models; no new entity table was added.

| Metric family (all with `pharma_` prefix) | Definition |
| --- | --- |
| `actual_value_with_budget` / `budget_value_with_actual` | In common cells having both complete actuals and complete budget, sum the actual and budget amounts respectively. |
| `actual_value_with_forecast` / `forecast_value_with_actual` | In common cells having both complete actuals and complete forecast, sum the actual and forecast amounts respectively. |
| `budget_value_with_forecast` / `forecast_value_with_budget` | In common cells having both complete budget and complete forecast, sum the budget and forecast amounts respectively. |
| The above amounts with `_qtd` / `_ytd` | Determine comparable cells month by month first, then accumulate the quarter/year amount; not summing the two fact tables and dividing directly. |
| `budget_achievement[_qtd/_ytd]` | Budget-comparable actual amount / actual-comparable budget amount; returns null when the budget amount is not greater than 0. |
| `budget_variance[_qtd/_ytd]` | Budget-comparable actual amount - actual-comparable budget amount. |
| `budget_gap[_qtd/_ytd]` | `max(actual-comparable budget amount - budget-comparable actual amount, 0)`. |
| `forecast_achievement[_qtd/_ytd]` | Forecast-comparable actual amount / actual-comparable forecast amount; returns null when the forecast amount is not greater than 0. |
| `forecast_variance[_qtd/_ytd]` | Forecast-comparable actual amount - actual-comparable forecast amount. |
| `forecast_budget_variance[_qtd/_ytd]` | Forecast amount - budget amount in budget-forecast comparable cells. |
| `forecast_budget_rate[_qtd/_ytd]` | Forecast amount / budget amount in budget-forecast comparable cells; returns null when the budget amount is not greater than 0. |
| `growing_but_below_budget[_qtd/_ytd]` | 1 when the YoY change over the same analysis period is greater than 0 and the comparable budget achievement is below 1. |
| `three_months_below_budget` | 1 when the comparable budget achievement for the current month and the prior two months are all below 1; returns null when any month lacks a valid achievement rate. |

Example: product A has actual 1,000k and budget 2,000k, both in the same common
cell, so `budget_achievement=50%`, `budget_variance=-1,000k`, `budget_gap=1,000k`.
Another product with only a 10,000k budget and no same-cell actual record does not
enter the achievement rate; comparing `budget_value` with `budget_value_with_actual`
shows how much budget did not enter the comparable scope.

### Reconciliation metrics

Reconciliation metrics find data issues and do not represent business events such
as orders or returns.

| Metric name (prefix omitted) | Name | Simple example |
| --- | --- | --- |
| `actual_rows` | Actual summary row count (not order count) | 12 aggregated records in the query scope yields 12; it does not mean 12 orders. |
| `actual_null_rows` | Rows missing actual amount | 1 of 12 actual records has a null sales value, so the result is 1. |
| `negative_sales_value` | Absolute value of negative sales adjustments (not returns) | Two negatives -20 and -5 yield `20+5=25`. |
| `negative_sales_rows` | Negative sales summary row count (not return-order count) | Two negative-amount records -20 and -5 yield 2. |
| `actual_package_count` | Actual package-code count (average-price check) | Package codes P1, P2, P2 and a blank yield 2 distinct non-blank packages. |
| `missing_package_rows` | Rows missing package code | 1 of 12 actual records has a blank package code, so the result is 1. |
| `quantity_null_rows` | Rows missing actual quantity | 1 of 12 actual records has a null sales quantity, so the result is 1. |
| `missing_brand_value` | Sales value with missing brand code | Records with a blank brand code have sales value 10, so the result is 10. |
| `missing_sales_type_rows` | Rows missing sales type | 1 of 12 actual records has a blank sales type, so the result is 1; such records do not enter the budget/forecast comparable cells. |
| `budget_rows` | Budget summary row count | 10 `bgt` records in scope yields 10. |
| `budget_null_rows` | Rows missing budget amount | 1 of 10 `bgt` records has a null amount, so the result is 1. |
| `forecast_rows` | Forecast summary row count | 8 `fcst` records in scope yields 8. |
| `forecast_null_rows` | Rows missing forecast amount | 1 of 8 `fcst` records has a null amount, so the result is 1. |

#### Actual uses in the current system, and counter-examples

| Actual use | Reference metrics | Counter-example if the reconciliation metric is ignored |
| --- | --- | --- |
| Protect budget comparison | `budget_rows`, `budget_null_rows` | Two budgets of 1,000k and null, actual 800k. The SQL runs fine, but the cell does not enter `budget_achievement`, and `budget_null_rows`=1 signals an incomplete amount; you cannot compute 80% from the known 1,000k and treat it as a complete achievement rate. |
| Protect forecast comparison | `forecast_rows`, `forecast_null_rows` | Two forecasts of 900k and null, actual 800k. The SQL runs fine, but the cell does not enter `forecast_achievement`, and `forecast_null_rows`=1 signals an incomplete amount; you cannot treat 88.9% as a complete forecast achievement rate. |
| Protect same-package average price | `actual_package_count`, `missing_package_rows`, `quantity_null_rows`, `actual_null_rows` | Mixing 100mg and 500mg packages into one "average price" yields no clear product meaning; the current system requires a single package with complete inputs. |
| Explain negative-sales impact | `negative_sales_value`, `negative_sales_rows` | Positive sales 1,000k, negative adjustments -400k, net 600k. Without looking at the negatives, you might explain all 600k as normal positive performance. |
| Reconcile brand amount | `missing_brand_value` | Total sales 1,000k, known brands sum to 900k, the other 100k has a blank brand code. Ignoring the missing-brand amount, you would wrongly think the by-brand report undercounts 100k. |
| Observe aggregated data scale | `actual_rows`, `budget_rows`, `forecast_rows` | If the actual summary row count drops from 100 to 50, it only signals that grain or load scope may have changed; treating it as an order count would wrongly conclude "orders halved". |

Reconciliation metrics mainly protect calculations and aid troubleshooting: only
when records exist and amounts are complete do actuals, budget and forecast enter
their comparable scope; the same-package average additionally requires a single
package, complete amount and quantity, and net quantity greater than zero.
Incomplete data does not make MetricFlow fail to compile, because no data is
queried at compile time; after executing SQL, unreliable ratios or differences
return null, and you can query the corresponding reconciliation metric to explain
why.

These metrics only surface some risks and cannot by themselves prove data
completeness. `actual_rows=100` only means 100 aggregated records, not 100 orders;
a normal record count does not prove that every month and brand has complete data.

Definition files: [base, reconciliation and comparable-amount metrics](../medical-olap-dbt/models/pharma-dm/schema.yml),
[time and derived metrics](../medical-olap-dbt/models/pharma-dm/pharma_metrics.yml),
[comparable budget/forecast metrics](../medical-olap-dbt/models/pharma-dm/pharma_comparison_metrics.yml).

Totaling sales by brand, country, distributor, etc. is the same
`pharma_sales_value` with different grouping dimensions, not a separate batch of
duplicate SUM metrics. Budget/forecast can only use the dimensions they have;
cross-fact comparisons use the common brand, country and sales-type entities.

## 3. Executive follow-ups and combined analysis

**Did we grow this year -> who contributed -> did the growing products meet
budget -> is the forecast enough for budget?**

These questions are all answered with native MetricFlow metrics: the agent picks
metrics and grouping dimensions via `run_query`, and the SQL is generated by
MetricFlow. Common combinations are also defined as saved queries
([pharma_queries.yml](../medical-olap-dbt/models/pharma-dm/pharma_queries.yml)).

| Executive question | Metrics used (`pharma_` prefix omitted) | Common grouping |
| --- | --- | --- |
| Did we grow? Who contributed, who dragged? | `sales_value`, `sales_yoy_change`, `sales_yoy_rate`; for cumulative periods use `sales_ytd_yoy_*` / `sales_qtd_yoy_*` | brand, country, region, sales type, distributor, sales BU, package |
| Which keep getting worse? | `sales_rolling_3m`, `sales_mom_change`, `sales_mom_rate` | by month + any dimension |
| Did the growing products meet budget? | `budget_achievement[_qtd/_ytd]`, `budget_variance[_qtd/_ytd]`, `budget_gap[_qtd/_ytd]`, `growing_but_below_budget[_qtd/_ytd]` | brand, country, sales type and their crosses |
| Which miss budget for consecutive periods? | `three_months_below_budget` | by month + brand / country / sales type |
| How far is actual from forecast? Is the forecast enough for budget? | `forecast_achievement`, `forecast_variance`, `forecast_budget_variance`, `forecast_budget_rate` (all have `_qtd/_ytd`) | brand, country, sales type and their crosses |
| Does the target cover these sales? | `sales_value` vs `actual_value_with_budget`, `budget_value` vs `budget_value_with_actual`, combined with `*_rows`, `*_null_rows` | brand, country, sales type |

Budget and forecast can only be grouped by the brand, country and sales type
shared across the two fact tables; package, distributor and sales BU can only be
used for actual-sales metrics.

## 4. Metrics that explicitly cannot be analyzed with the current data

| Metric that explicitly cannot be analyzed | Reason |
| --- | --- |
| Package/SKU budget achievement, variance, gap | The actuals table has `package_code`, but the plan table does not; it only has free-text `package_name` that cannot be reliably matched one-to-one to the product dimension, so you cannot compare by package code directly. |
| Distributor budget achievement, variance, gap | The plan table has no distributor field. |
| Sales-BU budget achievement, variance, gap | The plan table has no sales-BU field. |
| Cross-product total volume, overall quantity achievement | No unified unit of measure; quantities of different products cannot be added directly. |
| Daily sales, MTD-to-date, daily peak, daily volatility | The fact tables have only monthly aggregates, no daily records. |
| Recognized financial revenue, gross profit, gross margin, profit | No revenue recognition, cost or expense data. |
| Collections, overdue amounts, aging | No receivables, due-date or write-off data. |
| Terminal sell-through, channel inventory, inventory turnover | No terminal flow or inventory data. |
| True return amount, return rate | Only negative sales amounts; cannot tell whether they are returns, reversals, allowances or other adjustments. |
| Market share | No external market total; internal sales share cannot substitute for market share. |
| Historical forecast accuracy | No versioned historical forecast snapshots with forecast timestamps. |
| FX-impact amount, FX-impact rate | No original-currency amounts or applicable FX rates. |
| Order count, hospital count, individual performance | No order, hospital or salesperson detail. |
| Average amount per order, average per deal | No order or transaction count; the aggregated table's row count cannot substitute for the denominator. |
| Transaction price median, quantiles, price distribution | Aggregated amounts and quantities cannot reconstruct per-transaction price distribution. |
| License fees, milestone revenue | No contract or rights-settlement data; 授权-type shipments cannot substitute. |
