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
EXTRA_FILES = [CORPUS / "held_out.jsonl", HERE / "records.jsonl"]
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
P_HWD = f"{GR.SW}/hw-address"
P_DYNAMIC = f"{GR.SW}/mode/mode-choice/dynamic-case/dynamic"


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


def all_prompts(files=None) -> set[str]:
    out = set()
    for fn in (files or existing_files()):
        for r in load_jsonl(fn):
            out.add(r["prompt"])
    return out


def pm_used_paths(files=None) -> set[str]:
    out = set()
    for fn in (files or existing_files()):
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

    used_paths = pm_used_paths()
    prompts = all_prompts()

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
