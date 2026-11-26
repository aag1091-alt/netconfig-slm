#!/usr/bin/env python3
"""Canonical prompt templates for the YANG-grounding pilot.

This module is the single source of truth for prompt text. run_pilot.py
imports from here; samples.md was rendered from here; prompts.md quotes
these strings for human review. If the text here changes, the freeze is
broken — that is deliberate.

Arms:
  A (no-schema): context + state + task. No YANG material.
  B (schema):    identical, plus the pinned schema packet
                 (pilot/schema_packet.txt) inserted before the task.

Both arms receive the same output contract. The ONLY difference between
arms is the schema packet — that is the variable under test.
"""

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))

SYSTEM = (
    "You are a network configuration assistant being evaluated in a pilot. "
    "Follow the output format exactly. Do not add commentary outside the "
    "requested format."
)

# The plan output contract, from docs/PLAN_CONTRACT.md / src/plan/schema.py.
# Given to BOTH arms: it is our own format, not schema evidence. The plan
# and clarify shapes are byte-identical to the engine's; {"type": "no_op"}
# is a pilot-surface addition (the engine has no no-op output type — in
# engine semantics a no-op is an idempotent plan that changes nothing).
CONTRACT = """\
Output contract for Edit tasks — reply with ONE JSON object, one of:
- a plan: {"device": "<device>", "operation": "<op>", ...fields}
- a multi-step plan: {"device": "<device>", "steps": [<plan>, ...]} — one
  step per interface when a request targets several interfaces
- a clarify: {"type": "clarify", "reason": "<reason>", "question":
  "<question>", "candidates": ["<interface>", ...]}
- a no-op: {"type": "no_op"} — when the requested end-state already holds
Operations and their fields:
  create_vlan: vlan_id, name (optional)
  delete_vlan: vlan_id
  set_access_vlan: interface, vlan_id (the VLAN must already exist)
  add_trunk_vlans / remove_trunk_vlans / replace_trunk_vlans: interface,
    vlans (non-empty list of VLAN ids)
  set_native_vlan: interface, vlan_id
  set_port_mode: interface, mode ("access" or "trunk")
  set_admin_state: interface, admin ("up" or "down")
Clarify reasons: ambiguous_alias, unknown_interface, unknown_vlan,
missing_vlan_id, missing_interface, unsupported_remove_from_all. If a
name or alias matches more than one interface, answer clarify with
reason "ambiguous_alias" and the exact candidate list."""


def schema_packet() -> str:
    with open(os.path.join(HERE, "schema_packet.txt"), encoding="utf-8") as fh:
        return fh.read().rstrip()


def _context_block(item) -> str:
    ctx = item["context"]
    aliases = ctx.get("aliases") or {}
    alias_text = "; ".join(
        f"{iface}: {', '.join(names)}" for iface, names in aliases.items() if names
    ) or "none"
    return (
        f"Device: {ctx['device']} (hostname {ctx['hostname']})\n"
        f"Interface aliases: {alias_text}"
    )


def _state_block(item) -> str:
    if item["view"] == "cli":
        return "Current configuration (IOS XE CLI):\n" + item["cli_state"].rstrip()
    return (
        "Current state (OpenConfig YANG JSON instance):\n"
        + json.dumps(item["yang_state"], indent=2, sort_keys=True)
    )


def build_user(item, arm: str) -> str:
    """Render the exact user message for one item under one arm."""
    parts = []
    if item["task"] in ("R", "P"):
        parts.append(_context_block(item))
        parts.append(_state_block(item))
    if arm == "B":
        parts.append("Schema evidence (pinned YANG excerpts):\n" + schema_packet())
    if item["task"] == "R":
        parts.append(
            "Question: " + item["request"] + "\n"
            "Reply with ONE JSON object containing exactly the facts asked "
            "for, using these field names as applicable: vlans (object of "
            "vlan id -> name), and per interface: mode, access_vlan, "
            "trunk_allowed, native_vlan, admin, description. Shape: "
            '{"vlans": {...}, "interfaces": {"<interface>": {...}}}. '
            "Omit sections that were not asked about."
        )
    elif item["task"] == "P":
        parts.append("Request: " + item["request"])
        parts.append(CONTRACT)
    elif item["task"] == "E":
        parts.append(
            "Question about the OpenConfig YANG schema for VLAN/interface "
            "configuration on IOS XE: " + item["request"] + "\n"
            "Answer in one to three sentences, then end with a line of the "
            'form: Citation: <module> / <path>'
        )
    else:  # pragma: no cover
        raise ValueError(item["task"])
    return "\n\n".join(parts)


def build_messages(item, arm: str):
    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": build_user(item, arm)},
    ]
