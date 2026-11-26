"""Render IOS XE CLI from plans and states.

Two renderers:

- render_delta(plan, state_before) -> the CLI an operator would apply
  to move S -> S'. Uses the exact IOS forms that carry the plan's
  semantics: `allowed vlan add/remove <list>` for incremental trunk
  changes vs the bare `allowed vlan <list>` for replace, so the CLI
  itself preserves the add/replace distinction.
- render_config(state) -> a full config for S' in the project's
  existing dataset style: `hostname` first, `!` separators, VLAN
  blocks, then interface blocks with a two-space... (dataset style:
  single-space indented subcommands, `no shutdown` for admin up,
  `shutdown` for admin down).

Descriptions are rendered when present (dataset convention: interface
descriptions echo role labels). Aliases are inventory metadata, not
CLI, and are never rendered.
"""


def _vlan_list(vlans):
    return ",".join(str(v) for v in sorted(vlans))


def _iface_header(name):
    return [f"interface {name}"]


def render_delta(plan, state_before):
    """CLI commands implementing one plan (single or multi-step)."""
    if "steps" in plan:
        lines = []
        cur = state_before
        from .apply import apply_plan
        for step in plan["steps"]:
            lines.extend(render_delta(step, cur))
            cur, _ = apply_plan(cur, step)
        return lines
    op = plan["operation"]
    if op == "create_vlan":
        lines = [f"vlan {plan['vlan_id']}"]
        if plan.get("name"):
            lines.append(f" name {plan['name']}")
        return lines
    if op == "delete_vlan":
        return [f"no vlan {plan['vlan_id']}"]
    lines = _iface_header(plan["interface"])
    if op == "set_access_vlan":
        # Parameter assignment only — apply() does not change mode, so
        # the delta must not emit a mode line either. (IOS accepts the
        # access-vlan parameter while the port is in trunk mode; it
        # takes effect if the mode is later changed.)
        lines.append(f" switchport access vlan {plan['vlan_id']}")
    elif op == "add_trunk_vlans":
        cur = state_before["interfaces"][plan["interface"]]["trunk_allowed"]
        if cur != "all":  # adding to "all" changes nothing; emit no-op guard
            lines.append(
                f" switchport trunk allowed vlan add {_vlan_list(plan['vlans'])}")
        else:
            lines.append(" ! already allowed (trunk allows all VLANs)")
    elif op == "remove_trunk_vlans":
        lines.append(
            f" switchport trunk allowed vlan remove {_vlan_list(plan['vlans'])}")
    elif op == "replace_trunk_vlans":
        lines.append(
            f" switchport trunk allowed vlan {_vlan_list(plan['vlans'])}")
    elif op == "set_native_vlan":
        lines.append(f" switchport trunk native vlan {plan['vlan_id']}")
    elif op == "set_port_mode":
        lines.append(f" switchport mode {plan['mode']}")
    elif op == "set_admin_state":
        lines.append(" no shutdown" if plan["admin"] == "up" else " shutdown")
    return lines


def _render_interface(name, iface):
    lines = _iface_header(name)
    if iface.get("description"):
        lines.append(f" description {iface['description']}")
    lines.append(f" switchport mode {iface['mode']}")
    if iface["mode"] == "access":
        if iface.get("access_vlan") is not None:
            lines.append(f" switchport access vlan {iface['access_vlan']}")
    else:
        lines.append(f" switchport trunk native vlan {iface['native_vlan']}")
        allowed = iface.get("trunk_allowed")
        if allowed == "all":
            lines.append(" switchport trunk allowed vlan all")
        elif allowed:
            lines.append(
                f" switchport trunk allowed vlan {_vlan_list(allowed)}")
        else:
            lines.append(" switchport trunk allowed vlan none")
    lines.append(" no shutdown" if iface["admin"] == "up" else " shutdown")
    return lines


def render_config(state):
    """Full config for a state, in the dataset's config style."""
    lines = [f"hostname {state['hostname']}", "!"]
    for vid in sorted(state["vlans"], key=lambda v: int(v)):
        lines.append(f"vlan {vid}")
        name = state["vlans"][vid].get("name")
        if name:
            lines.append(f" name {name}")
        lines.append("!")
    for name in sorted(state["interfaces"]):
        lines.extend(_render_interface(name, state["interfaces"][name]))
        lines.append("!")
    return "\n".join(lines) + "\n"
