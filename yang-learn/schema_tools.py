#!/usr/bin/env python3
"""Schema fact extraction for the IOS XE 26.2.1 native model (cat9k set).

Labels/provenance come from pyang's parsed tree ONLY. Instance verdicts
are produced separately by yangson (see validate_instances.py) — never
by this module — per the project's validation-independence rule.

Usage:
  schema_tools.py inventory [--bundle DIR] [--out FILE]
      Print the leaf inventory of the in-scope subtrees:
      /native/vlan and /native/interface/*/switchport (+ siblings).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pyang import context, repository

ROOT_MODULE = "Cisco-IOS-XE-native"
DEVIATIONS = [
    "aaa", "aging-time", "cef", "cts-switching", "device-sensor",
    "device-tracking-cat9k", "dialer", "ethernet-cat9k", "flow",
    "interfaces-cat9k", "interfaces", "line-common", "line-nonquake",
    "lisp", "logging", "nd", "ospf", "ospfv3", "pnp", "poch-lb-switch",
    "policy-cat9k", "sanet", "snmp", "switch", "switchport", "vlan-ewlc",
    "vrrp",
]
DATA_KWS = {"container", "list", "leaf", "leaf-list", "choice", "case",
            "anydata", "anyxml"}


def load_context(bundle: Path):
    repo = repository.FileRepository(str(bundle))
    ctx = context.Context(repo)
    files = [bundle / f"{ROOT_MODULE}.yang"]
    files += [bundle / f"Cisco-IOS-XE-{d}-deviation.yang" for d in DEVIATIONS]
    for f in files:
        with open(f, encoding="utf-8") as fh:
            ctx.add_module(f.name, fh.read())
    ctx.validate()
    errors = [e for e in ctx.errors if e[1].tag in (
        "MODULE_NOT_FOUND", "SYNTAX_ERROR", "TYPE_NOT_FOUND",
        "GROUPING_NOT_FOUND")]
    return ctx, errors


def schema_path(stmt) -> str:
    parts = []
    node = stmt
    while node is not None:
        if node.keyword in DATA_KWS:
            parts.append(node.arg)
        node = getattr(node, "i_parent", None) or node.parent
        if node is not None and node.keyword in ("module", "submodule"):
            break
    return "/" + "/".join(reversed(parts))


def defining_module(stmt) -> str:
    node = stmt
    while node is not None:
        if node.keyword in ("module", "submodule"):
            return node.arg
        node = node.parent
    return "?"


def pos_of(stmt) -> dict:
    pos = stmt.pos
    ref = getattr(pos, "ref", "") or ""
    return {"file": Path(str(ref)).name, "line": getattr(pos, "line", None)}


def render_type(type_stmt, depth=0) -> str:
    """Render a type statement (following typedefs) as a compact string."""
    if type_stmt is None:
        return "?"
    arg = type_stmt.arg
    spec = getattr(type_stmt, "i_type_spec", None)
    spec_name = type(spec).__name__ if spec is not None else ""
    out = arg
    if spec_name == "RangeTypeSpec" and spec is not None:
        out += " range " + " | ".join(
            f"{lo}..{hi}" for lo, hi in spec.ranges)
    elif spec_name == "EnumTypeSpec" and spec is not None:
        def _ename(e):
            if isinstance(e, (list, tuple)):
                return str(e[0])
            return str(getattr(e, "name", e))
        out = "enumeration {" + ", ".join(
            _ename(e) for e in spec.enums) + "}"
    elif spec_name == "PatternTypeSpec" and spec is not None:
        out += " pattern"
    elif spec_name == "UnionTypeSpec" and spec is not None and depth < 3:
        branches = [render_type(t, depth + 1) for t in spec.types]
        out = "union {" + ", ".join(branches) + "}"
    elif spec_name == "LengthTypeSpec" and spec is not None:
        out += " length " + " | ".join(
            f"{lo}..{hi}" for lo, hi in spec.lengths)
    td = getattr(type_stmt, "i_typedef", None)
    if td is not None and depth < 3:
        base = td.search_one("type")
        if base is not None:
            out += f" -> {render_type(base, depth + 1)}"
    return out


def leaf_facts(stmt) -> dict:
    type_stmt = stmt.search_one("type")
    default = stmt.search_one("default")
    whens, musts = [], []
    node = stmt
    while node is not None and node.keyword not in ("module", "submodule"):
        w = node.search_one("when")
        if w is not None:
            whens.append({"on": node.arg, "xpath": w.arg})
        for m in node.search("must"):
            musts.append({"on": node.arg, "xpath": m.arg})
        node = getattr(node, "i_parent", None) or node.parent
    cfg = stmt.search_one("config")
    desc = stmt.search_one("description")
    return {
        "path": schema_path(stmt),
        "leaf": stmt.arg,
        "kind": stmt.keyword,
        "type": render_type(type_stmt),
        "default": default.arg if default is not None else None,
        "config": cfg.arg if cfg is not None else None,
        "when": whens,
        "must": musts,
        "description": (desc.arg.split("\n")[0].strip()
                        if desc is not None else None),
        "source": {"module": defining_module(stmt), **pos_of(stmt)},
    }


def iter_leaves(stmt):
    for child in getattr(stmt, "i_children", []) or []:
        if child.keyword in ("leaf", "leaf-list"):
            yield child
        elif child.keyword in DATA_KWS:
            yield from iter_leaves(child)


def inventory(bundle: Path) -> list[dict]:
    ctx, errors = load_context(bundle)
    if errors:
        print(f"FATAL: {len(errors)} load errors", file=sys.stderr)
        for pos, etype, earg in errors[:10]:
            print(" ", pos, etype, earg, file=sys.stderr)
        sys.exit(2)
    mod = ctx.get_module(ROOT_MODULE)
    facts = []
    for top in getattr(mod, "i_children", []) or []:
        for leaf in iter_leaves(top):
            p = schema_path(leaf)
            if p.startswith("/native/vlan/") or "/switchport" in p:
                facts.append(leaf_facts(leaf))
    return sorted(facts, key=lambda f: f["path"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["inventory"])
    ap.add_argument("--bundle", default=str(
        Path(__file__).resolve().parents[2] / "bundles" / "xe-2621"))
    ap.add_argument("--out")
    args = ap.parse_args()
    facts = inventory(Path(args.bundle))
    text = "\n".join(json.dumps(f, indent=1) for f in facts)
    if args.out:
        Path(args.out).write_text(text + "\n")
    print(f"in-scope leaves: {len(facts)}")
    from collections import Counter
    tops = Counter(f["path"].split("/")[2] for f in facts)
    print("by top container:", dict(tops))
    sw = [f for f in facts if "/switchport" in f["path"]]
    print("switchport leaves:", len(sw))
    for f in sw[:8]:
        print(" ", f["path"], "|", f["type"][:70])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
