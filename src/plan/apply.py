"""S' = apply(S, Δ): deterministic application of a validated plan.

Semantics (the choices a scorer can rely on; see docs/PLAN_SLICE.md):

- add_trunk_vlans: union. On a trunk whose allowed set is "all" this is
  a no-op (every VLAN is already allowed). Never touches native VLAN,
  mode, or admin state.
- remove_trunk_vlans: set difference. Removing a VLAN that is not in an
  explicit list is an idempotent no-op. Removing from "all" is a
  PlanError — rendering it would mean expanding ~4094 ids; the operator
  must use replace_trunk_vlans to state the intended list explicitly.
- replace_trunk_vlans: the allowed set becomes exactly the given list
  (this is IOS `switchport trunk allowed vlan <list>`). An empty list
  is expressed as vlans=[] being rejected by the schema; to clear a
  trunk the plan uses vlans with the sentinel handled in render as
  `none` — in this slice replace requires >= 1 VLAN (schema-enforced).
- set_access_vlan: sets mode-affecting field only; the port's mode is
  NOT changed implicitly (a trunk stays a trunk — mode changes are
  their own operation), and admin state is never touched.
- set_port_mode: changes mode only; access_vlan / trunk_allowed /
  native_vlan values are preserved (as IOS preserves the configured
  values when toggling mode).
- create_vlan: creates, or renames if the VLAN exists with a different
  name; identical create is a no-op.
- delete_vlan: removes the VLAN and cascades, exactly as IOS XE leaves
  the device: access ports assigned to it fall back to access_vlan=null
  (inactive), and the id is removed from explicit trunk allowed lists
  (a trunk set to "all" is unaffected — "all" no longer includes it
  because the VLAN no longer exists). The cascade paths are returned in
  `effects` so scorers can authorize them.
- set_native_vlan / set_admin_state: single-field changes.

apply_plan returns (new_state, effects) where effects is a list of
additional changed paths caused by cascades (delete_vlan only).
Raises PlanError for semantic errors the schema cannot see.
"""
from .diff import diff_states
from .schema import validate_plan
from .state import clone


class PlanError(Exception):
    pass


def _iface(state, plan):
    name = plan["interface"]
    if name not in state["interfaces"]:
        raise PlanError(f"interface {name!r} not in inventory")
    return state["interfaces"][name]


def _apply_one(state, plan):
    op = plan["operation"]
    s = clone(state)
    if op == "create_vlan":
        vid = str(plan["vlan_id"])
        name = plan.get("name")
        if vid in s["vlans"]:
            if name is not None:
                s["vlans"][vid]["name"] = name
        else:
            s["vlans"][vid] = {"name": name}
    elif op == "delete_vlan":
        vid = str(plan["vlan_id"])
        if vid not in s["vlans"]:
            return s  # IOS accepts `no vlan <id>` for a missing VLAN: no-op
        del s["vlans"][vid]
        for iface in s["interfaces"].values():
            if iface.get("access_vlan") == plan["vlan_id"]:
                iface["access_vlan"] = None
            allowed = iface.get("trunk_allowed")
            if isinstance(allowed, list) and plan["vlan_id"] in allowed:
                iface["trunk_allowed"] = [v for v in allowed
                                           if v != plan["vlan_id"]]
    elif op == "set_access_vlan":
        _iface(s, plan)["access_vlan"] = plan["vlan_id"]
    elif op in ("add_trunk_vlans", "remove_trunk_vlans", "replace_trunk_vlans"):
        iface = _iface(s, plan)
        allowed = iface.get("trunk_allowed")
        wanted = list(plan["vlans"])
        if op == "add_trunk_vlans":
            if allowed != "all":
                iface["trunk_allowed"] = sorted(set(allowed) | set(wanted))
        elif op == "remove_trunk_vlans":
            if allowed == "all":
                raise PlanError(
                    "cannot remove from trunk_allowed='all'; "
                    "use replace_trunk_vlans to state the list explicitly")
            iface["trunk_allowed"] = [v for v in allowed if v not in wanted]
        else:  # replace
            iface["trunk_allowed"] = sorted(set(wanted))
    elif op == "set_native_vlan":
        _iface(s, plan)["native_vlan"] = plan["vlan_id"]
    elif op == "set_port_mode":
        _iface(s, plan)["mode"] = plan["mode"]
    elif op == "set_admin_state":
        _iface(s, plan)["admin"] = plan["admin"]
    else:  # pragma: no cover - schema validation runs first
        raise PlanError(f"unknown operation {op!r}")
    return s


def apply_plan(state, plan, validate=True):
    """Apply a plan (single or multi-step). Returns (new_state, effects).

    effects = changed paths beyond the operation's primary target(s),
    i.e. delete_vlan cascades. Computed by diffing, so it can never
    under-report a side effect.
    """
    if isinstance(plan, dict) and "steps" in plan:
        cur = clone(state)
        for step in plan["steps"]:
            cur, _ = apply_plan(cur, step, validate=validate)
        return cur, _cascade_effects(state, cur, plan)
    if validate:
        errors = validate_plan(plan, state)
        if errors:
            raise PlanError("; ".join(errors))
    new_state = _apply_one(state, plan)
    return new_state, _cascade_effects(state, new_state, plan)


def _primary_paths(plan):
    """Paths the operation itself is authorized to change."""
    op = plan.get("operation", "")
    iface = plan.get("interface")
    if op in ("create_vlan",):
        return {f"vlans.{plan['vlan_id']}", f"vlans.{plan['vlan_id']}.name"}
    if op == "delete_vlan":
        return {f"vlans.{plan['vlan_id']}"}
    field = {
        "set_access_vlan": "access_vlan",
        "add_trunk_vlans": "trunk_allowed",
        "remove_trunk_vlans": "trunk_allowed",
        "replace_trunk_vlans": "trunk_allowed",
        "set_native_vlan": "native_vlan",
        "set_port_mode": "mode",
        "set_admin_state": "admin",
    }.get(op)
    return {f"interfaces.{iface}.{field}"} if field else set()


def _cascade_effects(before, after, plan):
    changed = set(diff_states(before, after))
    if isinstance(plan, dict) and "steps" in plan:
        primary = set()
        for step in plan["steps"]:
            primary |= _primary_paths(step)
    else:
        primary = _primary_paths(plan)
    return sorted(changed - primary)


def authorized_paths(before, after, plan):
    """Every path this plan is allowed to have changed (primary + cascade)."""
    _, effects = apply_plan(before, plan)
    primary = set()
    steps = plan["steps"] if "steps" in plan else [plan]
    for step in steps:
        primary |= _primary_paths(step)
    return primary | set(effects)
