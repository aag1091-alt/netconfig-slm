#!/usr/bin/env python3
"""Batch 9 generator: 287 rows per family, 861 total - Phase 1,
Batch 2 (docs/PHASE1_PLAN.md), growing the corpus 5,861 -> ~6,722
(dataset v1.2).

New families:
- acl (acl): named / numbered, standard / extended ACLs, applied
  inbound / outbound on a routed interface or unapplied; ACEs with
  eq / range / gt / lt port operators, established, log, remarks,
  optional sequence numbers.
- nat (nat): PAT via outside-interface overload, dynamic pool NAT
  with / without overload, static 1:1 mappings, static+dynamic
  combos; standard ACL (named or numbered) as the source list.
- hsrp (hsrp): HSRP v1 / v2 on routed interfaces and SVIs, one or
  two groups, priority / preempt, hello-hold timers in seconds or
  milliseconds.

Construct discipline comes from 24 Batfish probes (b9logs/probes.jsonl
+ probes.log, b9logs/probes2.jsonl + probes2.log; 16 + 8 configs,
run 2026-11-25/26). Batfish-clean and used here: every ACL construct
above except `fragments`; NAT interface PAT, pool NAT (netmask
255.255.255.0 only), pool+overload, static 1:1; HSRP groups /
priority / preempt / timers / version 2 / SVI groups.
Batfish-UNMODELABLE, excluded from generation (parse+intent-only
classes, reported with the batch): the ACL `fragments` keyword,
NAT static port forwarding (`ip nat inside source static tcp/udp`,
with or without `extendable`), NAT pools with a /28 netmask, an
extended ACL as NAT source list, HSRP `standby track` and
`standby name`.

Same architecture as gen_batch8.py:
- Dedupe vs the full v1.1 corpus (5,861 rows) + within batch: exact
  pair + shape signature.
- Intent-match gate BEFORE Batfish: scripts/intent_match.py itself
  (new check_acl / check_nat / check_hsrp, mutation-tested 15/15 in
  scripts/intent_match_b9_controls.py). Flagged candidates are
  dropped and regenerated, never patched.
- RFC1918 / documentation IPs only; hostname first line; bounded
  ACE / group / mapping counts (run-3 truncation lesson); shared
  expressions rendered once (batch-4 lesson).

Writes data/generated/{family}_b9.jsonl with validation flags null;
fill them from the validator run afterwards (scripts/fill_b9_flags.py).
"""
import json
import random
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "scripts"))

import gen_batch7 as b7  # noqa: E402  (shared pools/helpers, main guarded)
import intent_match as im  # noqa: E402  (the gate)

FAMILIES = ["acl", "nat", "hsrp"]
PREFIX = {"acl": "acl", "nat": "nat", "hsrp": "hsrp"}
TARGETS = {"acl": 287, "nat": 287, "hsrp": 287}

ROUTER_HOSTS = b7.ROUTER_HOSTS
SWITCH_HOSTS = b7.SWITCH_HOSTS
TICKET_PREFIXES = b7.TICKET_PREFIXES
VLAN_NAMES = b7.VLAN_NAMES
device = b7.device
join_list = b7.join_list
signature = b7.signature
mask_of = b7.mask_of
wild_of = b7.wild_of
net_of = b7.net_of
ip_ok = b7.ip_ok


def load_corpus():
    rows = []
    p = BASE / "data/v1.1/dataset.jsonl"
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
            "notes": "Generated batch 009"}


def gate(r):
    c = im.Checker(r)
    c.assert_hostname()
    im.FAMILY_CHECKS[r["family"]](c)
    return [f"{f['check']}:{f['entity']}" for f in c.failures]


# ------------------------------------------------------------------ helpers
def lan_net(rng):
    """A random RFC1918 /24 as (network_address, 24)."""
    which = rng.random()
    if which < 0.45:
        return f"10.{rng.randint(0, 200)}.{rng.randint(0, 250)}.0", 24
    if which < 0.75:
        return f"192.168.{rng.randint(0, 250)}.0", 24
    return f"172.{rng.randint(16, 31)}.{rng.randint(0, 250)}.0", 24


def doc_ip(rng, used=None):
    """A random documentation-range host IP (TEST-NET-1/2/3)."""
    for _ in range(100):
        net = rng.choice(["192.0.2", "198.51.100", "203.0.113"])
        ip = f"{net}.{rng.randint(2, 250)}"
        if used is None or ip not in used:
            if used is not None:
                used.add(ip)
            return ip
    raise RuntimeError("doc ip pool exhausted")


def addr_choices(rng, lan):
    """An intent address token + its config rendering."""
    kind = rng.choices(["any", "host", "net"], weights=[3, 3, 4])[0]
    if kind == "any":
        return "any", "any"
    if kind == "host":
        ip = (f"{lan.rsplit('.', 1)[0]}.{rng.randint(2, 250)}"
              if rng.random() < 0.5 else doc_ip(rng))
        return f"host {ip}", f"host {ip}"
    if rng.random() < 0.6:
        net, plen = lan, 24
    else:
        net, plen = lan_net(rng)
    return f"{net}/{plen}", f"{net} {wild_of(plen)}"


# ---------------------------------------------------------------------- acl
ACL_NAMES = ["EDGE-IN", "EDGE-OUT", "WAN-IN", "WAN-OUT", "MGMT-ONLY",
             "LAN-IN", "LAN-OUT", "BLOCK-SCAN", "WEB-ACL", "GUEST-FILTER",
             "VPN-FILTER", "DMZ-IN", "DMZ-OUT", "SERVER-ACL", "USER-ACL",
             "INFRA-PROTECT", "OUTBOUND-FILTER", "INBOUND-FILTER",
             "BRANCH-IN", "VOICE-ACL", "IOT-FILTER", "PARTNER-ACL",
             "CAMPUS-IN", "TRANSIT-ACL"]
ACL_DESCS = ["LAN side", "WAN uplink", "Internet edge", "Server farm",
             "User segment", "Branch uplink", "DMZ segment",
             "Management segment"]
REMARKS = ["Allow web and DNS, block RDP", "Guest traffic restrictions",
           "Management access only", "Block scanning and telnet",
           "Permit business applications", "Edge filtering policy"]


def _ace_pool(rng, lan):
    """One extended ACE as (intent_clause, config_line)."""
    kind = rng.choices(
        ["web", "dns", "ssh", "deny_bad", "established", "range",
         "gt", "lt", "icmp", "ip_pair"],
        weights=[5, 3, 3, 3, 2, 2, 1, 1, 2, 3])[0]
    src_i, src_c = addr_choices(rng, lan)
    if kind == "web":
        dst = doc_ip(rng)
        port = rng.choice([443, 80, 443, 8443])
        return (f"permit tcp from {src_i} to host {dst} port {port}",
                f"permit tcp {src_c} host {dst} eq {port}")
    if kind == "dns":
        dst = rng.choice(["198.51.100.53", "192.0.2.53", "203.0.113.53",
                          doc_ip(rng)])
        return (f"permit udp from {src_i} to host {dst} port 53",
                f"permit udp {src_c} host {dst} eq 53")
    if kind == "ssh":
        dst_i, dst_c = addr_choices(rng, lan)
        return (f"permit tcp from {src_i} to {dst_i} port 22",
                f"permit tcp {src_c} {dst_c} eq 22")
    if kind == "deny_bad":
        port = rng.choice([3389, 23, 445, 135])
        return (f"deny tcp from any to any port {port}",
                f"deny tcp any any eq {port}")
    if kind == "established":
        return ("permit tcp from any to any established",
                "permit tcp any any established")
    if kind == "range":
        a = rng.choice([8000, 8080, 9000, 6000])
        b = a + rng.choice([80, 100, 443, 1000])
        return (f"permit tcp from {src_i} to any port range {a} to {b}",
                f"permit tcp {src_c} any range {a} {b}")
    if kind == "gt":
        n = rng.choice([1023, 2048, 4096])
        return (f"permit tcp from {src_i} to any ports above {n}",
                f"permit tcp {src_c} any gt {n}")
    if kind == "lt":
        n = rng.choice([512, 1024])
        return (f"permit udp from any to any ports below {n}",
                f"permit udp any any lt {n}")
    if kind == "icmp":
        dst_i, dst_c = addr_choices(rng, lan)
        return (f"permit icmp from {src_i} to {dst_i}",
                f"permit icmp {src_c} {dst_c}")
    dst_i, dst_c = addr_choices(rng, lan)
    act = rng.choice(["permit", "permit", "deny"])
    return (f"{act} ip from {src_i} to {dst_i}",
            f"{act} ip {src_c} {dst_c}")


def gen_acl(rng, rid):
    dev, cfgname = device(rng, ROUTER_HOSTS, rng.choice(
        ["edge_router", "branch_router", "core_router", "wan_router"]))
    host = dev["hostname"]
    atype = rng.choices(["extended", "standard"], weights=[7, 3])[0]
    numbered = rng.random() < 0.25
    if numbered:
        name = str(rng.randint(100, 199) if atype == "extended"
                   else rng.randint(1, 99))
    else:
        name = rng.choice(ACL_NAMES)
        if rng.random() < 0.2:
            name += f"-{rng.choice([10, 20, 100, 200])}"
    applied = rng.random() < 0.65
    lan, _ = lan_net(rng)
    clauses, lines = [], []
    if atype == "extended" and rng.random() < 0.25:
        remark = rng.choice(REMARKS)
        clauses.append(f"remark '{remark}'")
        lines.append(f"remark {remark}")
    if atype == "extended":
        n_ace = rng.choices([3, 4, 5, 6, 7], weights=[2, 4, 4, 2, 1])[0]
        seen = set()
        for _ in range(n_ace - 1):
            ci, cc = _ace_pool(rng, lan)
            if cc in seen:
                continue
            seen.add(cc)
            clauses.append(ci)
            lines.append(cc)
        if rng.random() < 0.55:
            log = " with logging" if rng.random() < 0.4 else ""
            clauses.append(f"deny ip from any to any{log}")
            lines.append("deny ip any any" + (" log" if log else ""))
        else:
            ci, cc = _ace_pool(rng, lan)
            if cc not in seen:
                clauses.append(ci)
                lines.append(cc)
    else:
        n_ace = rng.choices([2, 3, 4, 5], weights=[2, 4, 3, 1])[0]
        seen = set()
        for _ in range(n_ace):
            ai, ac = addr_choices(rng, lan)
            act = rng.choices(["permit", "deny"], weights=[3, 1])[0]
            ci, cc = f"{act} {ai}", f"{act} {ac}"
            if cc in seen:
                continue
            seen.add(cc)
            clauses.append(ci)
            lines.append(cc)
        if not any(cc.startswith("deny") for cc in lines) \
                and rng.random() < 0.6:
            clauses.append("deny any")
            lines.append("deny any")
    seq = atype == "extended" and not numbered and rng.random() < 0.35
    acl_lines = [f"ip access-list {atype} {name}"]
    for i, ln in enumerate(lines):
        acl_lines.append(f" {10 * (i + 1)} {ln}" if seq else f" {ln}")
    cfg = [f"hostname {cfgname}", "!"]
    body = "; ".join(clauses)
    if applied:
        iface = rng.choice(["GigabitEthernet0/0/0", "GigabitEthernet0/0/1",
                            "GigabitEthernet0/0/2", "GigabitEthernet0/1/0"])
        gw = f"{lan.rsplit('.', 1)[0]}.1"
        desc = rng.choice(ACL_DESCS) if rng.random() < 0.45 else None
        direction = rng.choice(["inbound", "outbound"])
        dpart = f", description '{desc}'" if desc else ""
        if rng.random() < 0.5:
            intent = (f"On {host}, apply {atype} ACL {name} {direction} "
                      f"on {iface} ({gw}/24{dpart}): {body}.")
        else:
            intent = (f"On {host}, {atype} ACL {name} applied {direction} "
                      f"on {iface} ({gw}/24{dpart}): {body}.")
        cfg.append(f"interface {iface}")
        if desc:
            cfg.append(f" description {desc}")
        cfg += [f" ip address {gw} {mask_of(24)}",
                f" ip access-group {name} "
                f"{'in' if direction == 'inbound' else 'out'}",
                " no shutdown", "!"]
    else:
        verb = rng.choice(["create", "define"])
        intent = f"On {host}, {verb} {atype} ACL {name}: {body}."
    cfg += acl_lines + ["!"]
    return row(rid, "acl", intent, dev, "\n".join(cfg) + "\n")


# ---------------------------------------------------------------------- nat
NAT_ACL_NAMES = ["NAT-SRC", "NAT-ACL", "INSIDE-SRC", "LAN-NAT", "PAT-SRC",
                 "NAT-USERS", "INSIDE-NAT", "BRANCH-NAT"]
POOL_NAMES = ["PUBPOOL", "PATPOOL", "NATPOOL", "GLOBALPOOL", "EDGEPOOL",
              "PUBLIC-POOL"]


def _nat_interfaces(rng):
    lan, _ = lan_net(rng)
    in_iface = rng.choice(["GigabitEthernet0/0/1", "GigabitEthernet0/1/1",
                           "GigabitEthernet0/0/2"])
    out_iface = rng.choice(["GigabitEthernet0/0/0", "GigabitEthernet0/1/0"])
    in_ip = f"{lan.rsplit('.', 1)[0]}.1"
    out_base = rng.choice(["203.0.113", "198.51.100", "192.0.2"])
    out_ip = f"{out_base}.{rng.choice([1, 5, 9, 13])}"
    return lan, in_iface, in_ip, out_iface, out_ip, out_base


def _source_acl(rng, lan):
    named = rng.random() < 0.7
    acl = rng.choice(NAT_ACL_NAMES) if named else str(rng.randint(1, 99))
    nets = [lan]
    for _ in range(rng.choices([0, 1, 2], weights=[5, 3, 1])[0]):
        extra, _ = lan_net(rng)
        if extra not in nets:
            nets.append(extra)
    entries = join_list([f"permit {n}/24" for n in nets])
    lines = [f"permit {n} {wild_of(24)}" for n in nets]
    return acl, entries, lines


def gen_nat(rng, rid):
    dev, cfgname = device(rng, ROUTER_HOSTS, rng.choice(
        ["edge_router", "branch_router", "perimeter_router",
         "internet_edge"]))
    host = dev["hostname"]
    lan, in_iface, in_ip, out_iface, out_ip, out_base = \
        _nat_interfaces(rng)
    structure = rng.choices(
        ["pat", "pool", "pool_overload", "static", "static_pat"],
        weights=[7, 5, 4, 2, 2])[0]
    acl, entries, acl_lines = _source_acl(rng, lan)
    sentences = []
    globals_ = []
    need_acl = structure != "static"
    if structure in ("pat", "static_pat"):
        sentences.append(
            f"Translate inside sources matching standard ACL {acl} "
            f"({entries}) to the outside interface address with overload.")
        globals_.append(
            f"ip nat inside source list {acl} interface {out_iface} "
            f"overload")
    elif structure in ("pool", "pool_overload"):
        pool = rng.choice(POOL_NAMES)
        start = rng.choice([16, 32, 64, 128])
        size = rng.choice([16, 32])
        a, b = f"{out_base}.{start}", f"{out_base}.{start + size - 1}"
        ov = structure == "pool_overload"
        sentences.append(
            f"Translate inside sources matching standard ACL {acl} "
            f"({entries}) to pool {pool} ({a} to {b}, netmask "
            f"255.255.255.0){' with overload' if ov else ''}.")
        globals_.append(f"ip nat pool {pool} {a} {b} "
                        f"netmask 255.255.255.0")
        globals_.append(f"ip nat inside source list {acl} pool {pool}"
                        + (" overload" if ov else ""))
    if structure in ("static", "static_pat"):
        n_map = rng.choices([1, 2, 3], weights=[3, 3, 1])[0]
        used = set()
        maps = []
        for _ in range(n_map):
            inside = f"{lan.rsplit('.', 1)[0]}.{rng.randint(10, 99)}"
            outside = doc_ip(rng, used)
            maps.append(f"{inside} to {outside}")
            globals_.append(
                f"ip nat inside source static {inside} {outside}")
        noun = "mapping" if n_map == 1 else "mappings"
        sentences.append(
            f"Add static NAT {noun}: {'; '.join(maps)}.")
    if rng.random() < 0.5:
        header = (f"On {host}, configure NAT with {in_iface} "
                  f"({in_ip}/24) as the inside interface and {out_iface} "
                  f"({out_ip}/30) as the outside interface.")
    else:
        header = (f"{host}: NAT, inside is {in_iface} ({in_ip}/24), "
                  f"outside is {out_iface} ({out_ip}/30).")
    intent = header + " " + " ".join(sentences)
    cfg = [f"hostname {cfgname}", "!",
           f"interface {in_iface}",
           f" ip address {in_ip} {mask_of(24)}",
           " ip nat inside", " no shutdown", "!",
           f"interface {out_iface}",
           f" ip address {out_ip} {mask_of(30)}",
           " ip nat outside", " no shutdown", "!"]
    if need_acl:
        cfg.append(f"ip access-list standard {acl}")
        cfg += [f" {ln}" for ln in acl_lines]
        cfg.append("!")
    cfg += globals_ + ["!"]
    return row(rid, "nat", intent, dev, "\n".join(cfg) + "\n")


# --------------------------------------------------------------------- hsrp
def _hsrp_group(rng, gid, subnet_base, used_vips, first):
    vip_last = rng.choice([1, 254]) if first else \
        rng.choice([1, 254, 100, 200])
    vip = f"{subnet_base}.{vip_last}"
    while vip in used_vips:
        vip_last += 1
        vip = f"{subnet_base}.{vip_last}"
    used_vips.add(vip)
    prio = rng.choice([90, 95, 100, 105, 110, 115, 120])
    preempt = rng.random() < 0.65
    timers = None
    t = rng.random()
    if t < 0.25:
        h, hd = rng.choice([(1, 3), (2, 6), (3, 10)])
        timers = ("sec", h, hd)
    elif t < 0.45:
        h, hd = rng.choice([(250, 750), (200, 600), (300, 900)])
        timers = ("msec", h, hd)
    clause = f"group {gid} virtual IP {vip}, priority {prio}"
    if preempt:
        clause += ", preempt enabled"
    if timers:
        if timers[0] == "sec":
            clause += f", hello {timers[1]} hold {timers[2]}"
        else:
            clause += f", hello {timers[1]} msec hold {timers[2]} msec"
    lines = [f" standby {gid} ip {vip}",
             f" standby {gid} priority {prio}"]
    if preempt:
        lines.append(f" standby {gid} preempt")
    if timers:
        if timers[0] == "sec":
            lines.append(f" standby {gid} timers {timers[1]} {timers[2]}")
        else:
            lines.append(f" standby {gid} timers msec {timers[1]} "
                         f"msec {timers[2]}")
    return clause, lines


def gen_hsrp(rng, rid):
    svi = rng.random() < 0.35
    if svi:
        dev, cfgname = device(rng, SWITCH_HOSTS, rng.choice(
            ["core_switch", "distribution_switch", "campus_switch"]))
    else:
        dev, cfgname = device(rng, ROUTER_HOSTS, rng.choice(
            ["core_router", "branch_router", "edge_router"]))
    host = dev["hostname"]
    lan, _ = lan_net(rng)
    base = lan.rsplit(".", 1)[0]
    dev_last = rng.choice([2, 3])
    dev_ip = f"{base}.{dev_last}"
    two = rng.random() < 0.35
    v2 = rng.random() < (0.75 if two else 0.4)
    gids = rng.sample(range(1, 100), 2 if two else 1)
    used_vips = {dev_ip}
    clauses, glines = [], []
    if v2:
        clauses.append("HSRP version 2")
    for i, gid in enumerate(gids):
        clause, lines = _hsrp_group(rng, gid, base, used_vips, i == 0)
        clauses.append(clause)
        glines += lines
    body = "; ".join(clauses)
    verb = rng.choice(["configure", "set up", "enable", "run"])
    cfg = [f"hostname {cfgname}", "!"]
    if svi:
        vid = rng.randint(10, 400)
        iface = f"Vlan{vid}"
        intent = (f"On {host}, {verb} HSRP on {iface} ({dev_ip}/24) "
                  f"for VLAN {vid}: {body}.")
        cfg += [f"vlan {vid}", "!"]
    else:
        iface = rng.choice(["GigabitEthernet0/0/1", "GigabitEthernet0/0/2",
                            "GigabitEthernet0/1/0", "GigabitEthernet0/1/1"])
        if rng.random() < 0.5:
            intent = (f"On {host}, {verb} HSRP on {iface} ({dev_ip}/24): "
                      f"{body}.")
        else:
            intent = f"{host}: HSRP on {iface} ({dev_ip}/24): {body}."
    cfg.append(f"interface {iface}")
    cfg.append(f" ip address {dev_ip} {mask_of(24)}")
    if v2:
        cfg.append(" standby version 2")
    cfg += glines
    cfg += [" no shutdown", "!"]
    return row(rid, "hsrp", intent, dev, "\n".join(cfg) + "\n")


GENS = {"acl": gen_acl, "nat": gen_nat, "hsrp": gen_hsrp}


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
        rng = random.Random(f"batch9-{fam}")
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
        path = BASE / f"data/generated/{fam}_b9.jsonl"
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
