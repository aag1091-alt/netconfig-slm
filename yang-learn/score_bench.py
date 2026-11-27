#!/usr/bin/env python3
"""Mechanical grading of yang-learn bench v1 generations.

Implements SCALE_DESIGN.md §3 + amendment A2 — mechanical only,
no human or model grading:

- path_meaning: the verbatim type expression from fact.answer
  appears in the generation (plus the default, where the fact
  carries one). When-records (fact.answer carries an xpath
  instead of a type) are graded on the xpath appearing verbatim.
- instance_validity: the generation's leading verdict (Valid /
  Invalid) matches the record's validated intended verdict.
  Secondary stat: for invalid items, whether the generation names
  the expected node's leaf.
- state_reconstruction: the first complete JSON object in the
  generation deep-equals the record's after-state (its parsed
  completion).
- typed_edit: positives — the extracted JSON's "edits" array
  deep-equals fact.edits. Selector-negatives — the extracted
  JSON's "error" equals fact.error.
- revised_schema: leading Yes/No matches fact.answer.verdict
  AND the exact citation fact.answer.rule appears verbatim.

Usage: score_bench.py GENERATIONS_JSONL [OUT_JSON]
Reference records are read from corpus/benchmark.jsonl and
held_out.jsonl next to this script.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def extract_json(text):
    """First balanced {...} object in text, parsed; None if no
    complete object. String-aware brace scan."""
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def lead_verdict(gen):
    g = gen.lstrip()
    if g.startswith("Valid"):
        return "valid"
    if g.startswith("Invalid"):
        return "invalid"
    if g.startswith("Yes"):
        return "Yes"
    if g.startswith("No"):
        return "No"
    return None


def completion_lead(text):
    return lead_verdict(text)


def grade_pm(rec, gen):
    fact = rec["fact"]
    ans = fact.get("answer", {})
    if "type" in ans:
        ok = ans["type"] in gen
        if ok and ans.get("default") is not None:
            ok = str(ans["default"]) in gen
        return ok
    if "xpath" in ans:
        return ans["xpath"] in gen
    # hand-authored formats (held_out)
    if completion_lead(rec["completion"]) in ("Yes", "No"):
        return lead_verdict(gen) == completion_lead(rec["completion"])
    if "when" in fact:
        return fact["when"] in gen
    if "wrapper" in fact and "config" in fact:
        return fact["wrapper"] in gen and fact["config"] in gen
    return False


def grade_iv(rec, gen):
    fact = rec["fact"]
    if "cases" in fact:  # hand-authored multi-case (HO-01/02)
        all_ok = True
        for label, verdict in fact["cases"].items():
            idx = gen.find(str(label))
            if idx < 0:
                all_ok = False
                continue
            window = gen[idx:idx + 160].lower()
            said_invalid = ("invalid" in window
                            or "not a valid" in window
                            or "not valid" in window)
            said_valid = (not said_invalid) and "valid" in window
            said = ("invalid" if said_invalid
                    else "valid" if said_valid else None)
            if said != verdict:
                all_ok = False
        return all_ok, None
    intended = fact.get("intended_verdict") or completion_lead(
        rec["completion"])
    ok = lead_verdict(gen) == intended
    node_hit = None
    if intended == "invalid":
        node = fact.get("expected_node") or ""
        leaf = node.rstrip("/").split("/")[-1]
        node_hit = bool(leaf) and leaf.lower() in gen.lower()
    return ok, node_hit


def grade_sr(rec, gen):
    got = extract_json(gen)
    if got is None:
        return False
    try:
        want = json.loads(rec["completion"])
    except json.JSONDecodeError:
        return False
    return got == want


def grade_te(rec, gen):
    got = extract_json(gen)
    if got is None:
        return False
    if rec["fact"].get("expected") == "reject_target":
        return got.get("error") == rec["fact"].get("error")
    if rec["fact"].get("edits") is not None:
        return got.get("edits") == rec["fact"].get("edits")
    # hand-authored full-state form (HO-06): whole-object compare
    want = extract_json(rec["completion"])
    return want is not None and got == want


def grade_rs(rec, gen):
    ans = rec["fact"].get("answer", {})
    if ans.get("rule"):
        want = "Yes" if ans.get("verdict") == "allowed" else "No"
        return lead_verdict(gen) == want and ans["rule"] in gen
    # hand-authored form (HO-07/08): leading verdict + profile named
    want = completion_lead(rec["completion"])
    profile = (rec["fact"].get("profile") or "").split()[0]
    return lead_verdict(gen) == want and profile in gen


GRADERS = {"path_meaning": lambda r, g: (grade_pm(r, g), None),
           "instance_validity": grade_iv,
           "state_reconstruction": lambda r, g: (grade_sr(r, g), None),
           "typed_edit": lambda r, g: (grade_te(r, g), None),
           "revised_schema": lambda r, g: (grade_rs(r, g), None)}


def main() -> int:
    gens_path = Path(sys.argv[1])
    refs = {}
    for name in ("corpus/benchmark.jsonl", "held_out.jsonl"):
        for line in (HERE / name).read_text().splitlines():
            r = json.loads(line)
            refs[r["id"]] = (r, "held_out" if name.startswith("held")
                             else "benchmark")
    rows = []
    for line in gens_path.read_text().splitlines():
        g = json.loads(line)
        rec, split = refs[g["id"]]
        ok, node_hit = GRADERS[rec["family"]](rec, g["generated"])
        rows.append({"id": g["id"], "family": rec["family"],
                     "split": split, "pass": ok,
                     "node_hit": node_hit,
                     "stopped_cleanly": g["stopped_cleanly"]})

    def agg(rs):
        n = len(rs)
        p = sum(r["pass"] for r in rs)
        return {"n": n, "pass": p,
                "pct": round(100 * p / n, 1) if n else None}

    out = {"overall": agg(rows),
           "benchmark_only": agg([r for r in rows
                                  if r["split"] == "benchmark"]),
           "held_out_only": agg([r for r in rows
                                 if r["split"] == "held_out"]),
           "by_family": {}}
    for fam in sorted({r["family"] for r in rows}):
        out["by_family"][fam] = agg([r for r in rows
                                     if r["family"] == fam])
    iv_bad = [r for r in rows if r["family"] == "instance_validity"
              and r["node_hit"] is not None]
    out["iv_invalid_node_hit"] = {
        "n": len(iv_bad),
        "hit": sum(1 for r in iv_bad if r["node_hit"])}
    for label, rs in (("stopped", [r for r in rows
                                   if r["stopped_cleanly"]]),
                      ("not_stopped", [r for r in rows
                                       if not r["stopped_cleanly"]])):
        out[f"pass_when_{label}"] = agg(rs)
    out["rows"] = rows
    print(json.dumps({k: v for k, v in out.items() if k != "rows"},
                     indent=1))
    if len(sys.argv) > 2:
        Path(sys.argv[2]).write_text(json.dumps(out, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
