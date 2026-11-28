#!/usr/bin/env python3
"""Generate coverage/data/coverage.json for the YANG coverage dashboard.

Measures how much of the IOS XE 26.2.1 native YANG surface (Catalyst 9K
family set, YangModels/yang pinned bundle) the yang-learn corpus v1
exercises, per canonical schema path.

Method (see coverage/README.md for the full methodology):
  * Inventory: pyang resolves the bundle's Cisco-IOS-XE modules
    (config-bearing set: everything except *-oper / *-rpc / *-events /
    *-actions-rpc modules, which are excluded by rule and counted as
    classes). The tree walked is the /native root of
    Cisco-IOS-XE-native with all augments applied.
  * Canonical leaf path: the schema path with choice/case segments
    elided (the corpus's own path convention — a choice is not
    addressable in instance data). Leaves are leaf/leaf-list data
    nodes. A leaf is ELIGIBLE for the denominator iff it is
    config-true and its status is not "obsolete". Deprecated leaves
    remain eligible (still supported) and are counted separately.
  * Coverage: a corpus record covers exactly the leaves it directly
    exercises (rules per family in extract_record_paths). Records
    that name a construct (container / list / choice / case) attach
    as construct references to that tree node and cover NO leaves.
  * Rollups: every percentage is descendant covered/eligible leaf
    counts — never an average of child percentages.

Deterministic: same bundle + same corpus -> same JSON (records and
leaves are sorted; no timestamps inside data sections except the
provenance block's generated_at, which --no-timestamp omits).
meta.fingerprint records sha256 hashes of every input (corpus
splits, scores snapshot, priorities, bundle digest, this generator)
so coverage/check_freshness.py can detect a stale committed JSON
without re-running pyang.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

BUNDLE_PIN = "9c23db975096b67843858128f37947a0c30a2d45"
ROOT_MODULE = "Cisco-IOS-XE-native"
EXCLUDED_FILE_RE = re.compile(r"-(oper|rpc|events|actions-rpc)\.yang$")
DATA_SEG = {"container", "list", "leaf", "leaf-list"}
ALL_SEG = DATA_SEG | {"choice", "case"}
SPLITS = ["train", "dev", "test", "benchmark", "held_out"]
SPLIT_FILES = {
    "train": "yang-learn/corpus/train.jsonl",
    "dev": "yang-learn/corpus/dev.jsonl",
    "test": "yang-learn/corpus/test.jsonl",
    "benchmark": "yang-learn/corpus/benchmark.jsonl",
    "held_out": "yang-learn/held_out.jsonl",
}
# Snapshot of outputs/yang-learn-run1/scores.json (run-1 bench v1
# scores, produced by yang-learn/score_bench.py in the main checkout;
# the outputs/ tree is not committed, so the snapshot is vendored
# here to keep the dashboard reproducible from this repo alone).
SCORES_FILE = "coverage/data/run1_scores.json"

# Hand-authored held-out records whose subject is a construct or a
# leaf named only in prose. Mapped by hand from the record text;
# see README "Evidence rules". HO-07/HO-08 (revised_schema profiles)
# name no schema node and stay unmapped (family-level pass only).
HAND_EVIDENCE = {
    # mode as a leaf (wrapper surface) + the mode choice construct.
    "YL-HO-04": {
        "leaves": ["/native/interface/GigabitEthernet/switchport-wrapper"
                   "/switchport/mode"],
        "constructs": ["/native/interface/GigabitEthernet"
                       "/switchport-config/switchport/mode"],
    },
    # mode choice exclusivity — construct only, no leaf.
    "YL-HO-05": {
        "leaves": [],
        "constructs": ["/native/interface/GigabitEthernet"
                       "/switchport-config/switchport/mode"],
    },
}


# ---------------------------------------------------------------- paths

def transparent_path(stmt) -> str:
    """Canonical path: container/list/leaf segments only (choice and
    case elided) — the corpus's path convention."""
    parts = []
    node = stmt
    while node is not None:
        if node.keyword in DATA_SEG:
            parts.append(node.arg)
        node = getattr(node, "i_parent", None) or node.parent
        if node is not None and node.keyword in ("module", "submodule"):
            break
    return "/" + "/".join(reversed(parts))


def full_path(stmt) -> str:
    """Schema path including choice/case segments (pyang schema_path
    convention, as used by yang-learn/schema_tools.py)."""
    parts = []
    node = stmt
    while node is not None:
        if node.keyword in ALL_SEG:
            parts.append(node.arg)
        node = getattr(node, "i_parent", None) or node.parent
        if node is not None and node.keyword in ("module", "submodule"):
            break
    return "/" + "/".join(reversed(parts))


def defining_file(stmt) -> str:
    ref = getattr(stmt.pos, "ref", "") or ""
    return Path(str(ref)).name if ref else ""


# ---------------------------------------------------------------- inventory

def yang_set_deviations(bundle: Path) -> set[str]:
    """Deviation module names advertised in the bundle's
    yang-set-cat9k.xml. The bundle directory is shared across
    platform sets (e.g. Cisco-IOS-XE-vlan-vxe-deviation belongs to a
    different set and marks /native/vlan/internal/allocation/policy
    not-supported); only the advertised set is device truth for
    cat9k. The Cisco-IOS-XE subset equals the 27 deviations in
    yang-learn/schema_tools.py."""
    import xml.etree.ElementTree as ET
    ns = {"y": "urn:ietf:params:xml:ns:yang:ietf-yang-library"}
    tree = ET.parse(bundle / "yang-set-cat9k.xml")
    devs = set()
    for mel in tree.getroot().iter(
            "{urn:ietf:params:xml:ns:yang:ietf-yang-library}module"):
        for d in mel.findall("y:deviation", ns):
            n = d.find("y:name", ns)
            if n is not None and n.text:
                devs.add(n.text.strip())
    return devs


def load_schema(bundle: Path):
    from pyang import context, repository
    advertised_devs = yang_set_deviations(bundle)
    files = sorted(bundle.glob("Cisco-IOS-XE-*.yang"))
    load_files, skipped, skipped_devs = [], [], []
    for f in files:
        if EXCLUDED_FILE_RE.search(f.name):
            skipped.append(f.name)
        elif f.name.endswith("-deviation.yang") and (
                f.stem not in advertised_devs):
            skipped_devs.append(f.name)
        else:
            load_files.append(f)
    repo = repository.FileRepository(str(bundle))
    ctx = context.Context(repo)
    for f in load_files:
        with open(f, encoding="utf-8") as fh:
            ctx.add_module(f.name, fh.read())
    ctx.validate()
    err_tags = Counter()
    for e in ctx.errors:
        try:
            err_tags[e[1].tag] += 1
        except Exception:
            err_tags["unparsed"] += 1
    info = {"loaded": [f.name for f in load_files],
            "skipped_excluded_class": skipped,
            "skipped_unadvertised_deviations": skipped_devs}
    return ctx, info, dict(err_tags)


def iter_data(stmt):
    for ch in getattr(stmt, "i_children", []) or []:
        if ch.keyword in ALL_SEG:
            yield ch
            yield from iter_data(ch)


def build_inventory(ctx):
    """Walk the resolved /native tree. Returns (leaves, stmt_index,
    other_roots) where leaves are dicts keyed by transparent path,
    stmt_index maps full paths -> (kind, transparent path), and
    other_roots summarises config roots outside /native in loaded
    modules (counted, not inventoried)."""
    mod = ctx.get_module(ROOT_MODULE)
    leaves: dict[str, dict] = {}
    collisions = 0
    stmt_index: dict[str, tuple[str, str]] = {}
    for top in getattr(mod, "i_children", []) or []:
        for st in iter_data(top):
            tp = transparent_path(st)
            fp = full_path(st)
            stmt_index.setdefault(fp, (st.keyword, tp))
            if st.keyword not in ("leaf", "leaf-list"):
                # Constructs are also addressed by their transparent
                # path in corpus evidence (e.g. SR states name the
                # mode case's presence container as .../mode/access).
                stmt_index.setdefault(tp, (st.keyword, tp))
            if st.keyword in ("leaf", "leaf-list"):
                status = st.search_one("status")
                status = status.arg if status is not None else "current"
                rec = {
                    "path": tp, "kind": st.keyword,
                    "config": bool(getattr(st, "i_config", True)),
                    "status": status, "file": defining_file(st),
                }
                if tp in leaves:
                    collisions += 1
                else:
                    leaves[tp] = rec
    # Non-/native config roots in other loaded modules: counted only.
    other_roots = []
    seen_mod_ids = set()
    for key, m in ctx.modules.items():
        name = key[0] if isinstance(key, tuple) else key
        if name == ROOT_MODULE or id(m) in seen_mod_ids:
            continue
        seen_mod_ids.add(id(m))
        for top in getattr(m, "i_children", []) or []:
            if top.keyword not in DATA_SEG:
                continue
            n_eligible = n_leaves = 0
            for st in iter_data(top):
                if st.keyword in ("leaf", "leaf-list"):
                    n_leaves += 1
                    status = st.search_one("status")
                    status = status.arg if status is not None else "current"
                    if getattr(st, "i_config", True) and status != "obsolete":
                        n_eligible += 1
            if n_leaves:
                other_roots.append({
                    "module": name, "root": "/" + top.arg,
                    "leaves": n_leaves, "eligible_leaves": n_eligible,
                })
    other_roots.sort(key=lambda r: (r["root"], r["module"]))
    return leaves, stmt_index, other_roots, collisions


def yang_set_classes(bundle: Path) -> dict:
    """Module-class census of the bundle's yang-set-cat9k.xml plus the
    deviation not-supported count (static, from deviation sources)."""
    import xml.etree.ElementTree as ET
    ns = {"y": "urn:ietf:params:xml:ns:yang:ietf-yang-library"}
    tree = ET.parse(bundle / "yang-set-cat9k.xml")
    names = {}
    for mel in tree.getroot().iter(
            "{urn:ietf:params:xml:ns:yang:ietf-yang-library}module"):
        n = mel.find("y:name", ns)
        if n is None or not n.text:
            continue
        ct = mel.find("y:conformance-type", ns)
        names[n.text.strip()] = (ct.text.strip()
                                 if ct is not None and ct.text else "import")
    implement = {n for n, c in names.items() if c == "implement"}

    def cls(n: str) -> str:
        if n.endswith("-oper"):
            return "oper (operational state)"
        if n.endswith("-rpc") or n.endswith("-actions-rpc"):
            return "rpc"
        if n.endswith("-events"):
            return "events (notifications)"
        if n.endswith("-types") or n.endswith("-tc"):
            return "types only (no data nodes)"
        if "deviation" in n:
            return "deviation"
        if n.endswith("-MIB") or "-MIB" in n:
            return "SNMP MIB module"
        if n.startswith("openconfig-"):
            return "OpenConfig (separate model)"
        if n.startswith("ietf-"):
            return "IETF (separate/infrastructure model)"
        if n.startswith("tailf-"):
            return "tailf (server infrastructure)"
        if n.startswith("Cisco-IOS-XE-"):
            return "Cisco-IOS-XE"
        return "other vendor/helper"

    census = Counter(cls(n) for n in implement)
    advertised = yang_set_deviations(bundle)
    not_supported = 0
    dev_files = 0
    for name in sorted(advertised):
        f = bundle / f"{name}.yang"
        if f.exists():
            dev_files += 1
            not_supported += f.read_text(encoding="utf-8").count(
                "deviate not-supported")
    return {
        "yang_set_modules": len(names),
        "yang_set_implement": len(implement),
        "implement_by_class": dict(sorted(census.items())),
        "deviation_modules_advertised_in_yang_set": len(advertised),
        "deviation_files_applied": dev_files,
        "deviate_not_supported_statements": not_supported,
    }


# ---------------------------------------------------------------- evidence

_SEG_MOD_RE = re.compile(r"\[\d+\]$")
_ANNOT_RE = re.compile(r"\s*\((added|removed|changed)\)\s*$")


def canon_instance_path(p: str) -> str:
    """Canonicalise an instance-style path (module-prefixed segments,
    [i] list indices, trailing annotations) to a transparent schema
    path."""
    p = _ANNOT_RE.sub("", p.strip())
    parts = []
    for seg in p.split("/"):
        if not seg:
            continue
        seg = seg.split(":")[-1]
        seg = _SEG_MOD_RE.sub("", seg)
        parts.append(seg)
    out = "/" + "/".join(parts)
    if out.startswith("/interface/"):
        out = "/native" + out
    return out


def _src_paths(record: dict) -> set[str]:
    out = set()
    src = record.get("source") or {}
    for m in src.get("modules") or []:
        for p in (m or {}).get("paths") or []:
            out.add(p)
    return out


def extract_record_paths(record: dict) -> tuple[set[str], set[str]]:
    """(leaf_paths, construct_paths) a record directly exercises.

    Rules per family (README "Evidence rules"):
      path_meaning       the fact's path (leaf or construct).
      instance_validity  the judged node(s): fact.path, else the
                         record's source paths. Bystander leaves in
                         the instance are scaffolding, not coverage.
      state_reconstruction  every leaf in the before/after states:
                         fact.changed_paths + fact.preserved_paths,
                         canonicalised from instance-path form.
      typed_edit         only the path(s) the edit directly changes:
                         edits[]/attempted_edits[] .path, edited_path.
      revised_schema     the schema node the profile question is
                         about: the record's source paths.
    A path that names a construct is returned in construct_paths by
    the caller-side resolution (here both are returned unresolved;
    resolution against the schema index decides)."""
    fam = record.get("family")
    fact = record.get("fact") or {}
    if record.get("id") in HAND_EVIDENCE:
        h = HAND_EVIDENCE[record["id"]]
        return set(h["leaves"]), set(h["constructs"])
    if fam == "path_meaning":
        return ({fact["path"]} if fact.get("path") else set()), set()
    if fam == "instance_validity":
        if fact.get("path"):
            return {fact["path"]}, set()
        return _src_paths(record), set()
    if fam == "state_reconstruction":
        ps = {canon_instance_path(p) for p in
              (fact.get("changed_paths") or [])
              + (fact.get("preserved_paths") or [])}
        return (ps or _src_paths(record)), set()
    if fam == "typed_edit":
        ps = set()
        for e in (fact.get("edits") or []) + (fact.get("attempted_edits")
                                              or []):
            if e.get("path"):
                ps.add(e["path"])
        if fact.get("edited_path"):
            ps.add(fact["edited_path"])
        if not ps and fact.get("changed_paths"):
            ps = {canon_instance_path(p) for p in fact["changed_paths"]}
        return ps, set()
    if fam == "revised_schema":
        return _src_paths(record), set()
    return set(), set()


def load_records(repo: Path) -> dict[str, list[dict]]:
    out = {}
    for split in SPLITS:
        fn = repo / SPLIT_FILES[split]
        out[split] = [json.loads(l) for l in
                      fn.read_text(encoding="utf-8").splitlines()
                      if l.strip()]
    return out


# ---------------------------------------------------------------- fingerprints

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def bundle_digest(bundle: Path):
    """Digest of the exact bundle files the inventory reads:
    yang-set-cat9k.xml plus every Cisco-IOS-XE-*.yang file. Catches
    bundle drift that the recorded pin string alone would miss.
    None when the bundle is absent (freshness checks then skip it —
    a real generation cannot happen without the bundle anyway)."""
    if not (bundle / "yang-set-cat9k.xml").exists():
        return None
    names = sorted([p.name for p in bundle.glob("Cisco-IOS-XE-*.yang")]
                   + ["yang-set-cat9k.xml"])
    h = hashlib.sha256()
    for name in names:
        h.update(f"{name}:{sha256_file(bundle / name)}\n".encode())
    return h.hexdigest()


def compute_fingerprints(repo: Path, bundle: Path,
                         records: dict | None = None) -> dict:
    """Fingerprints of every input that determines coverage.json:
    the corpus split files, the vendored scores snapshot,
    priorities.yaml, the YANG bundle, and this generator itself.
    Recorded in meta.fingerprint at generation time;
    coverage/check_freshness.py recomputes and compares them."""
    inputs: dict[str, str] = {}
    for rel in list(SPLIT_FILES.values()) + [SCORES_FILE,
                                             "coverage/priorities.yaml"]:
        inputs[rel] = sha256_file(repo / rel)
    digest = hashlib.sha256()
    for rel in sorted(inputs):
        digest.update(f"{rel}:{inputs[rel]}\n".encode())
    if records is None:
        counts = {}
        for split in SPLITS:
            text = (repo / SPLIT_FILES[split]).read_text(
                encoding="utf-8")
            counts[split] = sum(1 for l in text.splitlines() if l.strip())
    else:
        counts = {split: len(records[split]) for split in SPLITS}
    return {
        "inputs": inputs,
        "inputs_digest": digest.hexdigest(),
        "split_counts": counts,
        "bundle_digest": bundle_digest(bundle),
        "generator_sha256": sha256_file(Path(__file__).resolve()),
    }


# ---------------------------------------------------------------- tree

class Node:
    __slots__ = ("name", "path", "children", "leaves", "total",
                 "covered", "excluded", "construct_refs")

    def __init__(self, name: str, path: str):
        self.name = name
        self.path = path
        self.children: dict[str, "Node"] = {}
        self.leaves: list[dict] = []
        self.total = 0
        self.covered: Counter = Counter()
        self.excluded = 0
        self.construct_refs: dict[str, list[str]] = defaultdict(list)


def build_tree(leaves: dict[str, dict]) -> Node:
    root = Node("", "")
    for path in sorted(leaves):
        segs = path.strip("/").split("/")
        node = root
        for i, seg in enumerate(segs[:-1]):
            if seg not in node.children:
                node.children[seg] = Node(seg, "/" + "/".join(segs[:i + 1]))
            node = node.children[seg]
        node.leaves.append(leaves[path])
    return root


def rollup(node: Node) -> None:
    """Bottom-up counts. Percentages are always derived from these
    descendant leaf counts, never averaged from children."""
    total = 0
    excluded = 0
    covered: Counter = Counter()
    for leaf in node.leaves:
        if leaf["eligible"]:
            total += 1
            for split in leaf["splits"]:
                covered[split] += 1
            if leaf["splits"] & {"train", "dev", "test"}:
                covered["corpus"] += 1
        else:
            excluded += 1
    for ch in node.children.values():
        rollup(ch)
        total += ch.total
        excluded += ch.excluded
        covered.update(ch.covered)
    node.total = total
    node.excluded = excluded
    node.covered = covered


def pct(covered: int, total: int):
    return None if total == 0 else round(100.0 * covered / total, 2)


# ---------------------------------------------------------------- priorities

def load_priorities(path: Path) -> list[dict]:
    """Minimal YAML-subset reader for priorities.yaml (a flat list of
    groups with scalar fields and a patterns list) — avoids a PyYAML
    dependency; the file format is documented in the file itself."""
    groups: list[dict] = []
    cur: dict | None = None
    in_patterns = False
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith("- id:"):
            cur = {"id": line.split(":", 1)[1].strip(), "patterns": []}
            groups.append(cur)
            in_patterns = False
        elif cur is not None and line.startswith("  patterns:"):
            in_patterns = True
        elif in_patterns and line.startswith("    - "):
            cur["patterns"].append(line[6:].strip().strip('"'))
        elif cur is not None and ":" in line and not line.startswith(" "):
            in_patterns = False
        elif cur is not None and line.startswith("  ") and ":" in line:
            k, v = line.strip().split(":", 1)
            if k != "patterns":
                cur[k] = v.strip().strip('"')
                in_patterns = False
    return groups


def glob_match(pattern: str, path: str) -> bool:
    """Pattern segments: '*' = exactly one segment, '**' = the rest."""
    pp = pattern.strip("/").split("/")
    sp = path.strip("/").split("/")
    for i, seg in enumerate(pp):
        if seg == "**":
            return True
        if i >= len(sp):
            return False
        if seg != "*" and seg != sp[i]:
            return False
    return len(pp) == len(sp)


def assign_priority(path: str, groups: list[dict]):
    for g in groups:
        for pat in g["patterns"]:
            if glob_match(pat, path):
                return g["id"]
    return None


# ---------------------------------------------------------------- emit

def node_to_json(node: Node, files_idx: dict[str, int]) -> dict:
    out: dict = {"n": node.name, "t": node.total}
    cov = {s: node.covered[s] for s in SPLITS + ["corpus"]
           if node.covered[s]}
    if cov:
        out["c"] = cov
    if node.excluded:
        out["x"] = node.excluded
    if node.construct_refs:
        out["r"] = {s: ids for s, ids in
                    sorted(node.construct_refs.items())}
    if node.leaves:
        # Leaf row: [name, flags, split_mask, file_idx, priority,
        # pass_n, pass_p, evidence]. flags: bit0 leaf-list,
        # bit1 excluded from denominator (obsolete / config-false),
        # bit2 deprecated (eligible, but flagged).
        out["lf"] = [[
            leaf["path"].rsplit("/", 1)[1],
            (1 if leaf["kind"] == "leaf-list" else 0)
            | (0 if leaf["eligible"] else 2)
            | (4 if leaf["status"] == "deprecated" else 0),
            leaf["split_mask"],
            files_idx.get(leaf["file"], -1),
            leaf.get("priority") or "",
            leaf["pass_n"], leaf["pass_p"],
            leaf.get("evidence") or {},
        ] for leaf in sorted(node.leaves, key=lambda l: l["path"])]
    if node.children:
        out["ch"] = [node_to_json(ch, files_idx) for ch in
                      sorted(node.children.values(), key=lambda c: c.name)]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    here = Path(__file__).resolve().parent
    ap.add_argument("--repo", default=str(here.parent))
    ap.add_argument("--bundle", default=None)
    ap.add_argument("--out", default=str(here / "data" / "coverage.json"))
    ap.add_argument("--inventory-cache", default=None,
                    help="reuse a cached inventory JSON instead of "
                         "running pyang (development aid)")
    ap.add_argument("--no-timestamp", action="store_true")
    args = ap.parse_args()
    repo = Path(args.repo)
    bundle = Path(args.bundle) if args.bundle else (
        repo.parent / "bundles" / "xe-2621")

    import pyang
    if args.inventory_cache and Path(args.inventory_cache).exists():
        cache = json.loads(Path(args.inventory_cache).read_text())
        leaves = {p: r for p, r in cache["leaves"].items()}
        stmt_index = {k: tuple(v) for k, v in cache["stmt_index"].items()}
        other_roots = cache["other_roots"]
        collisions = cache["collisions"]
        load_info = cache["load_info"]
    else:
        ctx, load_info, err_tags = load_schema(bundle)
        load_info["pyang_error_tags"] = err_tags
        leaves, stmt_index, other_roots, collisions = build_inventory(ctx)
        if args.inventory_cache:
            Path(args.inventory_cache).write_text(json.dumps({
                "leaves": leaves, "stmt_index": stmt_index,
                "other_roots": other_roots, "collisions": collisions,
                "load_info": load_info}))

    for leaf in leaves.values():
        leaf["eligible"] = leaf["config"] and leaf["status"] != "obsolete"
        leaf["splits"] = set()
        leaf["pass_n"] = 0
        leaf["pass_p"] = 0
        leaf["evidence"] = {}

    records = load_records(repo)
    resolution = Counter()
    unresolved: list[str] = []

    def resolve(path: str):
        """-> (leaf record | None, construct transparent path | None)"""
        if path in leaves:
            return leaves[path], None
        hit = stmt_index.get(path)
        if hit is not None:
            kind, tp = hit
            if kind in ("leaf", "leaf-list") and tp in leaves:
                return leaves[tp], None
            return None, tp
        return None, None

    per_split_paths: dict[str, set[str]] = {}
    construct_pending: list[tuple[str, str, str]] = []
    for split in SPLITS:
        covered_paths: set[str] = set()
        for rec in records[split]:
            leaf_ps, construct_ps = extract_record_paths(rec)
            for p in leaf_ps | construct_ps:
                leaf, cnode = resolve(p)
                if leaf is not None:
                    resolution["leaf"] += 1
                    covered_paths.add(leaf["path"])
                    ev = leaf["evidence"].setdefault(split, {"n": 0,
                                                             "ids": []})
                    ev["n"] += 1
                    if len(ev["ids"]) < 3:
                        ev["ids"].append(rec["id"])
                elif cnode is not None:
                    resolution["construct"] += 1
                    construct_pending.append((cnode, split, rec["id"]))
                else:
                    resolution["unresolved"] += 1
                    unresolved.append(f"{split}:{rec['id']}:{p}")
        per_split_paths[split] = covered_paths

    for split, paths in per_split_paths.items():
        for p in paths:
            leaves[p]["splits"].add(split)

    # Model pass overlay: bench v1 items (benchmark + held_out) from
    # the run-1 scores, joined per item to the leaves it exercises.
    scores = json.loads((repo / SCORES_FILE).read_text())
    by_id = {r["id"]: r for split in ("benchmark", "held_out")
             for r in records[split]}
    pass_items = 0
    for row in scores["rows"]:
        rec = by_id.get(row["id"])
        if rec is None:
            continue
        leaf_ps, _ = extract_record_paths(rec)
        joined = set()
        for p in leaf_ps:
            leaf, _c = resolve(p)
            if leaf is not None:
                joined.add(leaf["path"])
        for p in joined:
            leaves[p]["pass_n"] += 1
            leaves[p]["pass_p"] += 1 if row["pass"] else 0
        if joined:
            pass_items += 1

    # Priorities (heuristic labels; see priorities.yaml).
    groups = load_priorities(here / "priorities.yaml")
    for leaf in leaves.values():
        leaf["priority"] = assign_priority(leaf["path"], groups)
        leaf["split_mask"] = sum(1 << i for i, s in enumerate(SPLITS)
                                 if s in leaf["splits"])

    root = build_tree(leaves)
    # Attach construct references to their tree nodes.
    node_by_path = {}

    def index_nodes(node):
        node_by_path[node.path] = node
        for ch in node.children.values():
            index_nodes(ch)
    index_nodes(root)
    n_construct_attached = 0
    for cpath_, split, rid in construct_pending:
        target = cpath_
        while target and target not in node_by_path:
            target = target.rsplit("/", 1)[0]
        if target in node_by_path:
            ids = node_by_path[target].construct_refs[split]
            if rid not in ids:
                ids.append(rid)
            n_construct_attached += 1
    rollup(root)

    files_idx: dict[str, int] = {}
    for leaf in leaves.values():
        if leaf["file"] not in files_idx:
            files_idx[leaf["file"]] = len(files_idx)
    files_list = sorted(files_idx, key=lambda f: files_idx[f])

    eligible = [l for l in leaves.values() if l["eligible"]]
    denom = len(eligible)
    covered_by_split = {s: sum(1 for l in eligible if s in l["splits"])
                        for s in SPLITS}
    corpus_splits = ("train", "dev", "test")
    covered_corpus = sum(1 for l in eligible
                         if any(s in l["splits"] for s in corpus_splits))
    covered_train = covered_by_split["train"]
    norm_keys = set()
    for l in eligible:
        p = l["path"]
        m = re.match(r"^(/native/interface/)[^/]+(/.*)?$", p)
        norm_keys.add("iface*" + (m.group(2) or "") if m else p)
    prio_summary = []
    for g in groups:
        members = [l for l in eligible if l.get("priority") == g["id"]]
        cov = sum(1 for l in members if "train" in l["splits"])
        prio_summary.append({
            "id": g["id"], "label": g.get("label", g["id"]),
            "note": g.get("note", ""), "total": len(members),
            "covered_train": cov, "pct_train": pct(cov, len(members))})

    census = yang_set_classes(bundle)
    meta = {
        "title": "IOS XE 26.2.1 YANG coverage — yang-learn corpus v1",
        "scope": "Native model surface: the /native tree of "
                 "Cisco-IOS-XE-native with all Cisco-IOS-XE augments "
                 "applied. Schema coverage only — no device/XE "
                 "validation is implied.",
        "provenance": {
            "bundle": f"YangModels/yang @ {BUNDLE_PIN} "
                      "(vendor/cisco/xe/2621, IOS XE 26.2.1, "
                      "yang-set-cat9k)",
            "tool": f"pyang {pyang.__version__}",
            "inventory_load": "all Cisco-IOS-XE-*.yang in the bundle "
                              "except *-oper/*-rpc/*-events/"
                              "*-actions-rpc (excluded by rule) and "
                              "deviation files not advertised in "
                              "yang-set-cat9k.xml",
            "loaded_module_files": len(load_info["loaded"]),
            "skipped_unadvertised_deviation_files":
                load_info["skipped_unadvertised_deviations"],
            "pyang_error_tags": load_info["pyang_error_tags"],
            "corpus": "yang-learn corpus v1, frozen at commit "
                      "5189d33 (train/dev/test/benchmark) + "
                      "held_out.jsonl (8 hand-authored)",
            "model_scores": "coverage/data/run1_scores.json — "
                            "snapshot of outputs/yang-learn-run1/"
                            "scores.json (bench v1, run 1, 217 items, "
                            "score_bench.py mechanical graders)",
        },
        "denominator": {
            "eligible_leaves": denom,
            "covered_train": covered_train,
            "pct_train": pct(covered_train, denom),
            "covered_corpus_train_dev_test": covered_corpus,
            "pct_corpus": pct(covered_corpus, denom),
            "covered_by_split": covered_by_split,
            "interface_type_normalized_denominator": len(norm_keys),
            "excluded": {
                "obsolete_leaves": sum(
                    1 for l in leaves.values() if l["status"] == "obsolete"
                    and l["config"]),
                "config_false_leaves_in_native_tree": sum(
                    1 for l in leaves.values() if not l["config"]),
                "deprecated_leaves_included_in_denominator": sum(
                    1 for l in eligible if l["status"] == "deprecated"),
                "duplicate_transparent_paths_collapsed": collisions,
                "deviate_not_supported_statements":
                    census["deviate_not_supported_statements"],
            },
            "module_census": census,
            "non_native_roots_counted_not_inventoried": other_roots,
        },
        "evidence_resolution": {
            "leaf_matches": resolution["leaf"],
            "construct_matches": resolution["construct"],
            "construct_refs_attached": n_construct_attached,
            "unresolved": resolution["unresolved"],
            "unresolved_items": unresolved[:50],
            "pass_items_joined_to_leaves": pass_items,
            "pass_items_total": len(scores["rows"]),
        },
        "priorities": prio_summary,
        "family_pass_bench_v1": scores["by_family"],
        "overall_pass_bench_v1": scores["overall"],
        "splits": SPLITS,
        "files": files_list,
        "fingerprint": compute_fingerprints(repo, bundle, records),
    }
    if not args.no_timestamp:
        from datetime import datetime, timezone
        meta["generated_at"] = datetime.now(timezone.utc).isoformat()
    out = {"meta": meta, "tree": node_to_json(root, files_idx)}
    Path(args.out).write_text(json.dumps(out, separators=(",", ":")))
    print(f"denominator (eligible leaves): {denom}")
    print(f"covered by train: {covered_train} "
          f"({pct(covered_train, denom)}%)")
    print(f"covered by corpus train+dev+test: {covered_corpus} "
          f"({pct(covered_corpus, denom)}%)")
    print(f"resolution: {dict(resolution)}; unresolved items: "
          f"{len(unresolved)}")
    pat = Counter(u.split(':', 2)[2].rsplit('/', 1)[0]
                  for u in unresolved)
    for pth, n in pat.most_common(15):
        print(f"  UNRESOLVED x{n} under {pth}")
    for u in unresolved[:10]:
        print("  UNRESOLVED", u)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
