# medical-olap-dbt

dbt project: a pharmaceutical sales mart (DM layer), dimension models and a
MetricFlow semantic layer, running on Redshift Serverless. All data is
synthesized by `scripts/generate_seeds.py`.

## Setup

```bash
uv venv --python 3.13 .venv
source .venv/bin/activate
uv pip install -r requirements.txt
cp profiles.yml.example ~/.dbt/profiles.yml
```

`requirements.txt` pins the direct dependencies. The dbt runner image installs
`requirements.lock`, which pins every package including transitive ones and
`awscli`; regenerate it with the command in its header after changing
`requirements.txt` or `requirements-runner.in`.

Database credentials are passed via environment variables:

```bash
export REDSHIFT_HOST=<workgroup-endpoint>
export REDSHIFT_USER=adminuser
export REDSHIFT_PASSWORD='...'
```

## Run

```bash
dbt debug
dbt seed
dbt run
dbt test
```

## Models

```text
seeds/
  pharma-dim/   dw_dim_product - dw_dim_country - dw_dim_region
  pharma-dm/    dm_fact_sales_detail - dm_fact_sales_forecast
  seed_date_spine.csv                     2021-01-01 to 2027-12-31

models/
  pharma-dim/   pharma_dim_product - pharma_dim_region - pharma_dim_country (region merged in)
  pharma-dm/    pharma_sales_detail - pharma_sales_forecast
                + semantic models, 83 metrics, 8 saved queries
  semantic/     metricflow_time_spine
```

Target schemas:

```text
sim_business   raw seed data
pharma_dim     dimensions
pharma_dm      actual sales, budget/forecast     <- the semantic layer is built here
semantic       MetricFlow time spine
```

The DM models only do type conversion and derived keys, with no data cleaning:
negative rows, comparator rows and missing values are all kept as-is, and exposed
by the semantic layer's reconciliation metrics (e.g. `pharma_negative_sales_value`,
`pharma_actual_null_rows`). `comparable_cell_id` (month x country x brand x
sales_type) is the smallest unit at which actuals align with budget/forecast for
comparison; the budget-achievement metrics are all built on it.

## MetricFlow version note

This project pins MetricFlow to a specific commit (installed from git, not from PyPI). The
verified and pinned combination:

```text
metricflow:   afab9635481dd3e32a9f1895f37ca83bde08a920 (0.213.0.dev0)
dbt-core:     1.12.4
dbt-redshift: 1.11.1
sqlparse:     0.6.0
```

It uses dbt 1.12+ model-level Semantic Layer config:

- semantic config is embedded in the corresponding dbt model's `models[].semantic_model`;
- entities and dimensions are configured via the model's `columns`;
- simple metrics are written directly under that model's `metrics`;
- cross-semantic-model ratio / derived metrics go in the top-level `metrics`;
- the legacy top-level `semantic_models`, `measures` or `type_params.measure` are not used.

`dbt parse` compiles this config into `target/semantic_manifest.json`, and the
MetricFlow Python Engine then consumes that manifest to generate Redshift SQL.
After changing the semantic layer, run
`medical-data-service/backend/refresh-manifest.sh` to package the new manifest
into the service image.

## Scripts

```bash
python scripts/generate_seeds.py                      # regenerate the synthetic data (fixed seed)
python scripts/dbt_parse.py                           # dbt parse compatibility entry point
python scripts/semantic/show_semantic_layer.py        # list semantic models and metrics
python scripts/semantic/compile_saved_queries.py      # compile all saved queries (no warehouse connection)
```
