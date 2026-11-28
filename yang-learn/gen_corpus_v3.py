#!/usr/bin/env python3
"""yang-learn corpus v3 generator (CORPUS_V3_PLAN.md, tranche 1).

Extends the v1/v2 tooling (gen_corpus.py, gen_corpus_v2.py,
gen_records.py, schema_tools.py) rather than rewriting it: facts
come only from pyang via schema_tools.inventory, verdicts only
from yangson via gen_corpus.validate_all / run_validator against
the scoped library (yang-learn/scoped-library.json), platform pin
YangModels/yang @ 9c23db9, IOS XE 26.2.1, cat9k set. Record formats
are EXACTLY the v1/v2 family formats (v2's format-discipline rule).

Subcommands (one per batch; each writes corpus/v3_parts/<name>.jsonl
and is safe to re-run — an existing part file is kept unless
--force is given):

  pm        path_meaning widening + when/must forms + plain types
  iv        instance_validity: fresh rings + the v3 classes
  rs        revised_schema: profiles P7-P12 + P1/P3/P4/P5 upkeep
  srte      state_reconstruction + typed_edit maintenance
  pv        paired_view: CLI<->YANG pairs (round-trip gated)
  holdout   the fresh held-out set -> corpus/held_out_v3.jsonl
  finalize  assemble train/dev additions + combined train,
            token counts, manifest addendum

Contamination guards (plan section 4) are enforced here, not by
convention: bench-reserved PM facts (the every-7th deduped slice)
are excluded at every concrete path; bench domains (interfaces
1/0/21, 1/0/22, 4/0/1; bench IV values; profiles P2/P6) never
enter train/dev; holdout-v3 reservations (interfaces 3/0/1, 3/0/2;
TwentyFiveGigE and HundredGigE PM paths; profiles P13/P14;
holdout grid values) are excluded from every train/dev batch;
every record is prompt-hash deduped against all v1 splits, v2
additions, the hand-authored sample, held_out.jsonl and all
earlier v3 parts.

Splits: scenario-group based and deterministic (plan sections
4.5 + 5). Groups are ordered by sha256(scenario_group); whole
groups go to dev until the family's dev quota (plan section 5:
PM 40, IV 40, RS 12, SR 16, TE 12, PV 20 records) is met, the
rest to train. SR and TE share scenario groups and are split
together so a scenario never straddles train and dev.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import gen_corpus as G           # noqa: E402
import gen_records as GR         # noqa: E402
from gen_corpus_v2 import if_family, norm_path  # noqa: E402
from schema_tools import inventory  # noqa: E402

CORPUS = HERE / "corpus"
PARTS = CORPUS / "v3_parts"
BUNDLE = HERE.parent.parent / "bundles" / "xe-2621"
LIBRARY = HERE / "scoped-library.json"
ROOT = G.ROOT
S, V = GR.S, GR.V
MODEL = GR.MODEL

V1_FILES = [CORPUS / f"{n}.jsonl"
            for n in ("train", "dev", "test", "benchmark")]
V2_FILES = [CORPUS / "train_v2_additions.jsonl",
            CORPUS / "dev_v2_additions.jsonl"]
EXTRA_FILES = [HERE / "held_out.jsonl", HERE / "records.jsonl"]
HOLDOUT_FILE = CORPUS / "held_out_v3.jsonl"

HOLDOUT_FAMS = {"TwentyFiveGigE", "HundredGigE"}   # PM probe lists
DEV_QUOTA = {"path_meaning": 40, "instance_validity": 40,
             "revised_schema": 12, "state_reconstruction": 16,
             "typed_edit": 12, "paired_view": 20}

P_MAXPLAIN = (f"{GR.SW}/port-security-config/port-security/"
              f"maximum-address-config/maximum/maximum")
P_MAXVA = G.P_MAXACC + "/maximum"
P_MAXCOUNT = (f"{GR.SW}/port-security-config/port-security/"
              f"maxcount/max-addresses")
P_VIOLATION = (f"{GR.SW}/port-security-config/port-security/"
               f"violation")
P_HWD = (f"{GR.SW}/port-security-config/port-security/"
         f"mac-address-config/mac-address/hw-address-access/"
         f"hw-address")
P_DYNAMIC = f"{GR.SW}/mode/mode-choice/dynamic/dynamic"


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines()
            if l.strip()]


def part_files() -> list[Path]:
    return sorted(PARTS.glob("*.jsonl")) if PARTS.exists() else []


def existing_files(include_holdout_v3: bool = True) -> list[Path]:
    files = V1_FILES + V2_FILES + EXTRA_FILES + part_files()
    if include_holdout_v3 and HOLDOUT_FILE.exists():
        files.append(HOLDOUT_FILE)
    return files


def all_prompts(files=None, exclude: Path | None = None) -> set[str]:
    out = set()
    for fn in (files or existing_files()):
        if exclude is not None and fn == exclude:
            continue
        for r in load_jsonl(fn):
            out.add(r["prompt"])
    return out


def pm_used_paths(files=None, exclude: Path | None = None) -> set[str]:
    out = set()
    for fn in files or existing_files():
        if exclude is not None and fn == exclude:
            continue
        for r in load_jsonl(fn):
            if r["family"] == "path_meaning":
                out.add(r["fact"]["path"])
    return out


def dedupe(records: list[dict], prompts: set[str]) -> tuple[list, int]:
    kept, dropped = [], 0
    for r in records:
        if r["prompt"] in prompts:
            dropped += 1
            continue
        prompts.add(r["prompt"])
        kept.append(r)
    return kept, dropped


def assign_splits(records: list[dict], family: str,
                  quota: int | None = None) -> None:
    """Deterministic group-ordered split: sha256(scenario_group)
    order; whole groups to dev until the record quota is met."""
    quota = DEV_QUOTA[family] if quota is None else quota
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        groups[r["scenario_group"]].append(r)
    order = sorted(groups, key=lambda g: hashlib.sha256(
        g.encode()).hexdigest())
    dev_n = 0
    for g in order:
        split = "dev" if dev_n < quota else "train"
        for r in groups[g]:
            r["split"] = split
        if split == "dev":
            dev_n += len(groups[g])


def write_part(name: str, records: list[dict]) -> Path:
    PARTS.mkdir(parents=True, exist_ok=True)
    out = PARTS / f"{name}.jsonl"
    with out.open("w") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")
    return out


def batch_guard(name: str, force: bool) -> bool:
    p = PARTS / f"{name}.jsonl"
    if p.exists() and not force:
        print(f"batch {name}: part file exists "
              f"({len(load_jsonl(p))} records) — skipping "
              f"(use --force to regenerate)")
        return False
    return True


def report(name: str, stats: dict) -> dict:
    print(f"{name}:", json.dumps(stats))
    return stats


# ---------------------------------------------------------------- PM

def _constrained(f: dict) -> bool:
    t = f["type"]
    return (any(k in t for k in
                ("range", "enumeration", "pattern", "union"))
            or f["default"] is not None or f["when"] or f["must"]
            or f["kind"] == "leaf-list")


def _prio(f: dict) -> int:
    t = f["type"]
    return (0 if (f["when"] or f["must"]) else
            1 if "pattern" in t else
            2 if "union" in t else
            3 if ("range" in t or "enumeration" in t) else 4)


def pm_pool(facts: dict) -> tuple[list, set]:
    """v1/v2 pool construction, replicated exactly: deduped
    constrained facts (lowest-prio representative per normalized
    key) and the bench-reserved key set (every 7th, first 48)."""
    pool: dict[tuple, dict] = {}
    for f in facts.values():
        if not _constrained(f):
            continue
        key = (norm_path(f["path"]), f["type"])
        if key not in pool or _prio(f) < _prio(pool[key]):
            pool[key] = f
    deduped = sorted(pool.values(), key=lambda f: (_prio(f),
                                                   f["path"]))
    bench_keys = {(norm_path(f["path"]), f["type"])
                  for f in deduped[::7][:48]}
    return deduped, bench_keys


def _pm_src(fact: dict, prov: dict) -> dict:
    return {**fact["source"],
            "revision": prov[fact["path"]]["revision"]}


def _pm_type_rec(fact: dict, rid: str, origin: str) -> dict:
    completion = f"{fact['type']}"
    if fact["default"] is not None:
        completion += f" Default: {fact['default']}."
    if fact["description"]:
        completion += f" Meaning: {fact['description']}"
    return {
        "id": rid, "family": "path_meaning", "origin": origin,
        "scenario_group": f"pm3:{fact['path']}",
        "prompt": (f"In {MODEL}, what is the type of the leaf at "
                   f"{fact['path']}?"),
        "completion": completion,
        "fact": {"path": fact["path"], "type": fact["type"],
                 "default": fact["default"],
                 "answer": {"type": fact["type"],
                            "default": fact["default"]}},
        "source": _pm_src(fact, _PM_PROV), "validation": None}


_PM_PROV: dict = {}


def gen_pm(force: bool = False) -> dict:
    if not batch_guard("pm", force):
        return {"skipped": True}
    facts = {f["path"]: f for f in inventory(BUNDLE)}
    prov = GR.build_prov_map(BUNDLE)
    _PM_PROV.update(prov)
    _deduped, bench_keys = pm_pool(facts)

    used_paths = pm_used_paths(exclude=PARTS / "pm.jsonl")
    prompts = all_prompts(exclude=PARTS / "pm.jsonl")

    # cumulative v2 type-form picks per fact key (the plan's caps
    # are cumulative across v2+v3); when/must train coverage
    v2_count: Counter = Counter()
    when_keys_td: set = set()
    must_keys_td: set = set()
    td_files = V1_FILES[:2] + V2_FILES
    for fn in V1_FILES + V2_FILES:
        for r in load_jsonl(fn):
            if r["family"] != "path_meaning":
                continue
            k = (norm_path(r["fact"]["path"]), r["fact"].get("type"))
            if fn in V2_FILES and r["fact"].get("type"):
                v2_count[k] += 1
            if fn in td_files and r["fact"].get("when"):
                when_keys_td.add(k)
            if fn in td_files and r["fact"].get("must"):
                must_keys_td.add(k)

    by_key: dict[tuple, list[dict]] = defaultdict(list)
    for f in facts.values():
        if not _constrained(f):
            continue
        k = (norm_path(f["path"]), f["type"])
        if k in bench_keys:
            continue
        by_key[k].append(f)

    used_fams: dict[tuple, set] = defaultdict(set)
    for f in facts.values():
        if f["path"] in used_paths:
            used_fams[(norm_path(f["path"]), f["type"])].add(
                if_family(f["path"]))

    def remaining(k: tuple) -> list[dict]:
        cands = [f for f in by_key[k]
                 if f["path"] not in used_paths
                 and if_family(f["path"]) not in HOLDOUT_FAMS]
        cands.sort(key=lambda f: (if_family(f["path"])
                                  in used_fams[k], f["path"]))
        return cands

    def is_failure(k: tuple) -> bool:
        rep = by_key[k][0]
        t = k[1] or ""
        return (_prio(rep) <= 2 or "range" in t or "->" in t)

    def cap(k: tuple) -> int:
        return 3 if is_failure(k) else 2

    records: list[dict] = []
    n = [0]

    def emit(fact: dict, origin: str = "generated_fact_v3") -> None:
        n[0] += 1
        records.append(_pm_type_rec(fact, f"V3-PM-{n[0]:04d}",
                                    origin))
        used_paths.add(fact["path"])

    # pass 1: every range fact to >=2 cumulative v2+v3 records
    # before any fact gets a 3rd (plan section 2 ring)
    for k in sorted(by_key):
        if "range" not in (k[1] or ""):
            continue
        rem = remaining(k)
        while v2_count[k] < 2 and rem:
            emit(rem.pop(0))
            v2_count[k] += 1
    # pass 2: failure classes to the cumulative cap of 3
    for k in sorted(by_key):
        if not is_failure(k):
            continue
        rem = remaining(k)
        while v2_count[k] < cap(k) and rem:
            emit(rem.pop(0))
            v2_count[k] += 1
    # pass 3: remaining classes to the cumulative cap of 2
    for k in sorted(by_key):
        if is_failure(k):
            continue
        rem = remaining(k)
        while v2_count[k] < cap(k) and rem:
            emit(rem.pop(0))
            v2_count[k] += 1
    type_records = len(records)

    # when-form: every non-reserved when-bearing fact that still
    # lacks a when-record in train/dev (plan section 2)
    when_added = 0
    for k in sorted(by_key):
        if k in when_keys_td:
            continue
        cands = [f for f in by_key[k] if f["when"]
                 and f["path"] not in used_paths
                 and if_family(f["path"]) not in HOLDOUT_FAMS]
        if not cands:
            continue
        cands.sort(key=lambda f: f["path"])
        fact = cands[0]
        w = fact["when"][0]
        n[0] += 1
        records.append({
            "id": f"V3-PM-{n[0]:04d}", "family": "path_meaning",
            "origin": "generated_fact_v3",
            "scenario_group": f"pm3:{fact['path']}",
            "prompt": (f"In {MODEL}, under what condition may the "
                       f"leaf at {fact['path']} be present?"),
            "completion": (
                f"It carries a when condition on {w['on']}: "
                f"`{w['xpath']}` — the node is only valid when "
                f"that XPath evaluates to true."),
            "fact": {"path": fact["path"], "when": fact["when"],
                     "answer": {"when_on": w["on"],
                                "xpath": w["xpath"]}},
            "source": _pm_src(fact, prov), "validation": None})
        used_paths.add(fact["path"])
        when_added += 1

    # must-form (NEW in v3 — no v1/v2 record explains a must):
    # same shape as the when form.
    must_added = 0
    for k in sorted(by_key):
        if k in must_keys_td:
            continue
        cands = [f for f in by_key[k] if f["must"]
                 and f["path"] not in used_paths
                 and if_family(f["path"]) not in HOLDOUT_FAMS]
        if not cands:
            continue
        cands.sort(key=lambda f: f["path"])
        fact = cands[0]
        cond = fact["must"][0]
        n[0] += 1
        records.append({
            "id": f"V3-PM-{n[0]:04d}", "family": "path_meaning",
            "origin": "generated_fact_v3_must",
            "scenario_group": f"pm3:{fact['path']}",
            "prompt": (f"In {MODEL}, what must condition must "
                       f"hold for the leaf at {fact['path']}?"),
            "completion": (
                f"It carries a must condition on {cond['on']}: "
                f"`{cond['xpath']}` — the node is valid only when "
                f"that XPath evaluates to true."),
            "fact": {"path": fact["path"], "must": fact["must"],
                     "answer": {"must_on": cond["on"],
                                "must_xpath": cond["xpath"]}},
            "source": _pm_src(fact, prov), "validation": None})
        used_paths.add(fact["path"])
        must_added += 1

    # plain types: exhaust the deduped unconstrained pool (plan
    # section 2 raises v2's quotas to the full remaining pool)
    plain_keys_used = set()
    for fn in existing_files(include_holdout_v3=False):
        if fn == PARTS / "pm.jsonl":
            continue
        for r in load_jsonl(fn):
            if r["family"] == "path_meaning":
                plain_keys_used.add(
                    (norm_path(r["fact"]["path"]),
                     r["fact"].get("type")))
    plain_reps: dict[tuple, dict] = {}
    for f in facts.values():
        if _constrained(f):
            continue
        k = (norm_path(f["path"]), f["type"])
        if k not in plain_reps:
            plain_reps[k] = f
    plain_added = 0
    for k in sorted(plain_reps):
        if k in plain_keys_used or k in bench_keys:
            continue
        cands = [f for f in facts.values()
                 if (norm_path(f["path"]), f["type"]) == k
                 and f["path"] not in used_paths
                 and if_family(f["path"]) not in HOLDOUT_FAMS]
        if not cands:
            continue
        cands.sort(key=lambda f: (if_family(f["path"])
                                  in used_fams[k], f["path"]))
        emit(cands[0], origin="generated_fact_v3_plain")
        plain_added += 1

    records, deduped_n = dedupe(records, prompts)
    assign_splits(records, "path_meaning")
    write_part("pm", records)
    return report("pm", {
        "generated": type_records + when_added + must_added
        + plain_added, "type": type_records, "when": when_added,
        "must": must_added, "plain": plain_added,
        "deduped": deduped_n, "final": len(records),
        "train": sum(1 for r in records if r["split"] == "train"),
        "dev": sum(1 for r in records if r["split"] == "dev")})


# ---------------------------------------------------------------- IV

TE_IFS = ["1/0/25", "1/0/26", "2/0/25", "2/0/26"]
TE_LIST = "TenGigabitEthernet"


def _vio_inner(cases_):
    """Instance shape for the port-security violation choice
    (probed against yangson, 2026-11-27): protect/restrict/report
    are empty-type leaves sitting directly under violation
    (JSON [null]); shutdown is a case container holding an
    empty vlan leaf. Two cases present -> SchemaError at
    violation (member-not-allowed)."""
    inner = {}
    for c in cases_:
        if c == "shutdown":
            inner[S + "shutdown"] = {S + "vlan": [None]}
        else:
            inner[S + c] = [None]
    return inner


def _psx(inner: dict) -> dict:
    return {S + "port-security-config": {S + "port-security": inner}}


def _maxplain(v):
    return _psx({S + "maximum-address-config":
                 {S + "maximum": {S + "maximum": v}}})


def _maxcount(v):
    return _psx({S + "maxcount": [{S + "max-addresses": v}]})


def _maxvlan(children: dict):
    return _psx({S + "maximum-address-config":
                 {S + "maximum-vlan": children}})


def _maxvoice(children: dict):
    return _psx({S + "maximum-address-config":
                 {S + "maximum-vlan-voice": children}})


P_MAXVLAN = (f"{GR.SW}/port-security-config/port-security/"
             f"maximum-address-config/maximum-vlan")
P_MAXVOICE = (f"{GR.SW}/port-security-config/port-security/"
              f"maximum-address-config/maximum-vlan-voice")


def variantize3(data, cls, v, ifs, listkey):
    """gen_corpus.variantize generalized to another interface
    list (used for the TenGigabitEthernet subset)."""
    d = copy.deepcopy(data)
    root = d[ROOT]
    ges = root["interface"][listkey]
    if cls != "pattern_ifname":
        ges[0]["name"] = ifs[v % len(ifs)]
    if v % 3 == 1 and cls != "pattern_entry":
        root["vlan"][V + "configuration-entry"] = [
            {"vlan-id": "10"}, {"vlan-id": "20"}]
    if v % 3 == 2:
        ges.append(GR.ge(GR.sw_access(30),
                         name=ifs[(v + 3) % len(ifs)]))
        if cls != "pattern_entry":
            root["vlan"][V + "configuration-entry"] = [
                {"vlan-id": "10"}, {"vlan-id": "30"}]
    return d


def _to_te(data):
    d = copy.deepcopy(data)
    root = d[ROOT]
    root["interface"][TE_LIST] = root["interface"].pop(
        "GigabitEthernet")
    return d


def iv3_cases():
    """Fresh values per class, disjoint from the v1 and v2 case
    lists (enum valid values necessarily recur — the enum IS the
    value space; their scenarios are new). Every invalid breaks
    exactly one rule. Case dicts mirror gen_corpus.iv_cases, plus
    an optional per-case variant count for balance."""
    cases = []

    def add(cls, label, data, intended, rule, paths, frag,
            variants=3):
        cases.append({"cls": cls, "label": label, "data": data,
                      "intended": intended, "rule": rule,
                      "paths": paths, "frag": frag,
                      "variants": variants})

    F_ACC = ("Cisco-IOS-XE-switch:access/"
             "Cisco-IOS-XE-switch:vlan")
    R_ACC = ("access/vlan/vlan is a union of uint16 range 1..4094 "
             "and the enumeration {dynamic}; the value matches "
             "neither")
    for v in [3, 500, 999, 2000, 2048, 3000, 3999, 4092]:
        add("range_access", f"access VLAN {v}",
            G._acc(v, G.TRAIN_IFS[0]), "valid", None,
            [G.P_ACCESS], F_ACC)
    for v in [4097, 4098, 4500, 5000, 6000, 10000, -2, -100]:
        add("range_access", f"access VLAN {v}",
            G._acc(v, G.TRAIN_IFS[0]), "invalid", R_ACC,
            [G.P_ACCESS], F_ACC)
    R_UNI = ("access/vlan/vlan is a union of uint16 range 1..4094 "
             "and the enumeration {dynamic}; this JSON value "
             "matches no branch (a string is not the numeric "
             "branch)")
    for v in [3, 100, 2000, 4093, "dynamic"]:
        add("union_access", f"access VLAN {v!r}",
            G._acc(v, G.TRAIN_IFS[0]), "valid", None,
            [G.P_ACCESS], F_ACC)
    for v in ["4097", "0", "Dynamic", "AUTO", "4094 "]:
        add("union_access", f"access VLAN {v!r}",
            G._acc(v, G.TRAIN_IFS[0]), "invalid", R_UNI,
            [G.P_ACCESS], F_ACC)
    R_ENC = ("trunk/encapsulation is an enumeration of "
             "{dot1q, isl, negotiate}")
    for v in ["dot1q", "isl", "negotiate"]:
        add("enum_encap", f"encapsulation {v}",
            G._trk({"encapsulation": v}, G.TRAIN_IFS[0]), "valid",
            None, [G.P_ENCAP], "encapsulation", variants=6)
    for v in ["Dot1q", "802.1Q", "dot-1q", "dot1q ", "DOT-1Q", ""]:
        add("enum_encap", f"encapsulation {v}",
            G._trk({"encapsulation": v}, G.TRAIN_IFS[0]), "invalid",
            R_ENC, [G.P_ENCAP], "encapsulation")
    R_NAT = "trunk/native/vlan/vlan-id is uint16 range 1..4094"
    for v in [2, 100, 555, 1000, 2048, 3000, 4092]:
        add("range_native", f"native VLAN {v}",
            G._trk({"native": {"vlan": {"vlan-id": v}}},
                   G.TRAIN_IFS[0]), "valid", None,
            [G.P_NATIVE], "native")
    for v in [4097, 4098, 5000, -1, 10000, 8192]:
        add("range_native", f"native VLAN {v}",
            G._trk({"native": {"vlan": {"vlan-id": v}}},
                   G.TRAIN_IFS[0]), "invalid", R_NAT,
            [G.P_NATIVE], "native")
    R_AGE = "port-security aging/time is uint16 range 1..1440"
    for v in [30, 90, 120, 300, 1000, 1438]:
        add("range_aging", f"aging time {v}",
            G._acc(10, G.TRAIN_IFS[0], GR.ps(aging={S + "time": v})),
            "valid", None, [G.P_AGING + "/time"], "aging")
    for v in [1443, 1500, 5000, 10000, -5, 2881]:
        add("range_aging", f"aging time {v}",
            G._acc(10, G.TRAIN_IFS[0], GR.ps(aging={S + "time": v})),
            "invalid", R_AGE, [G.P_AGING + "/time"], "aging")
    R_AGT = ("port-security aging/type is an enumeration of "
             "{absolute, inactivity}")
    for v in ["absolute", "inactivity"]:
        add("enum_aging", f"aging type {v}",
            G._acc(10, G.TRAIN_IFS[0], GR.ps(aging={S + "type": v})),
            "valid", None, [G.P_AGING + "/type"], "aging",
            variants=6)
    for v in ["ABSOLUTE", "Inactivity", "inactivity ", "static",
              "absolute ", "in-active"]:
        add("enum_aging", f"aging type {v}",
            G._acc(10, G.TRAIN_IFS[0], GR.ps(aging={S + "type": v})),
            "invalid", R_AGT, [G.P_AGING + "/type"], "aging",
            variants=2)
    R_ENT = ("configuration-entry/vlan-id is a string whose "
             "pattern encodes VLAN IDs 1..4094 (and range lists); "
             "the key does not match")
    for v in ["1", "4090-4094", "1,2,3", "100,200-300", "3999",
              "2049"]:
        add("pattern_entry", f"entry key {v!r}",
            GR.doc([GR.ge(GR.sw_access(10), name=G.TRAIN_IFS[0])],
                   [{"vlan-id": v}]), "valid", None,
            [G.P_ENTRY], "configuration-entry")
    for v in ["4094-4095", "0-10", "1-", "-100", "1,,2", "40940",
              " 100", "4100", "3.5"]:
        add("pattern_entry", f"entry key {v!r}",
            GR.doc([GR.ge(GR.sw_access(10), name=G.TRAIN_IFS[0])],
                   [{"vlan-id": v}]), "invalid", R_ENT,
            [G.P_ENTRY], "configuration-entry", variants=2)
    R_IFN = ("the GigabitEthernet list key 'name' must match the "
             "pattern (0|[1-9][0-9]*)(/(0|[1-9][0-9]*))*(\\.[0-9]*)? "
             "— numeric slot/port form only")
    P_NAME = "/native/interface/GigabitEthernet/name"
    for v in ["2/0/24", "0/0/0", "3/1/7.100", "10/1/1"]:
        add("pattern_ifname", f"interface name {v!r}",
            G._acc(10, v), "valid", None, [P_NAME], "GigabitEthernet")
    for v in ["1/0/01", "1 /0/1", "1/0/-1", "g1/0/1", "1/0/",
              "1..0/1"]:
        add("pattern_ifname", f"interface name {v!r}",
            G._acc(10, v), "invalid", R_IFN, [P_NAME],
            "GigabitEthernet", variants=2)
    R_CHO = ("mode/mode-choice is a YANG choice: at most one case "
             "may be present; this instance sets two")

    def mode_data(cases_):
        mode = {}
        for c in cases_:
            if c == "dynamic":
                mode[S + "dynamic"] = "auto"
            else:
                mode[S + c] = {}
        sw = {S + "mode": mode,
              S + "access": {S + "vlan": {S + "vlan": 10}}}
        return GR.doc([GR.ge(sw, name=G.TRAIN_IFS[0])],
                      [{"vlan-id": "10"}])

    for cases_ in [["access"], ["trunk"], ["dynamic"],
                   ["dot1q-tunnel"]]:
        add("choice_mode", f"mode cases {cases_}",
            mode_data(cases_), "valid", None, [G.P_MODE],
            "Cisco-IOS-XE-switch:mode")
    for cases_ in [["dot1q-tunnel", "dynamic"],
                   ["access", "trunk", "dot1q-tunnel"],
                   ["access", "trunk", "dynamic"],
                   ["access", "trunk", "dynamic", "dot1q-tunnel"]]:
        add("choice_mode", f"mode cases {cases_}",
            mode_data(cases_), "invalid", R_CHO, [G.P_MODE],
            "Cisco-IOS-XE-switch:mode")
    R_WHEN = ("maximum-vlan-access carries when "
              "'../../../../mode/access' — it requires the mode "
              "choice's access case")
    for v in [50, 500]:
        add("when_maxacc", f"maximum-vlan-access {v} with mode "
            f"access",
            G._acc(10, G.TRAIN_IFS[0],
                   GR.ps(max_access={S + "maximum": v,
                                     S + "vlan": "access"})),
            "valid", None, [G.P_MAXACC], "maximum-addres")
    add("when_maxacc", "maximum-vlan-access 50 with mode trunk",
        G._trk(None, G.TRAIN_IFS[0],
               GR.ps(max_access={S + "maximum": 50,
                                 S + "vlan": "access"})),
        "invalid", R_WHEN, [G.P_MAXACC], "maximum-addres")
    dyn_sw = {S + "mode": {S + "dynamic": "auto"},
              S + "access": {S + "vlan": {S + "vlan": 10}}}
    dyn_sw.update(GR.ps(max_access={S + "maximum": 50,
                                    S + "vlan": "access"}))
    add("when_maxacc", "maximum-vlan-access 50 with mode dynamic",
        GR.doc([GR.ge(dyn_sw, name=G.TRAIN_IFS[0])],
               [{"vlan-id": "10"}]),
        "invalid", R_WHEN, [G.P_MAXACC], "maximum-addres")
    R_ALL = ("trunk allowed vlans is a union of uint16 range "
             "1..4094 and string; this non-string value is outside "
             "1..4094, so it matches neither branch")
    for v in ["1-10", 1, "none", "4094,4095", 2000]:
        add("union_allowed", f"allowed vlans {v!r}",
            G._trk({"allowed": {"vlan": {"vlans": v}}},
                   G.TRAIN_IFS[0]), "valid", None,
            [G.P_ALLOWED], "vlans")
    for v in [4097, -1, 8192, 10000]:
        add("union_allowed", f"allowed vlans {v!r}",
            G._trk({"allowed": {"vlan": {"vlans": v}}},
                   G.TRAIN_IFS[0]), "invalid", R_ALL,
            [G.P_ALLOWED], "vlans")
    R_VOI = ("voice/vlan/vlan is a union of the enumeration "
             "{dot1p, none, untagged}, uint16 range 1..4094, and "
             "string; this non-string value is outside 1..4094, "
             "so it matches no branch")
    for v in [1, "dot1p", 2000, "untagged"]:
        sw = GR.sw_access(10)
        sw[S + "voice"] = {S + "vlan": {S + "vlan": v}}
        add("union_voice", f"voice VLAN {v!r}",
            GR.doc([GR.ge(sw, name=G.TRAIN_IFS[0])],
                   [{"vlan-id": "10"}]), "valid", None,
            [G.P_VOICE], "voice")
    for v in [4097, -2, 5000, 8192]:
        sw = GR.sw_access(10)
        sw[S + "voice"] = {S + "vlan": {S + "vlan": v}}
        add("union_voice", f"voice VLAN {v!r}",
            GR.doc([GR.ge(sw, name=G.TRAIN_IFS[0])],
                   [{"vlan-id": "10"}]), "invalid", R_VOI,
            [G.P_VOICE], "voice")

    # ---- v3 classes (the classes v2 could not build + probes)
    R_MAXP = ("port-security maximum-address-config/maximum/"
              "maximum is uint16 range 1..4096")
    for v in [1, 100, 2000, 4096]:
        add("range_maximum", f"maximum addresses {v}",
            G._acc(10, G.TRAIN_IFS[0], _maxplain(v)), "valid", None,
            [P_MAXPLAIN], "maximum/maximum")
    for v in [0, 4097, 4098, 8192]:
        add("range_maximum", f"maximum addresses {v}",
            G._acc(10, G.TRAIN_IFS[0], _maxplain(v)), "invalid",
            R_MAXP, [P_MAXPLAIN], "maximum/maximum")
    R_MAXVA = ("port-security maximum-vlan-access/maximum is "
               "uint16 range 1..4096")
    for v in [4096, 50, 1]:
        add("range_maxva", f"maximum-vlan-access maximum {v}",
            G._acc(10, G.TRAIN_IFS[0],
                   GR.ps(max_access={S + "maximum": v,
                                     S + "vlan": "access"})),
            "valid", None, [P_MAXVA], "maximum-vlan-access")
    for v in [4097, 0, 5000]:
        add("range_maxva", f"maximum-vlan-access maximum {v}",
            G._acc(10, G.TRAIN_IFS[0],
                   GR.ps(max_access={S + "maximum": v,
                                     S + "vlan": "access"})),
            "invalid", R_MAXVA, [P_MAXVA], "maximum-vlan-access")
    R_MAXC = ("port-security maxcount/max-addresses is uint16 "
              "range 1..4097")
    for v in [4097, 1, 2000, 4096]:
        add("range_maxcount", f"maxcount max-addresses {v}",
            G._acc(10, G.TRAIN_IFS[0], _maxcount(v)), "valid", None,
            [P_MAXCOUNT], "max-addresses")
    for v in [4098, 0, 5000, -1]:
        add("range_maxcount", f"maxcount max-addresses {v}",
            G._acc(10, G.TRAIN_IFS[0], _maxcount(v)), "invalid",
            R_MAXC, [P_MAXCOUNT], "max-addresses")
    R_EMV = ("port-security maximum-vlan-access/vlan is an "
             "enumeration of {access}")
    add("enum_maxvlan", "maximum-vlan-access vlan 'access'",
        G._acc(10, G.TRAIN_IFS[0],
               GR.ps(max_access={S + "maximum": 100,
                                 S + "vlan": "access"})),
        "valid", None, [G.P_MAXACC + "/vlan"], "maximum-vlan-access",
        variants=6)
    for v in ["voice", "trunk", "vlan"]:
        add("enum_maxvlan", f"maximum-vlan-access vlan {v!r}",
            G._acc(10, G.TRAIN_IFS[0],
                   GR.ps(max_access={S + "maximum": 100,
                                     S + "vlan": v})),
            "invalid", R_EMV, [G.P_MAXACC + "/vlan"],
            "maximum-vlan-access", variants=2)
    R_VIO = ("port-security violation is a YANG choice: at most "
             "one case (protect, restrict, report, shutdown) may "
             "be present; this instance sets more than one")

    def vio_data(cases_):
        return G._acc(10, G.TRAIN_IFS[0], _psx(
            {S + "violation": _vio_inner(cases_)}))

    for c in ["protect", "restrict", "report", "shutdown"]:
        add("violation_choice", f"violation case {c}",
            vio_data([c]), "valid", None, [P_VIOLATION],
            "violation")
    for cases_ in [["protect", "restrict"], ["protect", "report"],
                   ["restrict", "shutdown"],
                   ["protect", "restrict", "report"],
                   ["shutdown", "report"]]:
        add("violation_choice", f"violation cases {cases_}",
            vio_data(cases_), "invalid", R_VIO, [P_VIOLATION],
            "violation")
    R_MAC = ("switchport port-security hw-address must match "
             "the cisco-mac-address-type pattern: three groups "
             "of hexadecimal digits separated by dots")

    def mac_data(v):
        return G._acc(10, G.TRAIN_IFS[0], _psx(
            {S + "mac-address-config": {S + "mac-address":
             {S + "hw-address-access": [{S + "hw-address": v}]}}}))

    for v in ["0011.2233.4455", "0a1b.2c3d.4e5f", "aaaa.bbbb.cccc",
              "0123.4567.89ab"]:
        add("mac_pattern", f"hw-address {v}", mac_data(v), "valid",
            None, [P_HWD], "hw-address")
    for v in ["00:11:22:33:44:55", "0011.2233",
              "0011.2233.4455.6677", "0011.2233.445g", "0011..4455",
              "001122334455", "0011.2233.4455."]:
        add("mac_pattern", f"hw-address {v}", mac_data(v),
            "invalid", R_MAC, [P_HWD], "hw-address", variants=2)
    R_WMV = ("maximum-vlan carries when "
             "'../../../../../../mode/trunk' — it requires the "
             "mode choice's trunk case")
    add("when_maxvlan", "maximum-vlan with mode trunk",
        G._trk(None, G.TRAIN_IFS[0],
               _maxvlan({S + "maximum": 200, S + "vlan": "vlan"})),
        "valid", None, [P_MAXVLAN], "maximum-address-config")
    dyn2 = {S + "mode": {S + "dynamic": "auto"}, S + "trunk": {}}
    dyn2.update(_maxvlan({S + "maximum": 200, S + "vlan": "vlan"}))
    add("when_maxvlan", "maximum-vlan with mode dynamic",
        GR.doc([GR.ge(dyn2, name=G.TRAIN_IFS[0])],
               [{"vlan-id": "10"}]),
        "invalid", R_WMV, [P_MAXVLAN], "maximum-address-config")
    R_DYN = "mode/dynamic is an enumeration of {auto, desirable}"

    def dyn_data(v):
        sw = {S + "mode": {S + "dynamic": v},
              S + "access": {S + "vlan": {S + "vlan": 10}}}
        return GR.doc([GR.ge(sw, name=G.TRAIN_IFS[0])],
                      [{"vlan-id": "10"}])

    for v in ["auto", "desirable"]:
        add("enum_dynamic", f"dynamic mode {v}", dyn_data(v),
            "valid", None, [P_DYNAMIC], "dynamic", variants=4)
    for v in ["on", "AUTO", "desire", "Desirable"]:
        add("enum_dynamic", f"dynamic mode {v}", dyn_data(v),
            "invalid", R_DYN, [P_DYNAMIC], "dynamic", variants=2)
    return cases


def must_cases():
    """must_sibling: the maximum/vlan sibling pairs under
    maximum-address-config carry must conditions on each other
    ('../vlan' on maximum, '../maximum' on vlan). Validated by a
    dedicated pass: yangson raises SemanticError, which is not in
    gen_corpus.validate_all's REJECTION_CLASSES (documented,
    class-scoped extension; verdicts remain yangson-only)."""
    cases = []

    def add(label, data, intended, rule, paths, variants=3):
        cases.append({"cls": "must_sibling", "label": label,
                      "data": data, "intended": intended,
                      "rule": rule, "paths": paths,
                      "frag": "maximum-address-config",
                      "variants": variants})

    R_A = ("port-security maximum-vlan-access/maximum and "
           "maximum-vlan-access/vlan carry must conditions on "
           "each other ('../vlan' and '../maximum') — each "
           "requires the other to be present")
    R_T = ("port-security maximum-vlan/maximum and "
           "maximum-vlan/vlan carry must conditions on each "
           "other ('../vlan' and '../maximum') — each requires "
           "the other to be present")
    add("maximum-vlan-access with maximum only",
        G._acc(10, G.TRAIN_IFS[0],
               GR.ps(max_access={S + "maximum": 100})),
        "invalid", R_A, [G.P_MAXACC])
    add("maximum-vlan-access with vlan only",
        G._acc(10, G.TRAIN_IFS[0],
               GR.ps(max_access={S + "vlan": "access"})),
        "invalid", R_A, [G.P_MAXACC])
    add("maximum-vlan-access with maximum and vlan",
        G._acc(10, G.TRAIN_IFS[0],
               GR.ps(max_access={S + "maximum": 100,
                                 S + "vlan": "access"})),
        "valid", None, [G.P_MAXACC], variants=5)
    add("maximum-vlan with maximum only",
        G._trk(None, G.TRAIN_IFS[0],
               _maxvlan({S + "maximum": 200})),
        "invalid", R_T, [P_MAXVLAN])
    add("maximum-vlan with vlan only",
        G._trk(None, G.TRAIN_IFS[0],
               _maxvlan({S + "vlan": "vlan"})),
        "invalid", R_T, [P_MAXVLAN])
    add("maximum-vlan with maximum and vlan",
        G._trk(None, G.TRAIN_IFS[0],
               _maxvlan({S + "maximum": 200, S + "vlan": "vlan"})),
        "valid", None, [P_MAXVLAN], variants=5)
    voice_sw = GR.sw_access(10)
    voice_sw[S + "voice"] = {S + "vlan": {S + "vlan": 30}}
    # NB: no maximum-only voice case — yangson ACCEPTS
    # maximum-vlan-voice/maximum without its vlan sibling
    # (probed 2026-11-27): the voice pair carries no must, so
    # an intended-invalid case here would be a false label.
    d2 = GR.doc([GR.ge(voice_sw, name=G.TRAIN_IFS[0])],
                [{"vlan-id": "10"}, {"vlan-id": "30"}])
    d2[ROOT]["interface"]["GigabitEthernet"][0][
        "switchport-config"]["switchport"].update(
            _maxvoice({S + "maximum": 50, S + "vlan": "voice"}))
    add("maximum-vlan-voice with maximum and vlan", d2, "valid",
        None, [P_MAXVOICE], variants=5)
    return cases


def te_subset_cases():
    """A TenGigabitEthernet slice of the core classes (the v1/v2
    corpora are GigabitEthernet-only)."""
    cases = []

    def add(cls, label, data, intended, rule, paths, frag):
        cases.append({"cls": cls, "label": label,
                      "data": _to_te(data), "intended": intended,
                      "rule": rule, "paths": paths, "frag": frag,
                      "variants": 2, "te": True})

    F_ACC = ("Cisco-IOS-XE-switch:access/"
             "Cisco-IOS-XE-switch:vlan")
    add("range_access", "access VLAN 100 (TenGigabitEthernet)",
        G._acc(100, TE_IFS[0]), "valid", None, [G.P_ACCESS], F_ACC)
    add("range_access", "access VLAN 4097 (TenGigabitEthernet)",
        G._acc(4097, TE_IFS[0]), "invalid",
        "access/vlan/vlan is a union of uint16 range 1..4094 and "
        "the enumeration {dynamic}; the value matches neither",
        [G.P_ACCESS], F_ACC)
    add("union_access", "access VLAN 'dynamic' "
        "(TenGigabitEthernet)", G._acc("dynamic", TE_IFS[0]),
        "valid", None, [G.P_ACCESS], F_ACC)
    add("union_access", "access VLAN '4097' (TenGigabitEthernet)",
        G._acc("4097", TE_IFS[0]), "invalid",
        "access/vlan/vlan is a union of uint16 range 1..4094 and "
        "the enumeration {dynamic}; this JSON value matches no "
        "branch (a string is not the numeric branch)",
        [G.P_ACCESS], F_ACC)
    add("range_native", "native VLAN 100 (TenGigabitEthernet)",
        G._trk({"native": {"vlan": {"vlan-id": 100}}}, TE_IFS[0]),
        "valid", None, [G.P_NATIVE], "native")
    add("range_native", "native VLAN 4097 (TenGigabitEthernet)",
        G._trk({"native": {"vlan": {"vlan-id": 4097}}}, TE_IFS[0]),
        "invalid", "trunk/native/vlan/vlan-id is uint16 range "
        "1..4094", [G.P_NATIVE], "native")
    add("enum_encap", "encapsulation dot1q (TenGigabitEthernet)",
        G._trk({"encapsulation": "dot1q"}, TE_IFS[0]), "valid",
        None, [G.P_ENCAP], "encapsulation")
    add("enum_encap", "encapsulation Dot1q (TenGigabitEthernet)",
        G._trk({"encapsulation": "Dot1q"}, TE_IFS[0]), "invalid",
        "trunk/encapsulation is an enumeration of "
        "{dot1q, isl, negotiate}", [G.P_ENCAP], "encapsulation")
    mode = {S + "mode": {S + "access": {}, S + "trunk": {}},
            S + "access": {S + "vlan": {S + "vlan": 10}}}
    add("choice_mode", "mode cases ['access', 'trunk'] "
        "(TenGigabitEthernet)",
        GR.doc([GR.ge(mode, name=TE_IFS[0])], [{"vlan-id": "10"}]),
        "invalid",
        "mode/mode-choice is a YANG choice: at most one case may "
        "be present; this instance sets two",
        [G.P_MODE], "Cisco-IOS-XE-switch:mode")
    add("when_maxacc", "maximum-vlan-access 100 with mode access "
        "(TenGigabitEthernet)",
        G._acc(10, TE_IFS[0],
               GR.ps(max_access={S + "maximum": 100,
                                 S + "vlan": "access"})),
        "valid", None, [G.P_MAXACC], "maximum-addres")
    add("when_maxacc", "maximum-vlan-access 100 with mode trunk "
        "(TenGigabitEthernet)",
        G._trk(None, TE_IFS[0],
               GR.ps(max_access={S + "maximum": 100,
                                 S + "vlan": "access"})),
        "invalid",
        "maximum-vlan-access carries when "
        "'../../../../mode/access' — it requires the mode "
        "choice's access case",
        [G.P_MAXACC], "maximum-addres")
    return cases


def _to_list(data, listkey):
    d = copy.deepcopy(data)
    root = d[ROOT]
    root["interface"][listkey] = root["interface"].pop(
        "GigabitEthernet")
    return d


def iv3_cases_x():
    """IV expansion (batch 2b): fresh values audited disjoint
    from the v1, v2 and iv3 case lists — completing the plan's
    value rings and adding audited interior values — so the IV
    additions approach the plan's protected >=900 train floor.
    Same discipline: every invalid breaks exactly one rule and
    yangson arbitrates every label."""
    cases = []

    def add(cls, label, data, intended, rule, paths, frag,
            variants=3):
        cases.append({"cls": cls, "label": label, "data": data,
                      "intended": intended, "rule": rule,
                      "paths": paths, "frag": frag,
                      "variants": variants})

    F_ACC = ("Cisco-IOS-XE-switch:access/"
             "Cisco-IOS-XE-switch:vlan")
    R_ACC = ("access/vlan/vlan is a union of uint16 range 1..4094 "
             "and the enumeration {dynamic}; the value matches "
             "neither")
    for v in [4, 10, 25, 1000, 2500, 4000, 4090, 4091]:
        add("range_access", f"access VLAN {v}",
            G._acc(v, G.TRAIN_IFS[0]), "valid", None,
            [G.P_ACCESS], F_ACC)
    for v in [-3, 4099, 4100, 8191, 16384, 65535]:
        add("range_access", f"access VLAN {v}",
            G._acc(v, G.TRAIN_IFS[0]), "invalid", R_ACC,
            [G.P_ACCESS], F_ACC)
    R_UNI = ("access/vlan/vlan is a union of uint16 range 1..4094 "
             "and the enumeration {dynamic}; this JSON value "
             "matches no branch (a string is not the numeric "
             "branch)")
    for v in [7, 250, 4000]:
        add("union_access", f"access VLAN {v!r}",
            G._acc(v, G.TRAIN_IFS[0]), "valid", None,
            [G.P_ACCESS], F_ACC)
    for v in ["4098", "5000", "DYNAMIC ", "none "]:
        add("union_access", f"access VLAN {v!r}",
            G._acc(v, G.TRAIN_IFS[0]), "invalid", R_UNI,
            [G.P_ACCESS], F_ACC)
    R_ENC = ("trunk/encapsulation is an enumeration of "
             "{dot1q, isl, negotiate}")
    for v in ["ISL", "Negotiate", "dot-1Q", "dot1q.", "negotiate "]:
        add("enum_encap", f"encapsulation {v}",
            G._trk({"encapsulation": v}, G.TRAIN_IFS[0]), "invalid",
            R_ENC, [G.P_ENCAP], "encapsulation", variants=2)
    R_NAT = "trunk/native/vlan/vlan-id is uint16 range 1..4094"
    for v in [3, 500, 2500, 4000, 4090, 4091]:
        add("range_native", f"native VLAN {v}",
            G._trk({"native": {"vlan": {"vlan-id": v}}},
                   G.TRAIN_IFS[0]), "valid", None,
            [G.P_NATIVE], "native")
    for v in [-2, 4099, 4100, 65535, 20000]:
        add("range_native", f"native VLAN {v}",
            G._trk({"native": {"vlan": {"vlan-id": v}}},
                   G.TRAIN_IFS[0]), "invalid", R_NAT,
            [G.P_NATIVE], "native")
    R_AGE = "port-security aging/time is uint16 range 1..1440"
    for v in [3, 45, 240, 600, 1437]:
        add("range_aging", f"aging time {v}",
            G._acc(10, G.TRAIN_IFS[0], GR.ps(aging={S + "time": v})),
            "valid", None, [G.P_AGING + "/time"], "aging")
    for v in [-1, 1444, 1445, 4320, 9999]:
        add("range_aging", f"aging time {v}",
            G._acc(10, G.TRAIN_IFS[0], GR.ps(aging={S + "time": v})),
            "invalid", R_AGE, [G.P_AGING + "/time"], "aging")
    R_AGT = ("port-security aging/type is an enumeration of "
             "{absolute, inactivity}")
    for v in ["Absolute ", "INACTIVITY", "absolute-time", "aging"]:
        add("enum_aging", f"aging type {v}",
            G._acc(10, G.TRAIN_IFS[0], GR.ps(aging={S + "type": v})),
            "invalid", R_AGT, [G.P_AGING + "/type"], "aging",
            variants=2)
    R_ENT = ("configuration-entry/vlan-id is a string whose "
             "pattern encodes VLAN IDs 1..4094 (and range lists); "
             "the key does not match")
    for v in ["1-4094", "2-3", "5,10-20", "1000-2000"]:
        add("pattern_entry", f"entry key {v!r}",
            GR.doc([GR.ge(GR.sw_access(10), name=G.TRAIN_IFS[0])],
                   [{"vlan-id": v}]), "valid", None,
            [G.P_ENTRY], "configuration-entry")
    for v in ["4095-4096", "1-4096", "2-", "1-2-3", "1,4095",
              "01"]:
        add("pattern_entry", f"entry key {v!r}",
            GR.doc([GR.ge(GR.sw_access(10), name=G.TRAIN_IFS[0])],
                   [{"vlan-id": v}]), "invalid", R_ENT,
            [G.P_ENTRY], "configuration-entry", variants=2)
    R_IFN = ("the GigabitEthernet list key 'name' must match the "
             "pattern (0|[1-9][0-9]*)(/(0|[1-9][0-9]*))*(\\.[0-9]*)? "
             "— numeric slot/port form only")
    P_NAME = "/native/interface/GigabitEthernet/name"
    for v in ["0/1/0", "12/0/1", "1/0/1.0", "255/0/1"]:
        add("pattern_ifname", f"interface name {v!r}",
            G._acc(10, v), "valid", None, [P_NAME], "GigabitEthernet")
    for v in ["1/0/1..", "1//0", "1/0/1/", "0x1/0/1"]:
        add("pattern_ifname", f"interface name {v!r}",
            G._acc(10, v), "invalid", R_IFN, [P_NAME],
            "GigabitEthernet", variants=2)
    R_CHO = ("mode/mode-choice is a YANG choice: at most one case "
             "may be present; this instance sets two")

    def mode_data(cases_):
        mode = {}
        for c in cases_:
            if c == "dynamic":
                mode[S + "dynamic"] = "auto"
            else:
                mode[S + c] = {}
        sw = {S + "mode": mode,
              S + "access": {S + "vlan": {S + "vlan": 10}}}
        return GR.doc([GR.ge(sw, name=G.TRAIN_IFS[0])],
                      [{"vlan-id": "10"}])

    for cases_ in [["access", "dynamic", "dot1q-tunnel"],
                   ["trunk", "dynamic", "dot1q-tunnel"]]:
        add("choice_mode", f"mode cases {cases_}",
            mode_data(cases_), "invalid", R_CHO, [G.P_MODE],
            "Cisco-IOS-XE-switch:mode")
    R_WHEN = ("maximum-vlan-access carries when "
              "'../../../../mode/access' — it requires the mode "
              "choice's access case")
    for v in [1, 1000, 4096]:
        add("when_maxacc", f"maximum-vlan-access {v} with mode "
            f"access",
            G._acc(10, G.TRAIN_IFS[0],
                   GR.ps(max_access={S + "maximum": v,
                                     S + "vlan": "access"})),
            "valid", None, [G.P_MAXACC], "maximum-addres")
    for v in [1000, 4096]:
        add("when_maxacc", f"maximum-vlan-access {v} with mode "
            f"trunk",
            G._trk(None, G.TRAIN_IFS[0],
                   GR.ps(max_access={S + "maximum": v,
                                     S + "vlan": "access"})),
            "invalid", R_WHEN, [G.P_MAXACC], "maximum-addres")
    tq_sw = {S + "mode": {S + "dot1q-tunnel": {}},
             S + "access": {S + "vlan": {S + "vlan": 10}}}
    tq_sw.update(GR.ps(max_access={S + "maximum": 50,
                                   S + "vlan": "access"}))
    add("when_maxacc", "maximum-vlan-access 50 with mode "
        "dot1q-tunnel",
        GR.doc([GR.ge(tq_sw, name=G.TRAIN_IFS[0])],
               [{"vlan-id": "10"}]),
        "invalid", R_WHEN, [G.P_MAXACC], "maximum-addres")
    R_ALL = ("trunk allowed vlans is a union of uint16 range "
             "1..4094 and string; this non-string value is outside "
             "1..4094, so it matches neither branch")
    for v in ["2-5", "100-200", 4093, "4094"]:
        add("union_allowed", f"allowed vlans {v!r}",
            G._trk({"allowed": {"vlan": {"vlans": v}}},
                   G.TRAIN_IFS[0]), "valid", None,
            [G.P_ALLOWED], "vlans")
    for v in [4098, -2, 65535]:
        add("union_allowed", f"allowed vlans {v!r}",
            G._trk({"allowed": {"vlan": {"vlans": v}}},
                   G.TRAIN_IFS[0]), "invalid", R_ALL,
            [G.P_ALLOWED], "vlans")
    R_VOI = ("voice/vlan/vlan is a union of the enumeration "
             "{dot1p, none, untagged}, uint16 range 1..4094, and "
             "string; this non-string value is outside 1..4094, "
             "so it matches no branch")
    for v in [2, 100, 4093, "voice100"]:
        sw = GR.sw_access(10)
        sw[S + "voice"] = {S + "vlan": {S + "vlan": v}}
        add("union_voice", f"voice VLAN {v!r}",
            GR.doc([GR.ge(sw, name=G.TRAIN_IFS[0])],
                   [{"vlan-id": "10"}]), "valid", None,
            [G.P_VOICE], "voice")
    for v in [4098, -3, 10000, 65535]:
        sw = GR.sw_access(10)
        sw[S + "voice"] = {S + "vlan": {S + "vlan": v}}
        add("union_voice", f"voice VLAN {v!r}",
            GR.doc([GR.ge(sw, name=G.TRAIN_IFS[0])],
                   [{"vlan-id": "10"}]), "invalid", R_VOI,
            [G.P_VOICE], "voice")
    R_MAXP = ("port-security maximum-address-config/maximum/"
              "maximum is uint16 range 1..4096")
    for v in [2, 1000, 4095]:
        add("range_maximum", f"maximum addresses {v}",
            G._acc(10, G.TRAIN_IFS[0], _maxplain(v)), "valid", None,
            [P_MAXPLAIN], "maximum/maximum")
    for v in [-2, 4099, 10000]:
        add("range_maximum", f"maximum addresses {v}",
            G._acc(10, G.TRAIN_IFS[0], _maxplain(v)), "invalid",
            R_MAXP, [P_MAXPLAIN], "maximum/maximum")
    R_MAXVA = ("port-security maximum-vlan-access/maximum is "
               "uint16 range 1..4096")
    for v in [100, 1000, 4095]:
        add("range_maxva", f"maximum-vlan-access maximum {v}",
            G._acc(10, G.TRAIN_IFS[0],
                   GR.ps(max_access={S + "maximum": v,
                                     S + "vlan": "access"})),
            "valid", None, [P_MAXVA], "maximum-vlan-access")
    for v in [-1, 4099, 8192]:
        add("range_maxva", f"maximum-vlan-access maximum {v}",
            G._acc(10, G.TRAIN_IFS[0],
                   GR.ps(max_access={S + "maximum": v,
                                     S + "vlan": "access"})),
            "invalid", R_MAXVA, [P_MAXVA], "maximum-vlan-access")
    R_MAXC = ("port-security maxcount/max-addresses is uint16 "
              "range 1..4097")
    for v in [2, 1000, 4095]:
        add("range_maxcount", f"maxcount max-addresses {v}",
            G._acc(10, G.TRAIN_IFS[0], _maxcount(v)), "valid", None,
            [P_MAXCOUNT], "max-addresses")
    for v in [-2, 4099, 4100]:
        add("range_maxcount", f"maxcount max-addresses {v}",
            G._acc(10, G.TRAIN_IFS[0], _maxcount(v)), "invalid",
            R_MAXC, [P_MAXCOUNT], "max-addresses")
    R_EMV = ("port-security maximum-vlan-access/vlan is an "
             "enumeration of {access}")
    for v in ["Access", "ACCESS", "access ", "none", ""]:
        add("enum_maxvlan", f"maximum-vlan-access vlan {v!r}",
            G._acc(10, G.TRAIN_IFS[0],
                   GR.ps(max_access={S + "maximum": 100,
                                     S + "vlan": v})),
            "invalid", R_EMV, [G.P_MAXACC + "/vlan"],
            "maximum-vlan-access", variants=2)
    R_VIO = ("port-security violation is a YANG choice: at most "
             "one case (protect, restrict, report, shutdown) may "
             "be present; this instance sets more than one")

    def vio_data(cases_):
        return G._acc(10, G.TRAIN_IFS[0], _psx(
            {S + "violation": _vio_inner(cases_)}))

    for cases_ in [["restrict", "report"],
                   ["restrict", "report", "shutdown"],
                   ["protect", "restrict", "shutdown"],
                   ["protect", "restrict", "report", "shutdown"]]:
        add("violation_choice", f"violation cases {cases_}",
            vio_data(cases_), "invalid", R_VIO, [P_VIOLATION],
            "violation")
    R_MAC = ("switchport port-security hw-address must match "
             "the cisco-mac-address-type pattern: three groups "
             "of hexadecimal digits separated by dots")

    def mac_data(v):
        return G._acc(10, G.TRAIN_IFS[0], _psx(
            {S + "mac-address-config": {S + "mac-address":
             {S + "hw-address-access": [{S + "hw-address": v}]}}}))

    for v in ["FFFF.FFFF.FFFF", "0123.4567.89AB", "dead.beef.cafe",
              "1.2.3"]:
        add("mac_pattern", f"hw-address {v}", mac_data(v), "valid",
            None, [P_HWD], "hw-address", variants=2)
    for v in ["0011-2233-4455", "gg11.2233.4455",
              "0011.2233.4455 "]:
        add("mac_pattern", f"hw-address {v}", mac_data(v),
            "invalid", R_MAC, [P_HWD], "hw-address", variants=2)
    R_WMV = ("maximum-vlan carries when "
             "'../../../../../../mode/trunk' — it requires the "
             "mode choice's trunk case")
    for v in [100, 4096]:
        add("when_maxvlan", f"maximum-vlan {v} with mode trunk",
            G._trk(None, G.TRAIN_IFS[0],
                   _maxvlan({S + "maximum": v, S + "vlan": "vlan"})),
            "valid", None, [P_MAXVLAN], "maximum-address-config")
    acc2 = GR.sw_access(10)
    acc2.update(_maxvlan({S + "maximum": 100, S + "vlan": "vlan"}))
    add("when_maxvlan", "maximum-vlan 100 with mode access",
        GR.doc([GR.ge(acc2, name=G.TRAIN_IFS[0])],
               [{"vlan-id": "10"}]),
        "invalid", R_WMV, [P_MAXVLAN], "maximum-address-config")
    R_DYN = "mode/dynamic is an enumeration of {auto, desirable}"

    def dyn_data(v):
        sw = {S + "mode": {S + "dynamic": v},
              S + "access": {S + "vlan": {S + "vlan": 10}}}
        return GR.doc([GR.ge(sw, name=G.TRAIN_IFS[0])],
                      [{"vlan-id": "10"}])

    for v in ["Auto", "DESIRABLE", "off", "active"]:
        add("enum_dynamic", f"dynamic mode {v}", dyn_data(v),
            "invalid", R_DYN, [P_DYNAMIC], "dynamic", variants=2)
    return cases


FORTYGIG_IFS = ["1/1/1", "1/1/2", "2/1/1", "2/1/2"]
FIVEGIG_IFS = ["5/0/1", "5/0/2", "6/0/1", "6/0/2"]


def alt_subset_cases():
    """Non-GigabitEthernet slices beyond the TenGig slice:
    extended TenGig classes, plus FortyGigabitEthernet and
    FiveGigabitEthernet subsets of the core classes (both lists
    probed to validate with 1/0/1-form names)."""
    cases = []

    def add(cls, label, data, intended, rule, paths, frag, ifs,
            listkey, variants=2):
        cases.append({"cls": cls, "label": label,
                      "data": _to_list(data, listkey),
                      "intended": intended, "rule": rule,
                      "paths": paths, "frag": frag,
                      "variants": variants, "te": True,
                      "ifs": ifs, "listkey": listkey})

    F_ACC = ("Cisco-IOS-XE-switch:access/"
             "Cisco-IOS-XE-switch:vlan")
    T = " (TenGigabitEthernet)"
    add("range_aging", "aging time 1438" + T,
        G._acc(10, TE_IFS[0], GR.ps(aging={S + "time": 1438})),
        "valid", None, [G.P_AGING + "/time"], "aging",
        TE_IFS, TE_LIST)
    add("range_aging", "aging time 1443" + T,
        G._acc(10, TE_IFS[0], GR.ps(aging={S + "time": 1443})),
        "invalid", "port-security aging/time is uint16 range "
        "1..1440", [G.P_AGING + "/time"], "aging", TE_IFS, TE_LIST)
    add("enum_dynamic", "dynamic mode auto" + T,
        GR.doc([GR.ge({S + "mode": {S + "dynamic": "auto"},
                       S + "access": {S + "vlan": {S + "vlan": 10}}},
                      name=TE_IFS[0])], [{"vlan-id": "10"}]),
        "valid", None, [P_DYNAMIC], "dynamic", TE_IFS, TE_LIST)
    add("enum_dynamic", "dynamic mode AUTO" + T,
        GR.doc([GR.ge({S + "mode": {S + "dynamic": "AUTO"},
                       S + "access": {S + "vlan": {S + "vlan": 10}}},
                      name=TE_IFS[0])], [{"vlan-id": "10"}]),
        "invalid", "mode/dynamic is an enumeration of "
        "{auto, desirable}", [P_DYNAMIC], "dynamic", TE_IFS,
        TE_LIST)
    add("violation_choice", "violation case restrict" + T,
        G._acc(10, TE_IFS[0], _psx(
            {S + "violation": _vio_inner(["restrict"])})),
        "valid", None, [P_VIOLATION], "violation", TE_IFS, TE_LIST)
    add("violation_choice", "violation cases ['protect', "
        "'restrict']" + T,
        G._acc(10, TE_IFS[0], _psx(
            {S + "violation": _vio_inner(["protect", "restrict"])})),
        "invalid",
        "port-security violation is a YANG choice: at most one "
        "case (protect, restrict, report, shutdown) may be "
        "present; this instance sets more than one",
        [P_VIOLATION], "violation", TE_IFS, TE_LIST)
    add("mac_pattern", "hw-address 0011.2233.4455" + T,
        G._acc(10, TE_IFS[0], _psx(
            {S + "mac-address-config": {S + "mac-address":
             {S + "hw-address-access":
              [{S + "hw-address": "0011.2233.4455"}]}}})),
        "valid", None, [P_HWD], "hw-address", TE_IFS, TE_LIST)
    add("mac_pattern", "hw-address 00:11:22:33:44:55" + T,
        G._acc(10, TE_IFS[0], _psx(
            {S + "mac-address-config": {S + "mac-address":
             {S + "hw-address-access":
              [{S + "hw-address": "00:11:22:33:44:55"}]}}})),
        "invalid",
        "switchport port-security hw-address must match the "
        "cisco-mac-address-type pattern: three groups of "
        "hexadecimal digits separated by dots",
        [P_HWD], "hw-address", TE_IFS, TE_LIST)
    add("range_maximum", "maximum addresses 4096" + T,
        G._acc(10, TE_IFS[0], _maxplain(4096)), "valid", None,
        [P_MAXPLAIN], "maximum/maximum", TE_IFS, TE_LIST)
    add("range_maximum", "maximum addresses 4097" + T,
        G._acc(10, TE_IFS[0], _maxplain(4097)), "invalid",
        "port-security maximum-address-config/maximum/maximum "
        "is uint16 range 1..4096", [P_MAXPLAIN],
        "maximum/maximum", TE_IFS, TE_LIST)
    add("range_maxcount", "maxcount max-addresses 4097" + T,
        G._acc(10, TE_IFS[0], _maxcount(4097)), "valid", None,
        [P_MAXCOUNT], "max-addresses", TE_IFS, TE_LIST)
    add("range_maxcount", "maxcount max-addresses 4098" + T,
        G._acc(10, TE_IFS[0], _maxcount(4098)), "invalid",
        "port-security maxcount/max-addresses is uint16 range "
        "1..4097", [P_MAXCOUNT], "max-addresses", TE_IFS, TE_LIST)
    add("when_maxvlan", "maximum-vlan with mode trunk" + T,
        G._trk(None, TE_IFS[0],
               _maxvlan({S + "maximum": 200, S + "vlan": "vlan"})),
        "valid", None, [P_MAXVLAN], "maximum-address-config",
        TE_IFS, TE_LIST)

    for listkey, ifs, tag in (
            ("FortyGigabitEthernet", FORTYGIG_IFS,
             " (FortyGigabitEthernet)"),
            ("FiveGigabitEthernet", FIVEGIG_IFS,
             " (FiveGigabitEthernet)")):
        add("range_access", "access VLAN 100" + tag,
            G._acc(100, ifs[0]), "valid", None, [G.P_ACCESS],
            F_ACC, ifs, listkey)
        add("range_access", "access VLAN 4097" + tag,
            G._acc(4097, ifs[0]), "invalid",
            "access/vlan/vlan is a union of uint16 range 1..4094 "
            "and the enumeration {dynamic}; the value matches "
            "neither", [G.P_ACCESS], F_ACC, ifs, listkey)
        add("range_native", "native VLAN 100" + tag,
            G._trk({"native": {"vlan": {"vlan-id": 100}}}, ifs[0]),
            "valid", None, [G.P_NATIVE], "native", ifs, listkey)
        add("range_native", "native VLAN 4097" + tag,
            G._trk({"native": {"vlan": {"vlan-id": 4097}}},
                   ifs[0]),
            "invalid", "trunk/native/vlan/vlan-id is uint16 "
            "range 1..4094", [G.P_NATIVE], "native", ifs, listkey)
        add("enum_encap", "encapsulation dot1q" + tag,
            G._trk({"encapsulation": "dot1q"}, ifs[0]), "valid",
            None, [G.P_ENCAP], "encapsulation", ifs, listkey)
        add("enum_encap", "encapsulation Dot1q" + tag,
            G._trk({"encapsulation": "Dot1q"}, ifs[0]), "invalid",
            "trunk/encapsulation is an enumeration of "
            "{dot1q, isl, negotiate}", [G.P_ENCAP],
            "encapsulation", ifs, listkey)
        add("union_access", "access VLAN 'dynamic'" + tag,
            G._acc("dynamic", ifs[0]), "valid", None,
            [G.P_ACCESS], F_ACC, ifs, listkey)
        add("union_access", "access VLAN '4097'" + tag,
            G._acc("4097", ifs[0]), "invalid",
            "access/vlan/vlan is a union of uint16 range 1..4094 "
            "and the enumeration {dynamic}; this JSON value "
            "matches no branch (a string is not the numeric "
            "branch)", [G.P_ACCESS], F_ACC, ifs, listkey)
        mode = {S + "mode": {S + "access": {}, S + "trunk": {}},
                S + "access": {S + "vlan": {S + "vlan": 10}}}
        add("choice_mode", "mode cases ['access', 'trunk']" + tag,
            GR.doc([GR.ge(mode, name=ifs[0])],
                   [{"vlan-id": "10"}]),
            "invalid",
            "mode/mode-choice is a YANG choice: at most one case "
            "may be present; this instance sets two",
            [G.P_MODE], "Cisco-IOS-XE-switch:mode", ifs, listkey)
        add("when_maxacc", "maximum-vlan-access 100 with mode "
            "access" + tag,
            G._acc(10, ifs[0],
                   GR.ps(max_access={S + "maximum": 100,
                                     S + "vlan": "access"})),
            "valid", None, [G.P_MAXACC], "maximum-addres", ifs,
            listkey)
        add("when_maxacc", "maximum-vlan-access 100 with mode "
            "trunk" + tag,
            G._trk(None, ifs[0],
                   GR.ps(max_access={S + "maximum": 100,
                                     S + "vlan": "access"})),
            "invalid",
            "maximum-vlan-access carries when "
            "'../../../../mode/access' — it requires the mode "
            "choice's access case",
            [G.P_MAXACC], "maximum-addres", ifs, listkey)
    return cases


def must_cases_x():
    """IV expansion must cases: fresh maximum values for the
    two sibling pairs (validated through the same SemanticError
    pass as must_cases)."""
    cases = []

    def add(label, data, intended, rule, paths, variants=3):
        cases.append({"cls": "must_sibling", "label": label,
                      "data": data, "intended": intended,
                      "rule": rule, "paths": paths,
                      "frag": "maximum-address-config",
                      "variants": variants})

    R_A = ("port-security maximum-vlan-access/maximum and "
           "maximum-vlan-access/vlan carry must conditions on "
           "each other ('../vlan' and '../maximum') — each "
           "requires the other to be present")
    for v in [1, 4096]:
        add(f"maximum-vlan-access with maximum {v} and vlan",
            G._acc(10, G.TRAIN_IFS[0],
                   GR.ps(max_access={S + "maximum": v,
                                     S + "vlan": "access"})),
            "valid", None, [P_MAXVA])
    add("maximum-vlan-access with maximum 1 only",
        G._acc(10, G.TRAIN_IFS[0],
               GR.ps(max_access={S + "maximum": 1})),
        "invalid", R_A, [P_MAXVA])
    add("maximum-vlan-access with maximum 4096 only",
        G._acc(10, G.TRAIN_IFS[0],
               GR.ps(max_access={S + "maximum": 4096})),
        "invalid", R_A, [P_MAXVA])
    R_T = ("port-security maximum-vlan/maximum and "
           "maximum-vlan/vlan carry must conditions on each "
           "other ('../vlan' and '../maximum') — each requires "
           "the other to be present")
    for v in [1, 4096]:
        d = GR.sw_trunk()
        d.update(_maxvlan({S + "maximum": v, S + "vlan": "vlan"}))
        add(f"maximum-vlan with maximum {v} and vlan",
            GR.doc([GR.ge(d, name=G.TRAIN_IFS[0])],
                   [{"vlan-id": "10"}]),
            "valid", None, [P_MAXVLAN])
    d = GR.sw_trunk()
    d.update(_maxvlan({S + "maximum": 4096}))
    add("maximum-vlan with maximum 4096 only",
        GR.doc([GR.ge(d, name=G.TRAIN_IFS[0])],
               [{"vlan-id": "10"}]),
        "invalid", R_T, [P_MAXVLAN])
    d = GR.sw_trunk()
    d.update(_maxvlan({S + "vlan": "vlan"}))
    add("maximum-vlan with vlan only (second)",
        GR.doc([GR.ge(d, name=G.TRAIN_IFS[0])],
               [{"vlan-id": "10"}]),
        "invalid", R_T, [P_MAXVLAN])
    R_VB = None
    voice_sw = GR.sw_access(10)
    voice_sw[S + "voice"] = {S + "vlan": {S + "vlan": 30}}
    for v in [1, 200]:
        d2 = GR.doc([GR.ge(voice_sw, name=G.TRAIN_IFS[0])],
                    [{"vlan-id": "10"}, {"vlan-id": "30"}])
        d2[ROOT]["interface"]["GigabitEthernet"][0][
            "switchport-config"]["switchport"].update(
                _maxvoice({S + "maximum": v, S + "vlan": "voice"}))
        add(f"maximum-vlan-voice with maximum {v} and vlan",
            d2, "valid", R_VB, [P_MAXVOICE])
    return cases


def _iv_record(c, rid, data, prov):
    return {
        "id": rid, "family": "instance_validity",
        "origin": "template_instance_v3",
        "scenario_group": f"iv3:{c['cls']}:g{c['_case_idx'] % 3}",
        "prompt": (f"Is this configuration instance valid against "
                   f"{MODEL}? Answer valid or invalid, and if "
                   f"invalid name the single rule it breaks.\n\n"
                   f"Instance ({c['label']}):\n"
                   f"{json.dumps(data, indent=1)}"),
        "completion": ("Valid." if c["intended"] == "valid"
                       else f"Invalid — {c['rule']}."),
        "fact": {"instance_ref": rid,
                 "intended_verdict": c["intended"],
                 "violated_rule": c["rule"],
                 "expected_node": c["frag"],
                 "constraint_class": c["cls"]},
        "source": GR.source_for(c["paths"], prov),
        "validation": None,
        "_instance": data}


def _balance_trim(records):
    """Per-class valid:invalid inside 45:55..55:45 (plan section
    3): drop majority-side records from the end of the class."""
    by_cls = defaultdict(list)
    for r in records:
        by_cls[r["fact"]["constraint_class"]].append(r)
    drop = set()
    for cls, recs in by_cls.items():
        nv = sum(1 for r in recs
                 if r["fact"]["intended_verdict"] == "valid")
        ni = len(recs) - nv
        while recs and (nv / max(1, nv + ni) > 0.55
                        or ni / max(1, nv + ni) > 0.55):
            side = "valid" if nv > ni else "invalid"
            for r in reversed(recs):
                if id(r) not in drop and \
                        r["fact"]["intended_verdict"] == side:
                    drop.add(id(r))
                    if side == "valid":
                        nv -= 1
                    else:
                        ni -= 1
                    break
            else:
                break
    return [r for r in records if id(r) not in drop], len(drop)


def _validate_must(records, prov, dirname="iv_must"):
    """Dedicated yangson pass for must_sibling (SemanticError is
    the expected rejection class; see must_cases docstring)."""
    inst_dir = CORPUS / "instances_v3" / dirname
    inst_dir.mkdir(parents=True, exist_ok=True)
    for stale in inst_dir.glob("*.json"):
        stale.unlink()
    files = []
    for r in records:
        name = f"{r['id']}-inst.json"
        (inst_dir / name).write_text(
            json.dumps(r["_instance"], indent=1))
        files.append(name)
        r["fact"]["instance_file"] = \
            f"instances_v3/{dirname}/{name}"
    verdicts = G.run_validator(BUNDLE, LIBRARY, inst_dir,
                               sorted(files))
    kept, dropped = [], []
    for r in records:
        v = verdicts[f"{r['id']}-inst.json"]
        detail = v["detail"] or ""
        err_class = detail.split(":", 1)[0] if detail else None
        if r["fact"]["intended_verdict"] == "valid":
            kind = ("accepted" if v["verdict"] == "valid"
                    else "unexpected_rejection")
            ok = kind == "accepted"
        else:
            ok = (v["verdict"] == "invalid"
                  and err_class == "SemanticError"
                  and r["fact"]["expected_node"] in detail)
            kind = "expected_rejection" if ok \
                else "validator_exception"
        r["validation"] = {"tool": "yangson",
                           "verdict": v["verdict"],
                           "verdict_kind": kind,
                           "expected_node":
                               r["fact"]["expected_node"],
                           "detail": v["detail"]}
        r.pop("_instance", None)
        (kept if ok else dropped).append(
            r if ok else (r["id"], kind, v))
    return kept, dropped


def gen_iv(force: bool = False) -> dict:
    if not batch_guard("iv", force):
        return {"skipped": True}
    prov = GR.build_prov_map(BUNDLE)
    # Collision-driven variant shifting: variantize's space is
    # (interface x bystander pattern) = 12 combos; v1 used
    # v=0..5 and v2 v=0..2, so a v3 case whose value v1 already
    # enumerated (the enum/union/choice valids) collides on every
    # v in 0..5. When a built record's prompt is already known,
    # rebuild it at v+6, +12, +18 — combos no earlier corpus
    # used — so the scenario is genuinely new, not dropped.
    prior = all_prompts(exclude=PARTS / "iv.jsonl")
    seen: set = set()
    shifted = 0

    def build(c, rid, v):
        if c.get("te"):
            data = variantize3(c["data"], c["cls"], v,
                               c.get("ifs", TE_IFS),
                               c.get("listkey", TE_LIST))
        else:
            data = G.variantize(c["data"], c["cls"], v,
                                G.TRAIN_IFS)
        return _iv_record(c, rid, data, prov)

    def emit(c, sink):
        nonlocal shifted
        for v in range(c["variants"]):
            rid = f"V3-IV-{len(records) + len(must_records) + 1:04d}"
            rec = None
            for off in (0, 6, 12, 18):
                cand = build(c, rid, v + off)
                if cand["prompt"] not in prior \
                        and cand["prompt"] not in seen:
                    rec = cand
                    if off:
                        shifted += 1
                    break
            if rec is None:  # all shifts collide: keep v=0 form;
                rec = build(c, rid, v)  # dedupe drops it, reported
            seen.add(rec["prompt"])
            sink.append(rec)

    records, must_records = [], []
    per_class = Counter()
    for c in iv3_cases() + te_subset_cases() + iv3_cases_x() \
            + alt_subset_cases():
        c["_case_idx"] = per_class[c["cls"]]
        per_class[c["cls"]] += 1
        emit(c, records)
    per_class_m = Counter()
    for c in must_cases() + must_cases_x():
        c["_case_idx"] = per_class_m[c["cls"]]
        per_class_m[c["cls"]] += 1
        emit(c, must_records)
    generated = len(records) + len(must_records)
    records, trimmed = _balance_trim(records + must_records)
    must_records = [r for r in records
                    if r["fact"]["constraint_class"]
                    == "must_sibling"]
    records = [r for r in records
               if r["fact"]["constraint_class"] != "must_sibling"]
    prompts = all_prompts(exclude=PARTS / "iv.jsonl")
    records, dup1 = dedupe(records, prompts)
    must_records, dup2 = dedupe(must_records, prompts)
    kept, dropped = G.validate_all(
        records, BUNDLE, LIBRARY, CORPUS / "instances_v3" / "iv",
        "instances_v3/iv")
    kept_m, dropped_m = _validate_must(must_records, prov)
    final = kept + kept_m
    # the band is a property of the SHIPPED batch: one-sided
    # validation drops can push a class out of band after the
    # pre-validation trim, so trim once more on the kept list
    final, trimmed_post = _balance_trim(final)
    assign_splits(final, "instance_validity")
    write_part("iv", final)
    cls_counts = Counter(r["fact"]["constraint_class"]
                         for r in final)
    bal = {}
    for cls in cls_counts:
        sub = [r for r in final
               if r["fact"]["constraint_class"] == cls]
        nv = sum(1 for r in sub
                 if r["fact"]["intended_verdict"] == "valid")
        bal[cls] = f"{nv}V/{len(sub) - nv}I"
    return report("iv", {
        "generated": generated, "balance_trimmed": trimmed,
        "trimmed_post_validation": trimmed_post,
        "variant_shifted": shifted,
        "deduped": dup1 + dup2,
        "dropped_validation": len(dropped) + len(dropped_m),
        "dropped_detail": [d[0] for d in
                           (dropped + dropped_m)][:12],
        "final": len(final),
        "train": sum(1 for r in final if r["split"] == "train"),
        "dev": sum(1 for r in final if r["split"] == "dev"),
        "per_class": dict(cls_counts), "balance": bal})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("batch", choices=["pm", "iv", "rs", "srte",
                                      "pv", "holdout", "finalize"])
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    if args.batch == "pm":
        gen_pm(args.force)
    elif args.batch == "iv":
        gen_iv(args.force)
    elif args.batch == "rs":
        gen_rs(args.force)
    elif args.batch == "srte":
        gen_srte(args.force)
    elif args.batch == "pv":
        gen_pv(args.force)
    elif args.batch == "holdout":
        gen_holdout(args.force)
    elif args.batch == "finalize":
        gen_finalize()
    return 0


# ---------------------------------------------------------------- RS
# Verdicts are computed from explicit predicate specs (the v2
# discipline); rationale strings are templated per predicate kind
# in the v1/v2 voice and always embed the exact citation.

_Q = {
    "access": "An access port is configured with access VLAN {v}.",
    "native": "A trunk is configured with native VLAN {v}.",
    "voice": "A port is configured with voice VLAN {v}.",
    "aging_time": "Port-security aging time is set to {v} minutes.",
    "aging_type": "Port-security aging type is set to {v}.",
    "encap": "A trunk is configured with encapsulation {v}.",
    "allowed": "A trunk's allowed VLAN list is '{v}'.",
    "maximum": "Port-security maximum addresses is set to {v}.",
}
_PHRASES = [" Is that allowed?", " Does that comply?",
            " Is that permitted under the profile?"]


def _pred(kind, param, v):
    if kind == "range":
        return param[0] <= v <= param[1]
    if kind == "cap":
        return v <= param
    if kind == "floor":
        return v >= param
    if kind == "exact":
        return v == param
    if kind == "forbidden":
        return v != param
    if kind == "parity":
        return (v % 2 == 0) == (param == "even")
    if kind == "set":
        return v in param
    if kind == "list_incl":
        return param in G.expand_entry_key(v)
    if kind == "list_excl":
        return param not in G.expand_entry_key(v)
    if kind == "list_cap":
        return len(G.expand_entry_key(v)) <= param
    if kind == "conj_incl_excl":
        s = G.expand_entry_key(v)
        return param[0] in s and param[1] not in s
    raise ValueError(kind)


def _why(kind, param, v, ok, cite, noun, idx):
    alt = idx % 2
    if kind == "range":
        lo, hi = param
        if ok and v == lo:
            return (f"{v} is the bottom of profile {cite}'s "
                    f"restricted range {lo}..{hi}.")
        if ok and v == hi:
            return (f"{v} is the top of profile {cite}'s "
                    f"restricted range {lo}..{hi}.")
        if ok:
            return (f"{v} is inside profile {cite}'s restricted "
                    f"range {lo}..{hi}.")
        if v < lo:
            return (f"profile {cite} restricts {noun} to "
                    f"{lo}..{hi}; {v} is below it.")
        if alt:
            return (f"the base schema allows {v}, but profile "
                    f"{cite} restricts {noun} to {lo}..{hi}.")
        return (f"profile {cite} restricts {noun} to {lo}..{hi}; "
                f"{v} is above it.")
    if kind == "cap":
        if ok and v == param:
            return f"{v} is exactly the cap in profile {cite}."
        if ok:
            return (f"{v} is under the cap in profile {cite} "
                    f"({param}).")
        if alt:
            return (f"the base schema allows {v}, but profile "
                    f"{cite} caps {noun} at {param}.")
        return (f"profile {cite} caps {noun} at {param}; {v} is "
                f"above the cap.")
    if kind == "floor":
        if ok and v == param:
            return f"{v} is exactly the floor in profile {cite}."
        if ok:
            return (f"{v} is above the floor in profile {cite} "
                    f"({param}).")
        if alt:
            return (f"profile {cite} requires {noun} of at least "
                    f"{param}; {v} is below the floor.")
        return (f"profile {cite} requires {noun} of at least "
                f"{param} minutes; {v} is below it."
                if "aging" in noun else
                f"profile {cite} requires {noun} of at least "
                f"{param}; {v} is below it.")
    if kind == "exact":
        if ok:
            return f"{v} is the {noun} profile {cite} requires."
        if alt:
            return (f"the base schema allows {v}, but profile "
                    f"{cite} requires the {noun} to be {param}.")
        return (f"profile {cite} requires the {noun} to be "
                f"{param}; {v} is not {param}.")
    if kind == "forbidden":
        if not ok:
            return (f"the base schema allows {noun} {param}, but "
                    f"profile {cite} forbids {noun} {param}.")
        return (f"profile {cite} forbids only {noun} {param}; "
                f"{v} is unrestricted by the profile.")
    if kind == "parity":
        other = "odd" if param == "even" else "even"
        if ok:
            return (f"{v} is {param}, satisfying profile {cite}.")
        return (f"{v} is {other}; profile {cite} requires "
                f"{param} {noun}.")
    if kind == "set":
        want = ", ".join(sorted(param))
        if ok:
            return (f"{v} is the {noun} profile {cite} requires "
                    f"(the profile allows only {want}).")
        return (f"the base schema allows {v}, but profile {cite} "
                f"allows only {want} for the {noun}.")
    if kind == "list_incl":
        if ok:
            return (f"the list includes VLAN {param}, as profile "
                    f"{cite} requires.")
        return (f"profile {cite} requires every trunk's allowed "
                f"VLAN list to include VLAN {param}; this list "
                f"does not.")
    if kind == "list_excl":
        if not ok:
            return (f"the list includes VLAN {param}, which "
                    f"profile {cite} forbids.")
        return (f"the list does not include VLAN {param}, so "
                f"profile {cite} is satisfied.")
    if kind == "list_cap":
        k = len(G.expand_entry_key(v))
        if ok and k == param:
            return (f"the list names exactly {param} VLANs, the "
                    f"cap in profile {cite}.")
        if ok:
            return (f"the list names {k} VLANs, under the cap in "
                    f"profile {cite} ({param}).")
        return (f"profile {cite} caps allowed VLAN lists at "
                f"{param} entries; this list names {k}.")
    if kind == "conj_incl_excl":
        inc, exc = param
        s = G.expand_entry_key(v)
        if ok:
            return (f"the list includes VLAN {inc} and does not "
                    f"include VLAN {exc}, satisfying both parts "
                    f"of profile {cite}.")
        if inc not in s and exc in s:
            return (f"the list neither includes VLAN {inc} nor "
                    f"excludes VLAN {exc}; profile {cite} "
                    f"requires both.")
        if inc not in s:
            return (f"profile {cite} requires the list to "
                    f"include VLAN {inc}; it does not, so the "
                    f"rule fails even though VLAN {exc} is absent.")
        return (f"the list includes VLAN {exc}, which profile "
                f"{cite} forbids, even though it includes VLAN "
                f"{inc}.")
    raise ValueError(kind)


# rule spec: (cite, path, kind, param, label, noun, values)
RS_V3_PROFILES = {
    "P7": ("Deployment profile P7 (campus voice standard): "
           "(1) voice VLAN IDs are restricted to 40..49; "
           "(2) port-security aging type must be absolute; "
           "(3) a trunk's native VLAN must be 1001.", [
        ("P7 rule (1)", G.P_VOICE, "range", (40, 49), "voice",
         "voice VLAN IDs",
         [40, 41, 42, 45, 47, 48, 49, 39, 50, 30, 4, 400, 100,
          4094]),
        ("P7 rule (2)", G.P_AGING + "/type", "set", {"absolute"},
         "aging_type", "aging type", ["absolute", "inactivity"]),
        ("P7 rule (3)", G.P_NATIVE, "exact", 1001, "native",
         "native VLAN",
         [1001, 999, 777, 1000, 1002, 1, 42, 4094]),
    ]),
    "P8": ("Deployment profile P8 (high-security standard): "
           "(1) port-security maximum addresses must not exceed "
           "5; (2) access VLAN IDs are restricted to 200..299; "
           "(3) port-security aging time must be at least 120 "
           "minutes.", [
        ("P8 rule (1)", P_MAXPLAIN, "cap", 5, "maximum",
         "maximum addresses", [1, 2, 4, 5, 6, 7, 10, 25, 100, 4096]),
        ("P8 rule (2)", G.P_ACCESS, "range", (200, 299), "access",
         "access VLAN IDs",
         [200, 201, 210, 250, 290, 298, 299, 199, 300, 100, 1000,
          4094, 1, 150]),
        ("P8 rule (3)", G.P_AGING + "/time", "floor", 120,
         "aging_time", "aging time",
         [120, 121, 200, 600, 1440, 119, 100, 60, 10, 1]),
    ]),
    "P9": ("Deployment profile P9 (legacy migration standard): "
           "(1) a trunk's native VLAN must be 1; (2) every "
           "trunk's allowed VLAN list must include VLAN 10; "
           "(3) access VLAN 100 is forbidden.", [
        ("P9 rule (1)", G.P_NATIVE, "exact", 1, "native",
         "native VLAN", [1, 2, 3, 999, 777, 1000, 1001, 4094]),
        ("P9 rule (2)", G.P_ALLOWED, "list_incl", 10, "allowed",
         "allowed VLAN list",
         ["10", "10,20", "1,10,100", "10,200,300", "10,20,30",
          "20,30", "1", "100,200", "1,2,3"]),
        ("P9 rule (3)", G.P_ACCESS, "forbidden", 100, "access",
         "access VLAN", [100, 99, 101, 1, 4094, 50, 1000]),
    ]),
    "P10": ("Deployment profile P10 (branch lite standard): "
            "(1) a trunk's native VLAN must be an even number; "
            "(2) trunk encapsulation negotiate is forbidden; "
            "(3) port-security aging time must be at least 5 "
            "minutes.", [
        ("P10 rule (1)", G.P_NATIVE, "parity", "even", "native",
         "native VLAN IDs", [2, 4, 6, 100, 1000, 4094, 1, 3, 7,
                             999, 777, 51]),
        ("P10 rule (2)", G.P_ENCAP, "forbidden", "negotiate",
         "encap", "encapsulation", ["negotiate", "dot1q", "isl"]),
        ("P10 rule (3)", G.P_AGING + "/time", "floor", 5,
         "aging_time", "aging time", [5, 6, 10, 60, 1440, 4, 3, 1]),
    ]),
    "P11": ("Deployment profile P11 (datacenter east standard): "
            "(1) trunk allowed VLAN lists must name at most 5 "
            "VLANs; (2) port-security maximum addresses for the "
            "access VLAN must not exceed 25; (3) port-security "
            "aging time must be at least 15 minutes.", [
        ("P11 rule (1)", G.P_ALLOWED, "list_cap", 5, "allowed",
         "allowed VLAN list",
         ["10", "10,20", "100,200", "10,20,30,40,50", "5,15,25",
          "10,20,30,40,50,60", "1-10", "1,2,3,4,5,6,7"]),
        ("P11 rule (2)", P_MAXVA, "cap", 25, "maximum",
         "maximum addresses",
         [1, 10, 15, 20, 24, 25, 26, 30, 100, 1000]),
        ("P11 rule (3)", G.P_AGING + "/time", "floor", 15,
         "aging_time", "aging time",
         [15, 16, 30, 120, 1440, 14, 10, 1]),
    ]),
    "P12": ("Deployment profile P12 (guest network standard): "
            "(1) access VLAN IDs are restricted to 3000..3999; "
            "(2) every trunk's allowed VLAN list must include "
            "VLAN 10 and must not include VLAN 1; (3) a trunk's "
            "native VLAN must be 42.", [
        ("P12 rule (1)", G.P_ACCESS, "range", (3000, 3999),
         "access", "access VLAN IDs",
         [3000, 3001, 3100, 3500, 3900, 3998, 3999, 2999, 4000,
          2000, 100, 4094]),
        ("P12 rule (2)", G.P_ALLOWED, "conj_incl_excl", (10, 1),
         "allowed", "allowed VLAN list",
         ["10,20", "10", "10,100,200", "20,30", "1,10", "1,20",
          "100,200", "1"]),
        ("P12 rule (3)", G.P_NATIVE, "exact", 42, "native",
         "native VLAN", [42, 41, 43, 1, 999, 777, 1000, 4094]),
    ]),
}

# maintenance: fresh values on the existing train profiles
# (values disjoint from the v1 and v2 case lists for each rule;
# P3 rule (1) and P4 rule (3) have no fresh values left — their
# enum value spaces are exhausted — and are skipped)
RS_MAINTENANCE = [
    ("P1", "P1 rule (1)", G.P_ACCESS, "range", (1, 100), "access",
     "access VLAN IDs", [3, 7, 42, 77, 98, 150, 200, 300, 1000]),
    ("P1", "P1 rule (2)", G.P_NATIVE, "forbidden", 1, "native",
     "native VLAN", [100, 500, 777, 4093]),
    ("P1", "P1 rule (3)", G.P_AGING + "/time", "cap", 60,
     "aging_time", "aging time", [2, 15, 30, 45, 90, 240, 300]),
    ("P3", "P3 rule (2)", G.P_AGING + "/time", "cap", 30,
     "aging_time", "aging time", [1, 5, 15, 60, 120, 180]),
    ("P3", "P3 rule (3)", G.P_ACCESS, "range", (100, 199),
     "access", "access VLAN IDs",
     [101, 120, 175, 198, 98, 201, 300, 500]),
    ("P4", "P4 rule (1)", G.P_VOICE, "exact", 40, "voice",
     "voice VLAN", [42, 39, 44, 4000]),
    ("P4", "P4 rule (2)", G.P_ALLOWED, "list_excl", 1, "allowed",
     "allowed VLAN list",
     ["2,3", "5", "100", "1,2", "1,5", "1,100,200"]),
    ("P5", "P5 rule (1)", P_MAXVA, "cap", 10, "maximum",
     "maximum addresses", [2, 3, 5, 25, 50, 500]),
    ("P5", "P5 rule (2)", G.P_ACCESS, "forbidden", 1, "access",
     "access VLAN", [4, 777, 1000]),
    ("P5", "P5 rule (3)", G.P_NATIVE, "exact", 1000, "native",
     "native VLAN", [1002, 42, 500, 777]),
]

RS_HOLDOUT_PROFILES = {
    "P13": ("Deployment profile P13 (holdout standard): "
            "(1) access VLAN IDs are restricted to 500..599; "
            "(2) port-security aging time must be at least 25 "
            "minutes; (3) trunk encapsulation must be isl.", [
        ("P13 rule (1)", G.P_ACCESS, "range", (500, 599),
         "access", "access VLAN IDs",
         [500, 550, 599, 499, 600, 100]),
        ("P13 rule (2)", G.P_AGING + "/time", "floor", 25,
         "aging_time", "aging time", [25, 30, 100, 24, 10, 1]),
        ("P13 rule (3)", G.P_ENCAP, "exact", "isl", "encap",
         "encapsulation", ["isl", "dot1q", "negotiate"]),
    ]),
    "P14": ("Deployment profile P14 (holdout standard): "
            "(1) voice VLAN IDs are restricted to 60..69; "
            "(2) trunk allowed VLAN lists must name at most 3 "
            "VLANs; (3) a trunk's native VLAN must be 2024.", [
        ("P14 rule (1)", G.P_VOICE, "range", (60, 69), "voice",
         "voice VLAN IDs", [60, 65, 69, 59, 70, 100]),
        ("P14 rule (2)", G.P_ALLOWED, "list_cap", 3, "allowed",
         "allowed VLAN list",
         ["10", "10,20,30", "100,200", "10,20,30,40", "1-5"]),
        ("P14 rule (3)", G.P_NATIVE, "exact", 2024, "native",
         "native VLAN", [2024, 2023, 2025, 1]),
    ]),
}


def _rs_records(specs, texts, prov, id_prefix, group_prefix,
                phrasings_for):
    """specs: [(pname, cite, path, kind, param, label, noun,
    values)]; phrasings_for(case_idx, boundary) -> count."""
    out = []
    n = 0
    per_rule = Counter()
    for pname, cite, path, kind, param, label, noun, values in specs:
        for v in values:
            idx = per_rule[cite]
            per_rule[cite] += 1
            ok = _pred(kind, param, v)
            why = _why(kind, param, v, ok, cite, noun, idx)
            boundary = (
                (kind == "range" and v in (param[0], param[1],
                                           param[0] - 1,
                                           param[1] + 1))
                or (kind in ("cap", "floor") and v == param)
                or (kind == "exact" and ok)
                or (kind == "forbidden" and not ok)
                or (kind == "list_cap" and
                    len(G.expand_entry_key(v)) == param))
            for ph in range(phrasings_for(idx, boundary)):
                n += 1
                qq = _Q[label].format(v=v) + _PHRASES[
                    (idx + ph) % 3]
                out.append({
                    "id": f"{id_prefix}-{n:04d}",
                    "family": "revised_schema",
                    "origin": "template_instance_v3",
                    "scenario_group": f"{group_prefix}:{cite}:"
                                      f"g{idx % 3}",
                    "prompt": (f"Answer using the deployment "
                               f"profile below, which overrides "
                               f"the base schema where they "
                               f"differ.\n\n{texts[pname]}\n\n{qq}"),
                    "completion": (f"{'Yes' if ok else 'No'}. "
                                   f"{why}"),
                    "fact": {"profile_rule": cite,
                             "answer": {
                                 "verdict": "allowed" if ok
                                 else "not_allowed",
                                 "rule": cite}},
                    "source": GR.source_for(
                        [path], prov,
                        {"profile": f"{pname} (authored, embedded "
                                    f"in prompt)"}),
                    "validation": {"tool": "none",
                                   "note": "profile rule, not "
                                           "base schema"}})
    return out


def gen_rs(force: bool = False) -> dict:
    if not batch_guard("rs", force):
        return {"skipped": True}
    prov = GR.build_prov_map(BUNDLE)
    texts = {p: t for p, (t, _r) in RS_V3_PROFILES.items()}
    specs = []
    for pname, (_t, rules) in RS_V3_PROFILES.items():
        for cite, path, kind, param, label, noun, values in rules:
            specs.append((pname, cite, path, kind, param, label,
                          noun, values))
    new_recs = _rs_records(
        specs, texts, prov, "V3-RS", "rs3",
        phrasings_for=lambda idx, b: 2)
    texts_m = {p: t for p, (t, _r) in G.RS_PROFILES.items()}
    maint_recs = _rs_records(
        RS_MAINTENANCE, texts_m, prov, "V3-RSM", "rsm3",
        phrasings_for=lambda idx, b: 2 if b else 1)
    records = new_recs + maint_recs
    generated = len(records)
    prompts = all_prompts(exclude=PARTS / "rs.jsonl")
    records, dup = dedupe(records, prompts)
    assign_splits(records, "revised_schema")
    write_part("rs", records)
    prof_counts = Counter(r["fact"]["profile_rule"].split()[0]
                          for r in records)
    verdicts = Counter(r["fact"]["answer"]["verdict"]
                       for r in records)
    return report("rs", {
        "generated": generated, "deduped": dup,
        "new_profiles": len(new_recs),
        "maintenance": len(maint_recs), "final": len(records),
        "train": sum(1 for r in records if r["split"] == "train"),
        "dev": sum(1 for r in records if r["split"] == "dev"),
        "by_profile": dict(prof_counts),
        "verdicts": dict(verdicts)})


# --------------------------------------------------------------- SR/TE

KIND_GRIDS_V3 = {
    "set_access_vlan": [(15, 25), (35, 45), (70, 80), (90, 111),
                        (222, 333), (500, 600), (2000, 3000),
                        (45, 55)],
    "swap_mode": ["access", "trunk"],
    "set_native_vlan": [42, 123, 2222, 3000, 64, 11, 222],
    "add_entry": [60, 70, 111, 1234, 2500, 90, 800],
    "set_voice_vlan": [35, 45, 55, 65, 85, 95, 105],
    "remove_entry": [60, 70, 111, 222, 90, 800, 300],
    "set_allowed": [("100", "100,200"), ("100,200", "100"),
                    ("5", "5,15"), ("1000", "1000,2000"),
                    ("10,30", "20,40"), ("7", "7,77"),
                    ("300", "300,400")],
    "set_encap": ["dot1q", "isl", "negotiate"],
}
ENTRY_POOLS_V3 = [["100"], ["5", "15"], ["1-100"], ["2000"],
                  ["111", "222"], ["42"], ["5"]]


def gen_te_neg_v3(prov, rounds=20):
    """TE selector negatives in the v2 shape, with the plan's
    new malformed-key forms (section 2) — each mechanically
    asserted against GE_NAME_RE at generation. '1/0/1.' is NOT
    used: it matches the pattern (trailing dot, zero digits)."""
    absent_ifs = ["5/0/1", "1/0/33", "2/0/24"]
    absent_entries = ["3000", "1-50", "777"]
    bad_keys = ["01/0/1", "1-0-1", "GigabitEthernet1/0/1",
                "1/0/1a"]
    out = []
    n = 0
    plan = []
    for t in range(rounds):
        plan.append(("absent_if", absent_ifs[t % len(absent_ifs)]))
        plan.append(("absent_entry",
                     absent_entries[t % len(absent_entries)]))
        plan.append(("bad_key", bad_keys[t % len(bad_keys)]))
    for t, (kind, key) in enumerate(plan):
        base_if = G.TRAIN_IFS[t % len(G.TRAIN_IFS)]
        base = GR.doc([GR.ge(GR.sw_access(10), name=base_if),
                       GR.ge(GR.sw_access(20), name=G.TRAIN_IFS[
                           (t + 1) % len(G.TRAIN_IFS)])],
                      [{"vlan-id": "10"}, {"vlan-id": "20"}])
        names = [g["name"] for g in
                 base[ROOT]["interface"]["GigabitEthernet"]]
        keys = [e["vlan-id"] for e in
                base[ROOT]["vlan"][V + "configuration-entry"]]
        if kind == "absent_if":
            assert key not in names and G.GE_NAME_RE.match(key)
            target = {"list": G.GE_LIST, "key": {"name": key}}
            err = "target_not_found"
            instr = (f"Set the access VLAN of GigabitEthernet "
                     f"{key} from 10 to 20.")
            attempted = [{"target": target, "path": G.P_ACCESS,
                          "op": "set", "old": 10, "new": 20}]
        elif kind == "absent_entry":
            assert key not in keys
            target = {"list": G.ENTRY_LIST, "key": {"vlan-id": key}}
            err = "target_not_found"
            instr = f"Remove the configuration entry for VLAN {key}."
            attempted = [{"target": target, "path": G.ENTRY_LIST,
                          "op": "remove_entry"}]
        else:
            assert not G.GE_NAME_RE.match(key), key
            target = {"list": G.GE_LIST, "key": {"name": key}}
            err = "invalid_key"
            instr = (f"Set the access VLAN of GigabitEthernet "
                     f"{key} from 10 to 20.")
            attempted = [{"target": target, "path": G.P_ACCESS,
                          "op": "set", "old": 10, "new": 20}]
        n += 1
        out.append({
            "id": f"V3-TE-N{n:02d}", "family": "typed_edit",
            "origin": "template_instance_v3",
            "task_form": "edit_spec", "executable_on_device": False,
            "scenario_group": f"neg3:{err}:{t}",
            "prompt": (f"Return the typed edit that transforms "
                       f"the state below as instructed — the edit "
                       f"operations only, not the full state. Each "
                       f"edit names its target list and key. If "
                       f"the target cannot be resolved, return an "
                       f"error object instead.\n\nEdit: {instr}\n\n"
                       f"State:\n{json.dumps(base, indent=1)}"),
            "completion": json.dumps({"error": err, "target": target},
                                     indent=1),
            "fact": {"expected": "reject_target", "error": err,
                     "target": target, "attempted_edits": attempted,
                     "schema_only_reason":
                         "deliberate selector-negative: the "
                         "correct behavior is rejection, not an "
                         "edit"},
            "source": GR.source_for([G.P_ACCESS], prov),
            "validation": None,
            "_before": base})
    return out


def gen_srte(force: bool = False, quota: int = 66) -> dict:
    if not batch_guard("srte", force):
        return {"skipped": True}
    prov = GR.build_prov_map(BUNDLE)
    saved_grids, saved_pools = G.KIND_GRIDS, G.ENTRY_POOLS
    G.KIND_GRIDS, G.ENTRY_POOLS = KIND_GRIDS_V3, ENTRY_POOLS_V3
    try:
        records = G.gen_scenarios("train", prov, "V3", quota,
                                  te_stride=3)
    finally:
        G.KIND_GRIDS, G.ENTRY_POOLS = saved_grids, saved_pools
    for r in records:
        r["origin"] = "template_instance_v3"
        r["scenario_group"] = r["scenario_group"].replace(
            "scen:train:", "scen3:")
    records += gen_te_neg_v3(prov)
    generated = len(records)
    prompts = all_prompts(exclude=PARTS / "srte.jsonl")
    records, dup = dedupe(records, prompts)
    kept, dropped = G.validate_all(
        records, BUNDLE, LIBRARY, CORPUS / "instances_v3" / "srte",
        "instances_v3/srte")
    sr = [r for r in kept if r["family"] == "state_reconstruction"]
    te = [r for r in kept if r["family"] == "typed_edit"
          and not r["id"].startswith("V3-TE-N")]
    neg = [r for r in kept if r["id"].startswith("V3-TE-N")]
    assign_splits(sr, "state_reconstruction")
    group_split = {r["scenario_group"]: r["split"] for r in sr}
    for r in te:
        r["split"] = group_split[r["scenario_group"]]
    assign_splits(neg, "typed_edit", quota=2)
    final = sr + te + neg
    write_part("srte", final)
    kinds = Counter()
    for r in sr:
        kinds[r["scenario_group"].split(":")[1]] += 1
    return report("srte", {
        "generated": generated, "deduped": dup,
        "dropped_validation": len(dropped),
        "dropped_detail": [d[0] for d in dropped][:12],
        "final": len(final), "sr": len(sr), "te": len(te),
        "te_negatives": len(neg),
        "sr_train": sum(1 for r in sr if r["split"] == "train"),
        "sr_dev": sum(1 for r in sr if r["split"] == "dev"),
        "te_train": sum(1 for r in te + neg
                        if r["split"] == "train"),
        "te_dev": sum(1 for r in te + neg if r["split"] == "dev"),
        "sr_per_kind": dict(kinds)})


# --------------------------------------------------------- paired_view
# States come from this batch's own templates over the in-scope
# subtree (plan section 2: the 17.18.1 goldens and the Phase 1
# corpus are NOT data sources — wrong pin / non-schema dialect).
# The CLI side is produced by a deterministic renderer over the
# state; a record survives only if parsing the rendered CLI with
# ciscoconfparse2 reconstructs the state EXACTLY (round-trip
# gate) and yangson accepts the state.

PV_ACC = [12, 24, 36, 48, 96, 128, 256, 512, 1024, 2048]
PV_VOICE = [36, 48, 96, 128]
PV_AGING = [(7, "absolute"), (14, "inactivity"), (21, "absolute"),
            (45, "inactivity"), (90, "absolute")]
PV_MAX = [10, 50, 200, 1000]
PV_NATIVE = [96, 128, 256, 512]
PV_ALLOWED = ["12,24", "36,48,96", "128", "256,512"]
PV_ENCAP = ["dot1q", "isl", "negotiate"]
CLI_IFACE = {"GigabitEthernet": "GigabitEthernet",
             "TenGigabitEthernet": "TenGigabitEthernet"}


def _pv_state(listkey, name, sw):
    refs = G.referenced_vlans(sw)
    entries = [{"vlan-id": str(v)} for v in sorted(refs)]
    entry = GR.ge(sw, name=name)
    return {ROOT: {"interface": {listkey: [entry]},
                   "vlan": {V + "configuration-entry": entries}}}


def _pv_subjects(sw, entries):
    subs = []
    if (S + "access") in sw:
        subs.append(G.P_ACCESS)
    if (S + "voice") in sw:
        subs.append(G.P_VOICE)
    trunk = sw.get(S + "trunk", {})
    if (S + "encapsulation") in trunk:
        subs.append(G.P_ENCAP)
    if (S + "native") in trunk:
        subs.append(G.P_NATIVE)
    if (S + "allowed") in trunk:
        subs.append(G.P_ALLOWED)
    psc = sw.get(S + "port-security-config", {}).get(
        S + "port-security", {})
    aging = psc.get(S + "aging", {})
    if (S + "time") in aging:
        subs.append(G.P_AGING + "/time")
    if (S + "type") in aging:
        subs.append(G.P_AGING + "/type")
    mac = psc.get(S + "maximum-address-config", {})
    if (S + "maximum") in mac:
        subs.append(P_MAXPLAIN)
    if (S + "maximum-vlan-access") in mac:
        subs.append(P_MAXVA)
    if entries:
        subs.append(G.P_ENTRY)
    return subs


def render_cli(state) -> str:
    root = state[ROOT]
    lines = []
    entries = root["vlan"][V + "configuration-entry"]
    for e in entries:
        lines.append(f"vlan {e['vlan-id']}")
    lines.append("!")
    (listkey, ifaces), = root["interface"].items()
    iface = ifaces[0]
    lines.append(f"interface {CLI_IFACE[listkey]}{iface['name']}")
    sw = iface["switchport-config"]["switchport"]
    mode = sw[S + "mode"]
    if (S + "access") in mode:
        lines.append(" switchport mode access")
    elif (S + "trunk") in mode:
        lines.append(" switchport mode trunk")
    trunk = sw.get(S + "trunk", {})
    if (S + "encapsulation") in trunk:
        lines.append(f" switchport trunk encapsulation "
                     f"{trunk[S + 'encapsulation']}")
    acc = sw.get(S + "access", {}).get(S + "vlan", {})
    if (S + "vlan") in acc:
        lines.append(f" switchport access vlan {acc[S + 'vlan']}")
    voice = sw.get(S + "voice", {}).get(S + "vlan", {})
    if (S + "vlan") in voice:
        lines.append(f" switchport voice vlan {voice[S + 'vlan']}")
    native = trunk.get(S + "native", {}).get(S + "vlan", {})
    if (S + "vlan-id") in native:
        lines.append(f" switchport trunk native vlan "
                     f"{native[S + 'vlan-id']}")
    allowed = trunk.get(S + "allowed", {}).get(S + "vlan", {})
    if (S + "vlans") in allowed:
        lines.append(f" switchport trunk allowed vlan "
                     f"{allowed[S + 'vlans']}")
    psc = sw.get(S + "port-security-config", {}).get(
        S + "port-security", {})
    aging = psc.get(S + "aging", {})
    if (S + "time") in aging:
        lines.append(f" switchport port-security aging time "
                     f"{aging[S + 'time']}")
    if (S + "type") in aging:
        lines.append(f" switchport port-security aging type "
                     f"{aging[S + 'type']}")
    mac = psc.get(S + "maximum-address-config", {})
    if (S + "maximum") in mac:
        lines.append(f" switchport port-security maximum "
                     f"{mac[S + 'maximum'][S + 'maximum']}")
    if (S + "maximum-vlan-access") in mac:
        mva = mac[S + "maximum-vlan-access"][S + "maximum"]
        lines.append(f" switchport port-security maximum "
                     f"{mva} vlan access")
    return "\n".join(lines)


_LINE_RES = [
    (re.compile(r"^switchport mode (access|trunk)$"), "mode"),
    (re.compile(r"^switchport trunk encapsulation (\S+)$"), "encap"),
    (re.compile(r"^switchport access vlan (\d+)$"), "access"),
    (re.compile(r"^switchport voice vlan (\d+)$"), "voice"),
    (re.compile(r"^switchport trunk native vlan (\d+)$"), "native"),
    (re.compile(r"^switchport trunk allowed vlan (\S+)$"),
     "allowed"),
    (re.compile(r"^switchport port-security aging time (\d+)$"),
     "aging_time"),
    (re.compile(r"^switchport port-security aging type (\S+)$"),
     "aging_type"),
    (re.compile(r"^switchport port-security maximum (\d+) "
                r"vlan access$"), "maxva"),
    (re.compile(r"^switchport port-security maximum (\d+)$"),
     "maxplain"),
]


def parse_cli(text: str):
    """Strict inverse of render_cli, structured by
    ciscoconfparse2. Raises ValueError on any unrecognized line —
    the caller drops the record (round-trip gate)."""
    from ciscoconfparse2 import CiscoConfParse
    parse = CiscoConfParse(text.splitlines())
    entries = []
    for obj in parse.find_objects(r"^vlan \S+$"):
        entries.append({"vlan-id": obj.text.split()[1]})
    entries.sort(key=lambda e: int(e["vlan-id"])
                 if e["vlan-id"].isdigit() else 0)
    ifaces = parse.find_objects(r"^interface \S+$")
    if len(ifaces) != 1:
        raise ValueError(f"{len(ifaces)} interface blocks")
    obj = ifaces[0]
    m = re.match(r"^interface (GigabitEthernet|TenGigabitEthernet)"
                 r"(\S+)$", obj.text)
    if not m:
        raise ValueError(f"bad interface line {obj.text!r}")
    listkey, name = m.group(1), m.group(2)
    sw: dict = {}
    trunk: dict = {}
    psc: dict = {}
    mac: dict = {}
    aging: dict = {}
    for child in obj.children:
        line = child.text.strip()
        hit = None
        for rx, kind in _LINE_RES:
            mm = rx.match(line)
            if mm:
                hit = (kind, mm.group(1))
                break
        if hit is None:
            raise ValueError(f"unrecognized line {line!r}")
        kind, val = hit
        if kind == "mode":
            sw[S + "mode"] = {S + val: {}}
        elif kind == "encap":
            trunk[S + "encapsulation"] = val
        elif kind == "access":
            sw[S + "access"] = {S + "vlan": {S + "vlan": int(val)}}
        elif kind == "voice":
            sw[S + "voice"] = {S + "vlan": {S + "vlan": int(val)}}
        elif kind == "native":
            trunk[S + "native"] = {S + "vlan":
                                   {S + "vlan-id": int(val)}}
        elif kind == "allowed":
            trunk[S + "allowed"] = {S + "vlan": {S + "vlans": val}}
        elif kind == "aging_time":
            aging[S + "time"] = int(val)
        elif kind == "aging_type":
            aging[S + "type"] = val
        elif kind == "maxplain":
            mac[S + "maximum"] = {S + "maximum": int(val)}
        elif kind == "maxva":
            mac[S + "maximum-vlan-access"] = {
                S + "maximum": int(val), S + "vlan": "access"}
    if trunk:
        sw[S + "trunk"] = trunk
    if aging:
        psc[S + "aging"] = aging
    if mac:
        psc[S + "maximum-address-config"] = mac
    if psc:
        sw[S + "port-security-config"] = {S + "port-security": psc}
    return {ROOT: {"interface": {listkey: [GR.ge(sw, name=name)]},
                   "vlan": {V + "configuration-entry": entries}}}


def pv_candidate_states():
    """Deterministic candidate states, interleaved across the
    ten templates so the first N are template-diverse."""
    gi = G.TRAIN_IFS
    te = TE_IFS
    buckets: list[list] = []

    def bucket():
        b: list = []
        buckets.append(b)
        return b

    b = bucket()
    for i, vlan in enumerate(PV_ACC):
        for j, iface in enumerate(gi):
            b.append(("GigabitEthernet", iface,
                      GR.sw_access(vlan)))
    b = bucket()
    for i in range(40):
        vlan = PV_ACC[i % len(PV_ACC)]
        voice = PV_VOICE[i % len(PV_VOICE)]
        sw = GR.sw_access(vlan)
        sw[S + "voice"] = {S + "vlan": {S + "vlan": voice}}
        b.append(("GigabitEthernet", gi[i % len(gi)], sw))
    b = bucket()
    for i in range(50):
        t, ty = PV_AGING[i % len(PV_AGING)]
        sw = GR.sw_access(PV_ACC[i % len(PV_ACC)],
                          GR.ps(aging={S + "time": t,
                                       S + "type": ty}))
        b.append(("GigabitEthernet", gi[i % len(gi)], sw))
    b = bucket()
    for i in range(40):
        sw = GR.sw_access(PV_ACC[i % len(PV_ACC)],
                          _maxplain(PV_MAX[i % len(PV_MAX)]))
        b.append(("GigabitEthernet", gi[i % len(gi)], sw))
    b = bucket()
    for i in range(40):
        sw = GR.sw_access(
            PV_ACC[i % len(PV_ACC)],
            GR.ps(max_access={S + "maximum":
                              PV_MAX[i % len(PV_MAX)],
                              S + "vlan": "access"}))
        b.append(("GigabitEthernet", gi[i % len(gi)], sw))
    b = bucket()
    for i in range(30):
        t, ty = PV_AGING[i % len(PV_AGING)]
        sw = GR.sw_access(PV_ACC[i % len(PV_ACC)],
                          GR.ps(aging={S + "time": t,
                                       S + "type": ty}))
        sw[S + "voice"] = {S + "vlan":
                           {S + "vlan": PV_VOICE[i % len(PV_VOICE)]}}
        sw[S + "port-security-config"][S + "port-security"][
            S + "maximum-address-config"] = {
                S + "maximum": {S + "maximum":
                                PV_MAX[i % len(PV_MAX)]}}
        b.append(("GigabitEthernet", gi[i % len(gi)], sw))
    b = bucket()
    for i in range(40):
        sw = GR.sw_trunk({"native": {"vlan": {
            "vlan-id": PV_NATIVE[i % len(PV_NATIVE)]}}})
        b.append(("GigabitEthernet", gi[i % len(gi)], sw))
    b = bucket()
    for i in range(40):
        sw = GR.sw_trunk({"allowed": {"vlan": {
            "vlans": PV_ALLOWED[i % len(PV_ALLOWED)]}}})
        b.append(("GigabitEthernet", gi[i % len(gi)], sw))
    b = bucket()
    for i in range(30):
        sw = GR.sw_trunk({"encapsulation":
                          PV_ENCAP[i % len(PV_ENCAP)],
                          "native": {"vlan": {
                              "vlan-id":
                              PV_NATIVE[i % len(PV_NATIVE)]}}})
        b.append(("GigabitEthernet", gi[i % len(gi)], sw))
    b = bucket()
    for i in range(30):
        sw = GR.sw_trunk({"encapsulation":
                          PV_ENCAP[i % len(PV_ENCAP)],
                          "native": {"vlan": {
                              "vlan-id":
                              PV_NATIVE[i % len(PV_NATIVE)]}},
                          "allowed": {"vlan": {
                              "vlans":
                              PV_ALLOWED[i % len(PV_ALLOWED)]}}})
        b.append(("GigabitEthernet", gi[i % len(gi)], sw))
    b = bucket()
    for i in range(40):
        if i % 2 == 0:
            sw = GR.sw_access(PV_ACC[i % len(PV_ACC)])
        else:
            sw = GR.sw_trunk({"native": {"vlan": {
                "vlan-id": PV_NATIVE[i % len(PV_NATIVE)]}}})
        b.append((TE_LIST, te[i % len(te)], sw))
    out, seen = [], set()
    idx = 0
    while True:
        progressed = False
        for b in buckets:
            if idx < len(b):
                progressed = True
                listkey, name, sw = b[idx]
                state = _pv_state(listkey, name, sw)
                key = json.dumps(state, sort_keys=True)
                if key not in seen:
                    seen.add(key)
                    out.append(state)
        if not progressed:
            break
        idx += 1
    return out


def _pv_records_for_states(states, prov, id_prefix, group_prefix,
                           directions=("A", "B")):
    """Validate (yangson) + round-trip-gate the states and build
    records. Returns (records, dropped_roundtrip, dropped_yangson).
    Instance files are written under instances_v3/pv/."""
    inst_dir = CORPUS / "instances_v3" / "pv"
    inst_dir.mkdir(parents=True, exist_ok=True)
    files, meta = [], []
    for i, state in enumerate(states):
        name = f"{id_prefix}-{i + 1:04d}-inst.json"
        (inst_dir / name).write_text(json.dumps(state, indent=1))
        files.append(name)
        meta.append((state, f"instances_v3/pv/{name}"))
    verdicts = G.run_validator(BUNDLE, LIBRARY, inst_dir,
                               sorted(files))
    records = []
    n = 0
    drop_rt = drop_y = 0
    for state, inst_ref in meta:
        fname = inst_ref.split("/")[-1]
        v = verdicts[fname]
        if v["verdict"] != "valid":
            drop_y += 1
            continue
        cli = render_cli(state)
        try:
            back = parse_cli(cli)
        except Exception:  # noqa: BLE001
            drop_rt += 1
            continue
        if back != state:
            drop_rt += 1
            continue
        root = state[ROOT]
        (listkey, ifaces), = root["interface"].items()
        sw = ifaces[0]["switchport-config"]["switchport"]
        entries = root["vlan"][V + "configuration-entry"]
        subjects = _pv_subjects(sw, entries)
        group = f"{group_prefix}:" + hashlib.sha256(
            json.dumps(state, sort_keys=True).encode()
        ).hexdigest()[:12]
        state_json = json.dumps(state, indent=1)
        for d in directions:
            n += 1
            if d == "A":
                prompt = (f"Here is an IOS XE interface "
                          f"configuration in CLI form. Return the "
                          f"equivalent instance of {MODEL} as "
                          f"JSON.\n\n{cli}")
                completion = state_json
            else:
                prompt = (f"Here is an instance of {MODEL}. "
                          f"Return the equivalent IOS XE CLI "
                          f"configuration.\n\n{state_json}")
                completion = cli
            records.append({
                "id": f"{id_prefix}-{n:04d}",
                "family": "paired_view",
                "origin": "template_instance_v3",
                "scenario_group": group,
                "prompt": prompt, "completion": completion,
                "fact": {"direction": "cli_to_yang" if d == "A"
                         else "yang_to_cli",
                         "subject_paths": subjects,
                         "instance_file": inst_ref},
                "source": GR.source_for(subjects, prov),
                "validation": {
                    "tool": "yangson+ciscoconfparse2-round-trip",
                    "verdict": v["verdict"],
                    "round_trip": "pass"}})
    return records, drop_rt, drop_y


def gen_pv(force: bool = False, n_states: int = 400) -> dict:
    if not batch_guard("pv", force):
        return {"skipped": True}
    prov = GR.build_prov_map(BUNDLE)
    states = pv_candidate_states()[:n_states]
    records, drop_rt, drop_y = _pv_records_for_states(
        states, prov, "V3-PV", "pv3", directions=("A", "B"))
    # direction mix: A for every state, B for the first 200
    # states (plan section 2: A 400 / B 200 at 400 states)
    a = [r for r in records
         if r["fact"]["direction"] == "cli_to_yang"]
    b = [r for r in records
         if r["fact"]["direction"] == "yang_to_cli"]
    records = a + b[:len(a) // 2]
    generated = len(records)
    prompts = all_prompts(exclude=PARTS / "pv.jsonl")
    records, dup = dedupe(records, prompts)
    assign_splits(records, "paired_view")
    write_part("pv", records)
    return report("pv", {
        "states": len(states), "generated": generated,
        "dropped_roundtrip": drop_rt, "dropped_yangson": drop_y,
        "deduped": dup, "final": len(records),
        "dir_a": sum(1 for r in records
                     if r["fact"]["direction"] == "cli_to_yang"),
        "dir_b": sum(1 for r in records
                     if r["fact"]["direction"] == "yang_to_cli"),
        "train": sum(1 for r in records if r["split"] == "train"),
        "dev": sum(1 for r in records if r["split"] == "dev")})


# ------------------------------------------------------------- holdout

HOLDOUT_IFS = ["3/0/1", "3/0/2"]
HOLDOUT_GRIDS = {
    "set_access_vlan": [(60, 70)],
    "swap_mode": ["access", "trunk"],
    "set_native_vlan": [888],
    "add_entry": [40, 50],
    "set_voice_vlan": [75],
    "remove_entry": [40],
    "set_allowed": [("40", "40,50")],
    "set_encap": ["dot1q"],
}
HOLDOUT_POOLS = [["40", "50"], ["40"], ["50"]]


def _holdout_iv_cases():
    """35 cases on the reserved interfaces with trap values
    disjoint from the train rings (plan section 5)."""
    cases = []

    def add(cls, label, data, intended, rule, paths, frag):
        cases.append({"cls": cls, "label": label, "data": data,
                      "intended": intended, "rule": rule,
                      "paths": paths, "frag": frag, "variants": 1})

    F_ACC = ("Cisco-IOS-XE-switch:access/"
             "Cisco-IOS-XE-switch:vlan")
    R_ACC = ("access/vlan/vlan is a union of uint16 range 1..4094 "
             "and the enumeration {dynamic}; the value matches "
             "neither")
    F = HOLDOUT_IFS[0]
    add("range_access", "access VLAN 4100", G._acc(4100, F),
        "invalid", R_ACC, [G.P_ACCESS], F_ACC)
    add("range_access", "access VLAN 4090", G._acc(4090, F),
        "valid", None, [G.P_ACCESS], F_ACC)
    add("range_aging", "aging time 1437",
        G._acc(10, F, GR.ps(aging={S + "time": 1437})), "valid",
        None, [G.P_AGING + "/time"], "aging")
    add("range_aging", "aging time 1445",
        G._acc(10, F, GR.ps(aging={S + "time": 1445})), "invalid",
        "port-security aging/time is uint16 range 1..1440",
        [G.P_AGING + "/time"], "aging")
    R_ENT = ("configuration-entry/vlan-id is a string whose "
             "pattern encodes VLAN IDs 1..4094 (and range lists); "
             "the key does not match")
    add("pattern_entry", "entry key '3998'",
        GR.doc([GR.ge(GR.sw_access(10), name=F)],
               [{"vlan-id": "3998"}]), "valid", None,
        [G.P_ENTRY], "configuration-entry")
    add("pattern_entry", "entry key '4000-4001'",
        GR.doc([GR.ge(GR.sw_access(10), name=F)],
               [{"vlan-id": "4000-4001"}]), "invalid", R_ENT,
        [G.P_ENTRY], "configuration-entry")
    R_UNI = ("access/vlan/vlan is a union of uint16 range 1..4094 "
             "and the enumeration {dynamic}; this JSON value "
             "matches no branch (a string is not the numeric "
             "branch)")
    add("union_access", "access VLAN '4099'", G._acc("4099", F),
        "invalid", R_UNI, [G.P_ACCESS], F_ACC)
    add("union_access", "access VLAN 4091", G._acc(4091, F),
        "valid", None, [G.P_ACCESS], F_ACC)
    add("enum_encap", "encapsulation dOt1q",
        G._trk({"encapsulation": "dOt1q"}, F), "invalid",
        "trunk/encapsulation is an enumeration of "
        "{dot1q, isl, negotiate}", [G.P_ENCAP], "encapsulation")
    add("enum_encap", "encapsulation isl",
        G._trk({"encapsulation": "isl"}, F), "valid", None,
        [G.P_ENCAP], "encapsulation")
    mode = {S + "mode": {S + "trunk": {}, S + "dot1q-tunnel": {},
                         S + "dynamic": "auto"},
            S + "access": {S + "vlan": {S + "vlan": 10}}}
    add("choice_mode", "mode cases ['trunk', 'dot1q-tunnel', "
        "'dynamic']", GR.doc([GR.ge(mode, name=F)],
                             [{"vlan-id": "10"}]), "invalid",
        "mode/mode-choice is a YANG choice: at most one case may "
        "be present; this instance sets two",
        [G.P_MODE], "Cisco-IOS-XE-switch:mode")
    add("choice_mode", "mode cases ['access']",
        G._acc(10, F), "valid", None, [G.P_MODE],
        "Cisco-IOS-XE-switch:mode")
    add("range_maxcount", "maxcount max-addresses 4099",
        G._acc(10, F, _maxcount(4099)), "invalid",
        "port-security maxcount/max-addresses is uint16 range "
        "1..4097", [P_MAXCOUNT], "max-addresses")
    add("range_maxcount", "maxcount max-addresses 4095",
        G._acc(10, F, _maxcount(4095)), "valid", None,
        [P_MAXCOUNT], "max-addresses")
    add("violation_choice", "violation cases ['protect', "
        "'shutdown']",
        G._acc(10, F, _psx({S + "violation":
                            _vio_inner(["protect", "shutdown"])})),
        "invalid",
        "port-security violation is a YANG choice: at most one "
        "case (protect, restrict, report, shutdown) may be "
        "present; this instance sets more than one",
        [P_VIOLATION], "violation")
    add("violation_choice", "violation case shutdown",
        G._acc(10, F, _psx({S + "violation":
                            _vio_inner(["shutdown"])})),
        "valid", None, [P_VIOLATION], "violation")
    R_MAC = ("switchport port-security hw-address must match "
             "the cisco-mac-address-type pattern: three groups "
             "of hexadecimal digits separated by dots")

    def mac_data(v):
        return G._acc(10, F, _psx(
            {S + "mac-address-config": {S + "mac-address":
             {S + "hw-address-access": [{S + "hw-address": v}]}}}))

    add("mac_pattern", "hw-address 00-11-22-33-44-55",
        mac_data("00-11-22-33-44-55"), "invalid", R_MAC, [P_HWD],
        "hw-address")
    add("mac_pattern", "hw-address 00aa.bbcc.dd11",
        mac_data("00aa.bbcc.dd11"), "valid", None, [P_HWD],
        "hw-address")
    tunnel_sw = {S + "mode": {S + "dot1q-tunnel": {}},
                 S + "access": {S + "vlan": {S + "vlan": 10}}}
    tunnel_sw.update(GR.ps(max_access={S + "maximum": 75,
                                       S + "vlan": "access"}))
    add("when_maxacc", "maximum-vlan-access 75 with mode "
        "dot1q-tunnel",
        GR.doc([GR.ge(tunnel_sw, name=F)], [{"vlan-id": "10"}]),
        "invalid",
        "maximum-vlan-access carries when "
        "'../../../../mode/access' — it requires the mode "
        "choice's access case", [G.P_MAXACC], "maximum-addres")
    add("range_native", "native VLAN 4100",
        G._trk({"native": {"vlan": {"vlan-id": 4100}}}, F),
        "invalid", "trunk/native/vlan/vlan-id is uint16 range "
        "1..4094", [G.P_NATIVE], "native")
    add("range_native", "native VLAN 4088",
        G._trk({"native": {"vlan": {"vlan-id": 4088}}}, F),
        "valid", None, [G.P_NATIVE], "native")
    R_VOI = ("voice/vlan/vlan is a union of the enumeration "
             "{dot1p, none, untagged}, uint16 range 1..4094, and "
             "string; this non-string value is outside 1..4094, "
             "so it matches no branch")
    for v, ok in [(4099, 0), ("none", 1)]:
        sw = GR.sw_access(10)
        sw[S + "voice"] = {S + "vlan": {S + "vlan": v}}
        add("union_voice", f"voice VLAN {v!r}",
            GR.doc([GR.ge(sw, name=F)], [{"vlan-id": "10"}]),
            "valid" if ok else "invalid", None if ok else R_VOI,
            [G.P_VOICE], "voice")
    add("range_maximum", "maximum addresses 5000",
        G._acc(10, F, _maxplain(5000)), "invalid",
        "port-security maximum-address-config/maximum/maximum "
        "is uint16 range 1..4096", [P_MAXPLAIN],
        "maximum/maximum")
    add("range_maximum", "maximum addresses 4000",
        G._acc(10, F, _maxplain(4000)), "valid", None,
        [P_MAXPLAIN], "maximum/maximum")
    add("enum_dynamic", "dynamic mode 'auto '",
        GR.doc([GR.ge({S + "mode": {S + "dynamic": "auto "},
                       S + "access": {S + "vlan":
                                      {S + "vlan": 10}}}, name=F)],
               [{"vlan-id": "10"}]), "invalid",
        "mode/dynamic is an enumeration of {auto, desirable}",
        [P_DYNAMIC], "dynamic")
    add("enum_dynamic", "dynamic mode desirable",
        GR.doc([GR.ge({S + "mode": {S + "dynamic": "desirable"},
                       S + "access": {S + "vlan":
                                      {S + "vlan": 10}}}, name=F)],
               [{"vlan-id": "10"}]), "valid", None, [P_DYNAMIC],
        "dynamic")
    R_IFN = ("the GigabitEthernet list key 'name' must match the "
             "pattern (0|[1-9][0-9]*)(/(0|[1-9][0-9]*))*(\\.[0-9]*)? "
             "— numeric slot/port form only")
    P_NAME = "/native/interface/GigabitEthernet/name"
    add("pattern_ifname", "interface name '3/0/01'",
        G._acc(10, "3/0/01"), "invalid", R_IFN, [P_NAME],
        "GigabitEthernet")
    add("pattern_ifname", "interface name '3/0/2.5'",
        G._acc(10, "3/0/2.5"), "valid", None, [P_NAME],
        "GigabitEthernet")
    add("range_maxva", "maximum-vlan-access maximum 8192",
        G._acc(10, F, GR.ps(max_access={S + "maximum": 8192,
                                        S + "vlan": "access"})),
        "invalid",
        "port-security maximum-vlan-access/maximum is uint16 "
        "range 1..4096", [P_MAXVA], "maximum-vlan-access")
    add("range_maxva", "maximum-vlan-access maximum 4000",
        G._acc(10, F, GR.ps(max_access={S + "maximum": 4000,
                                        S + "vlan": "access"})),
        "valid", None, [P_MAXVA], "maximum-vlan-access")
    add("union_allowed", "allowed vlans 4099",
        G._trk({"allowed": {"vlan": {"vlans": 4099}}}, F),
        "invalid",
        "trunk allowed vlans is a union of uint16 range 1..4094 "
        "and string; this non-string value is outside 1..4094, "
        "so it matches neither branch", [G.P_ALLOWED], "vlans")
    add("when_maxvlan", "maximum-vlan with mode access",
        G._acc(10, F, _maxvlan({S + "maximum": 200,
                                S + "vlan": "vlan"})),
        "invalid",
        "maximum-vlan carries when "
        "'../../../../../../mode/trunk' — it requires the mode "
        "choice's trunk case", [P_MAXVLAN],
        "maximum-address-config")
    add("enum_maxvlan", "maximum-vlan-access vlan 'none'",
        G._acc(10, F, GR.ps(max_access={S + "maximum": 100,
                                        S + "vlan": "none"})),
        "invalid",
        "port-security maximum-vlan-access/vlan is an "
        "enumeration of {access}", [G.P_MAXACC + "/vlan"],
        "maximum-vlan-access")
    return cases


def _retarget_iface(data, iface):
    d = copy.deepcopy(data)
    d[ROOT]["interface"]["GigabitEthernet"][0]["name"] = iface
    return d


def gen_holdout(force: bool = False) -> dict:
    if HOLDOUT_FILE.exists() and not force:
        print(f"holdout: {HOLDOUT_FILE.name} exists — skipping "
              f"(use --force to regenerate)")
        return {"skipped": True}
    prov = GR.build_prov_map(BUNDLE)
    _PM_PROV.update(prov)
    facts = {f["path"]: f for f in inventory(BUNDLE)}
    _deduped, bench_keys = pm_pool(facts)
    records: list[dict] = []
    dropped_all: list = []

    # PM: 50 records at TwentyFiveGigE/HundredGigE paths only
    cand = [f for f in facts.values()
            if if_family(f["path"]) in HOLDOUT_FAMS
            and (norm_path(f["path"]), f["type"]) not in bench_keys]
    cand.sort(key=lambda f: f["path"])
    by_fam = defaultdict(list)
    for f in cand:
        by_fam[if_family(f["path"])].append(f)
    picks = []
    for fam in sorted(by_fam):
        lst = by_fam[fam]
        step = max(1, len(lst) // 25)
        picks += lst[::step][:25]
    for i, f in enumerate(picks, 1):
        rec = _pm_type_rec(f, f"V3-HPM-{i:03d}",
                           "generated_fact_v3_holdout")
        rec["scenario_group"] = f"pmh:{f['path']}"
        rec["split"] = "held_out_v3"
        records.append(rec)

    # IV: 35 cases x the two reserved interfaces = 70
    iv_recs, must_recs = [], []
    n = 0
    per_class = Counter()
    for c in _holdout_iv_cases():
        c["_case_idx"] = per_class[c["cls"]]
        per_class[c["cls"]] += 1
        for iface in HOLDOUT_IFS:
            n += 1
            rid = f"V3-HIV-{n:03d}"
            data = _retarget_iface(c["data"], iface)
            rec = _iv_record(c, rid, data, prov)
            rec["origin"] = "template_instance_v3_holdout"
            rec["scenario_group"] = f"ivh:{c['cls']}"
            rec["split"] = "held_out_v3"
            iv_recs.append(rec)
    for c in must_cases():
        if "maximum-vlan-access" not in c["label"]:
            continue
        c["_case_idx"] = 0
        for iface in HOLDOUT_IFS:
            n += 1
            rid = f"V3-HIV-{n:03d}"
            rec = _iv_record(c, rid,
                             _retarget_iface(c["data"], iface),
                             prov)
            rec["origin"] = "template_instance_v3_holdout"
            rec["scenario_group"] = "ivh:must_sibling"
            rec["split"] = "held_out_v3"
            must_recs.append(rec)
    kept, dropped = G.validate_all(
        iv_recs, BUNDLE, LIBRARY,
        CORPUS / "instances_v3" / "holdout_iv",
        "instances_v3/holdout_iv")
    kept_m, dropped_m = _validate_must(must_recs, prov,
                                       dirname="holdout_iv_must")
    for r in kept + kept_m:
        r["split"] = "held_out_v3"
    records += kept + kept_m
    dropped_all += dropped + dropped_m

    # RS: profiles P13/P14 (36 records)
    texts_h = {p: t for p, (t, _r) in RS_HOLDOUT_PROFILES.items()}
    specs_h = []
    for pname, (_t, rules) in RS_HOLDOUT_PROFILES.items():
        for cite, path, kind, param, label, noun, values in rules:
            specs_h.append((pname, cite, path, kind, param, label,
                            noun, values))
    rs_recs = _rs_records(
        specs_h, texts_h, prov, "V3-HRS", "rsh",
        phrasings_for=lambda idx, b: 2 if idx == 0 else 1)
    for r in rs_recs:
        r["origin"] = "template_instance_v3_holdout"
        r["split"] = "held_out_v3"
    records += rs_recs

    # SR/TE: holdout grid values on reserved interfaces
    saved = (G.KIND_GRIDS, G.ENTRY_POOLS, G.TRAIN_IFS)
    G.KIND_GRIDS, G.ENTRY_POOLS = HOLDOUT_GRIDS, HOLDOUT_POOLS
    G.TRAIN_IFS = ["3/0/1", "3/0/2", "3/0/3"]
    try:
        srte = G.gen_scenarios("train", prov, "V3H", 4, te_stride=3)
    finally:
        G.KIND_GRIDS, G.ENTRY_POOLS, G.TRAIN_IFS = saved
    for r in srte:
        r["origin"] = "template_instance_v3_holdout"
        r["scenario_group"] = r["scenario_group"].replace(
            "scen:train:", "scenh:")
        r["split"] = "held_out_v3"
    prompts = all_prompts(V1_FILES + V2_FILES + EXTRA_FILES
                          + part_files())
    srte, dup_h = dedupe(srte, prompts)
    kept_s, dropped_s = G.validate_all(
        srte, BUNDLE, LIBRARY,
        CORPUS / "instances_v3" / "holdout_srte",
        "instances_v3/holdout_srte")
    for r in kept_s:
        r["split"] = "held_out_v3"
    records += kept_s
    dropped_all += dropped_s

    # paired_view: 24 reserved states x 2 directions = 48
    states = []
    for iface in HOLDOUT_IFS:
        combos = []
        combos.append(GR.sw_access(18))
        combos.append(GR.sw_access(28))
        sw = GR.sw_access(18)
        sw[S + "voice"] = {S + "vlan": {S + "vlan": 28}}
        combos.append(sw)
        combos.append(GR.sw_access(
            28, GR.ps(aging={S + "time": 18,
                             S + "type": "absolute"})))
        combos.append(GR.sw_access(18, _maxplain(18)))
        sw = GR.sw_access(28)
        sw[S + "voice"] = {S + "vlan": {S + "vlan": 28}}
        sw.update(GR.ps(aging={S + "time": 18,
                               S + "type": "absolute"}))
        combos.append(sw)
        combos.append(GR.sw_trunk({"native": {"vlan": {
            "vlan-id": 18}}}))
        combos.append(GR.sw_trunk({"allowed": {"vlan": {
            "vlans": "18,28"}}}))
        combos.append(GR.sw_trunk({"encapsulation": "dot1q",
                                   "native": {"vlan": {
                                       "vlan-id": 18}}}))
        combos.append(GR.sw_trunk({"encapsulation": "dot1q",
                                   "native": {"vlan": {
                                       "vlan-id": 18}},
                                   "allowed": {"vlan": {
                                       "vlans": "18,28"}}}))
        combos.append(GR.sw_access(
            18, GR.ps(max_access={S + "maximum": 18,
                                  S + "vlan": "access"})))
        combos.append(GR.sw_trunk({"native": {"vlan": {
            "vlan-id": 28}}}))
        for sw in combos:
            states.append(_pv_state("GigabitEthernet", iface, sw))
    pv_recs, drop_rt, drop_y = _pv_records_for_states(
        states, prov, "V3-HPV", "pvh", directions=("A", "B"))
    for r in pv_recs:
        r["origin"] = "template_instance_v3_holdout"
        r["split"] = "held_out_v3"
    records += pv_recs

    records.sort(key=lambda r: r["id"])
    with HOLDOUT_FILE.open("w") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")
    fams = Counter(r["family"] for r in records)
    return report("holdout", {
        "final": len(records), "by_family": dict(fams),
        "pm": len(picks), "iv": len(kept) + len(kept_m),
        "rs": len(rs_recs), "srte": len(kept_s),
        "pv": len(pv_recs), "pv_dropped_roundtrip": drop_rt,
        "pv_dropped_yangson": drop_y,
        "dropped_validation": len(dropped_all),
        "dropped_detail": [d[0] for d in dropped_all][:12]})


# ------------------------------------------------------------ finalize

def _token_counts(records):
    from tokenizers import Tokenizer
    tok = Tokenizer.from_file("/tmp/qwen-tok/tokenizer.json")
    per_family: dict[str, list[int]] = defaultdict(
        lambda: [0, 0, 0])
    for r in records:
        p = len(tok.encode(r["prompt"]).tokens)
        c = len(tok.encode(r["completion"]).tokens)
        e = per_family[r["family"]]
        e[0] += 1
        e[1] += p + c
        e[2] = max(e[2], p + c)
    return {k: {"records": v[0], "tokens": v[1], "max_len": v[2]}
            for k, v in sorted(per_family.items())}


def gen_finalize() -> dict:
    parts = {}
    for name in ("pm", "iv", "rs", "srte", "pv"):
        p = PARTS / f"{name}.jsonl"
        if not p.exists():
            raise SystemExit(f"missing part file {p}")
        parts[name] = load_jsonl(p)
    all_v3 = [r for name in ("pm", "iv", "rs", "srte", "pv")
              for r in parts[name]]
    train_add = sorted((r for r in all_v3 if r["split"] == "train"),
                       key=lambda r: r["id"])
    dev_add = sorted((r for r in all_v3 if r["split"] == "dev"),
                     key=lambda r: r["id"])
    for fname, recs in (("train_v3_additions.jsonl", train_add),
                        ("dev_v3_additions.jsonl", dev_add)):
        (CORPUS / fname).write_text(
            "".join(json.dumps(r) + "\n" for r in recs))
    v1_train = load_jsonl(CORPUS / "train.jsonl")
    v2_train = load_jsonl(CORPUS / "train_v2_additions.jsonl")
    combined = v1_train + v2_train + train_add
    (CORPUS / "train_v3_combined.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in combined))
    holdout = load_jsonl(HOLDOUT_FILE)
    tokens_add = _token_counts(train_add)
    tokens_combined = _token_counts(combined)
    addendum = {
        "corpus": "v3 additions (tranche 1; train/dev only; "
                  "benchmark v1 frozen, untouched)",
        "generator": "gen_corpus_v3.py",
        "plan": "yang-learn/CORPUS_V3_PLAN.md @ fa85fbc",
        "splits": {
            "train_additions": {
                "total": len(train_add),
                "by_family": dict(Counter(
                    r["family"] for r in train_add))},
            "dev_additions": {
                "total": len(dev_add),
                "by_family": dict(Counter(
                    r["family"] for r in dev_add))},
            "combined_train_total": len(combined)},
        "held_out_v3": {
            "total": len(holdout),
            "by_family": dict(Counter(
                r["family"] for r in holdout)),
            "file": "held_out_v3.jsonl"},
        "tokens": {
            "tokenizer": "Qwen/Qwen3-4B-Base @ 906bfd4b",
            "train_additions_by_family": tokens_add,
            "train_additions_total": sum(
                v["tokens"] for v in tokens_add.values()),
            "combined_by_family": tokens_combined,
            "combined_total": sum(
                v["tokens"] for v in tokens_combined.values())},
        "validation": "PM facts via pyang inventory; IV/SR/TE "
                      "verdicts via yangson (validate_all; "
                      "must_sibling via the documented "
                      "SemanticError pass); RS verdicts via "
                      "in-generator predicates; paired_view via "
                      "yangson + ciscoconfparse2 round-trip "
                      "equality; all additions prompt-deduped "
                      "against every earlier split and part",
    }
    (CORPUS / "manifest_v3_addendum.json").write_text(
        json.dumps(addendum, indent=1) + "\n")
    return report("finalize", {
        "train_additions": len(train_add),
        "dev_additions": len(dev_add),
        "combined_train": len(combined),
        "train_additions_by_family": dict(Counter(
            r["family"] for r in train_add)),
        "tokens_train_additions": addendum["tokens"]
        ["train_additions_total"],
        "tokens_combined": addendum["tokens"]["combined_total"]})


if __name__ == "__main__":
    raise SystemExit(main())
