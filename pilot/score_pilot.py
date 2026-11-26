#!/usr/bin/env python3
"""Pilot scorer — grades raw_outputs.jsonl against the frozen items.

Mechanical wherever a grader exists (docs: pilot/RUBRIC.md):
  R: extracted JSON facts must deep-equal the expected facts.
  P: validate_output() + apply_plan() + golden assertions + preservation
     via diff_states() (src/plan — the same engine that grades goldens).
  E: citation path equality (normalized) + every key point present.
Writes results/scores.json and results/RESULTS.md (the frozen report
format). Raw outputs are never modified.
"""

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)
from src.plan.apply import PlanError, apply_plan  # noqa: E402
from src.plan.diff import diff_states  # noqa: E402
from src.plan.schema import validate_output  # noqa: E402


def load_jsonl(path):
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def extract_json(text):
    """First balanced {...} object in the text, or None."""
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except ValueError:
                        break
        start = text.find("{", start + 1)
    return None


def norm_facts(obj):
    """Normalize fact objects: vlan-id keys to str, lists sorted where the
    domain treats them as sets (trunk_allowed)."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            out[str(k)] = norm_facts(v)
        return out
    if isinstance(obj, list):
        return sorted(obj, key=lambda x: json.dumps(x))
    return obj


def grade_read(item, raw):
    got = extract_json(raw)
    if got is None:
        return False, "unparseable"
    expected = item["expected"]["facts"]
    if norm_facts(got) == norm_facts(expected):
        return True, "ok"
    return False, "facts_mismatch"


def _assertions_hold(state, assertions):
    for a in assertions:
        node = state
        for part in a["path"].split("."):
            if not isinstance(node, dict) or part not in node:
                return False
            node = node[part]
        if node != a["equals"]:
            return False
    return True


def grade_plan(item, raw):
    got = extract_json(raw)
    if got is None:
        return False, "unparseable"
    expected = item["expected"]
    state = item["plan_state"]
    if expected["kind"] == "no_op":
        if got.get("type") == "no_op":
            return True, "ok"
        if "operation" in got or "steps" in got:
            try:
                after, _ = apply_plan(json.loads(json.dumps(state)), got)
            except (PlanError, KeyError, TypeError):
                return False, "validation_error"
            if not diff_states(state, after):
                return True, "ok"
        return False, "wrong_kind"
    if expected["kind"] == "clarify":
        if got.get("type") != "clarify":
            return False, "wrong_kind"
        want = expected["clarify"]
        if got.get("reason") != want["reason"]:
            return False, "clarify_mismatch"
        if sorted(got.get("candidates") or []) != sorted(want["candidates"]):
            return False, "clarify_mismatch"
        return True, "ok"
    # expected kind == plan: the extracted object IS the plan output
    if got.get("type") in ("clarify", "no_op") or (
            "operation" not in got and "steps" not in got):
        return False, "wrong_kind"
    errors = validate_output(got, state)
    if errors:
        return False, "validation_error"
    try:
        after, _effects = apply_plan(json.loads(json.dumps(state)), got)
    except (PlanError, KeyError, TypeError):
        return False, "validation_error"
    if not _assertions_hold(after, expected.get("assertions") or []):
        return False, "assertion_failed"
    asserted = {a["path"] for a in expected.get("assertions") or []}
    changed = set(diff_states(state, after))
    if not changed.issubset(asserted):
        return False, "preservation_violation"
    return True, "ok"


def grade_explain(item, raw):
    expected = item["expected"]
    m = re.findall(r"citation:\s*(.+)", raw, flags=re.IGNORECASE)
    if not m:
        return False, "citation_missing"
    norm = lambda s: re.sub(r"\s+", " ", s.strip().lower()).rstrip(".")
    if norm(m[-1]) != norm(expected["citation"]):
        return False, "citation_mismatch"
    text = raw.lower()
    for kp in expected["key_points"]:
        if kp.lower() not in text:
            return False, "keypoint_missing"
    return True, "ok"


GRADERS = {"R": grade_read, "P": grade_plan, "E": grade_explain}


def main():
    items = {i["id"]: i for i in load_jsonl(os.path.join(HERE, "items.jsonl"))}
    raws = load_jsonl(os.path.join(HERE, "results", "raw_outputs.jsonl"))
    records = []
    for rec in raws:
        item = items[rec["item_id"]]
        ok, cls = GRADERS[item["task"]](item, rec["raw_output"])
        records.append({"item_id": item["id"], "task": item["task"],
                        "view": item.get("view"), "arm": rec["arm"],
                        "correct": ok, "failure_class": cls})
    out_dir = os.path.join(HERE, "results")
    with open(os.path.join(out_dir, "scores.json"), "w",
              encoding="utf-8") as fh:
        json.dump(records, fh, indent=2)

    lines = ["# Pilot results", "",
             "| Task | Arm | View | Correct | Items | Accuracy |",
             "|---|---|---|---|---|---|"]
    keys = sorted({(r["task"], r["arm"], r["view"] if r["task"] == "P"
                    else "-") for r in records})
    for task, arm, view in keys:
        sel = [r for r in records if r["task"] == task and r["arm"] == arm
               and (task != "P" or r["view"] == view)]
        c = sum(1 for r in sel if r["correct"])
        lines.append(f"| {task} | {arm} | {view} | {c} | {len(sel)} | "
                     f"{c / len(sel):.2f} |")
    lines += ["", "Failure classes:"]
    from collections import Counter
    for cls, n in sorted(Counter(r["failure_class"] for r in records
                                 if not r["correct"]).items()):
        lines.append(f"- {cls}: {n}")
    with open(os.path.join(out_dir, "RESULTS.md"), "w",
              encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"scored {len(records)} outputs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
