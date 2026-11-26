#!/usr/bin/env python3
"""Grade src/plan's apply() against the HAND-AUTHORED golden diffs.

The expected after-states in data/plan_slice/golden_diffs.jsonl are
literals written by hand from docs/PLAN_CONTRACT.md — apply() is
graded against them, never the source of them. Also checks that the
schema validator accepts each golden plan and that every changed path
is inside the plan's authorized set (primary paths + documented
cascades).

Usage: .venv/bin/python scripts/test_golden_diffs.py
Exit code 0 iff all cases pass.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.plan.apply import apply_plan, authorized_paths, PlanError
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
                changed = set(diff_states(before, case["after"]))
                authorized = authorized_paths(before, case["after"], plan)
                if not changed <= authorized:
                    problems.append(
                        f"unauthorized paths changed: {sorted(changed - authorized)}")
        ok = not problems
        per_op.setdefault(op, []).append(ok)
        print(f"{'PASS' if ok else 'FAIL'}  {case['id']:<34} ({op})")
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
