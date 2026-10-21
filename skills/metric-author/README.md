# metric-author: auto-complete semantic-layer metrics for dbt + MetricFlow projects

`metric-author` is an agent skill. Once installed, tell the agent in your own dbt
project "take stock of which semantic-layer metrics are missing", and it will:

1. read your models and data;
2. derive all candidate metrics in one pass and compare each against your
   existing metrics;
3. lay them out in a confirm table for you to delete, edit, or add to in natural
   language;
4. after you confirm, write the new metrics into the semantic-layer YAML and
   validate with `dbt parse` and MetricFlow compilation.

The LLM does not write SQL and does not compute. Candidates are expanded by
scripts per rules, so the same input yields the same result every time; all SQL
is produced by MetricFlow compilation. The skill does not change any of your files
before you confirm, and does not auto-commit after.

## Prerequisites

- dbt Core 1.12 or above, using model-level semantic config
  (`models[].semantic_model`, `metrics`);
- able to run `dbt parse` locally to generate `target/semantic_manifest.json`
  (needs only the profile, no warehouse connection);
- model data can be produced locally from seed CSVs. Model SQL that uses only
  `ref()`, `var()`, `config()` is fine; when it uses other macros or `source()`,
  the profiling step needs warehouse sampling, which is not supported yet;
- supported SQL dialects: Redshift, Snowflake, BigQuery, Postgres, DuckDB.

## Installation

Prerequisite: [uv](https://docs.astral.sh/uv/) and Python 3.11+ installed.

Take the skill directory from this repo and copy it into the agent's skills
directory:

```bash
git clone https://github.com/<org>/sample-auto-agentic-olap.git
cd sample-auto-agentic-olap

# Kiro: available to all projects
cp -r skills/metric-author ~/.kiro/skills/

# Kiro: only within one project
cp -r skills/metric-author <your-project>/.kiro/skills/

# Claude Code
cp -r skills/metric-author ~/.claude/skills/
```

The skill's scripts pull dependencies on the fly via `uv run --with ...`
(pinned: pyyaml 6.0.3, duckdb 1.5.6, sqlglot 30.24.0, jinja2 3.1.6, ruamel.yaml 0.19.1), with no pre-install needed.

The validation step needs a Python environment with MetricFlow installed,
pinned to the same MetricFlow commit as the service and dbt project (or point `validate.metricflow_python` at the service's `.venv`):

```bash
uv venv .mf-venv && uv pip install --python .mf-venv/bin/python "metricflow @ git+https://github.com/dbt-labs/metricflow.git@afab9635481dd3e32a9f1895f37ca83bde08a920"
```

## Usage

1. Run `dbt parse` once at the dbt project root.
2. Open the agent and say "take stock of which semantic-layer metrics are
   missing" or "help me fill the semantic-layer metrics".
3. On the first run there is no scenario config `metric_scenario.yml` in the
   project yet. The skill profiles first, then drafts a config from the profile,
   explaining each item, and saves it to the dbt root after you confirm.
4. The skill sends a confirm-table summary. You can ask to expand a category,
   delete or modify by number, or add, e.g. "add a negative-sales share by
   distributor".
5. After you reply "confirm", the skill writes the YAML, runs validation, and
   finally lists the changed files, the dimensions available for each new metric
   and a SQL excerpt, for your review.

Intermediate artifacts (profile, candidates, confirm table) are written under the
dbt project's `target/metric-author/`.

## Scenario config

Project facts that the general rules cannot infer are written in
`metric_scenario.yml` at the dbt root:

- naming prefix, which files new metrics go into;
- each fact table's role (actual, target, event, attribution), and the scenario
  column (e.g. `data_type` splitting budget vs forecast);
- which convention is primary among several;
- which fact tables can be compared and the common grain;
- proposals for ratio and conversion metrics;
- validation commands.

See [`references/scenario-config.md`](references/scenario-config.md) for the
format. This repo's [`medical-olap-dbt/metric_scenario.yml`](../../medical-olap-dbt/metric_scenario.yml)
is a complete example. The config is usually drafted by the skill; you only need
to review it.

## Confirm table

Each candidate has one of five statuses:

| Status | Meaning |
| --- | --- |
| Existing | Your project already has a metric with the same definition (the name may differ) |
| New | Not present yet |
| Convention mismatch | Same name but different definition; the table lists both conventions |
| Needs a model first | Writing it straight into the semantic layer would error; a dbt model must be built first, and the table states what to build |
| Cannot do | Not supported by the data or MetricFlow rules, with the reason stated |

The table also ends with two lists: existing metrics not covered by the
derivation, and measure columns present in the data but not included in the
config. The skill gives a disposition for each item.

## Which metrics are derived

| Metric family | Applicable data | Example |
| --- | --- | --- |
| F1 additive measures | numeric columns that can be summed directly | sales value, payment amount |
| F2 scenario split | the same record appearing once per value | budget / forecast |
| F3 counts | IDs on a detail table | order count, active-customer count |
| F4 time | has a time dimension | YoY, MoM, QTD, YTD, rolling 3 months |
| F5 ratio | a business ratio between two metrics | payment rate, ROI, unit price |
| F6 cross-fact comparison | two fact tables with a common grain | budget achievement, variance, gap |
| F7 semi-additive | snapshot values | period-end inventory, balance |
| F8 reconciliation | negatives, nulls, missing keys, multiple conventions | negative sales value, missing-brand row count |
| F9a conversion | two event tables with a common entity and timestamp | prescription conversion within 30 days after a touch |
| F9b attribution | a fact table with an attribution model and weight | linear attributed revenue |

Each family's enabling conditions and decision rules are in
[`references/families.md`](references/families.md). The skill proactively blocks
these mistakes:

- **cross-table comparison by dividing totals**: when actuals and budget cover
  different scopes, first keep only the cells present on both sides at the common
  grain, then compute the achievement rate;
- **summing snapshot values over time**: inventory recorded once a day would be
  double-counted if summed monthly; take the period-end or period-start value;
- **summing different conventions**: budget and forecast, and different
  attribution models, must be kept separate, each with its own filter;
- **duplicated amounts on a bridge table**: when a column repeats on each related
  entity (e.g. promotion cost on the attribution bridge), summing it on that table
  inflates it, so it is marked cannot-do;
- **invalid conversion-metric inputs**: MetricFlow supports conversion rate, but
  both inputs must be count metrics and cannot carry a filter. When an input is
  not a count it is marked cannot-do; when an input carries a filter it is marked
  needs-a-model-first: build a dbt model with the filter already applied (e.g. a
  view with only completed orders), then use its unfiltered count. Do not put the
  filter on the conversion metric's input; MetricFlow compiles but drops the
  filter.

## Verified scenarios

| Scenario | Data shape | Existing metrics | Derivation coverage |
| --- | --- | --- | --- |
| This repo's pharma sample `medical-olap-dbt` | monthly aggregated mart | 83 | all match by definition |
| `sample-agentic-olap` | order, payment, touchpoint, prescription, attribution detail | 15 | all covered; 3 attribution metrics marked convention mismatch (missing the per-attribution-model filter) |

Semi-additive (F7) and conversion (F9a) have no ready metrics to compare against
in these two projects; only generation and compilation were verified. For the full
evaluation see the [coverage evaluation report](../../docs/metric-author-coverage-report.md).

## Files

```text
metric-author/
  SKILL.md                        the workflow the agent reads
  README.md                       this file
  references/families.md          metric-family rules
  references/scenario-config.md   scenario-config format
  scripts/profile.py              (1) data profiling
  scripts/derive.py               (2) derive candidates, generate the confirm table
  scripts/generate.py             (4) write YAML
  scripts/check_metrics.py        (5) offline compile, list available dimensions
```
