#!/usr/bin/env python3
"""Renderer lab check: independently interpret render()'s delta CLI.

render.py is graded, not trusted. This script contains a *separate*
mini-interpreter for the slice's delta-CLI subset: it reads only the
rendered CLI text (plus the golden before-state), computes the state
the CLI describes, and compares it to the HAND-AUTHORED golden
after-state. No code is shared with render.py or apply.py; the only
shared tool is diff (a measurement, not a transformation).

A second lab check with Batfish on the rendered full config is
planned but requires the Mac-hosted Batfish server (offline at the
time of writing) — later addition.

Usage: .venv/bin/python scripts/cli_lab_check.py
Exit code 0 iff every golden case's CLI reproduces the golden diff.
"""
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.plan.diff import diff_states
from src.plan.render import render_delta

GOLDEN = Path(__file__).resolve().parent.parent / "data/plan_slice/golden_diffs.jsonl"


class LabError(Exception):
    pass


def _ids(text):
    return [int(x) for x in text.split(",") if x.strip()]


def interpret(before, cli_lines):
    """Independently compute the state a delta-CLI describes."""
    s = copy.deepcopy(before)
    cur_iface = None
    cur_vlan = None
    for raw in cli_lines:
        line = raw.rstrip("\n")
        stripped = line.strip()
        if not stripped or stripped.startswith("!"):
            continue
        if line.startswith("vlan "):
            cur_vlan = int(line.split()[1])
            s["vlans"].setdefault(str(cur_vlan), {"name": None})
            cur_iface = None
        elif line.startswith("no vlan "):
            vid = int(line.split()[2])
            s["vlans"].pop(str(vid), None)
            for iface in s["interfaces"].values():
                if iface.get("access_vlan") == vid:
                    iface["access_vlan"] = None
                allowed = iface.get("trunk_allowed")
                if isinstance(allowed, list) and vid in allowed:
                    iface["trunk_allowed"] = [v for v in allowed if v != vid]
            cur_vlan = None
        elif line.startswith(" name ") and cur_vlan is not None:
            s["vlans"][str(cur_vlan)]["name"] = line.split(" name ", 1)[1]
        elif line.startswith("interface "):
            cur_iface = line.split(" ", 1)[1]
            cur_vlan = None
            if cur_iface not in s["interfaces"]:
                raise LabError(f"CLI touches unknown interface {cur_iface}")
        elif cur_iface is not None and line.startswith(" switchport mode "):
            s["interfaces"][cur_iface]["mode"] = line.split()[-1]
        elif cur_iface is not None and " switchport access vlan " in line:
            s["interfaces"][cur_iface]["access_vlan"] = int(line.split()[-1])
        elif cur_iface is not None and " switchport trunk native vlan " in line:
            s["interfaces"][cur_iface]["native_vlan"] = int(line.split()[-1])
        elif cur_iface is not None and " switchport trunk allowed vlan " in line:
            iface = s["interfaces"][cur_iface]
            rest = line.split("allowed vlan ", 1)[1].strip()
            if rest.startswith("add "):
                if iface["trunk_allowed"] != "all":
                    iface["trunk_allowed"] = sorted(
                        set(iface["trunk_allowed"]) | set(_ids(rest[4:])))
            elif rest.startswith("remove "):
                if iface["trunk_allowed"] == "all":
                    raise LabError("cannot remove from allowed vlan all")
                drop = set(_ids(rest[7:]))
                iface["trunk_allowed"] = [
                    v for v in iface["trunk_allowed"] if v not in drop]
            elif rest == "all":
                iface["trunk_allowed"] = "all"
            elif rest == "none":
                iface["trunk_allowed"] = []
            else:
                iface["trunk_allowed"] = sorted(set(_ids(rest)))
        elif cur_iface is not None and stripped == "shutdown":
            s["interfaces"][cur_iface]["admin"] = "down"
        elif cur_iface is not None and stripped == "no shutdown":
            s["interfaces"][cur_iface]["admin"] = "up"
        else:
            raise LabError(f"unrecognized CLI line: {raw!r}")
    return s


def main():
    cases = [json.loads(l) for l in open(GOLDEN) if l.strip()]
    failures = 0
    for case in cases:
        before, plan = case["before"], case["plan"]
        cli = render_delta(plan, before)
        problems = []
        if case.get("expected_error"):
            try:
                interpret(before, cli)
                problems.append("interpreter accepted CLI that should error")
            except LabError:
                pass
        else:
            try:
                got = interpret(before, cli)
            except LabError as e:
                problems.append(f"interpreter raised: {e}")
                got = None
            if got is not None and got != case["after"]:
                problems.append(
                    f"CLI-derived state != golden after-state; "
                    f"diff at {diff_states(case['after'], got)}")
        ok = not problems
        print(f"{'PASS' if ok else 'FAIL'}  {case['id']:<34} "
              f"({len(cli)} CLI lines)")
        for p in problems:
            print(f"      -> {p}")
        failures += 0 if ok else 1
    print(f"\n{len(cases) - failures}/{len(cases)} golden cases reproduce "
          "their golden diff from rendered CLI alone")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
