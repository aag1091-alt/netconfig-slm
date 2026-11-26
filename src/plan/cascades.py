"""Independent derivation of authorized change paths (scoring).

WHY THIS MODULE EXISTS (supervisor review, 2026-11-26): the previous
authorization logic lived in apply.py and computed "effects" as
*whatever apply() changed beyond the primary paths* — so the
preservation check authorized apply()'s own output. A bug that
flipped `admin` during a trunk edit would have authorized itself.

Here, authorization is derived from the OPERATION and the
BEFORE-STATE ONLY. Nothing in this module inspects an after-state or
a diff, and it imports nothing from apply.py — apply() is graded
against these derivations, never their source.

    authorized_paths(plan, before) = primary_paths(plan)
                                     ∪ permitted_cascade_paths(plan, before)

Cascade rules (the contract's documented cascades, §3): only
delete_vlan cascades. Deleting VLAN v, when v exists in `before`:
  - every interface whose access_vlan == v may change
    interfaces.<name>.access_vlan (falls back to null);
  - every interface whose trunk_allowed is an explicit list
    containing v may change interfaces.<name>.trunk_allowed
    (v is stripped).
"all" trunks never cascade; native_vlan pointers are never rewritten.
Deleting a VLAN that does not exist is a no-op: no cascades.
"""


def primary_paths(plan):
    """Paths the operation itself is authorized to change."""
    if not isinstance(plan, dict):
        return set()
    op = plan.get("operation", "")
    iface = plan.get("interface")
    if op == "create_vlan":
        return {f"vlans.{plan.get('vlan_id')}",
                f"vlans.{plan.get('vlan_id')}.name"}
    if op == "delete_vlan":
        return {f"vlans.{plan.get('vlan_id')}"}
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


def permitted_cascade_paths(plan, before):
    """Non-primary paths `plan` may change, from (operation, before).

    Single-operation plans only; multi-step plans are authorized
    step-by-step by the scorer (each step against the state it sees).
    """
    if not isinstance(plan, dict) or "steps" in plan:
        return set()
    if plan.get("operation") != "delete_vlan":
        return set()
    vid = plan.get("vlan_id")
    if not isinstance(vid, int) or isinstance(vid, bool):
        return set()
    if not isinstance(before, dict) or str(vid) not in before.get("vlans", {}):
        return set()  # deleting a nonexistent VLAN is a no-op
    out = set()
    for name, iface in before.get("interfaces", {}).items():
        if not isinstance(iface, dict):
            continue
        if iface.get("access_vlan") == vid:
            out.add(f"interfaces.{name}.access_vlan")
        allowed = iface.get("trunk_allowed")
        if isinstance(allowed, list) and vid in allowed:
            out.add(f"interfaces.{name}.trunk_allowed")
    return out


def authorized_paths(plan, before):
    """Every path this single-operation plan is allowed to change."""
    return primary_paths(plan) | permitted_cascade_paths(plan, before)
