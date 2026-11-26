"""S' = apply(S, Δ): deterministic application of a validated plan.

Semantics (the choices a scorer can rely on; see docs/PLAN_CONTRACT.md):

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
  because the VLAN no longer exists). The cascade paths that actually
  changed are returned in `effects`; which paths a cascade is
  *permitted* to change is derived independently in cascades.py.
- set_native_vlan / set_admin_state: single-field changes.

apply_plan is a public entry point and gates on the FULL validation
(schema.validate_output): device binding on the outer plan and every
step, non-empty steps, step i validated against the state produced by
steps 0..i-1. An invalid plan raises PlanError and produces no
candidate state. apply_plan returns (new_state, effects), where
effects = the documented cascade paths (per cascades.py, derived from
the operation and the before-state) that actually changed — never
"whatever else changed", so effects cannot launder a bug.
"""
from .cascades import permitted_cascade_paths
from .diff import diff_states
from .schema import validate_output
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

    The gate: with validate=True (the public path) the plan must pass
    schema.validate_output in full — the same validation every entry
    point uses. Internal callers that have already validated a step
    against the exact state it will see may pass validate=False.
    Raises PlanError on any validation or apply-time refusal (e.g.
    remove from trunk_allowed="all"); no partial state is produced.
    """
    if not isinstance(plan, dict):
        raise PlanError("plan: must be an object")
    if plan.get("type") == "clarify":
        raise PlanError("clarify output: changes no state; nothing to apply")
    if validate:
        errors = validate_output(plan, state)
        if errors:
            raise PlanError("; ".join(errors))
    steps = plan["steps"] if "steps" in plan else [plan]
    cur = clone(state)
    effects = set()
    for step in steps:
        nxt = _apply_one(cur, step)
        changed = set(diff_states(cur, nxt))
        # Effects are the independently-permitted cascade paths that
        # actually changed — derived from (operation, before-state)
        # in cascades.py, NOT "any other path that changed".
        effects |= changed & permitted_cascade_paths(step, cur)
        cur = nxt
    return cur, sorted(effects)
