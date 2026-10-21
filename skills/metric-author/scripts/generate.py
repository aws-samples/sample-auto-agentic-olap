"""Write confirmed candidate metrics into the project's semantic-layer YAML.

Usage:
    python generate.py --profile profile.json --candidates candidates.json \
        --confirmed confirmed.json --config metric_scenario.yml [--dry-run]

confirmed.json:
    {"metrics": ["pharma_x", ...],              # names from the review table
     "labels": {"pharma_x": "..."},              # optional label overrides
     "descriptions": {"pharma_x": "..."}}        # optional

Only candidates with status "new" are written; anything else is refused.
New blocks are inserted as text after the target list; existing lines are never rewritten.
Run with: uv run --with pyyaml --with ruamel.yaml python generate.py ...
"""

from __future__ import annotations

import argparse
import io
import textwrap
import json
import sys
from pathlib import Path

import yaml
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.scalarstring import FoldedScalarString

RT = YAML()
RT.preserve_quotes = True
RT.width = 4096
RT.indent(mapping=2, sequence=4, offset=2)


def offset(text: str | None) -> str | None:
    if not text:
        return None
    count, grain = text.split()
    return f"{count} {grain}s" if int(count) > 1 and not grain.endswith("s") else text


def to_yaml(c: dict, label: str, description: str | None) -> CommentedMap:
    m = CommentedMap()
    m["name"] = c["name"]
    if description:
        m["description"] = description
    m["label"] = label
    m["type"] = c["type"]
    if c["type"] == "simple":
        m["agg"] = c["agg"]
        m["expr"] = c["expr"]
        if c.get("filter"):
            filt = c["filter"]
            m["filter"] = FoldedScalarString(filt.replace(" and ", "\nand ")) if len(filt) > 100 else filt
        if c.get("non_additive"):
            n = c["non_additive"]
            nad = CommentedMap(name=n["dimension"], window_agg=n["window_agg"])
            if n.get("group_by"):
                nad["group_by"] = list(n["group_by"])
            m["non_additive_dimension"] = nad
    elif c["type"] == "ratio":
        m["numerator"] = c["numerator"]
        m["denominator"] = c["denominator"]
    elif c["type"] == "conversion":
        m["entity"] = c["entity"]
        m["calculation"] = c["calculation"]
        m["base_metric"] = c["base_metric"]
        m["conversion_metric"] = c["conversion_metric"]
        m["window"] = c["window"]
    elif c["type"] == "cumulative":
        m["input_metric"] = c["input_metric"]
        m["grain_to_date"] = c["grain_to_date"]
        m["period_agg"] = "last"
    elif c["type"] == "derived":
        if c.get("time_granularity"):
            m["time_granularity"] = c["time_granularity"]
        m["expr"] = c["expr"]
        inputs = CommentedSeq()
        for i in c["inputs"]:
            item = CommentedMap(name=i["name"], alias=i["alias"])
            if i.get("offset_window"):
                item["offset_window"] = offset(i["offset_window"])
            inputs.append(item)
        m["input_metrics"] = inputs
    else:
        raise SystemExit(f"{c['name']}: metric type {c['type']} is not generated yet")
    return m


def target_file(c: dict, profile: dict, config: dict, project: Path) -> tuple[Path, str | None]:
    """(file, model name) — model name is set when the metric goes under models[].metrics."""
    targets = config.get("targets", {})
    if c["type"] == "simple" and targets.get("simple", "model_yaml") == "model_yaml":
        sm = profile["semantic_models"][c["semantic_model"]]
        return project / sm["yaml_file"], sm["model"]
    key = {"F6": "comparison", "F5": "ratio", "F9a": "conversion"}.get(c["family"], "time")
    if c["type"] == "simple":
        key = "simple"
    path = targets.get(key) or targets.get("time")
    if not path:
        raise SystemExit(f"config targets.{key} is not set")
    return project / path, None


def dump_item(metric: CommentedMap, dash_col: int) -> list[str]:
    """Render one sequence item as text lines, dash at ``dash_col``."""
    buf = io.StringIO()
    RT.dump(CommentedSeq([metric]), buf)
    body = textwrap.dedent(buf.getvalue())
    return [" " * dash_col + line if line.strip() else line for line in body.splitlines()]


def block_end(lines: list[str], start: int, dash_col: int) -> int:
    """Index just past the last content line of the item starting at ``start``."""
    last = start
    for i in range(start + 1, len(lines)):
        text = lines[i]
        if not text.strip() or text.lstrip().startswith("#"):
            continue
        if len(text) - len(text.lstrip()) <= dash_col:
            break
        last = i
    return last + 1


def insert_metric(text: str, model: str | None, metric: CommentedMap) -> str:
    """Insert one metric into YAML text, leaving every existing line untouched.

    ruamel is used only to locate the target list; the file is never re-dumped,
    so folded descriptions and quoting styles survive.
    """
    doc = RT.load(text) if text.strip() else None
    lines = text.splitlines()
    if doc is None:
        return "version: 2\n\nmetrics:\n" + "\n".join(dump_item(metric, 2)) + "\n"
    if model:
        models = doc.get("models") or []
        idx = next((i for i, m in enumerate(models) if m["name"] == model), None)
        if idx is None:
            raise SystemExit(f"model {model} not found")
        owner, owner_line, owner_col = models[idx], models.lc.data[idx][0], models.lc.data[idx][1]
    else:
        owner, owner_line, owner_col = doc, 0, 0
    seq = owner.get("metrics")
    if seq:
        last_line, item_col = seq.lc.data[len(seq) - 1]
        dash_col = item_col - 2
        at = block_end(lines, last_line, dash_col)
        new = dump_item(metric, dash_col)
    else:
        key_col = owner_col
        at = block_end(lines, owner_line, owner_col - 2) if model else len(lines)
        new = [" " * key_col + "metrics:"] + dump_item(metric, key_col + 2)
    lines[at:at] = new
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument("--candidates", required=True)
    parser.add_argument("--confirmed", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    profile = json.loads(Path(args.profile).read_text())
    project = Path(profile["project"])
    config = yaml.safe_load(Path(args.config).read_text())
    by_name = {c["name"]: c for c in json.loads(Path(args.candidates).read_text())["candidates"]}
    confirmed = json.loads(Path(args.confirmed).read_text())

    plan: dict[Path, list[tuple[str | None, CommentedMap]]] = {}
    for name in confirmed["metrics"]:
        c = by_name.get(name)
        if c is None:
            raise SystemExit(f"{name}: not in candidates.json (re-run derive.py?)")
        if c["status"] != "new":
            raise SystemExit(f"{name}: status is {c['status']}, only new metrics are written")
        label = confirmed.get("labels", {}).get(name, c["label"])
        path, model = target_file(c, profile, config, project)
        plan.setdefault(path, []).append((model, to_yaml(c, label, confirmed.get("descriptions", {}).get(name))))

    for path, items in plan.items():
        text = path.read_text() if path.exists() else ""
        for model, metric in items:
            text = insert_metric(text, model, metric)
        rel = path.relative_to(project) if path.is_relative_to(project) else path
        print(f"{rel}: +{len(items)} ({', '.join(m['name'] for _, m in items)})")
        if not args.dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
