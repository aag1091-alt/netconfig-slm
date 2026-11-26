# Prompt templates — human-readable mirror

Canonical source: `prompts.py` (the runner imports it; `samples.md` is
rendered from it). This file exists so a reviewer can read the exact
prompts without reading code. If this file and `prompts.py` ever
disagree, `prompts.py` governs and the freeze is broken.

## System (both arms, all tasks)

> You are a network configuration assistant being evaluated in a pilot.
> Follow the output format exactly. Do not add commentary outside the
> requested format.

## Assembly order (user message)

1. Context block (R and P only): device, hostname, interface aliases.
2. State block (R and P only): the IOS XE CLI text (view `cli`) or the
   OpenConfig YANG JSON instance (view `json`).
3. **Arm B only:** "Schema evidence (pinned YANG excerpts):" +
   the full text of `schema_packet.txt`.
4. Task block (below).

## Task block — R (Read)

> Question: \<question\>
> Reply with ONE JSON object containing exactly the facts asked for,
> using these field names as applicable: vlans (object of vlan id ->
> name), and per interface: mode, access_vlan, trunk_allowed,
> native_vlan, admin, description. Shape:
> {"vlans": {...}, "interfaces": {"<interface>": {...}}}. Omit sections
> that were not asked about.

## Task block — P (Edit)

> Request: \<request\>
> + the full output contract from `prompts.py` (`CONTRACT`): the reply
> shapes — a plan object in the engine's own format
> (`{"device", "operation", ...}` or `{"device", "steps": [...]}`), a
> clarify object (`{"type": "clarify", ...}`), or `{"type": "no_op"}` —
> the 9 operations with their fields, the multi-step rule, the no-op
> rule, and the clarify rule (reason `ambiguous_alias`, exact
> candidates). Both arms receive the contract verbatim — it is our own
> plan format, not schema evidence.

## Task block — E (Explain)

> Question about the OpenConfig YANG schema for VLAN/interface
> configuration on IOS XE: \<question\>
> Answer in one to three sentences, then end with a line of the form:
> Citation: <module> / <path>

E items carry no device state; arm B's packet is the only difference.
