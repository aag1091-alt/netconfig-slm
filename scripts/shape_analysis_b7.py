#!/usr/bin/env python3
"""Coarse structural-shape distribution per family, corpus before/after
batch 7. Coarse key = counts of top-level config block kinds (numbers
and names erased), so it measures structural balance, not surface
variety. Used for the gen-run-007 report."""
import json
from collections import Counter
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent


def coarse(config):
    kinds = []
    for line in config.splitlines():
        if line and not line.startswith((" ", "!")):
            h = line.strip()
            if h.startswith("hostname"):
                kinds.append("hostname")
            elif h.startswith("interface Vlan"):
                kinds.append("svi")
            elif h.startswith("vlan "):
                kinds.append("vlan")
            elif h.startswith("interface Loopback"):
                kinds.append("loopback")
            elif h.startswith("interface "):
                kinds.append("iface")
            elif h.startswith("router ospf"):
                kinds.append("ospf")
            elif h.startswith("router bgp"):
                kinds.append("bgp")
            elif h.startswith("route-map"):
                kinds.append("routemap")
            elif h.startswith("ip route"):
                kinds.append("staticroute")
            elif h.startswith("line con"):
                kinds.append("console")
            elif h.startswith("line vty"):
                kinds.append("vty")
            elif h.startswith("banner motd"):
                kinds.append("banner")
            elif h.startswith("access-list"):
                kinds.append("acl")
            elif h.startswith("ip domain-name"):
                kinds.append("domain")
            elif h.startswith("ip ssh"):
                kinds.append("ssh")
            else:
                kinds.append("other")
    c = Counter(kinds)
    return ",".join(f"{k}x{v}" for k, v in sorted(c.items())
                    if k != "hostname")


def load(paths):
    out = []
    for p in paths:
        for l in Path(p).read_text().splitlines():
            if l.strip():
                out.append(json.loads(l))
    return out


gen = BASE / "data/generated"
before = load([BASE / "data/examples/seed.jsonl"] +
              [p for p in sorted(gen.glob("*.jsonl"))
               if "_b7" not in p.name])
b7 = load(sorted(gen.glob("*_b7.jsonl")))
after = before + b7
print(f"corpus before b7: {len(before)}  b7: {len(b7)}  after: {len(after)}")
for fam in ["vlan_l2", "trunk_svi", "static_route", "ospf", "bgp",
            "interface_mgmt"]:
    fr_b = [r for r in before if r["family"] == fam]
    fr_a = [r for r in after if r["family"] == fam]
    dist_b = Counter(coarse(r["config"]) for r in fr_b)
    dist_a = Counter(coarse(r["config"]) for r in fr_a)
    top_a = dist_a.most_common(1)[0]
    print(f"{fam:16} rows {len(fr_b)} -> {len(fr_a)} | coarse shapes "
          f"{len(dist_b)} -> {len(dist_a)} | top shape after: "
          f"{top_a[1]} rows ({top_a[1] / len(fr_a):.1%}) [{top_a[0][:60]}]")
