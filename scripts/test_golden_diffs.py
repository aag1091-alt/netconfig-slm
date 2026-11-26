#!/usr/bin/env python3
"""Grade src/plan's apply() against the HAND-AUTHORED golden diffs.

The expected after-states in data/plan_slice/golden_diffs.jsonl are
literals written by hand from docs/PLAN_CONTRACT.md — apply() is
graded against them, never the source of them. Also checks that the
schema validator accepts each golden plan and that every changed path
is authorized. Authorization is derived INDEPENDENTLY of apply() by
src/plan/cascades.py (operation + before-state only): a step's actual
changed paths must lie in primary paths ∪ permitted cascades. (The
previous logic — "effects = whatever apply() changed" — authorized
apply()'s own output and was removed in the 2026-11-26 review fixes.)

Usage: .venv/bin/python scripts/test_golden_diffs.py
Exit code 0 iff all cases pass.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.plan.apply import apply_plan, PlanError
from src.plan.cascades import permitted_cascade_paths, primary_paths
from src.plan.diff import diff_states
from src.plan.schema import validate_output

GOLDEN = Path(__file__).resolve().parent.parent / "data/plan_slice/golden_diffs.jsonl"


def main():
    cases = [json.loads(l) for l in open(GOLDEN) if l.strip()]
    failures = 0
    per_op = {}
    for case in cases:
        op = case["operation"]
        before, plan = case["before"], case["plan"]
        problems = []
        errors = validate_output(plan, before)
        if errors:
            problems.append(f"schema rejected golden plan: {errors}")
        if case.get("expected_error"):
            try:
                apply_plan(before, plan)
                problems.append("expected PlanError, apply() succeeded")
            except PlanError:
                pass
        else:
            try:
                got, _ = apply_plan(before, plan)
            except PlanError as e:
                problems.append(f"apply() raised PlanError: {e}")
                got = None
            if got is not None:
                if got != case["after"]:
                    gd = diff_states(case["after"], got)
                    problems.append(
                        f"after-state mismatch at {gd}: "
                        f"expected {[(p, case['after']) for p in gd][:1]}")
                # Independent authorization walk: per step, the actual
                # changed paths must be inside primary ∪ permitted
                # cascades, both derived from (operation, before-state)
                # by cascades.py — never from apply()'s own diff.
                steps = plan["steps"] if "steps" in plan else [plan]
                cur = before
                for si, step in enumerate(steps):
                    nxt, _ = apply_plan(cur, step)
                    changed_step = set(diff_states(cur, nxt))
                    allowed_step = (primary_paths(step)
                                    | permitted_cascade_paths(step, cur))
                    if not changed_step <= allowed_step:
                        problems.append(
                            f"step {si}: unauthorized paths changed: "
                            f"{sorted(changed_step - allowed_step)}")
                    cur = nxt
                if len(steps) == 1:
                    authored = set(diff_states(before, case["after"]))
                    allowed = (primary_paths(plan)
                               | permitted_cascade_paths(plan, before))
                    if not authored <= allowed:
                        problems.append(
                            "hand-authored diff has unauthorized paths: "
                            f"{sorted(authored - allowed)}")
                if case.get("expected_changed_interfaces") is not None:
                    want = set(case["expected_changed_interfaces"])
                    for label, other in (("hand-authored", case["after"]),
                                         ("produced", got)):
                        touched = {p.split(".")[1]
                                   for p in diff_states(before, other)
                                   if p.startswith("interfaces.")}
                        if touched != want:
                            problems.append(
                                f"{label} diff touches interfaces "
                                f"{sorted(touched)}, expected exactly "
                                f"{sorted(want)}")
        ok = not problems
        per_op.setdefault(op, []).append(ok)
        tag = ("  [lab verification pending]"
               if case.get("lab_verification") == "pending" else "")
        print(f"{'PASS' if ok else 'FAIL'}  {case['id']:<34} ({op}){tag}")
        for p in problems:
            print(f"      -> {p}")
        failures += 0 if ok else 1
    print("\nPer-operation:")
    for op, results in sorted(per_op.items()):
        print(f"  {op:<24} {sum(results)}/{len(results)}")
    print(f"\n{len(cases) - failures}/{len(cases)} golden cases pass")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
