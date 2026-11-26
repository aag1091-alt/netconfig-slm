#!/usr/bin/env python3
"""Build the frozen pilot item set (items.jsonl) + worked samples (samples.md).

Sources: data/yang_slice/paired_goldens.jsonl (9 records, verified 9/9) plus
hand-authored Read derivations and Explain questions below. Deterministic:
re-running produces byte-identical items.jsonl. plan_state and expected are
GRADER fields — prompts.py never reads them.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import prompts as P  # noqa: E402

GOLDENS = os.path.join(REPO, "data", "yang_slice", "paired_goldens.jsonl")


def load_goldens():
    with open(GOLDENS, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def base_item(rec, view):
    return {
        "view": view,
        "source": rec["id"],
        "context": rec["context"],
        "cli_state": rec["cli_state"],
        "yang_state": rec["yang_state"],
        "plan_state": rec["plan_state"],
    }


def build_items():
    goldens = {r["id"]: r for r in load_goldens()}
    items = []

    # ---- P: plan/edit items from the plan goldens, both views ----
    p_reqs = []
    for rec in goldens.values():
        if rec["kind"] not in ("paired_plan", "paired_contrast"):
            continue
        if rec["kind"] == "paired_interpretation":
            continue
        reqs = rec.get("requests") or [{"request": rec["request"],
                                        "expected": rec["expected"]}]
        for rq in reqs:
            p_reqs.append((rec, rq))
    n = 0
    for rec, rq in p_reqs:
        for view in ("cli", "json"):
            n += 1
            item = base_item(rec, view)
            item.update({
                "id": f"P-{n:02d}",
                "task": "P",
                "request": rq["request"],
                "expected": rq["expected"],
            })
            items.append(item)

    # ---- R: read items — interpretation goldens + derived fact reads ----
    r_specs = []
    for rid in ("interp-trunk-facts", "interp-access-enabled-facts"):
        rec = goldens[rid]
        r_specs.append((rec, rec["request"], rec["expected"]["facts"]))
    rec = goldens["pair-add-vlan20-canonical"]
    r_specs.append((rec,
                    "List all VLANs defined on the switch with their names, "
                    "and state GigabitEthernet1/0/48's mode and admin state.",
                    {"vlans": {"10": "Users", "20": "Engineering",
                               "30": "Voice"},
                     "interfaces": {"GigabitEthernet1/0/48":
                                    {"mode": "trunk", "admin": "up"}}}))
    rec = goldens["pair-access-assign-admin-preserved"]
    r_specs.append((rec,
                    "What is GigabitEthernet1/0/7's description, mode, and "
                    "admin state?",
                    {"interfaces": {"GigabitEthernet1/0/7":
                                    {"description": "Spare desk",
                                     "mode": "access", "admin": "down"}}}))
    n = 0
    for rec, request, facts in r_specs:
        for view in ("cli", "json"):
            n += 1
            item = base_item(rec, view)
            item.update({
                "id": f"R-{n:02d}",
                "task": "R",
                "request": request,
                "expected": {"kind": "facts", "facts": facts},
            })
            items.append(item)

    # ---- E: explain items — authored schema questions ----
    e_specs = [
        ("Which YANG leaf holds the list of VLANs allowed on a trunk "
         "interface, and what is its type?",
         ["trunk-vlans", "leaf-list", "vlan-id"],
         "openconfig-vlan / vlan-switched-config / trunk-vlans"),
        ("Can a schema-valid OpenConfig instance carry an access-vlan value "
         "for an interface whose interface-mode is TRUNK?",
         ["no", "when", "access"],
         "openconfig-vlan / vlan-switched-config / access-vlan"),
        ("In OpenConfig, what does it mean when a trunk interface's state "
         "has no trunk-vlans list at all?",
         ["all", "allowed"],
         "openconfig-vlan / vlan-switched-config / trunk-vlans"),
        ("Which leaf controls whether an interface is administratively "
         "enabled in openconfig-interfaces, and what is its type and "
         "default?",
         ["enabled", "boolean", "true"],
         "openconfig-interfaces / interface / config / enabled"),
        ("Where does the native VLAN of a trunk live in the schema, and "
         "under what condition is it valid?",
         ["native-vlan", "trunk"],
         "openconfig-vlan / vlan-switched-config / native-vlan"),
        ("Does the pinned VLAN/interface slice define the device hostname? "
         "If not, where does hostname live in OpenConfig?",
         ["openconfig-system", "hostname"],
         "openconfig-system / system / config / hostname"),
        ("In which module and list are a VLAN's id and name defined?",
         ["openconfig-vlan", "vlans", "name"],
         "openconfig-vlan / vlans / vlan / config"),
        ("What values can a VLAN id take, according to the schema's type "
         "definitions?",
         ["1..4094", "uint16"],
         "openconfig-vlan-types / vlan-id"),
    ]
    for i, (q, key_points, citation) in enumerate(e_specs, 1):
        items.append({
            "id": f"E-{i:02d}",
            "task": "E",
            "view": "na",
            "source": "authored for this pilot",
            "request": q,
            "expected": {"kind": "explain", "key_points": key_points,
                         "citation": citation},
        })
    return items


SAMPLE_NOTES = {
    "R-01": (
        '{"interfaces": {"GigabitEthernet1/0/48": {"trunk_allowed": [10, 30], '
        '"native_vlan": 10}}}',
        "Scoring: the reply's JSON is extracted and compared field-by-field "
        "with the expected facts. Exact equality on trunk_allowed and "
        "native_vlan = correct. Any extra or missing fact, or a wrong value, "
        "= incorrect (the raw output is kept for audit). The paired JSON-view "
        "item R-02 asks the same facts from the YANG instance; agreement "
        "between the two views is reported separately.",
    ),
    "E-02": (
        "No. In openconfig-vlan, the access-vlan leaf carries a when "
        "condition requiring the sibling interface-mode to be 'ACCESS', so a "
        "schema-valid instance cannot carry access-vlan on a TRUNK "
        "interface.\nCitation: openconfig-vlan / vlan-switched-config / "
        "access-vlan",
        "Scoring: correct requires (a) the citation line to match the "
        "expected path (module + leaf, normalized), and (b) every expected "
        "key point present in the answer text (no / when / access, "
        "case-insensitive). This is the F1 finding from docs/YANG_SLICE.md "
        "turned into a question.",
    ),
}


def render_samples(items):
    by_id = {i["id"]: i for i in items}
    p_sample = next(i for i in items
                    if i["source"] == "pair-add-vlan20-canonical"
                    and i["view"] == "json")
    out = ["# Worked samples — one per task (arm B rendering)", "",
           "Rendered by build_items.py from prompts.py, so the prompt text "
           "below is byte-identical to what the runner will send. Expected "
           "outputs are the graders' references.", ""]
    for label, item in (("Read", by_id["R-01"]), ("Explain", by_id["E-02"]),
                        ("Edit", p_sample)):
        out.append(f"## {label} — item {item['id']} "
                   f"(source: {item['source']}, view: {item['view']})")
        out.append("")
        out.append("### Prompt (system)")
        out.append("```")
        out.append(P.SYSTEM)
        out.append("```")
        out.append("### Prompt (user)")
        out.append("```")
        out.append(P.build_user(item, "B"))
        out.append("```")
        out.append("### Expected output")
        out.append("```")
        if item["task"] == "P":
            out.append(json.dumps(item["expected"]["plan"]
                                  if item["expected"]["kind"] == "plan"
                                  else item["expected"], indent=2))
            out.append("```")
            out.append("### Scoring")
            out.append(
                "The plan is validated with validate_output() and applied "
                "with apply_plan() from src/plan/ against the item's "
                "plan_state. Correct requires: validation passes; every "
                "assertion in the item's expected object holds after apply "
                "(here trunk_allowed becomes [10, 20, 30]); and "
                "diff_states() shows no changed path outside the asserted "
                "set (native_vlan stays 10 — preservation is graded, not "
                "assumed). A parse failure, a wrong operation (e.g. "
                "replace_trunk_vlans), or any extra change = incorrect, "
                "with the failure class recorded.")
        else:
            expected_text, note = SAMPLE_NOTES[item["id"]]
            out.append(expected_text)
            out.append("```")
            out.append("### Scoring")
            out.append(note)
        out.append("")
    return "\n".join(out)


def main():
    items = build_items()
    with open(os.path.join(HERE, "items.jsonl"), "w", encoding="utf-8") as fh:
        for item in items:
            fh.write(json.dumps(item, sort_keys=True) + "\n")
    with open(os.path.join(HERE, "samples.md"), "w", encoding="utf-8") as fh:
        fh.write(render_samples(items))
    counts = {}
    for item in items:
        counts[item["task"]] = counts.get(item["task"], 0) + 1
    print("items:", len(items), counts)


if __name__ == "__main__":
    main()
