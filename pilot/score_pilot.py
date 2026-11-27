#!/usr/bin/env python3
"""Pilot scorer v2 — grades raw_outputs.jsonl against the frozen items.

Revised after Codex's review of freeze 0231af9 (board ids 27 + 29):

1. STRICT score for R/P: the ENTIRE raw output must parse as exactly one
   JSON object. First-object extraction survives only as a separate
   diagnostic column, never the headline. Clarify and no_op shapes are
   validated (clarify via the engine's validate_output, which requires
   the question and list-of-strings candidates; no_op must be exactly
   {"type": "no_op"}).
2. E is split: citation accuracy (mechanical) and factual correctness
   (BLIND rubric grading — see e_rubric.md) are reported separately.
   Keyword coverage is a diagnostic only: it cannot distinguish an
   answer from its negation, so it never counts as correctness.
3. Integrity: exactly one raw record per frozen (item, arm) is asserted
   before scoring; duplicates or missing records abort with exit 2.
   R is reported by input view, and R/P paired outcomes (both views
   correct / one / neither) are reported per arm.

Blind flow: --make-blind-sheet writes results/blind_e_sheet.jsonl
(arm labels stripped, deterministic shuffle) + results/blind_key.jsonl
(sealed mapping). A grader fills results/e_blind_grades.jsonl with
{blind_id, factual_correct}; the next scoring run merges factual grades
via the key. Until then E factual is reported as pending.
"""

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)
from src.plan.apply import PlanError, apply_plan  # noqa: E402
from src.plan.diff import diff_states  # noqa: E402
from src.plan.schema import validate_output  # noqa: E402

ARMS = ("A", "B")


def load_jsonl(path):
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def extract_json(text):
    """First balanced {...} object in the text, or None. DIAGNOSTIC only."""
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


def parse_strict(text):
    """The whole output as one JSON value, or None."""
    try:
        return json.loads(text.strip())
    except ValueError:
        return None


def norm_facts(obj):
    if isinstance(obj, dict):
        return {str(k): norm_facts(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return sorted(obj, key=lambda x: json.dumps(x))
    return obj


def grade_read_obj(item, obj):
    if not isinstance(obj, dict):
        return False, "unparseable"
    if norm_facts(obj) == norm_facts(item["expected"]["facts"]):
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


def grade_plan_obj(item, obj):
    """Grade one parsed output object against a P item."""
    if not isinstance(obj, dict):
        return False, "unparseable"
    expected = item["expected"]
    state = item["plan_state"]
    if obj.get("type") == "no_op":
        if set(obj.keys()) != {"type"}:
            return False, "shape_error"
        if expected["kind"] == "no_op":
            return True, "ok"
        return False, "wrong_kind"
    # Engine validation covers plan, multi-step, and clarify shapes —
    # including the clarify question and candidate types.
    errors = validate_output(obj, state)
    if errors:
        return False, "validation_error"
    if expected["kind"] == "clarify":
        if obj.get("type") != "clarify":
            return False, "wrong_kind"
        want = expected["clarify"]
        if obj.get("reason") != want["reason"]:
            return False, "clarify_mismatch"
        if sorted(obj.get("candidates") or []) != sorted(want["candidates"]):
            return False, "clarify_mismatch"
        return True, "ok"
    if obj.get("type") == "clarify":
        return False, "wrong_kind"
    try:
        after, _effects = apply_plan(json.loads(json.dumps(state)), obj)
    except (PlanError, KeyError, TypeError):
        return False, "validation_error"
    if expected["kind"] == "no_op":
        if not diff_states(state, after):
            return True, "ok"
        return False, "wrong_kind"
    if not _assertions_hold(after, expected.get("assertions") or []):
        return False, "assertion_failed"
    asserted = {a["path"] for a in expected.get("assertions") or []}
    changed = set(diff_states(state, after))
    if not changed.issubset(asserted):
        return False, "preservation_violation"
    return True, "ok"


def grade_explain(item, raw):
    """Returns (citation_correct, citation_class, keyword_diag)."""
    expected = item["expected"]
    m = re.findall(r"citation:\s*(.+)", raw, flags=re.IGNORECASE)
    if not m:
        return False, "citation_missing", False
    norm = lambda s: re.sub(r"\s+", " ", s.strip().lower()).rstrip(".")
    citation_ok = norm(m[-1]) == norm(expected["citation"])
    text = raw.lower()
    keyword_diag = all(kp.lower() in text for kp in expected["key_points"])
    return citation_ok, ("ok" if citation_ok else "citation_mismatch"), \
        keyword_diag


def check_integrity(items, raws):
    """Exactly one raw record per frozen (item, arm). Returns error list."""
    errors = []
    counts = Counter((r.get("item_id"), r.get("arm")) for r in raws)
    for item in items:
        for arm in ARMS:
            n = counts.get((item["id"], arm), 0)
            if n == 0:
                errors.append(f"missing record: {item['id']} arm {arm}")
            elif n > 1:
                errors.append(f"duplicate record: {item['id']} arm {arm} x{n}")
    known = {(i["id"], a) for i in items for a in ARMS}
    for key in counts:
        if key not in known:
            errors.append(f"unknown record: {key}")
    return errors


def make_blind_sheet(items, raws, out_dir):
    by_id = {i["id"]: i for i in items}
    entries = []
    for rec in raws:
        item = by_id[rec["item_id"]]
        if item["task"] != "E":
            continue
        blind_id = "B" + hashlib.sha256(
            (rec["item_id"] + rec["arm"] + rec["raw_output"]).encode()
        ).hexdigest()[:10]
        entries.append((blind_id, item, rec))
    entries.sort(key=lambda e: hashlib.sha256(
        ("sheet" + e[0]).encode()).hexdigest())
    with open(os.path.join(out_dir, "blind_e_sheet.jsonl"), "w",
              encoding="utf-8") as fh:
        for blind_id, item, rec in entries:
            fh.write(json.dumps({"blind_id": blind_id,
                                 "question": item["request"],
                                 "raw_output": rec["raw_output"]},
                                sort_keys=True) + "\n")
    with open(os.path.join(out_dir, "blind_key.jsonl"), "w",
              encoding="utf-8") as fh:
        for blind_id, item, rec in entries:
            fh.write(json.dumps({"blind_id": blind_id,
                                 "item_id": item["id"], "arm": rec["arm"]},
                                sort_keys=True) + "\n")
    print(f"blind sheet: {len(entries)} E outputs")


def load_factual(out_dir):
    """Merge blind factual grades via the sealed key. {(item, arm): bool}"""
    key_path = os.path.join(out_dir, "blind_key.jsonl")
    grades_path = os.path.join(out_dir, "e_blind_grades.jsonl")
    if not (os.path.exists(key_path) and os.path.exists(grades_path)):
        return {}
    key = {r["blind_id"]: (r["item_id"], r["arm"])
           for r in load_jsonl(key_path)}
    factual = {}
    for g in load_jsonl(grades_path):
        if g["blind_id"] in key:
            factual[key[g["blind_id"]]] = bool(g["factual_correct"])
    return factual


def pair_outcomes(records, task):
    """Per arm: counts of pairs where both views / one / neither correct."""
    pairs = {}
    for r in records:
        if r["task"] != task:
            continue
        pairs.setdefault((r["arm"], r["pair_key"]), []).append(r["correct"])
    out = {}
    for arm in ARMS:
        sel = [v for (a, _), v in pairs.items() if a == arm]
        out[arm] = (sum(1 for v in sel if all(v)),
                    sum(1 for v in sel if any(v) and not all(v)),
                    sum(1 for v in sel if not any(v)), len(sel))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--items", default=os.path.join(HERE, "items.jsonl"))
    ap.add_argument("--raw", default=os.path.join(HERE, "results",
                                                  "raw_outputs.jsonl"))
    ap.add_argument("--out-dir", default=os.path.join(HERE, "results"))
    ap.add_argument("--make-blind-sheet", action="store_true")
    args = ap.parse_args()

    items = load_jsonl(args.items)
    by_id = {i["id"]: i for i in items}
    raws = load_jsonl(args.raw)

    errors = check_integrity(items, raws)
    if errors:
        for e in errors:
            print("INTEGRITY:", e)
        return 2

    os.makedirs(args.out_dir, exist_ok=True)
    if args.make_blind_sheet:
        make_blind_sheet(items, raws, args.out_dir)
    factual = load_factual(args.out_dir)

    records = []
    for rec in raws:
        item = by_id[rec["item_id"]]
        raw = rec["raw_output"]
        entry = {"item_id": item["id"], "task": item["task"],
                 "view": item.get("view"), "arm": rec["arm"],
                 "pair_key": item["source"] + "|" + item["request"]}
        if item["task"] in ("R", "P"):
            grader = grade_read_obj if item["task"] == "R" else grade_plan_obj
            strict_obj = parse_strict(raw)
            if strict_obj is None:
                entry["correct"], entry["failure_class"] = \
                    False, "not_single_json"
            else:
                entry["correct"], entry["failure_class"] = \
                    grader(item, strict_obj)
            diag_obj = extract_json(raw)
            entry["diag_correct"] = bool(
                diag_obj is not None and grader(item, diag_obj)[0])
        elif item["task"] == "E":
            cit_ok, cit_class, kw = grade_explain(item, raw)
            entry["citation_correct"] = cit_ok
            entry["citation_class"] = cit_class
            entry["keyword_diag"] = kw
            entry["factual_correct"] = factual.get(
                (item["id"], rec["arm"]))
            entry["correct"] = entry["factual_correct"]
        records.append(entry)

    with open(os.path.join(args.out_dir, "scores.json"), "w",
              encoding="utf-8") as fh:
        json.dump(records, fh, indent=2)

    lines = ["# Pilot results (strict scoring; see RUBRIC.md)", ""]
    for task in ("R", "P"):
        lines += [f"## {task} — strict, by view", "",
                  "| Arm | View | Correct | Items | Accuracy |",
                  "|---|---|---|---|---|"]
        for arm in ARMS:
            for view in ("cli", "json"):
                sel = [r for r in records if r["task"] == task
                       and r["arm"] == arm and r["view"] == view]
                c = sum(1 for r in sel if r["correct"])
                lines.append(f"| {arm} | {view} | {c} | {len(sel)} | "
                             f"{c / len(sel):.2f} |")
        po = pair_outcomes(records, task)
        lines += ["", f"Paired outcomes ({task}):"]
        for arm in ARMS:
            b, o, n, total = po[arm]
            lines.append(f"- arm {arm}: both views correct {b}, one {o}, "
                         f"neither {n} (of {total} pairs)")
        diag = [r for r in records if r["task"] == task]
        for arm in ARMS:
            sel = [r for r in diag if r["arm"] == arm]
            c = sum(1 for r in sel if r["diag_correct"])
            lines.append(f"- arm {arm} extraction diagnostic (not the "
                         f"score): {c}/{len(sel)}")
        lines.append("")
    lines += ["## E — citation (mechanical) and factual (blind rubric)", "",
              "| Arm | Citation correct | Items | Keyword diagnostic "
              "(not a score) | Factual correct |",
              "|---|---|---|---|---|"]
    for arm in ARMS:
        sel = [r for r in records if r["task"] == "E" and r["arm"] == arm]
        cit = sum(1 for r in sel if r["citation_correct"])
        kw = sum(1 for r in sel if r["keyword_diag"])
        graded = [r for r in sel if r["factual_correct"] is not None]
        fact = (f"{sum(1 for r in graded if r['factual_correct'])}/"
                f"{len(graded)}" if graded else "pending blind grading")
        lines.append(f"| {arm} | {cit} | {len(sel)} | {kw}/{len(sel)} | "
                     f"{fact} |")
    lines += ["", "Failure classes (strict, R/P):"]
    for cls, n in sorted(Counter(r["failure_class"] for r in records
                                 if r["task"] in ("R", "P")
                                 and not r["correct"]).items()):
        lines.append(f"- {cls}: {n}")
    with open(os.path.join(args.out_dir, "RESULTS.md"), "w",
              encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"scored {len(records)} outputs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
