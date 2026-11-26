"""Plan schema validation for the VLAN/trunk slice.

A model output is either a *plan* or a *clarify* object:

  plan:    {"device": str, "operation": <enum>, ...typed fields}
           or a multi-step plan {"device": str, "steps": [plan, ...]}
  clarify: {"type": "clarify", "reason": <enum>, "question": str,
            "candidates": [str, ...]}        # candidates may be empty

validate_output(output, state) returns a list of error strings; empty
means valid. Validation is deliberately strict about anything the
deterministic compiler would have to guess: unknown interfaces, VLAN
ids outside 1-4094, the reserved 1002-1005 range, and (for
set_access_vlan) reference to a VLAN that does not exist in S — the
compiler never invents state the operator did not ask for.
"""
from .state import VLAN_MIN, VLAN_MAX, RESERVED_VLANS

OPERATIONS = {
    "create_vlan", "delete_vlan", "set_access_vlan", "add_trunk_vlans",
    "remove_trunk_vlans", "replace_trunk_vlans", "set_native_vlan",
    "set_port_mode", "set_admin_state",
}
CLARIFY_REASONS = {
    "ambiguous_alias", "unknown_interface", "unknown_vlan",
    "missing_vlan_id", "missing_interface",
}
_IFACE_OPS = {
    "set_access_vlan", "add_trunk_vlans", "remove_trunk_vlans",
    "replace_trunk_vlans", "set_native_vlan", "set_port_mode",
    "set_admin_state",
}
_VLAN_LIST_OPS = {"add_trunk_vlans", "remove_trunk_vlans", "replace_trunk_vlans"}


def _check_vlan_id(v, errors, field="vlan_id"):
    if not isinstance(v, int) or isinstance(v, bool):
        errors.append(f"{field}: must be an integer, got {type(v).__name__}")
    elif not (VLAN_MIN <= v <= VLAN_MAX):
        errors.append(f"{field}: {v} outside {VLAN_MIN}-{VLAN_MAX}")
    elif v in RESERVED_VLANS:
        errors.append(f"{field}: {v} is reserved (1002-1005) and unusable")


def _check_vlan_list(vs, errors):
    if not isinstance(vs, list) or not vs:
        errors.append("vlans: must be a non-empty list of VLAN ids")
        return
    for v in vs:
        _check_vlan_id(v, errors, field="vlans[]")
    if len(set(vs)) != len(vs):
        errors.append("vlans: duplicate ids")


def validate_plan(plan, state):
    """Validate one single-operation plan against state S."""
    errors = []
    if not isinstance(plan, dict):
        return ["plan: must be an object"]
    if plan.get("device") != state["device"]:
        errors.append(
            f"device: plan targets {plan.get('device')!r}, "
            f"context device is {state['device']!r}")
    op = plan.get("operation")
    if op not in OPERATIONS:
        errors.append(f"operation: {op!r} not in {sorted(OPERATIONS)}")
        return errors
    if op in _IFACE_OPS:
        iface = plan.get("interface")
        if not isinstance(iface, str) or not iface:
            errors.append("interface: required string")
        elif iface not in state["interfaces"]:
            errors.append(f"interface: {iface!r} not in device inventory")
    if op == "create_vlan":
        _check_vlan_id(plan.get("vlan_id"), errors)
        name = plan.get("name")
        if name is not None and (not isinstance(name, str) or not name.strip()):
            errors.append("name: must be a non-empty string when present")
    elif op == "delete_vlan":
        _check_vlan_id(plan.get("vlan_id"), errors)
    elif op == "set_access_vlan":
        _check_vlan_id(plan.get("vlan_id"), errors)
        vid = plan.get("vlan_id")
        if isinstance(vid, int) and str(vid) not in state["vlans"]:
            errors.append(
                f"vlan_id: VLAN {vid} does not exist in device state; "
                "create it first (compiler does not invent VLANs)")
    elif op in _VLAN_LIST_OPS:
        _check_vlan_list(plan.get("vlans"), errors)
    elif op == "set_native_vlan":
        _check_vlan_id(plan.get("vlan_id"), errors, field="vlan_id")
    elif op == "set_port_mode":
        if plan.get("mode") not in ("access", "trunk"):
            errors.append("mode: must be 'access' or 'trunk'")
    elif op == "set_admin_state":
        if plan.get("admin") not in ("up", "down"):
            errors.append("admin: must be 'up' or 'down'")
    return errors


def validate_clarify(out, state):
    errors = []
    if out.get("reason") not in CLARIFY_REASONS:
        errors.append(f"reason: {out.get('reason')!r} not in {sorted(CLARIFY_REASONS)}")
    q = out.get("question")
    if not isinstance(q, str) or not q.strip():
        errors.append("question: required non-empty string")
    cands = out.get("candidates", [])
    if not isinstance(cands, list) or not all(isinstance(c, str) for c in cands):
        errors.append("candidates: must be a list of strings")
    return errors


def validate_output(output, state):
    """Validate a full model output (plan, multi-step plan, or clarify)."""
    if not isinstance(output, dict):
        return ["output: must be an object"]
    if output.get("type") == "clarify":
        return validate_clarify(output, state)
    if "steps" in output:
        errors = []
        if output.get("device") != state["device"]:
            errors.append("device: does not match context device")
        steps = output["steps"]
        if not isinstance(steps, list) or not steps:
            errors.append("steps: must be a non-empty list")
            return errors
        # Steps validate against the state they will actually see:
        # step i is checked against S after steps 0..i-1 are applied,
        # so a step may reference a VLAN created by an earlier step.
        from .apply import apply_plan, PlanError
        cur = state
        for i, step in enumerate(steps):
            step_errors = validate_plan(step, cur)
            for e in step_errors:
                errors.append(f"steps[{i}]: {e}")
            if step_errors:
                break
            try:
                cur, _ = apply_plan(cur, step, validate=False)
            except PlanError as e:  # pragma: no cover - defensive
                errors.append(f"steps[{i}]: {e}")
                break
        return errors
    return validate_plan(output, state)
