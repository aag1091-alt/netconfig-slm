#!/usr/bin/env python3
"""Rejection tests through the PUBLIC entry points (2026-11-26 fixes).

Supervisor finding: validate_output() rejected plans that apply_plan()
accepted (wrong outer device on a multi-step plan, empty steps),
malformed values raised TypeError out of validation instead of
returning errors, and render_delta() happily emitted CLI for
remove-from-"all", which the contract refuses.

The rule under test: apply_plan() and render_delta() run the SAME
complete validation first; validation is total (errors, never
exceptions); an invalid plan produces NEITHER a candidate state NOR
any CLI. render_config() gates on validate_state.

Usage: .venv/bin/python scripts/test_entry_points.py
Exit code 0 iff all checks pass.
"""
import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.plan.apply import apply_plan, PlanError
from src.plan.render import render_config, render_delta
from src.plan.schema import validate_output

STATE = {
    "device": "sw-acc-1", "hostname": "switch1",
    "vlans": {"10": {"name": "Users"}, "20": {"name": "Engineering"},
              "30": {"name": "Voice"}},
    "interfaces": {
        "GigabitEthernet1/0/48": {
            "mode": "trunk", "access_vlan": 1, "trunk_allowed": "all",
            "native_vlan": 1, "admin": "up", "description": "Uplink to core",
            "aliases": ["uplink"]},
        "GigabitEthernet1/0/47": {
            "mode": "trunk", "access_vlan": 1, "trunk_allowed": [10, 30],
            "native_vlan": 10, "admin": "up", "description": "",
            "aliases": []},
        "GigabitEthernet1/0/1": {
            "mode": "access", "access_vlan": 10, "trunk_allowed": "all",
            "native_vlan": 1, "admin": "up", "description": "",
            "aliases": []},
    },
}

RESULTS = []


def report(name, problems):
    RESULTS.append(not problems)
    print(f"{'PASS' if not problems else 'FAIL'}  {name}")
    for p in problems:
        print(f"      -> {p}")


def expect_reject(name, plan, check_validate=True):
    """All entry points reject; validation returns errors, never raises."""
    problems = []
    if check_validate:
        try:
            errs = validate_output(plan, STATE)
            if not errs:
                problems.append("validate_output returned no errors")
        except Exception as e:  # noqa: BLE001 - totality is the point
            problems.append(f"validate_output raised {type(e).__name__}: {e}")
    try:
        apply_plan(STATE, plan)
        problems.append("apply_plan ACCEPTED the plan")
    except PlanError:
        pass
    except Exception as e:  # noqa: BLE001
        problems.append(f"apply_plan raised {type(e).__name__}, not PlanError")
    try:
        lines = render_delta(plan, STATE)
        problems.append(f"render_delta emitted CLI: {lines}")
    except PlanError:
        pass
    except Exception as e:  # noqa: BLE001
        problems.append(f"render_delta raised {type(e).__name__}, not PlanError")
    report(name, problems)


def test_device_binding():
    expect_reject(
        "wrong outer device, single-step",
        {"device": "sw-WRONG", "operation": "create_vlan", "vlan_id": 40})
    expect_reject(
        "wrong outer device, multi-step (steps carry correct device)",
        {"device": "sw-WRONG", "steps": [
            {"device": "sw-acc-1", "operation": "create_vlan",
             "vlan_id": 40}]})
    expect_reject(
        "wrong device on step 2 (outer correct)",
        {"device": "sw-acc-1", "steps": [
            {"device": "sw-acc-1", "operation": "create_vlan",
             "vlan_id": 40},
            {"device": "sw-WRONG", "operation": "set_access_vlan",
             "interface": "GigabitEthernet1/0/1", "vlan_id": 40}]})
    expect_reject(
        "missing device entirely",
        {"operation": "create_vlan", "vlan_id": 40})


def test_steps_shape():
    expect_reject("empty steps", {"device": "sw-acc-1", "steps": []})
    expect_reject("steps not a list", {"device": "sw-acc-1", "steps": "x"})
    expect_reject("non-dict step", {"device": "sw-acc-1", "steps": [42]})
    expect_reject("None step", {"device": "sw-acc-1", "steps": [None]})
    expect_reject("step without operation",
                  {"device": "sw-acc-1", "steps": [{"device": "sw-acc-1"}]})


def test_late_invalid_step():
    """STANDALONE REGRESSION (kept separate from the golden set):
    a valid first step followed by an invalid second step must reject
    the WHOLE plan — no candidate state, no CLI preview, and the
    caller's state object untouched."""
    name = ("STANDALONE: valid step 1 + invalid step 2 -> "
            "no candidate state, no CLI")
    problems = []
    state = copy.deepcopy(STATE)
    plan = {"device": "sw-acc-1", "steps": [
        {"device": "sw-acc-1", "operation": "create_vlan", "vlan_id": 40,
         "name": "IoT"},
        {"device": "sw-acc-1", "operation": "set_access_vlan",
         "interface": "GigabitEthernet1/0/1", "vlan_id": 99}]}  # no VLAN 99
    errs = validate_output(plan, state)
    if not any("steps[1]" in e for e in errs):
        problems.append(f"validate_output did not blame steps[1]: {errs}")
    try:
        apply_plan(state, plan)
        problems.append("apply_plan ACCEPTED (would yield a partial state)")
    except PlanError:
        pass
    try:
        lines = render_delta(plan, state)
        problems.append(f"render_delta emitted a CLI preview: {lines}")
    except PlanError:
        pass
    if state != STATE:
        problems.append("input state object was mutated by the rejected plan")
    report(name, problems)


def test_remove_from_all():
    plan = {"device": "sw-acc-1", "operation": "remove_trunk_vlans",
            "interface": "GigabitEthernet1/0/48", "vlans": [20]}
    # Schema alone cannot see this refusal (it is an apply-time rule),
    # so only apply/render rejection is asserted here.
    expect_reject("remove-from-'all', single-step (apply + render refuse)",
                  plan, check_validate=False)
    expect_reject(
        "remove-from-'all' as step 2 of a multi-step plan",
        {"device": "sw-acc-1", "steps": [
            {"device": "sw-acc-1", "operation": "create_vlan",
             "vlan_id": 40},
            plan]})


def test_fuzz_malformed():
    base = {"device": "sw-acc-1", "operation": "add_trunk_vlans",
            "interface": "GigabitEthernet1/0/47", "vlans": [20]}
    cases = {
        "vlans as string": {**base, "vlans": "20"},
        "vlans with string member": {**base, "vlans": [20, "30"]},
        "vlans with unhashable member": {**base, "vlans": [[20]]},
        "vlans with None member": {**base, "vlans": [20, None]},
        "vlans empty": {**base, "vlans": []},
        "vlans duplicated": {**base, "vlans": [20, 20]},
        "vlan_id as string": {"device": "sw-acc-1",
                              "operation": "create_vlan", "vlan_id": "20"},
        "vlan_id None": {"device": "sw-acc-1", "operation": "create_vlan",
                         "vlan_id": None},
        "vlan_id out of range": {"device": "sw-acc-1",
                                 "operation": "create_vlan",
                                 "vlan_id": 5000},
        "vlan_id reserved": {"device": "sw-acc-1",
                             "operation": "create_vlan", "vlan_id": 1002},
        "interface None": {**base, "interface": None},
        "interface numeric": {**base, "interface": 42},
        "interface as list": {**base, "interface": ["GigabitEthernet1/0/47"]},
        "interface unknown": {**base, "interface": "GigabitEthernet9/9/9"},
        "operation unhashable": {"device": "sw-acc-1",
                                 "operation": ["create_vlan"],
                                 "vlan_id": 40},
        "operation None": {"device": "sw-acc-1", "operation": None},
        "operation unknown": {"device": "sw-acc-1", "operation": "explode"},
        "mode as list": {"device": "sw-acc-1", "operation": "set_port_mode",
                         "interface": "GigabitEthernet1/0/1",
                         "mode": ["access"]},
        "admin None": {"device": "sw-acc-1", "operation": "set_admin_state",
                       "interface": "GigabitEthernet1/0/1", "admin": None},
        "create name numeric": {"device": "sw-acc-1",
                                "operation": "create_vlan", "vlan_id": 40,
                                "name": 42},
        "plan is a list": [1, 2],
        "plan is a string": "add vlan 20",
        "plan is None": None,
        "plan is a number": 42,
        "clarify reason unhashable": {
            "type": "clarify", "reason": ["ambiguous_alias"],
            "question": "Which one?", "candidates": []},
        "clarify candidates not a list": {
            "type": "clarify", "reason": "ambiguous_alias",
            "question": "Which one?", "candidates": "uplink"},
        "clarify question empty": {
            "type": "clarify", "reason": "ambiguous_alias",
            "question": "", "candidates": []},
    }
    for label, bad in cases.items():
        expect_reject(f"fuzz: {label}", bad)


def test_positive_controls():
    problems = []
    plan = {"device": "sw-acc-1", "operation": "add_trunk_vlans",
            "interface": "GigabitEthernet1/0/47", "vlans": [20]}
    if validate_output(plan, STATE):
        problems.append("valid plan failed validation")
    got, _ = apply_plan(STATE, plan)
    if got["interfaces"]["GigabitEthernet1/0/47"]["trunk_allowed"] != [10, 20, 30]:
        problems.append("valid plan produced the wrong state")
    lines = render_delta(plan, STATE)
    if not any("allowed vlan add 20" in l for l in lines):
        problems.append(f"valid plan rendered unexpected CLI: {lines}")
    cfg = render_config(STATE)
    if not cfg.startswith("hostname switch1"):
        problems.append("render_config output does not start with hostname")
    # the new clarify reason is a valid model output
    clarify = {"type": "clarify", "reason": "unsupported_remove_from_all",
               "question": "What should the allowed list be?",
               "candidates": []}
    if validate_output(clarify, STATE):
        problems.append("unsupported_remove_from_all clarify failed validation")
    report("positive controls (valid plan / config / new clarify reason)",
           problems)


def test_render_config_gate():
    problems = []
    for label, bad_state in {
        "state not an object": [1, 2],
        "hostname missing": {"device": "sw-acc-1", "vlans": {},
                             "interfaces": {}},
        "interface mode garbage": {
            "device": "sw-acc-1", "hostname": "switch1", "vlans": {},
            "interfaces": {"GigabitEthernet1/0/1": {
                "mode": "routed", "access_vlan": 10,
                "trunk_allowed": "all", "native_vlan": 1, "admin": "up"}}},
        "trunk_allowed garbage": {
            "device": "sw-acc-1", "hostname": "switch1", "vlans": {},
            "interfaces": {"GigabitEthernet1/0/1": {
                "mode": "trunk", "access_vlan": 1,
                "trunk_allowed": "some", "native_vlan": 1, "admin": "up"}}},
    }.items():
        try:
            render_config(bad_state)
            problems.append(f"{label}: render_config ACCEPTED")
        except PlanError:
            pass
        except Exception as e:  # noqa: BLE001
            problems.append(f"{label}: raised {type(e).__name__}, not PlanError")
    report("render_config gates malformed states", problems)


def main():
    test_device_binding()
    test_steps_shape()
    test_late_invalid_step()
    test_remove_from_all()
    test_fuzz_malformed()
    test_positive_controls()
    test_render_config_gate()
    print(f"\n{sum(RESULTS)}/{len(RESULTS)} entry-point checks pass")
    return 0 if all(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
