#!/usr/bin/env python3
"""Regression tests for score_pilot.py v2.

Each case maps to a finding from Codex's review of freeze 0231af9
(board ids 27 + 29) or to a scorer behavior the freeze relies on.
Builds complete synthetic raw-output files (all 68 item/arm records,
fillers elsewhere) in a temp dir and runs the real scorer against them.

Run: python3 pilot/test_score_pilot.py  (exit 0 = all pass)
"""

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ITEMS = os.path.join(HERE, "items.jsonl")
SCORER = os.path.join(HERE, "score_pilot.py")

ITEM_LIST = [json.loads(l) for l in open(ITEMS) if l.strip()]
BY_ID = {i["id"]: i for i in ITEM_LIST}
CLARIFY = next(i for i in ITEM_LIST
               if i["expected"]["kind"] == "clarify" and i["view"] == "json")
NOOP = next(i for i in ITEM_LIST
            if i["expected"]["kind"] == "no_op" and i["view"] == "json")
FACTS_R01 = ('{"interfaces": {"GigabitEthernet1/0/48": '
             '{"trunk_allowed": [10, 30], "native_vlan": 10}}}')
GOOD_PLAN = ('{"device":"sw-acc-1","operation":"add_trunk_vlans",'
             '"interface":"GigabitEthernet1/0/48","vlans":[20]}')

FAILURES = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f" — {detail}" if detail
                                                   and not cond else ""))
    if not cond:
        FAILURES.append(name)


def run_scorer(overrides, drop=None, duplicate=None):
    """overrides: {(item_id, arm): raw_output}. Returns (rc, scores|None)."""
    tmp = tempfile.mkdtemp()
    raw_path = os.path.join(tmp, "raw.jsonl")
    with open(raw_path, "w") as fh:
        for item in ITEM_LIST:
            for arm in ("A", "B"):
                if drop == (item["id"], arm):
                    continue
                raw = overrides.get((item["id"], arm),
                                    "filler, not json"
                                    if item["task"] in ("R", "P")
                                    else "No citation here.")
                rec = {"item_id": item["id"], "task": item["task"],
                       "arm": arm, "raw_output": raw}
                fh.write(json.dumps(rec) + "\n")
                if duplicate == (item["id"], arm):
                    fh.write(json.dumps(rec) + "\n")
    proc = subprocess.run(
        [sys.executable, SCORER, "--items", ITEMS, "--raw", raw_path,
         "--out-dir", tmp], capture_output=True, text=True)
    scores = None
    sp = os.path.join(tmp, "scores.json")
    if os.path.exists(sp):
        scores = {(r["item_id"], r["arm"]): r for r in json.load(open(sp))}
    return proc.returncode, scores


def main():
    # 1. Trailing conflicting JSON: strict must fail, diagnostic may pass.
    rc, s = run_scorer({("R-01", "B"): FACTS_R01 + "\n" + FACTS_R01.replace(
        "[10, 30]", "[99]").replace('"native_vlan": 10',
                                    '"native_vlan": 99')})
    e = s[("R-01", "B")]
    check("trailing-conflict strict fails", rc == 0 and e["correct"] is False
          and e["failure_class"] == "not_single_json", str(e))
    check("trailing-conflict diagnostic passes", e["diag_correct"] is True)

    # 2. Clean single-object R answer passes strict.
    rc, s = run_scorer({("R-01", "A"): FACTS_R01})
    check("clean R passes", s[("R-01", "A")]["correct"] is True)

    # 3. Correct bare plan passes (engine shapes).
    rc, s = run_scorer({("P-02", "B"): GOOD_PLAN})
    check("bare plan passes", s[("P-02", "B")]["correct"] is True,
          str(s[("P-02", "B")]))

    # 4. Clarify without the required question fails validation.
    no_q = json.dumps({"type": "clarify", "reason": "ambiguous_alias",
                       "candidates": CLARIFY["expected"]["clarify"]
                       ["candidates"]})
    rc, s = run_scorer({(CLARIFY["id"], "B"): no_q})
    e = s[(CLARIFY["id"], "B")]
    check("clarify without question fails",
          e["correct"] is False and e["failure_class"] == "validation_error",
          str(e))

    # 5. Clarify with malformed candidate type fails, scorer survives.
    bad_c = json.dumps({"type": "clarify", "reason": "ambiguous_alias",
                        "question": "Which one?",
                        "candidates": "GigabitEthernet1/0/47"})
    rc, s = run_scorer({(CLARIFY["id"], "A"): bad_c})
    e = s[(CLARIFY["id"], "A")]
    check("malformed candidates fail cleanly",
          rc == 0 and e["correct"] is False
          and e["failure_class"] == "validation_error", str(e))

    # 6. Exact no_op shape passes on a no_op item.
    rc, s = run_scorer({(NOOP["id"], "B"): '{"type": "no_op"}'})
    check("exact no_op passes", s[(NOOP["id"], "B")]["correct"] is True)

    # 7. no_op with extra keys is a shape error.
    rc, s = run_scorer({(NOOP["id"], "A"):
                        '{"type": "no_op", "operation": "add_trunk_vlans"}'})
    e = s[(NOOP["id"], "A")]
    check("no_op extra keys shape_error",
          e["correct"] is False and e["failure_class"] == "shape_error",
          str(e))

    # 8. E-01 incomplete answer (vlan-id only, no union/vlan-range) must
    #    fail the keyword diagnostic (both union branches required).
    incomplete = ("trunk-vlans is a leaf-list of vlan-id values.\n"
                  "Citation: openconfig-vlan / vlan-switched-config / "
                  "trunk-vlans")
    rc, s = run_scorer({("E-01", "B"): incomplete})
    e = s[("E-01", "B")]
    check("E-01 incomplete fails keyword diagnostic",
          e["keyword_diag"] is False and e["citation_correct"] is True,
          str(e))

    # 9. Negating E answer: keywords + citation pass mechanically, but
    #    factual stays pending — the split is the protection.
    negating = ("Yes it can: there is no when restriction, and access "
                "mode is not required for access-vlan.\n"
                "Citation: openconfig-vlan / vlan-switched-config / "
                "access-vlan")
    rc, s = run_scorer({("E-02", "A"): negating})
    e = s[("E-02", "A")]
    check("negating E answer: citation/keyword pass, factual pending",
          e["citation_correct"] is True and e["keyword_diag"] is True
          and e["factual_correct"] is None, str(e))

    # 10. Integrity: duplicate and missing records abort with exit 2.
    rc, _ = run_scorer({}, duplicate=("R-01", "A"))
    check("duplicate record aborts", rc == 2, f"rc={rc}")
    rc, _ = run_scorer({}, drop=("P-05", "B"))
    check("missing record aborts", rc == 2, f"rc={rc}")

    print(f"\n{10 - len(FAILURES)}/10 regression groups passed"
          if not FAILURES else f"\nFAILURES: {FAILURES}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
