#!/usr/bin/env python3
"""Batch 8 generator: 287 rows per family, 861 total - Phase 1,
Batch 1 (the L2 cluster), growing the corpus 5,000 -> ~5,861
(dataset v1.1). Spec: docs/PHASE1_PLAN.md.

New families (Avinash's Nov 24 expansion order: L2 security / STP /
EtherChannel first):
- etherchannel (ether): channel-group mode active/passive/on,
  interface Port-channel<n> (trunk with allowed-VLANs / native, or
  access), port-channel min-links, port-channel load-balance.
- stp (stp): spanning-tree mode rapid-pvst/pvst/mst, per-VLAN
  priority, PortFast + BPDU guard (per-port, list and by-default
  forms), root guard / loop guard / PortFast trunk on uplinks.
- port_security (psec): switchport port-security with maximum,
  violation shutdown/restrict/protect, sticky or static MAC,
  aging time/type.

Same architecture as gen_batch7.py:
- Dedupe vs full corpus (data/v1.0/dataset.jsonl) + within batch:
  exact pair + shape signature (intent+config normalised).
- Intent-match self-check gate BEFORE any Batfish validation: the
  gate is scripts/intent_match.py itself (Checker + FAMILY_CHECKS,
  including the batch-8 checkers for the three new families), so the
  generator gate and the dataset audit can never drift apart.
  Flagged candidates are dropped and regenerated, never patched.
- Every CLI construct used here was probe-passed against Batfish
  (PASSED, 0 init issues) on 2026-11-25 before generation.
- Batch-4 lesson: a shared expression (the Port-channel / uplink
  allowed-VLAN list) is rendered ONCE and reused verbatim in intent
  and config.
- Run-3 lesson: configs stay short - member/edge/port lists are
  bounded (<=4 ports, <=8 allowed VLANs) so no row approaches the
  length regime where the published model truncated.
- hostname first line on every config; no IPs are used by these
  families at all (L2 only), so the RFC1918 rule is trivially met;
  static MACs are locally-administered unicast, generated.

Writes data/generated/{family}_b8.jsonl with validation flags null;
fill them from the validator run afterwards (scripts/fill_b8_flags.py).
"""
import json
import random
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "scripts"))

import gen_batch7 as b7  # noqa: E402  (shared pools/helpers, main guarded)
import intent_match as im  # noqa: E402  (the gate)

FAMILIES = ["etherchannel", "stp", "port_security"]
PREFIX = {"etherchannel": "ether", "stp": "stp", "port_security": "psec"}
TARGETS = {"etherchannel": 287, "stp": 287, "port_security": 287}

SWITCH_HOSTS = b7.SWITCH_HOSTS
TICKET_PREFIXES = b7.TICKET_PREFIXES
VLAN_NAMES = b7.VLAN_NAMES
device = b7.device
join_list = b7.join_list
signature = b7.signature
render_vlan_expr = b7.render_vlan_expr


def load_corpus():
    rows = []
    p = BASE / "data/v1.0/dataset.jsonl"
    for line in p.read_text().splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def row(rid, family, intent, dev, config):
    return {"id": rid, "family": family, "intent_nl": intent,
            "intent_oc": None, "device": dev, "config": config,
            "source": "synthetic", "source_ref": None,
            "validation": {"parse_ok": None, "batfish_ok": None,
                           "emulation_ok": None},
            "notes": "Generated batch 008"}


def gate(r):
    """The intent-match gate: scripts/intent_match.py's own checker."""
    c = im.Checker(r)
    c.assert_hostname()
    im.FAMILY_CHECKS[r["family"]](c)
    return [f"{f['check']}:{f['entity']}" for f in c.failures]


def pick_ports(rng, module, pmax, n, used):
    ports = []
    tries = 0
    while len(ports) < n and tries < 200:
        tries += 1
        p = f"GigabitEthernet{module}/{rng.randint(1, pmax)}"
        if p not in used:
            used.add(p)
            ports.append(p)
    return ports


# ---------------------------------------------------------------- etherchannel
PO_DESCS = ["LACP uplink to core", "EtherChannel to distribution",
            "Server farm bundle", "Access bundle to closet switch",
            "Trunk bundle to core", "Storage bundle",
            "Bundle to aggregation layer", "Inter-switch bundle"]
LOAD_BALANCE = ["src-dst-ip", "src-dst-mac", "src-ip", "dst-ip",
                "src-mac", "dst-mac", "src-dst-port"]


def gen_ether(rng, rid):
    dev, cfgname = device(rng, SWITCH_HOSTS, rng.choice(
        ["access_switch", "access_switch", "distribution_switch",
         "campus_switch", "leaf_switch", "branch_switch"]))
    module = rng.choice(["1/0", "1/0", "1/0", "2/0", "1/1", "3/0"])
    pmax = 48 if module in ("1/0", "2/0") else 24
    used = set()
    n_members = rng.choices([2, 3, 4], weights=[5, 3, 2])[0]
    members = pick_ports(rng, module, pmax, n_members, used)
    group = rng.randint(1, 16)
    mode = rng.choices(["active", "passive", "on"], weights=[9, 4, 7])[0]
    role = rng.choices(["trunk", "access"], weights=[13, 7])[0]
    base = rng.randrange(10, 600, 10)
    native = None
    expr = None
    if role == "trunk":
        n_vlan = rng.choices([3, 4, 5, 6, 7, 8],
                             weights=[2, 3, 3, 2, 1, 1])[0]
        allowed = sorted(rng.sample(range(base, base + 90), n_vlan))
        expr = render_vlan_expr(rng, allowed)  # rendered ONCE (batch-4)
        if rng.random() < 0.4:
            native = rng.choice(allowed)
        vlans = allowed
    else:
        access_vlan = rng.randrange(base, base + 90)
        vlans = [access_vlan]
    min_links = rng.randint(2, n_members) if rng.random() < 0.3 else None
    lb = rng.choice(LOAD_BALANCE) if rng.random() < 0.35 else None
    desc = rng.choice(PO_DESCS) if rng.random() < 0.45 else None
    member_desc = rng.random() < 0.4
    host = dev["hostname"]
    plist = join_list(members)
    if mode == "on":
        bundle = (f"bundle {plist} into Port-channel{group} with "
                  f"channel-group mode on" if rng.random() < 0.5 else
                  f"group {plist} into Port-channel{group} with "
                  f"channel-group mode on")
    else:
        bundle = (f"bundle {plist} into Port-channel{group} with LACP "
                  f"{mode} mode" if rng.random() < 0.5 else
                  f"group {plist} into Port-channel{group} with LACP "
                  f"{mode} mode")
    dpart = f" (description '{desc}')" if desc else ""
    suffix = ""
    if native:
        suffix += f" with native VLAN {native}"
    if min_links:
        suffix += f" with min-links {min_links}"
    if role == "trunk":
        role_clause = (f"Configure Port-channel{group}{dpart} as a trunk "
                       f"allowing VLANs {expr}{suffix}"
                       if rng.random() < 0.5 else
                       f"Port-channel{group}{dpart} is a trunk permitting "
                       f"VLANs {expr}{suffix}")
    else:
        role_clause = (f"Configure Port-channel{group}{dpart} as an "
                       f"access port-channel in VLAN {access_vlan}{suffix}"
                       if rng.random() < 0.5 else
                       f"Port-channel{group}{dpart} is an access "
                       f"port-channel in VLAN {access_vlan}{suffix}")
    lb_clause = ""
    if lb:
        lb_clause = (f" Set port-channel load-balance to {lb}."
                     if rng.random() < 0.5 else
                     f" Use port-channel load-balance {lb}.")
    if rng.random() < 0.5:
        intent = f"On {host}, {bundle}. {role_clause}.{lb_clause}"
    else:
        intent = f"{host}: {bundle}; {role_clause}.{lb_clause}"
    cfg = [f"hostname {cfgname}", "!"]
    for v in vlans:
        cfg += [f"vlan {v}", "!"]
    for p in members:
        cfg.append(f"interface {p}")
        if member_desc:
            cfg.append(" description EtherChannel member")
        if role == "trunk":
            cfg.append(" switchport mode trunk")
        else:
            cfg += [" switchport mode access",
                    f" switchport access vlan {access_vlan}"]
        cfg += [f" channel-group {group} mode {mode}",
                " no shutdown", "!"]
    cfg.append(f"interface Port-channel{group}")
    if desc:
        cfg.append(f" description {desc}")
    if role == "trunk":
        cfg.append(" switchport mode trunk")
        if native:
            cfg.append(f" switchport trunk native vlan {native}")
        cfg.append(f" switchport trunk allowed vlan {expr}")
    else:
        cfg += [" switchport mode access",
                f" switchport access vlan {access_vlan}"]
    if min_links:
        cfg.append(f" port-channel min-links {min_links}")
    cfg += [" no shutdown", "!"]
    if lb:
        cfg = cfg[:2] + [f"port-channel load-balance {lb}", "!"] + cfg[2:]
    return row(rid, "etherchannel", intent, dev, "\n".join(cfg) + "\n")


# ---------------------------------------------------------------- stp
PRIORITIES = [0, 4096, 8192, 12288, 16384, 20480, 24576, 28672, 32768]


def gen_stp(rng, rid):
    dev, cfgname = device(rng, SWITCH_HOSTS, rng.choice(
        ["access_switch", "access_switch", "distribution_switch",
         "core_switch", "campus_switch", "branch_switch"]))
    mode = rng.choices(["rapid-pvst", "pvst", "mst"],
                       weights=[11, 5, 4])[0]
    structure = rng.choices(["clauses", "list", "defaults"],
                            weights=[12, 5, 3])[0]
    module = rng.choice(["1/0", "1/0", "1/0", "2/0", "1/1"])
    pmax = 48 if module in ("1/0", "2/0") else 24
    used = set()
    base = rng.randrange(10, 600, 10)
    n_prio = rng.choices([0, 1, 2, 3], weights=[2, 4, 3, 1])[0]
    prios = [(v, rng.choice(PRIORITIES))
             for v in rng.sample(range(base, base + 60), n_prio)] \
        if n_prio else []
    all_vlans = {v for v, _ in prios}
    clauses = []
    edge_cfg = []  # (port, vlan, portfast, bpduguard)
    if structure == "clauses":
        n_edge = rng.choices([1, 2, 3, 4], weights=[2, 4, 3, 1])[0]
        edge_vlans = rng.sample(range(base, base + 60),
                                rng.choices([1, 2, 3], weights=[3, 3, 1])[0])
        for p in pick_ports(rng, module, pmax, n_edge, used):
            v = rng.choice(edge_vlans)
            guard = rng.random() < 0.45
            edge_cfg.append((p, v, True, guard))
            all_vlans.add(v)
            clauses.append(f"{p} in VLAN {v} with PortFast" +
                           (" and BPDU guard" if guard else ""))
    elif structure == "list":
        n_edge = rng.choices([2, 3, 4], weights=[3, 3, 1])[0]
        v = rng.randrange(base, base + 60)
        ports = pick_ports(rng, module, pmax, n_edge, used)
        guarded = [p for p in ports if rng.random() < 0.5]
        for p in ports:
            edge_cfg.append((p, v, True, p in guarded))
        all_vlans.add(v)
        clauses.append(f"PortFast on {join_list(ports)}, all in VLAN {v}")
        if guarded:
            clauses.append(f"BPDU guard on {join_list(guarded)}")
    else:  # defaults
        n_edge = rng.choices([2, 3], weights=[3, 2])[0]
        ports = pick_ports(rng, module, pmax, n_edge, used)
        pairs = []
        for p in ports:
            v = rng.randrange(base, base + 60)
            edge_cfg.append((p, v, False, False))
            all_vlans.add(v)
            pairs.append(f"{p} in VLAN {v}")
        clauses.append("PortFast on all access ports by default")
        if rng.random() < 0.7:
            clauses.append("BPDU guard by default")
        clauses.append(f"access ports {join_list(pairs)}")
    uplinks = []  # (port, allowed, expr, suffix)
    n_up = rng.choices([0, 1, 2], weights=[3, 4, 2])[0]
    for _ in range(n_up):
        if rng.random() < 0.3:
            p = f"TenGigabitEthernet1/0/{rng.randint(1, 2)}"
            if p in used:
                continue
            used.add(p)
        else:
            got = pick_ports(rng, module, pmax, 1, used)
            if not got:
                continue
            p = got[0]
        n_vlan = rng.choices([2, 3, 4, 5, 6], weights=[2, 3, 3, 1, 1])[0]
        allowed = sorted(rng.sample(range(base, base + 90), n_vlan))
        expr = render_vlan_expr(rng, allowed)  # rendered ONCE (batch-4)
        suffix = rng.choices([None, "root guard", "loop guard",
                              "PortFast trunk"], weights=[8, 5, 4, 3])[0]
        uplinks.append((p, allowed, expr, suffix))
        all_vlans.update(allowed)
        clauses.append(f"{p} as a trunk allowing VLANs {expr}" +
                       (f" with {suffix}" if suffix else ""))
    prio_clauses = []
    for v, p in prios:
        if rng.random() < 0.5:
            prio_clauses.append(
                f"set the spanning-tree priority for VLAN {v} to {p}")
        else:
            prio_clauses.append(f"VLAN {v} root priority {p}")
    host = dev["hostname"]
    body = "; ".join(prio_clauses + clauses)
    if rng.random() < 0.5:
        intent = (f"On {host}, set spanning-tree mode to {mode}. "
                  + (body + "." if body else ""))
    else:
        intent = f"{host}: spanning-tree mode {mode}; {body}."
    intent = intent.rstrip()
    if not intent.endswith("."):
        intent += "."
    cfg = [f"hostname {cfgname}", "!", f"spanning-tree mode {mode}"]
    if structure == "defaults":
        cfg.append("spanning-tree portfast default")
        if "BPDU guard by default" in intent:
            cfg.append("spanning-tree portfast bpduguard default")
    for v, p in prios:
        cfg.append(f"spanning-tree vlan {v} priority {p}")
    cfg.append("!")
    for v in sorted(all_vlans):
        cfg += [f"vlan {v}", "!"]
    for p, v, pf, guard in edge_cfg:
        cfg.append(f"interface {p}")
        cfg += [" switchport mode access",
                f" switchport access vlan {v}"]
        if pf:
            cfg.append(" spanning-tree portfast")
        if guard:
            cfg.append(" spanning-tree bpduguard enable")
        cfg += [" no shutdown", "!"]
    for p, allowed, expr, suffix in uplinks:
        cfg.append(f"interface {p}")
        cfg += [" switchport mode trunk",
                f" switchport trunk allowed vlan {expr}"]
        if suffix == "root guard":
            cfg.append(" spanning-tree guard root")
        elif suffix == "loop guard":
            cfg.append(" spanning-tree guard loop")
        elif suffix == "PortFast trunk":
            cfg.append(" spanning-tree portfast trunk")
        cfg += [" no shutdown", "!"]
    return row(rid, "stp", intent, dev, "\n".join(cfg) + "\n")


# ---------------------------------------------------------------- port_security
def _mac(rng):
    b0 = (rng.randrange(64) << 2) | 0x02  # locally administered, unicast
    bs = [b0] + [rng.randrange(256) for _ in range(5)]
    groups = [(bs[0] << 8) | bs[1], (bs[2] << 8) | bs[3],
              (bs[4] << 8) | bs[5]]
    return ".".join(f"{g:04x}" for g in groups)


def gen_psec(rng, rid):
    dev, cfgname = device(rng, SWITCH_HOSTS, rng.choice(
        ["access_switch", "access_switch", "campus_switch",
         "branch_switch", "leaf_switch"]))
    module = rng.choice(["1/0", "1/0", "1/0", "2/0", "1/1"])
    pmax = 48 if module in ("1/0", "2/0") else 24
    used = set()
    n_ports = rng.choices([1, 2, 3, 4], weights=[2, 4, 3, 2])[0]
    ports = pick_ports(rng, module, pmax, n_ports, used)
    base = rng.randrange(10, 600, 10)
    vlan_pool = rng.sample(range(base, base + 60),
                           rng.choices([1, 2, 3], weights=[3, 3, 1])[0])
    specs = []
    for p in ports:
        v = rng.choice(vlan_pool)
        maximum = rng.choices([1, 2, 3, 5, 10],
                              weights=[3, 4, 2, 2, 1])[0]
        violation = rng.choices(["shutdown", "restrict", "protect"],
                                weights=[5, 3, 2])[0]
        kind = rng.choices(["sticky", "static", "none"],
                           weights=[7, 4, 9])[0]
        mac = _mac(rng) if kind == "static" else None
        aging = None
        if rng.random() < 0.25:
            aging = (rng.choice([2, 5, 10, 15, 30]),
                     rng.choice(["absolute", "inactivity"]))
        specs.append((p, v, maximum, violation, kind, mac, aging))
    clauses = []
    for p, v, maximum, violation, kind, mac, aging in specs:
        cl = (f"{p} in VLAN {v}: port-security maximum {maximum}, "
              f"violation {violation}")
        if kind == "sticky":
            cl += ", sticky MAC learning"
        elif kind == "static":
            cl += f", static MAC {mac}"
        if aging:
            cl += f", aging time {aging[0]} minutes ({aging[1]})"
        clauses.append(cl)
    host = dev["hostname"]
    frame = rng.randint(1, 3)
    if frame == 1:
        intent = (f"On {host}, secure these access ports: "
                  + "; ".join(clauses) + ".")
    elif frame == 2:
        intent = f"{host}: port security — " + "; ".join(clauses) + "."
    else:
        intent = (f"Configure port security on {host}: "
                  + "; ".join(clauses) + ".")
    cfg = [f"hostname {cfgname}", "!"]
    for v in sorted({s[1] for s in specs}):
        cfg += [f"vlan {v}", "!"]
    for p, v, maximum, violation, kind, mac, aging in specs:
        cfg.append(f"interface {p}")
        cfg += [" switchport mode access",
                f" switchport access vlan {v}",
                " switchport port-security",
                f" switchport port-security maximum {maximum}",
                f" switchport port-security violation {violation}"]
        if kind == "sticky":
            cfg.append(" switchport port-security mac-address sticky")
        elif kind == "static":
            cfg.append(f" switchport port-security mac-address {mac}")
        if aging:
            cfg += [f" switchport port-security aging time {aging[0]}",
                    f" switchport port-security aging type {aging[1]}"]
        cfg += [" no shutdown", "!"]
    return row(rid, "port_security", intent, dev, "\n".join(cfg) + "\n")


GENS = {"etherchannel": gen_ether, "stp": gen_stp,
        "port_security": gen_psec}


def main():
    only = sys.argv[1:] or FAMILIES
    corpus = load_corpus()
    corpus_exact = {(r["intent_nl"], r["config"]) for r in corpus}
    corpus_sigs = {signature(r["intent_nl"], r["config"]) for r in corpus}
    print(f"corpus: {len(corpus)} rows, {len(corpus_sigs)} signatures")
    stats = {}
    for fam in FAMILIES:
        if fam not in only:
            continue
        rng = random.Random(f"batch8-{fam}")
        target = TARGETS[fam]
        out, sigs = [], set()
        caught, dupes, attempts = 0, 0, 0
        while len(out) < target:
            attempts += 1
            if attempts > 200000:
                raise SystemExit(f"{fam}: could not reach {target} rows "
                                 f"(got {len(out)})")
            rid = f"{PREFIX[fam]}-{len(out) + 1:04d}"
            cand = GENS[fam](rng, rid)
            if cand is None:
                continue
            if rng.random() < 0.24:
                cand["intent_nl"] = rng.choice(TICKET_PREFIXES) + \
                    cand["intent_nl"]
            if (cand["intent_nl"], cand["config"]) in corpus_exact:
                dupes += 1
                continue
            sig = signature(cand["intent_nl"], cand["config"])
            if sig in corpus_sigs or sig in sigs:
                dupes += 1
                continue
            errs = gate(cand)
            if errs:
                caught += 1
                if caught <= 3:
                    print(f"  [{fam}] gate caught {cand['id']}: "
                          f"{errs[:3]}")
                continue
            sigs.add(sig)
            out.append(cand)
        path = BASE / f"data/generated/{fam}_b8.jsonl"
        with open(path, "w") as f:
            for r in out:
                f.write(json.dumps(r) + "\n")
        stats[fam] = {"written": len(out), "gate_caught": caught,
                      "dupes_rejected": dupes, "attempts": attempts}
        print(f"{fam}: wrote {len(out)} "
              f"(gate caught {caught}, dupes rejected {dupes}, "
              f"{attempts} attempts)")
    print(json.dumps(stats, indent=1))


if __name__ == "__main__":
    main()
