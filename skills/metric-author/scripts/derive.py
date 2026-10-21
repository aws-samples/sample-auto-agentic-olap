"""Expand candidate metrics from a profile + scenario config, and diff them
against the existing semantic manifest by definition (not by name).

Usage:
    python derive.py --profile profile.json --config metric_scenario.yml \
        [--out-dir <dir>]

Writes <out-dir>/candidates.json and <out-dir>/review.md.
Families: F1 additive, F2 scenario split, F3 counts, F4 time, F5 ratios,
F6 cross-fact comparison, F7 semi-additive, F8 checks, F9a conversion, F9b attribution.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

RATE = "case when previous_value > 0 then (current_value - previous_value) / previous_value end"
CHANGE = "current_value - previous_value"

# variant -> (zh label, en label, min grain, builder)
TIME_LABELS = {
    "prior_year": ("上年同期{label}", "{label} prior year"),
    "yoy_change": ("{label}同比增量", "{label} YoY change"),
    "yoy_rate": ("{label}同比增长率", "{label} YoY rate"),
    "prior_month": ("上月{label}", "{label} prior month"),
    "mom_change": ("{label}月环比增量", "{label} MoM change"),
    "mom_rate": ("{label}月环比增长率", "{label} MoM rate"),
    "qoq_change": ("完整季度{label}环比增量", "{label} QoQ change (full quarters)"),
    "qoq_rate": ("完整季度{label}环比增长率", "{label} QoQ rate (full quarters)"),
    "ytd": ("年度累计{label}", "{label} YTD"),
    "qtd": ("季度累计{label}", "{label} QTD"),
    "mtd": ("月度累计{label}", "{label} MTD"),
    "ytd_yoy_change": ("年度累计{label}同比增量", "{label} YTD YoY change"),
    "ytd_yoy_rate": ("年度累计{label}同比增长率", "{label} YTD YoY rate"),
    "qtd_yoy_change": ("季度累计{label}同比增量", "{label} QTD YoY change"),
    "qtd_yoy_rate": ("季度累计{label}同比增长率", "{label} QTD YoY rate"),
    "rolling_3m": ("近三个月滚动{label}", "{label} rolling 3 months"),
}
CHECK_LABELS = {
    "rows": ("{fact}汇总行数（非业务单据数）", "{fact} aggregate rows (not documents)"),
    "null_rows": ("缺{label}汇总行数", "rows missing {label}"),
    "negative_value": ("负{label}绝对值合计", "absolute negative {label}"),
    "negative_rows": ("负{label}汇总行数", "rows with negative {label}"),
    "missing_key_rows": ("缺{key}汇总行数", "rows missing {key}"),
    "missing_key_value": ("缺{key}的{label}", "{label} with missing {key}"),
}
GRAIN_ORDER = ["day", "week", "month", "quarter", "year"]


def norm(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


class Builder:
    def __init__(self, profile: dict, config: dict) -> None:
        self.profile = profile
        self.config = config
        self.prefix = config.get("naming", {}).get("prefix", "")
        self.lang = 0 if config.get("naming", {}).get("language", "zh") == "zh" else 1
        self.candidates: list[dict] = []
        self.sides: dict[str, dict] = {}   # comparison side key -> value measure facts

    # -- candidate constructors -------------------------------------------
    def add(self, family: str, name: str, label: str, spec: dict, why: str,
            blocked: bool = False, needs_model: bool = False) -> dict:
        cand = {"family": family, "name": self.prefix + name, "label": label, "why": why, **spec}
        if blocked:
            cand["blocked"] = True
        elif needs_model:
            cand["needs_model"] = True
        self.candidates.append(cand)
        return cand

    @staticmethod
    def simple(sm: str, expr: str, agg: str = "sum", filt: str | None = None) -> dict:
        return {"type": "simple", "semantic_model": sm, "agg": agg, "expr": expr, "filter": filt}

    def time_spec(self, variant: str, base: str) -> dict:
        def inp(name, alias, offset=None):
            return {"name": name, "alias": alias, "offset_window": offset}

        if variant in ("ytd", "qtd", "mtd"):
            grain = {"y": "year", "q": "quarter", "m": "month"}[variant[0]]
            return {"type": "cumulative", "input_metric": base, "grain_to_date": grain}
        if variant == "prior_year":
            return {"type": "derived", "expr": "previous_value", "inputs": [inp(base, "previous_value", "1 year")]}
        if variant == "prior_month":
            return {"type": "derived", "expr": "previous_value", "inputs": [inp(base, "previous_value", "1 month")]}
        if variant == "rolling_3m":
            return {"type": "derived", "expr": "current_value + previous_value + earlier_value", "inputs": [
                inp(base, "current_value"), inp(base, "previous_value", "1 month"), inp(base, "earlier_value", "2 month")]}
        kind, measure = variant.rsplit("_", 1)
        expr = RATE if measure == "rate" else CHANGE
        offset = {"yoy": "1 year", "mom": "1 month", "qoq": "1 quarter"}[kind]
        spec = {"type": "derived", "expr": expr, "inputs": [inp(base, "current_value"), inp(base, "previous_value", offset)]}
        if kind == "qoq":
            spec["time_granularity"] = "quarter"
        return spec

    # -- families -----------------------------------------------------------
    def run(self) -> list[dict]:
        for sm_name, fact in self.config.get("facts", {}).items():
            sm = self.profile["semantic_models"].get(sm_name)
            if sm is None:
                raise SystemExit(f"config fact {sm_name} not in profile")
            scenario = fact.get("scenario")
            variants = (
                [(v, info["key"], info["label"]) for v, info in scenario["values"].items()]
                if scenario else [(None, None, None)]
            )
            scenario_dim = None
            if scenario:
                dim = next(n for n, d in sm["dimensions"].items() if d["column"] == scenario["column"])
                scenario_dim = f"{sm['primary_entity']}__{dim}"
            base_filter = None
            if fact.get("filter"):
                f = fact["filter"]
                base_filter = f"{{{{ Dimension('{sm['primary_entity']}__{f['dimension']}') }}}} = '{f['value']}'"
            attribution = fact.get("role") == "attribution"
            for value, key, slabel in variants:
                parts = [base_filter] if base_filter else []
                if value:
                    parts.append(f"{{{{ Dimension('{scenario_dim}') }}}} = '{value}'")
                filt = " and ".join(parts) or None
                fam = "F9b" if attribution else ("F2" if value else "F1")
                fact_label = slabel or fact.get("label", sm_name)
                check_stem = key or fact.get("key") or fact.get("role", "fact")
                value_measure = None
                for column, m in fact.get("measures", {}).items():
                    fmt = lambda s: s.format(scenario=key, scenario_label=slabel) if s else s  # noqa: E731
                    name, label, stem = fmt(m["name"]), fmt(m["label"]), fmt(m.get("stem") or m["name"])
                    stats = sm["columns"].get(column, {})
                    if stats.get("repeats_per_entity"):
                        self.add(fam, name, label, self.simple(sm_name, column, filt=filt),
                                 f"{column} repeats on each {stats['repeats_per_entity']}; summing it on this table inflates the result",
                                 blocked=True)
                        continue
                    if m.get("kind") == "snapshot":
                        spec = self.simple(sm_name, column, filt=filt)
                        spec["non_additive"] = {"dimension": sm["agg_time_dimension"],
                                                "window_agg": m.get("window_agg", "max"),
                                                "group_by": m.get("group_by", [])}
                        edge = "period-end" if m.get("window_agg", "max") == "max" else "period-start"
                        self.add("F7", name, label, spec,
                                 f"{column} is a snapshot value; take the {edge} value by {sm['agg_time_dimension']}, not summed across time")
                        continue
                    if m.get("role") == "check":
                        self.add("F8", name, label, self.simple(sm_name, column, filt=filt),
                                 f"{column} is a non-primary convention, used for reconciliation only")
                        continue
                    why = f"SUM({column})" + (f", {scenario['column']} = {value}" if value else "")
                    self.add(fam, name, label, self.simple(sm_name, column, filt=filt), why)
                    if m.get("kind") == "value" and value_measure is None:
                        value_measure = (column, label)
                        self.sides[check_stem] = {
                            "sm": sm_name, "column": column, "filter": filt, "label": fact_label,
                            "value_label": label, "value_metric": self.prefix + name,
                            "null_rows": f"{self.prefix}{check_stem}_null_rows",
                            "stem": stem, "time_grain": sm.get("time_grain") or "day",
                        }
                    self.time_family(sm, self.prefix + name, stem, label, m.get("time", "none"))
                    self.check_measure(sm_name, column, stem, label, stats, filt, m.get("kind"), check_stem)
                for column, cnt in (fact.get("counts") or {}).items():
                    expr = cnt.get("expr", column)
                    agg = cnt.get("agg", "count_distinct")
                    stats = sm["columns"].get(column, {})
                    why = (f"{column} distinct count" if agg == "count_distinct" else f"{column} count") + (
                        f" ({stats['distinct']} distinct values / {stats['rows']} rows)" if stats else "")
                    self.add("F3", cnt["name"], cnt["label"], self.simple(sm_name, expr, agg=agg, filt=filt), why)
                self.check_fact(sm_name, sm, fact, filt, check_stem, fact_label, value_measure)
        self.comparison_family()
        self.ratio_family()
        self.conversion_family()
        return self.candidates

    # -- F5 ratios -----------------------------------------------------------
    def ratio_family(self) -> None:
        p = self.prefix
        for r in self.config.get("ratios") or []:
            num, den = p + r["numerator"], p + r["denominator"]
            if not r.get("guards"):
                self.add("F5", r["name"], r["label"], {"type": "ratio", "numerator": num, "denominator": den},
                         f"{num} / {den}; numerator and denominator each aggregate at their own grain")
                continue
            inputs = [{"name": num, "alias": "numerator_value", "offset_window": None},
                      {"name": den, "alias": "denominator_value", "offset_window": None}]
            conds = []
            for g in r["guards"]:
                inputs.append({"name": p + g["metric"], "alias": g["alias"], "offset_window": None})
                conds.append(f"{g['alias']} {g['condition']}")
            conds.append("denominator_value > 0")
            expr = f"case when {' and '.join(conds)} then numerator_value / denominator_value end"
            self.add("F5", r["name"], r["label"], {"type": "derived", "expr": expr, "inputs": inputs},
                     f"{num} / {den}; preconditions: " + ", ".join(f"{g['metric']} {g['condition']}" for g in r["guards"]))

    # -- F9a conversion ------------------------------------------------------
    def conversion_family(self) -> None:
        p = self.prefix
        specs = {c["name"]: c for c in self.candidates}
        for c in self.config.get("conversions") or []:
            # Two different limits, verified against MetricFlow:
            # - a non-count input is rejected by dbt's semantic validation -> blocked;
            # - a filtered input fails to compile, and a filter placed on the
            #   conversion input reference compiles but is silently dropped from
            #   the SQL. The fix is an upstream dbt model that already applies the
            #   filter, so the input becomes an unfiltered count -> needs_model.
            problems, model_needed = [], []
            for side in ("base_metric", "conversion_metric"):
                spec = specs.get(p + c[side])
                if spec is None:
                    continue
                if spec.get("agg") not in ("count", "count_distinct") and spec.get("expr") != "1":
                    problems.append(f"{c[side]} is {spec.get('agg')}; a conversion metric's inputs must be counts (count / count_distinct)")
                elif spec.get("filter"):
                    model_needed.append(
                        f"{c[side]} carries a filter: a conversion metric's input cannot carry a filter. "
                        f"Build a dbt model that already applies the filter (e.g. a view with only COMPLETED "
                        f"orders) and define an unfiltered count on it; do not put the filter on the conversion "
                        f"input - MetricFlow compiles but drops it")
            for window in c.get("windows", [c.get("window")]):
                count, grain = window.split()
                suffix = f"_{count}{grain[0]}"
                calc = c.get("calculation", "conversion_rate")
                zh_grain = {"day": "天", "week": "周", "month": "个月"}.get(grain.rstrip("s"), grain)
                shown = f"{count}{zh_grain}" if self.lang == 0 else window
                self.add("F9a", c["name"] + suffix, c["label"].format(window=shown), {
                    "type": "conversion", "entity": c["entity"], "calculation": calc,
                    "base_metric": p + c["base_metric"], "conversion_metric": p + c["conversion_metric"],
                    "window": window,
                }, ("; ".join(problems + model_needed) or
                    f"matched by {c['entity']}; from {c['base_metric']} to {c['conversion_metric']} within {window}; window to be confirmed by the user"),
                    blocked=bool(problems), needs_model=bool(model_needed))

    # -- F6 cross-fact comparison --------------------------------------------
    def comparison_family(self) -> None:
        cfg = self.config.get("comparisons")
        if not cfg:
            return
        cell = cfg["comparable_entity"]
        p = self.prefix
        matched: dict[tuple[str, str], str] = {}

        def metric_ref(name: str) -> str:
            return f"{{{{ Metric('{name}', group_by=['{cell}']) }}}}"

        def matched_amount(own: str, other: str) -> str:
            key = (own, other)
            if key in matched:
                return matched[key]
            a, b = self.sides[own], self.sides[other]
            clauses = [a["filter"]] if a["filter"] else []
            clauses += [f"{metric_ref(b['value_metric'])} is not null",
                        f"{metric_ref(a['null_rows'])} = 0", f"{metric_ref(b['null_rows'])} = 0"]
            name = f"{own}_value_with_{other}"
            label = f"{a['value_label']} in {b['label']}-comparable cells"
            self.add("F6", name, label, self.simple(a["sm"], a["column"], filt=" and ".join(clauses)),
                     f"keep only cells on {cell} where both sides have complete amounts")
            matched[key] = p + name
            return p + name

        actual_key = next((k for k, s in self.sides.items() if self.config["facts"][s["sm"]].get("role") == "actual"), None)
        prefixes = {"": "月度" if self.lang == 0 else "monthly ", "qtd": "季度累计" if self.lang == 0 else "QTD ", "ytd": "年度累计" if self.lang == 0 else "YTD "}
        for pair in cfg.get("pairs", []):
            subj, ref = pair["subject"], pair["reference"]
            if subj not in self.sides or ref not in self.sides:
                raise SystemExit(f"comparison side not found: {subj} / {ref}")
            base = {"": (matched_amount(subj, ref), matched_amount(ref, subj))}
            for td in ("qtd", "ytd"):
                pair_names = []
                for own, other in ((subj, ref), (ref, subj)):
                    src = matched[(own, other)]
                    name = f"{src.removeprefix(p)}_{td}"
                    if p + name not in {c["name"] for c in self.candidates}:
                        label = f"{prefixes[td]}{self.sides[other]['label']}-comparable {self.sides[own]['value_label']}"
                        grain = {"qtd": "quarter", "ytd": "year"}[td]
                        self.add("F6", name, label, {"type": "cumulative", "input_metric": src, "grain_to_date": grain},
                                 f"{src} {td.upper()} cumulative: match month by month first, then accumulate")
                    pair_names.append(p + name)
                base[td] = tuple(pair_names)

            s_lab, r_lab = self.sides[subj]["label"], self.sides[ref]["label"]
            vs_actual = subj == actual_key
            sv, rv = f"{subj}_value", f"{ref}_value"
            outputs = {
                "achievement": (f"{ref}_achievement" if vs_actual else f"{subj}_{ref}_achievement",
                                f"{r_lab} achievement rate" if vs_actual else f"{s_lab} vs {r_lab} achievement rate",
                                f"case when {rv} > 0 then {sv} / {rv} end"),
                "rate": (f"{subj}_{ref}_rate", f"{s_lab} to {r_lab} ratio", f"case when {rv} > 0 then {sv} / {rv} end"),
                "variance": (f"{ref}_variance" if vs_actual else f"{subj}_{ref}_variance",
                             f"{r_lab} variance" if vs_actual else f"{s_lab} vs {r_lab} variance", f"{sv} - {rv}"),
                "gap": (f"{ref}_gap", f"{r_lab} net gap",
                        f"case when {sv} is not null and {rv} is not null then greatest({rv} - {sv}, 0) end"),
            }
            for td, (s_metric, r_metric) in base.items():
                suffix = f"_{td}" if td else ""
                inputs = [{"name": s_metric, "alias": sv, "offset_window": None},
                          {"name": r_metric, "alias": rv, "offset_window": None}]
                for out in pair.get("outputs", []):
                    if out not in outputs:
                        continue
                    name, label, expr = outputs[out]
                    self.add("F6", name + suffix, f"{prefixes[td]}comparable {label}",
                             {"type": "derived", "expr": expr, "inputs": inputs},
                             "both sides use comparable-cell amounts" + ("; returns null when the denominator is not greater than 0" if "/" in expr else ""))
            self.trend_flags(pair, subj, ref, vs_actual, prefixes, outputs)

    def trend_flags(self, pair, subj, ref, vs_actual, prefixes, outputs) -> None:
        """Flags built on top of achievement: growing-but-below and N months below."""
        if "achievement" not in pair.get("outputs", []) or not vs_actual:
            return
        p = self.prefix
        r_lab = self.sides[ref]["label"]
        ach = p + outputs["achievement"][0]
        stem = self.sides[subj]["stem"]
        names = {c["name"] for c in self.candidates}
        if "growing_below" in pair.get("flags", []):
            for td in ("", "qtd", "ytd"):
                suffix = f"_{td}" if td else ""
                growth = f"{p}{stem}{suffix}_yoy_change"
                if growth not in names:
                    continue
                expr = ("case when target_rate is null or growth_change is null then null "
                        "when target_rate < 1 and growth_change > 0 then 1 else 0 end")
                self.add("F6", f"growing_but_below_{ref}{suffix}", f"{prefixes[td]}growing but below {r_lab} flag",
                         {"type": "derived", "expr": expr, "inputs": [
                             {"name": ach + suffix, "alias": "target_rate", "offset_window": None},
                             {"name": growth, "alias": "growth_change", "offset_window": None}]},
                         f"YoY change > 0 and {r_lab} achievement rate < 1")
        months = pair.get("consecutive_below_months")
        if months and self.sides[subj]["time_grain"] in ("day", "week", "month"):
            aliases = ["current_value", "previous_value", "earlier_value"] + [f"value_{i}" for i in range(3, months)]
            aliases = aliases[:months]
            expr = ("case when " + " or ".join(f"{a} is null" for a in aliases) + " then null when "
                    + " and ".join(f"{a} < 1" for a in aliases) + " then 1 else 0 end")
            inputs = [{"name": ach, "alias": a, "offset_window": f"{i} month" if i else None} for i, a in enumerate(aliases)]
            label_n = str(months)
            self.add("F6", f"{'three' if months == 3 else months}_months_below_{ref}", f"below {r_lab} for {label_n} consecutive months flag",
                     {"type": "derived", "expr": expr, "inputs": inputs}, f"the current month and the prior {months - 1} months all have {r_lab} achievement rate < 1")

    def time_family(self, sm: dict, base: str, stem: str, label: str, mode: str) -> None:
        if mode == "none":
            return
        grain = sm.get("time_grain") or "day"
        g = GRAIN_ORDER.index(grain)
        lab = lambda v: TIME_LABELS[v][self.lang].format(label=label)  # noqa: E731
        to_date = [v for v, need in (("mtd", "day"), ("qtd", "month"), ("ytd", "month")) if g <= GRAIN_ORDER.index(need)]
        for v in to_date:
            self.add("F4", f"{stem}_{v}", lab(v), self.time_spec(v, base), f"{base} {v.upper()} cumulative")
        if mode != "full":
            return
        for v in ("prior_year", "yoy_change", "yoy_rate", "prior_month", "mom_change", "mom_rate",
                  "qoq_change", "qoq_rate", "rolling_3m"):
            if v in ("prior_month", "mom_change", "mom_rate", "rolling_3m") and g > GRAIN_ORDER.index("month"):
                continue
            self.add("F4", f"{stem}_{v}", lab(v), self.time_spec(v, base), f"{base} {v}")
        for td in ("ytd", "qtd"):
            if f"{td}" not in to_date:
                continue
            cumulative = self.prefix + f"{stem}_{td}"
            for kind, expr in (("change", CHANGE), ("rate", RATE)):
                v = f"{td}_yoy_{kind}"
                spec = {"type": "derived", "expr": expr, "inputs": [
                    {"name": cumulative, "alias": "current_value", "offset_window": None},
                    {"name": cumulative, "alias": "previous_value", "offset_window": "1 year"}]}
                self.add("F4", f"{stem}_{v}", lab(v), spec, f"{cumulative} vs prior-year same period")

    def check_measure(self, sm_name, column, stem, label, stats, filt, kind, check_stem) -> None:
        lab = lambda k, **kw: CHECK_LABELS[k][self.lang].format(label=label, **kw)  # noqa: E731
        null_name = f"{check_stem}_null_rows" if kind == "value" else f"{stem}_null_rows"
        self.add("F8", null_name, lab("null_rows"),
                 self.simple(sm_name, f"case when {column} is null then 1 else 0 end", filt=filt),
                 f"when {column} may be null, the ratio needs this guard")
        if stats.get("negatives"):
            why = f"the data has {stats['negatives']} rows with {column} < 0"
            self.add("F8", f"negative_{stem}_value", lab("negative_value"),
                     self.simple(sm_name, f"case when {column} < 0 then -{column} else 0 end", filt=filt), why)
            self.add("F8", f"negative_{stem}_rows", lab("negative_rows"),
                     self.simple(sm_name, f"case when {column} < 0 then 1 else 0 end", filt=filt), why)

    def check_fact(self, sm_name, sm, fact, filt, check_stem, fact_label, value_measure) -> None:
        if fact.get("grain", "aggregate") == "aggregate":
            self.add("F8", f"{check_stem}_rows", CHECK_LABELS["rows"][self.lang].format(fact=fact_label),
                     self.simple(sm_name, "1", filt=filt), "observe data scale; not to be used as a document count")
        comparable = {(self.config.get("comparisons") or {}).get("comparable_entity")}
        name_stem = "" if fact.get("role") == "actual" else f"{check_stem}_"
        for entity, info in sm["entities"].items():
            column = info["column"]
            stats = sm["columns"].get(column, {})
            if info["type"] == "primary" or entity in comparable or stats.get("not_null_test"):
                continue
            key = entity.removeprefix(self.prefix)
            why = f"{column} has no not_null constraint; " + (
                f"the data has {stats['nulls']} null rows" if stats.get("nulls") else "no nulls in the sample data for now"
            )
            missing = f"nullif({column}, '') is null"
            self.add("F8", f"{name_stem}missing_{key}_rows",
                     CHECK_LABELS["missing_key_rows"][self.lang].format(key=key),
                     self.simple(sm_name, f"case when {missing} then 1 else 0 end", filt=filt), why)
            if value_measure:
                vcol, vlabel = value_measure
                self.add("F8", f"{name_stem}missing_{key}_value",
                         CHECK_LABELS["missing_key_value"][self.lang].format(key=key, label=vlabel),
                         self.simple(sm_name, f"case when {missing} then {vcol} else 0 end", filt=filt), why)


# -- definition signatures -----------------------------------------------------
def manifest_spec(m: dict) -> dict:
    tp = m["type_params"]
    filt = " and ".join(f["where_sql_template"] for f in ((m.get("filter") or {}).get("where_filters") or []))
    if m["type"] == "simple":
        agg = tp["metric_aggregation_params"]
        spec = {"type": "simple", "semantic_model": agg["semantic_model"], "agg": agg["agg"],
                "expr": tp.get("expr"), "filter": filt or None}
        nad = agg.get("non_additive_dimension")
        if nad:
            spec["non_additive"] = {"dimension": nad["name"], "window_agg": nad["window_choice"],
                                    "group_by": list(nad.get("window_groupings") or [])}
        return spec
    if m["type"] == "ratio":
        return {"type": "ratio", "numerator": tp["numerator"]["name"], "denominator": tp["denominator"]["name"]}
    if m["type"] == "conversion":
        c = tp["conversion_type_params"]
        w = c.get("window")
        return {"type": "conversion", "entity": c["entity"], "calculation": c["calculation"],
                "base_metric": c["base_metric"]["name"], "conversion_metric": c["conversion_metric"]["name"],
                "window": f"{w['count']} {w['granularity']}" if w else None}
    if m["type"] == "cumulative":
        c = tp["cumulative_type_params"]
        return {"type": "cumulative", "input_metric": c["metric"]["name"], "grain_to_date": c.get("grain_to_date"),
                "window": c.get("window")}
    if m["type"] == "derived":
        inputs = []
        for i in tp["metrics"]:
            ow = i.get("offset_window")
            inputs.append({"name": i["name"], "alias": i.get("alias") or i["name"],
                           "offset_window": f"{ow['count']} {ow['granularity']}" if ow else None})
        return {"type": "derived", "expr": tp["expr"], "inputs": inputs, "time_granularity": m.get("time_granularity")}
    return {"type": m["type"], "raw": tp}


def signature(spec: dict, lookup: dict, offset: str | None = None) -> str:
    t = spec["type"]
    if t == "simple":
        clauses = sorted(norm(c) for c in re.split(r"\s+and\s+", spec.get("filter") or "") if c.strip())
        sig = f"simple({spec['semantic_model']},{spec['agg']},{norm(spec['expr'])},{' & '.join(clauses)})"
        if spec.get("non_additive"):
            n = spec["non_additive"]
            sig += f"|na({n['dimension']},{n['window_agg']},{sorted(n['group_by'])})"
    elif t == "cumulative":
        base = lookup.get(spec["input_metric"])
        inner = signature(base, lookup) if base else f"?{spec['input_metric']}"
        sig = f"cum({spec.get('grain_to_date')},{spec.get('window')},{inner})"
    elif t == "derived":
        inputs = spec["inputs"]
        if len(inputs) == 1 and norm(spec["expr"]) == norm(inputs[0]["alias"]):
            base = lookup.get(inputs[0]["name"])
            inner = signature(base, lookup) if base else f"?{inputs[0]['name']}"
            return f"{inner}@{inputs[0]['offset_window']}" + (f"@{offset}" if offset else "")
        expr = norm(spec["expr"])
        for i in sorted(inputs, key=lambda x: -len(x["alias"])):
            base = lookup.get(i["name"])
            inner = signature(base, lookup) if base else f"?{i['name']}"
            if i.get("offset_window"):
                inner = f"{inner}@{i['offset_window']}"
            expr = re.sub(rf"\b{re.escape(i['alias'].lower())}\b", f"<{inner}>", expr)
        sig = f"derived({spec.get('time_granularity')},{expr})"
    elif t == "ratio":
        parts = []
        for side in ("numerator", "denominator"):
            base = lookup.get(spec[side])
            parts.append(signature(base, lookup) if base else f"?{spec[side]}")
        sig = f"ratio({parts[0]} / {parts[1]})"
    elif t == "conversion":
        parts = []
        for side in ("base_metric", "conversion_metric"):
            base = lookup.get(spec[side])
            parts.append(signature(base, lookup) if base else f"?{spec[side]}")
        window = re.sub(r"s$", "", spec.get("window") or "")
        sig = f"conv({spec['entity']},{spec['calculation']},{window},{parts[0]} -> {parts[1]})"
    else:
        sig = f"{t}:{json.dumps(spec, sort_keys=True)}"
    return sig + (f"@{offset}" if offset else "")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--out-dir")
    args = parser.parse_args()
    profile = json.loads(Path(args.profile).read_text())
    config = yaml.safe_load(Path(args.config).read_text())
    manifest = json.loads(Path(profile["manifest"]).read_text())

    candidates = Builder(profile, config).run()
    existing = {m["name"]: manifest_spec(m) for m in manifest["metrics"]}
    labels = {m["name"]: m.get("label") for m in manifest["metrics"]}
    cand_specs = {c["name"]: c for c in candidates}
    # Candidates may reference each other (time variants) or existing metrics.
    lookup = {**existing, **cand_specs}
    existing_sigs: dict[str, str] = {}
    for name, spec in existing.items():
        existing_sigs.setdefault(signature(spec, existing), name)

    matched: set[str] = set()
    conflicted: set[str] = set()
    for c in candidates:
        sig = signature(c, lookup)
        c["signature"] = sig
        hit = existing_sigs.get(sig)
        if c.get("blocked"):
            c["status"] = "blocked"
        elif c.get("needs_model"):
            c["status"] = "needs_model"
        elif hit:
            c["status"] = "existing"
            c["existing_name"] = hit
            matched.add(hit)
        elif c["name"] in existing:
            c["status"] = "conflict"
            c["existing_name"] = c["name"]
            c["existing_signature"] = signature(existing[c["name"]], existing)
            conflicted.add(c["name"])
        else:
            c["status"] = "new"
    uncovered = [n for n in existing if n not in matched and n not in conflicted]
    configured = {(sm, col) for sm, fact in config.get("facts", {}).items()
                  for col in list((fact.get("measures") or {})) + list((fact.get("counts") or {}))}
    unconfigured = [
        {"semantic_model": sm, "column": col, "repeats_per_entity": st.get("repeats_per_entity")}
        for sm, info in profile["semantic_models"].items() if sm in config.get("facts", {})
        for col, st in info["columns"].items()
        if st.get("proposed_role") == "measure" and (sm, col) not in configured
    ]

    out_dir = Path(args.out_dir or Path(args.profile).parent)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "candidates.json").write_text(json.dumps(
        {"candidates": candidates, "uncovered_existing": uncovered, "unconfigured_measures": unconfigured},
        ensure_ascii=False, indent=2))
    (out_dir / "review.md").write_text(review(candidates, uncovered, labels, len(existing), unconfigured))
    print(f"candidates: {len(candidates)}  existing matched: {len(matched)}/{len(existing)}  "
          f"conflict: {len(conflicted)}  uncovered: {len(uncovered)}  -> {out_dir}/review.md")
    return 0


FAMILY_NAMES = {"F1": "additive measures", "F2": "scenario split", "F3": "counts", "F4": "time", "F5": "ratio",
                "F6": "cross-fact comparison", "F7": "semi-additive", "F8": "reconciliation", "F9a": "conversion", "F9b": "attribution"}
ICON = {"existing": "✅ existing", "new": "🆕 new", "conflict": "⚠️ convention mismatch", "blocked": "⛔ cannot do", "needs_model": "🧱 needs a model first"}


def review(candidates: list[dict], uncovered: list[str], labels: dict, total: int, unconfigured: list[dict]) -> str:
    lines = ["# Candidate metrics confirm table", "", "## Summary", "",
             "| Metric family | Existing | New | Convention mismatch | Needs a model first | Cannot do |", "| --- | --- | --- | --- | --- | --- |"]
    for fam, fname in FAMILY_NAMES.items():
        rows = [c for c in candidates if c["family"] == fam]
        if rows:
            count = lambda s: sum(1 for c in rows if c["status"] == s)  # noqa: E731
            n = {s: count(s) for s in ("existing", "new", "conflict", "needs_model", "blocked")}
            lines.append(f"| {fam} {fname} | {n['existing']} | {n['new']} | {n['conflict']} | {n['needs_model']} | {n['blocked']} |")
    lines += ["", f"{total} existing metrics, {total - len(uncovered)} covered by candidates (including convention mismatch).", ""]
    for fam, fname in FAMILY_NAMES.items():
        rows = [c for c in candidates if c["family"] == fam]
        if not rows:
            continue
        lines += [f"## {fam} {fname}", "", "| No. | Status | Metric | Label | Basis |", "| --- | --- | --- | --- | --- |"]
        for i, c in enumerate(rows, 1):
            name = c["name"]
            if c["status"] == "existing" and c["existing_name"] != c["name"]:
                name = f"{c['name']} (current name {c['existing_name']})"
            why = c["why"]
            if c["status"] == "conflict":
                why += f"<br>derived signature: `{c['signature']}`<br>existing signature: `{c['existing_signature']}`"
            lines.append(f"| {fam}-{i:02d} | {ICON[c['status']]} | `{name}` | {c['label']} | {why} |")
        lines.append("")
    lines += ["## Existing but not covered by candidates", "", "These belong to a not-yet-implemented family, or a rule is missing.", ""]
    lines += [f"- `{n}` {labels.get(n) or ''}" for n in uncovered] or ["(none)"]
    lines += ["", "## Measure columns in the profile but absent from the config", "", "Confirm whether to include them; a column marked duplicated cannot be summed on this table.", ""]
    lines += [
        f"- `{u['semantic_model']}.{u['column']}`" + (f" ⛔ repeats on each {u['repeats_per_entity']}" if u["repeats_per_entity"] else "")
        for u in unconfigured
    ] or ["(none)"]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.exit(main())
