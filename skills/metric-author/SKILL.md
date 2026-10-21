---
name: metric-author
description: For a dbt + MetricFlow project, inventory and derive all candidate metrics in one pass, list them for the user to confirm or add to, then generate semantic-layer YAML and validate. Use when the user wants to auto-generate, inventory, or fill gaps in semantic-layer metrics.
---

# Metric Author

**Inventory** the data of a dbt + MetricFlow project, derive all candidate metrics
per the metric-family rules, hand them to the user on a **confirm table** to
select, edit and add to, and only write semantic-layer YAML after confirmation.

Division of labor: scripts do the mechanical expansion, reproducibly; you handle
labels, business descriptions, feasibility judgments and user communication. You
only write semantic-layer YAML; all SQL is produced by MetricFlow compilation.

The scripts are under this skill's `scripts/`, the working directory is the
project's dbt root, and intermediate artifacts go to `target/metric-author/`.
Run `profile.py`, `derive.py`, `generate.py` with
`uv run --with pyyaml==6.0.3 --with duckdb==1.5.6 --with sqlglot==30.24.0 --with jinja2==3.1.6 --with ruamel.yaml==0.19.1 python <script>`;
`check_metrics.py` needs an environment with MetricFlow installed, i.e. the
config's `validate.metricflow_python`.

## Scenario config

One `metric_scenario.yml` per project, at the dbt root, stating the project facts
the general rules cannot infer: naming prefix, which files to write into, each
fact table's role and scenario column, primary vs reconciliation conventions,
cross-fact comparison relationships, convention-doc location, validation commands.
The format follows [`references/scenario-config.md`](references/scenario-config.md).

When the project has no config yet, run an inventory first, draft the config from
the profile, explain each item to the user, and save after they confirm.

## Workflow

### (1) Inventory

```bash
uv run --with pyyaml==6.0.3 --with duckdb==1.5.6 --with sqlglot==30.24.0 --with jinja2==3.1.6 --with ruamel.yaml==0.19.1 python <skill-dir>/scripts/profile.py --project . --config metric_scenario.yml --out target/metric-author/profile.json
```

Done when: the profile covers every column of every fact table in the config,
with no `profile_error`. Profiling renders `ref()`, `var()` (using defaults) and
`config()`; it errors when a model uses other macros or `source()`, at which point
tell the user warehouse sampling is needed.

### (2) Derive

```bash
uv run --with pyyaml==6.0.3 --with duckdb==1.5.6 --with sqlglot==30.24.0 --with jinja2==3.1.6 --with ruamel.yaml==0.19.1 python <skill-dir>/scripts/derive.py --profile target/metric-author/profile.json --config metric_scenario.yml
```

Produces `candidates.json` and `review.md`. Candidates are compared against the
existing manifest by definition (aggregation, expression, filter, time offset),
with statuses: existing, new, convention mismatch, needs a model first, cannot do.
A convention mismatch lists both the derived and the existing convention.

Then go through `review.md`, reading the decision rules for each metric family in
[`references/families.md`](references/families.md):

- rewrite the script-generated label into business language (e.g.
  `missing-brand summary row count` -> `rows missing brand code`);
- for "cannot do" items judged by the config's convention doc, state what data is
  missing; for "needs a model first" items, state what dbt model to build - do not
  hand-bypass on the semantic-layer side;
- check the "existing but not covered by candidates" list: for each, state which
  rule or config item is missing, add it, and re-run;
- check the "measure columns present in the profile but absent from the config"
  list: for each column, either add it to the config and re-run, or state why it
  is excluded;
- for each "convention mismatch", explain how the two sides differ and which is
  right, and let the user decide whether to change the existing metric or keep it.

Done when: every candidate has a status and a label; every "cannot do" / "needs a
model first" / "convention mismatch" has a reason; every item in the two trailing
lists has a disposition.

### (3) Confirm

First send the summary table (per family, the counts of existing, new, convention
mismatch, needs a model first, cannot do); expand a family's detail when the user
asks. When the project already has metrics, by default expand only "new",
"convention mismatch", "needs a model first" and "cannot do". List parameters like
conversion window and default attribution model separately for the user to
confirm.

The user can delete or modify by number, or add new metrics in natural language.
An added item is typed, de-duplicated and checked for feasibility by the same
metric-family rules, merged into the confirm table, and re-sent to the user.

Done when: the user explicitly replies confirm. No project file is written before
confirmation.

### (4) Generate

Write the confirmed result into `target/metric-author/confirmed.json`: `metrics`
lists the confirmed metric names, `labels` / `descriptions` hold your rewritten
names and descriptions. Then:

```bash
uv run --with pyyaml==6.0.3 --with duckdb==1.5.6 --with sqlglot==30.24.0 --with jinja2==3.1.6 --with ruamel.yaml==0.19.1 python <skill-dir>/scripts/generate.py --profile target/metric-author/profile.json \
  --candidates target/metric-author/candidates.json \
  --confirmed target/metric-author/confirmed.json --config metric_scenario.yml
```

The script writes only candidates with status "new", placing them into files per
the config `targets`: simple metrics into the fact model's own YAML, cross-fact
comparison into `comparison`, ratio into `ratio`, conversion into `conversion`,
the rest into `time`. New metric blocks are appended as text to the end of the
target list; existing lines are not rewritten. If a user-added metric cannot be
derived by the rules, add it to the scenario config or derivation rules and re-run
(2) - do not hand-write YAML.

Done when: every confirmed item is written, and `git diff` has only the new metric
blocks.

### (5) Validate

Run the commands in the config `validate` in order:

1. `parse`: regenerate `semantic_manifest.json`, with MetricFlow validation
   running along with it. dbt parse needs only the profile, no connection; when
   the profile is missing, drop the project's profile example into
   `target/metric-author/profiles/`.
2. With `metricflow_python`, run
   `scripts/check_metrics.py --manifest target/semantic_manifest.json --metrics <new metrics> --dialect <dialect>`
   to confirm each new metric compiles, and record the number of available
   dimensions.
3. Each command in `offline`.
4. The commands in `warehouse` only when the user asks to connect the warehouse.

Done when: all of the above pass. If any step fails, go back to (4), fix the
config or candidates and regenerate - do not hand-edit the generated YAML.

### (6) Report

- run `refresh` to package the new manifest for the service;
- re-run (1) (2) to confirm the new metrics' status becomes "existing";
- per the config `conventions_docs`, add a line for each new metric in the metrics
  guide;
- list for the user the changed files, each new metric's available dimension count
  and a SQL excerpt.

Done when: all of the above are done; stop and wait for the user's review - do not
auto-commit.
