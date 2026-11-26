#!/usr/bin/env python3
"""Batch 6 generator: 200 rows per family (1,200 total), harder and
wider than batch 5.

Same architecture as gen_batch5.py:
- Dedupe vs full corpus + within batch: exact pair + shape signature
  (intent+config with numbers/names/descriptions normalized).
- Intent-match self-check gate (check_row) parses the intent text and
  asserts every load-bearing entity appears in the config in the right
  role; flagged candidates are dropped and regenerated BEFORE any
  Batfish validation.
- IPs asserted valid and inside RFC1918 / documentation ranges.

Batch-6 deltas vs batch 5:
- Intent styles: ~22% of rows wrapped in ticket / ops-note framing
  ("Change request: ...", "Ops note: ...", "Ticket: ...",
  "Work order: ..."); one new clause style each for vlan_l2 and
  trunk_svi, new head/frame styles for static_route, ospf, bgp and
  interface_mgmt.
- vlan_l2: 3-6 VLANs, up to 8 access ports per VLAN.
- trunk_svi: 2-4 SVIs, longer explicit allowed lists (up to 8 extras
  on the first trunk, 3-12 on later trunks); allowed-VLAN expression
  still rendered ONCE and reused verbatim (batch-4 lesson).
- static_route: 5-8 routes plus floating backups and optional default.
- ospf: area 0 only (locked single-area scope), 2-5 network
  statements, 1-4 interface-mode interfaces.
- bgp: same proven per-peer policy set, policy probability raised.
- interface_mgmt: new combo structures routed_ssh_console and
  loopback_ssh_console (blocks individually probe-passed in batch 5).
Writes data/generated/{family}_b6.jsonl with validation flags null;
fill them from the validator run afterwards (scripts/fill_b6_flags.py).
"""
import ipaddress
import json
import random
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
FAMILIES = ["vlan_l2", "trunk_svi", "static_route", "ospf", "bgp",
            "interface_mgmt"]
PREFIX = {"vlan_l2": "vlan", "trunk_svi": "trunk", "static_route": "static",
          "ospf": "ospf", "bgp": "bgp", "interface_mgmt": "iface"}
START_ID = {"vlan_l2": 348, "trunk_svi": 348, "static_route": 348,
            "ospf": 348, "bgp": 348, "interface_mgmt": 346}
TARGET = 200

TICKET_PREFIXES = ["Change request: ", "Ops note: ", "Ticket: ",
                   "Work order: "]

ALLOWED_NETS = [ipaddress.ip_network(n) for n in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
    "192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")]


def ip_ok(ip: str) -> bool:
    a = ipaddress.ip_address(ip)
    return any(a in n for n in ALLOWED_NETS)


def mask_of(prefix: int) -> str:
    return str(ipaddress.ip_network(f"0.0.0.0/{prefix}").netmask)


def wild_of(prefix: int) -> str:
    return str(ipaddress.ip_network(f"0.0.0.0/{prefix}").hostmask)


def net_of(ip: str, prefix: int) -> str:
    return str(ipaddress.ip_network(f"{ip}/{prefix}", strict=False)
               .network_address)


# ---------------------------------------------------------------- dedupe
def norm_text(s: str, is_config: bool) -> str:
    s = s.lower()
    s = re.sub(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", " ip ", s)
    if is_config:
        s = re.sub(r"^\s*hostname .*$", "hostname host", s, flags=re.M)
        s = re.sub(r"description .*$", "description desc", s, flags=re.M)
        s = re.sub(r"^(\s*)name .*$", r"\1name name", s, flags=re.M)
    s = re.sub(r"named [a-z][\w-]*", "named name", s)
    s = re.sub(r"'[^']*'", "'name'", s)
    s = re.sub(r"\b\d+\b", "n", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def signature(intent: str, config: str) -> str:
    return norm_text(intent, False) + "||" + norm_text(config, True)


def load_corpus():
    rows = []
    gen_files = [p for p in sorted((BASE / "data/generated").glob("*.jsonl"))
                 if not p.name.endswith("_b6.jsonl")]
    for p in [BASE / "data/examples/seed.jsonl"] + gen_files + \
             [BASE / "data/v0.1/dataset.jsonl",
             BASE / "data/v0.2/dataset.jsonl"]:
        if p.exists():
            for line in p.read_text().splitlines():
                if line.strip():
                    rows.append(json.loads(line))
    return rows


# ---------------------------------------------------------------- devices
SWITCH_HOSTS = [("sw1", "switch1"), ("sw2", "switch2"), ("sw3", "switch3"),
                ("sw4", "switch4"), ("sw5", "switch5"), ("sw6", "switch6"),
                ("sw7", "switch7"), ("sw8", "switch8"),
                ("core1", "core1"), ("core2", "core2"),
                ("dist1", "dist1"), ("dist2", "dist2"),
                ("leaf1", "leaf1"), ("leaf2", "leaf2"),
                ("spine1", "spine1"),
                ("br-sw1", "br-sw1"), ("br-sw2", "br-sw2"),
                ("campus-sw1", "campus-sw1"), ("campus-sw2", "campus-sw2"),
                ("soho-sw1", "soho-sw1"), ("dc-sw1", "dc-sw1"),
                ("agg1", "agg1"), ("agg2", "agg2"),
                ("agg-sw1", "agg-sw1"), ("dist-sw1", "dist-sw1"),
                ("tor1", "tor1")]
ROUTER_HOSTS = [("r1", "router1"), ("r2", "router2"), ("r3", "router3"),
                ("r4", "router4"), ("r5", "router5"), ("r6", "router6"),
                ("edge1", "edge1"), ("edge2", "edge2"), ("edge3", "edge3"),
                ("br1", "br1"), ("br2", "br2"),
                ("hq-rtr1", "hq-rtr1"), ("dc-rtr1", "dc-rtr1"),
                ("soho-rtr1", "soho-rtr1"),
                ("campus-rtr1", "campus-rtr1"), ("wan-rtr1", "wan-rtr1"),
                ("wan-edge1", "wan-edge1"), ("inet-rtr1", "inet-rtr1"),
                ("peer-rtr1", "peer-rtr1")]
VERSIONS = ["17.9"] * 7 + ["17.6", "17.12", "17.3"]


def device(rng, hosts, role):
    short, cfgname = rng.choice(hosts)
    return {"vendor": "cisco", "os": "ios_xe", "version": rng.choice(VERSIONS),
            "role": role, "hostname": short}, cfgname


def row(rid, family, intent, oc, dev, config):
    return {"id": rid, "family": family, "intent_nl": intent, "intent_oc": oc,
            "device": dev, "config": config, "source": "synthetic",
            "source_ref": None,
            "validation": {"parse_ok": None, "batfish_ok": None,
                           "emulation_ok": None},
            "notes": "Generated batch 006"}


def join_list(items, conj="and"):
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} {conj} {items[1]}"
    return ", ".join(items[:-1]) + f", {conj} " + items[-1]


# ---------------------------------------------------------------- vlan_l2
VLAN_NAMES = ["Users", "Voice", "Printers", "Servers", "IoT", "GuestWiFi",
              "HR", "Finance", "Engineering", "Lab", "Cameras", "HVAC",
              "Parking", "Lighting", "AccessControl", "WiFi-Corp", "Mgmt",
              "Storage", "Backup", "QA", "DevOps", "Sales", "Support",
              "Training", "ConfRooms", "Warehouse", "Shipping", "Receiving",
              "Security", "AV", "Phones", "Workstations", "Research",
              "Testing", "Facilities", "Executive", "Contractors",
              "PointOfSale", "DigitalSignage", "BuildingMgmt",
              "NorthWing", "SouthWing", "EastWing", "WestWing",
              "Floor1", "Floor2", "Floor3", "Basement", "Annex",
              "Guest", "Staff", "Students", "Faculty", "Clinical",
              "Pharmacy", "Radiology", "Surgery", "ICU", "Emergency",
              "Retail", "Office", "CallCenter", "NOC", "SOC", "Build",
              "Deploy", "Staging", "Prod", "DevNet", "VoiceMail",
              "Intercom", "Elevator", "FireAlarm", "BMS", "Solar",
              "EVCharging", "Cafeteria", "Gym", "Library", "Auditorium"]
PORT_DESCS = ["Desk port", "Office workstation", "IP phone and PC",
              "Printer drop", "Access point", "Camera drop", "Wall jack",
              "Lab bench", "Conference room", "Hot desk", "Reception desk",
              "Nurse station", "Checkout counter", "Teacher desk",
              "Security post", "Loading dock"]


def gen_vlan(rng, rid):
    dev, cfgname = device(rng, SWITCH_HOSTS, rng.choice(
        ["access_switch", "access_switch", "distribution_switch",
         "branch_switch", "campus_switch", "leaf_switch"]))
    n = rng.choices([3, 4, 5, 6], weights=[2, 4, 3, 2])[0]
    vids = rng.sample(range(10, 1000), n)
    names = rng.sample(VLAN_NAMES, n)
    vlans = list(zip(vids, names))
    module = rng.choice(["1/0", "1/0", "1/0", "2/0", "1/1", "3/0", "2/1"])
    pmax = 48 if module in ("1/0", "2/0") else 24
    used_ports = set()
    assign = []  # (vid, [ports], desc_or_None)
    for vid, _ in vlans:
        k = rng.choices([0, 1, 2, 3, 4, 5, 6, 7, 8],
                        weights=[1, 2, 4, 4, 3, 2, 1, 1, 1])[0]
        ports = []
        for _ in range(k):
            for _try in range(60):
                p = f"GigabitEthernet{module}/{rng.randint(1, pmax)}"
                if p not in used_ports:
                    used_ports.add(p)
                    ports.append(p)
                    break
        desc = rng.choice(PORT_DESCS) if ports and rng.random() < 0.4 else None
        assign.append((vid, ports, desc))
    if sum(len(p) for _, p, _ in assign) < 3:  # keep it multi-part
        return None
    style = rng.randint(1, 7)
    vtxt = join_list([f"VLAN {v} named {nm}" for v, nm in vlans])
    order = assign[:]
    if rng.random() < 0.4:
        rng.shuffle(order)
    clauses = []
    for vid, ports, desc in order:
        if not ports:
            continue
        plist = join_list(ports)
        if style == 1:
            clauses.append(f"Assign {plist} to VLAN {vid}")
        elif style == 2:
            clauses.append(f"{plist} go in VLAN {vid}")
        elif style == 3:
            clauses.append(f"VLAN {vid} gets ports {plist}")
        elif style == 4:
            clauses.append(f"ports {plist} are assigned to VLAN {vid}")
        elif style == 5:
            clauses.append(f"{plist} belong to VLAN {vid}")
        elif style == 6:
            clauses.append(f"Put {plist} in VLAN {vid}")
        else:
            clauses.append(f"{plist} are access ports in VLAN {vid}")
    host = dev["hostname"]
    frame = rng.randint(1, 5)
    if frame == 1:
        intent = f"On {host}, create {vtxt}. " + ". ".join(clauses) + "."
    elif frame == 2:
        intent = (f"Create {vtxt} on {host}. " + ". ".join(clauses) +
                  ". Bring all access ports up.")
    elif frame == 3:
        intent = f"{host}: create {vtxt}; " + "; ".join(clauses) + "."
    elif frame == 4:
        intent = (f"Set up {vtxt} on {host}, with " +
                  "; ".join(clauses) + ".")
    else:
        intent = f"Provision on {host}: {vtxt}; " + "; ".join(clauses) + "."
    cfg = [f"hostname {cfgname}", "!"]
    for vid, nm in vlans:
        cfg += [f"vlan {vid}", f" name {nm}", "!"]
    for vid, ports, desc in assign:
        for p in ports:
            cfg.append(f"interface {p}")
            if desc:
                cfg.append(f" description {desc}")
            cfg += [" switchport mode access",
                    f" switchport access vlan {vid}", " no shutdown", "!"]
    oc = {"openconfig-vlan:vlans": {"vlan": [
        {"vlan-id": v, "config": {"name": nm}} for v, nm in vlans]}}
    return row(rid, "vlan_l2", intent, oc, dev, "\n".join(cfg) + "\n")


# ---------------------------------------------------------------- trunk_svi
def render_vlan_expr(rng, vids):
    """Render an explicit allowed-vlan expression ONCE for intent+config.

    Range shorthand is used only for spans that are truly consecutive,
    so the intent can never claim a VLAN the config does not carry.
    """
    vids = sorted(vids)
    if len(vids) >= 4 and vids == list(range(vids[0], vids[-1] + 1)) \
            and rng.random() < 0.5:
        return f"{vids[0]}-{vids[-1]}"
    if len(vids) >= 6 and rng.random() < 0.35 and \
            vids[1:-1] == list(range(vids[1], vids[-2] + 1)):
        return f"{vids[0]},{vids[1]}-{vids[-2]},{vids[-1]}"
    return ",".join(str(v) for v in vids)


def expand_vlan_expr(expr):
    out = set()
    for part in expr.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            out.update(range(int(a), int(b) + 1))
        elif part:
            out.add(int(part))
    return out


TRUNK_DESCS = ["Trunk uplink to core", "Trunk to access layer",
               "Trunk to distribution", "Uplink trunk",
               "Trunk to closet switch", "Trunk to branch core",
               "Leaf uplink trunk", "Campus backbone trunk"]
SVI_DESCS = ["Gateway for users", "SVI gateway", "Voice gateway",
             "Server segment gateway", "Management SVI",
             "Branch gateway", "Default gateway"]


def gen_trunk(rng, rid):
    role = rng.choice(["core_switch", "distribution_switch",
                       "distribution_switch", "campus_switch",
                       "leaf_switch", "branch_switch"])
    dev, cfgname = device(rng, SWITCH_HOSTS, role)
    n_trunk = rng.choices([1, 2, 3], weights=[3, 4, 2])[0]
    n_svi = rng.choices([2, 3, 4], weights=[3, 4, 3])[0]
    base = rng.randrange(10, 600, 10)
    svi_vids = sorted(rng.sample(range(base, base + 90), n_svi))
    port_pool = ["GigabitEthernet1/0/1", "GigabitEthernet1/0/2",
                 "GigabitEthernet1/0/3", "GigabitEthernet1/0/4",
                 "TenGigabitEthernet1/0/1", "TenGigabitEthernet1/0/2",
                 "GigabitEthernet2/0/1", "GigabitEthernet2/0/2",
                 "TenGigabitEthernet1/1/1", "GigabitEthernet1/1/1"]
    ports = rng.sample(port_pool, n_trunk)
    trunks = []
    for i, p in enumerate(ports):
        if i == 0:
            extras = rng.sample(
                [v for v in range(base, base + 90) if v not in svi_vids],
                rng.randint(0, 8))
            allowed = sorted(set(svi_vids) | set(extras))
        else:
            allowed = sorted(rng.sample(range(base, base + 90),
                                        rng.randint(3, 12)))
        native = rng.choice(allowed) if rng.random() < 0.4 else None
        desc = rng.choice(TRUNK_DESCS) if rng.random() < 0.5 else None
        trunks.append((p, allowed, native, desc,
                       render_vlan_expr(rng, allowed)))
    svis = []
    used_subnets = set()
    for vid in svi_vids:
        prefix = rng.choice([24, 24, 23, 26, 25, 27])
        for _try in range(60):
            kind = rng.random()
            if kind < 0.5:
                base_ip = f"10.{vid % 250}.{rng.randint(0, 200)}.0"
            elif kind < 0.75:
                base_ip = f"192.168.{rng.randint(0, 250)}.0"
            else:
                base_ip = f"172.{16 + vid % 16}.{rng.randint(0, 200)}.0"
            net = net_of(base_ip, prefix)
            if net not in used_subnets:
                used_subnets.add(net)
                break
        ip = str(ipaddress.ip_network(f"{net}/{prefix}").network_address + 1)
        desc = rng.choice(SVI_DESCS) if rng.random() < 0.4 else None
        svis.append((vid, ip, prefix, desc))
    tstyle = rng.randint(1, 5)
    sstyle = rng.randint(1, 4)
    tclauses = []
    for p, allowed, native, desc, expr in trunks:
        nat = f" with native VLAN {native}" if native else ""
        if tstyle == 1:
            tclauses.append(
                f"Configure {p} as a trunk allowing VLANs {expr}{nat}")
        elif tstyle == 2:
            tclauses.append(f"{p} trunk allowing VLANs {expr}{nat}")
        elif tstyle == 3:
            tclauses.append(
                f"Make {p} an 802.1Q trunk, allowed VLANs {expr}{nat}")
        elif tstyle == 4:
            tclauses.append(
                f"Set {p} as an 802.1Q trunk allowing VLANs {expr}{nat}")
        else:
            tclauses.append(
                f"{p} is a trunk permitting VLANs {expr}{nat}")
    sclauses = []
    for vid, ip, prefix, desc in svis:
        if sstyle == 1:
            sclauses.append(f"create the SVI for VLAN {vid} with IP "
                            f"{ip}/{prefix}")
        elif sstyle == 2:
            sclauses.append(f"gateway for VLAN {vid} at {ip}/{prefix}")
        elif sstyle == 3:
            sclauses.append(f"SVI for VLAN {vid} with IP {ip}/{prefix}")
        else:
            sclauses.append(f"the gateway for VLAN {vid} is {ip}/{prefix}")
    host = dev["hostname"]
    frame = rng.randint(1, 5)
    if frame <= 3:
        joined = "; ".join(tclauses + sclauses)
    else:
        joined = "; ".join(sclauses + tclauses)
    if frame == 1:
        intent = f"On {host}, {joined}."
    elif frame == 2:
        intent = f"{host}: {joined}."
    elif frame == 3:
        intent = f"Configure on {host}: {joined}."
    elif frame == 4:
        intent = f"On {host}, {joined}."
    else:
        intent = f"{host}: {joined}."
    cfg = [f"hostname {cfgname}", "!"]
    for p, allowed, native, desc, expr in trunks:
        cfg.append(f"interface {p}")
        if desc:
            cfg.append(f" description {desc}")
        cfg.append(" switchport mode trunk")
        if native:
            cfg.append(f" switchport trunk native vlan {native}")
        cfg.append(f" switchport trunk allowed vlan {expr}")
        cfg += [" no shutdown", "!"]
    for vid, ip, prefix, desc in svis:
        cfg.append(f"interface Vlan{vid}")
        if desc:
            cfg.append(f" description {desc}")
        cfg += [f" ip address {ip} {mask_of(prefix)}", " no shutdown", "!"]
    return row(rid, "trunk_svi", intent, None, dev, "\n".join(cfg) + "\n")


# ---------------------------------------------------------------- static_route
WAN_NH = ["192.0.2", "198.51.100", "203.0.113"]
IFACE_DESCS = ["WAN uplink", "Link to core", "Branch link", "Internet edge",
               "Metro Ethernet handoff", "Upstream handoff", "DC interconnect"]


def _dest_net(rng):
    kind = rng.random()
    prefix = rng.choice([24, 24, 25, 26, 27, 28, 23, 22, 21, 16])
    if kind < 0.45:
        base = f"192.168.{rng.randint(0, 250)}.0"
    elif kind < 0.8:
        base = f"172.{rng.randint(16, 31)}.{rng.randint(0, 250)}.0"
    else:
        base = f"10.{rng.randint(0, 250)}.{rng.randint(0, 250)}.0"
    return net_of(base, prefix), prefix


def _nh_ip(rng):
    if rng.random() < 0.5:
        return f"{rng.choice(WAN_NH)}.{rng.randint(1, 254)}"
    return f"10.{rng.randint(0, 250)}.{rng.randint(0, 250)}.{rng.randint(1, 254)}"


def gen_static(rng, rid):
    dev, cfgname = device(rng, ROUTER_HOSTS, rng.choice(
        ["router", "router", "edge_router", "branch_router", "soho_router"]))
    n = rng.choices([5, 6, 7, 8], weights=[2, 4, 3, 2])[0]
    use_exit = rng.random() < 0.5
    exit_pool = rng.sample(["GigabitEthernet0/0/1", "GigabitEthernet0/0/2",
                            "GigabitEthernet0/1/0", "GigabitEthernet0/0/3"],
                           2)
    iface_ip = {}
    if use_exit:
        chosen = exit_pool[:rng.choice([1, 1, 2])]
        for iface, linknum in zip(chosen,
                                  rng.sample(range(1, 201), len(chosen))):
            iface_ip[iface] = (f"10.0.{linknum}.1", 30)
    exit_ifaces = list(iface_ip.keys())
    routes = []  # dicts: net, prefix, kind(nh|exit|exitnh), nh, iface, ad
    seen_nets = set()
    tries = 0
    while len(routes) < n and tries < 200:
        tries += 1
        if rng.random() < 0.15:  # /32 host route
            net = (f"10.{rng.randint(0, 250)}.{rng.randint(0, 250)}."
                   f"{rng.randint(1, 254)}")
            prefix = 32
        else:
            net, prefix = _dest_net(rng)
        if net in seen_nets:
            continue
        seen_nets.add(net)
        r = {"net": net, "prefix": prefix, "ad": None}
        idx = len(routes)
        if exit_ifaces and idx < len(exit_ifaces):
            iface = exit_ifaces[idx]
            r["kind"] = rng.choice(["exit", "exitnh"])
            r["iface"] = iface
            if r["kind"] == "exitnh":
                link = iface_ip[iface][0].rsplit(".", 1)[0]
                r["nh"] = f"{link}.2"
        else:
            r["kind"] = "nh"
            r["nh"] = _nh_ip(rng)
        routes.append(r)
    # floating backups: first route likely, a second one sometimes
    float_targets = []
    if routes and rng.random() < 0.55:
        float_targets.append(0)
    if len(routes) >= 4 and rng.random() < 0.3:
        float_targets.append(rng.randrange(1, len(routes)))
    for ti in float_targets:
        base = routes[ti]
        ad = rng.choice([100, 150, 200, 220, 250])
        nh = _nh_ip(rng)
        while nh == base.get("nh"):
            nh = _nh_ip(rng)
        routes.append({"net": base["net"], "prefix": base["prefix"],
                       "kind": "nh", "nh": nh, "ad": ad})
    has_default = rng.random() < 0.6
    def_kind = None
    def_nh = def_iface = None
    if has_default:
        if exit_ifaces and rng.random() < 0.3:
            def_kind = "exit"
            def_iface = exit_ifaces[0]
        else:
            def_kind = "nh"
            def_nh = _nh_ip(rng)
    style = rng.randint(1, 3)
    clauses = []
    for r in routes:
        tgt = f"{r['net']}/{r['prefix']}"
        if r["kind"] == "nh":
            c = f"{tgt} via {r['nh']}"
            if r["ad"]:
                c += (f" as a floating backup with AD {r['ad']}"
                      if style != 3 else
                      f" (floating, administrative distance {r['ad']})")
        elif r["kind"] == "exit":
            ip, p = iface_ip[r["iface"]]
            c = (f"{tgt} out {r['iface']} (interface IP {ip}/{p})"
                 if style != 2 else
                 f"{tgt} via {r['iface']} (interface IP {ip}/{p})")
        else:
            ip, p = iface_ip[r["iface"]]
            c = f"{tgt} via {r['iface']} {r['nh']} (interface IP {ip}/{p})"
        clauses.append(c)
    if has_default:
        if def_kind == "nh":
            clauses.append(f"a default route via {def_nh}" if style != 2
                           else f"default via {def_nh}")
        else:
            ip, p = iface_ip[def_iface]
            clauses.append(f"a default route out {def_iface} "
                           f"(interface IP {ip}/{p})")
    host = dev["hostname"]
    frame = rng.randint(1, 6)
    body = "; ".join(clauses)
    if frame == 1:
        intent = f"On {host}, add static routes: {body}."
    elif frame == 2:
        intent = f"{host}: static routes — {body}."
    elif frame == 3:
        intent = f"Add these static routes on {host}: {body}."
    elif frame == 4:
        intent = f"Routing table on {host}: {body}."
    elif frame == 5:
        intent = f"{host} needs these static routes: {body}."
    else:
        intent = f"Per the routing plan for {host}: {body}."
    cfg = [f"hostname {cfgname}", "!"]
    for iface, (ip, p) in iface_ip.items():
        cfg += [f"interface {iface}",
                f" description {rng.choice(IFACE_DESCS)}",
                f" ip address {ip} {mask_of(p)}", " no shutdown", "!"]
    for r in routes:
        line = f"ip route {r['net']} {mask_of(r['prefix'])}"
        if r["kind"] == "nh":
            line += f" {r['nh']}"
        elif r["kind"] == "exit":
            line += f" {r['iface']}"
        else:
            line += f" {r['iface']} {r['nh']}"
        if r["ad"]:
            line += f" {r['ad']}"
        cfg.append(line)
    if has_default:
        if def_kind == "nh":
            cfg.append(f"ip route 0.0.0.0 0.0.0.0 {def_nh}")
        else:
            cfg.append(f"ip route 0.0.0.0 0.0.0.0 {def_iface}")
    return row(rid, "static_route", intent, None, dev, "\n".join(cfg) + "\n")


# ---------------------------------------------------------------- ospf
OSPF_IFACE_POOL = ["GigabitEthernet0/0/0", "GigabitEthernet0/0/1",
                   "GigabitEthernet0/0/2", "GigabitEthernet0/0/3",
                   "GigabitEthernet0/1/0", "GigabitEthernet0/1/1",
                   "GigabitEthernet0/1/2", "GigabitEthernet0/2/0"]


def gen_ospf(rng, rid):
    dev, cfgname = device(rng, ROUTER_HOSTS, rng.choice(
        ["router", "router", "branch_router", "dc_router", "campus_router"]))
    pid = rng.choice([1, 10, 20, 50, 64, 100, 110, 200, 300, 400, 1000,
                      1, 10, 100])
    area = 0  # batch 6: backbone only, per locked single-area scope
    rid_ip = f"10.255.{rng.randint(0, 250)}.{rng.randint(1, 254)}"
    use_loopback = rng.random() < 0.35
    n_net = rng.choices([2, 3, 4, 5], weights=[2, 4, 3, 2])[0]
    n_iface = rng.choices([1, 2, 3, 4], weights=[2, 4, 3, 1])[0]
    nets, seen = [], set()
    tries = 0
    while len(nets) < n_net and tries < 60:
        tries += 1
        prefix = rng.choice([24, 24, 16, 30, 22, 28])
        if prefix >= 24:
            base = (f"10.{rng.randint(0, 250)}.{rng.randint(0, 250)}.0"
                    if rng.random() < 0.6 else
                    f"172.{rng.randint(16, 31)}.{rng.randint(0, 250)}.0")
        else:
            base = f"10.{rng.randint(0, 250)}.0.0"
        net = net_of(base, prefix)
        if net not in seen:
            seen.add(net)
            nets.append((net, prefix))
    ifaces = rng.sample(OSPF_IFACE_POOL, n_iface + 3)
    links = rng.sample(range(1, 251), n_iface) if n_iface else []
    mode_ifaces = []  # (iface, ip, prefix, p2p)
    for i in range(n_iface):
        mode_ifaces.append((ifaces[i], f"10.0.{links[i]}.1", 30,
                            rng.random() < 0.3))
    passive_mode = rng.choices(["list", "default", "none"],
                               weights=[11, 6, 3])[0]
    passives, no_pass = [], []
    if passive_mode == "list":
        n_pass = rng.choices([0, 1, 2, 3], weights=[1, 3, 3, 2])[0]
        passives = ifaces[n_iface:n_iface + n_pass]
    elif passive_mode == "default":
        no_pass = ifaces[:max(1, n_iface)] if n_iface else ifaces[:1]
    style = rng.randint(1, 5)
    verb = rng.choice(["network", "network", "advertise",
                       "add network statement"])
    host = dev["hostname"]
    if style == 1:
        head = f"Configure OSPF process {pid} on {host} with router-id {rid_ip}"
    elif style == 2:
        head = f"{host}: OSPF {pid}, router-id {rid_ip}"
    elif style == 3:
        head = f"Set up OSPF process {pid} on {host} (router ID {rid_ip})"
    elif style == 4:
        head = f"Run OSPF process {pid} on {host} with router-id {rid_ip}"
    else:
        head = f"{host} runs OSPF process {pid} with router-id {rid_ip}"
    groups = []
    net_clauses = [f"{verb} {net}/{prefix} in area {area}"
                   for net, prefix in nets]
    if net_clauses:
        groups.append(net_clauses)
    iface_clauses = []
    for iface, ip, p, p2p in mode_ifaces:
        c = f"{iface} ({ip}/{p}) in area {area}"
        if p2p:
            c += " as point-to-point"
        iface_clauses.append(c)
    if iface_clauses:
        groups.append(iface_clauses)
    passive_clauses = []
    if passive_mode == "list" and passives:
        if rng.random() < 0.5:
            passive_clauses.append("make " + join_list(passives) + " passive")
        else:
            passive_clauses.append("with " + join_list(passives) + " passive")
    elif passive_mode == "default":
        passive_clauses.append("all interfaces passive except " +
                               join_list(no_pass))
    if passive_clauses:
        groups.append(passive_clauses)
    if rng.random() < 0.35:
        rng.shuffle(groups)
    parts = [head]
    if use_loopback:
        lb = (f"Loopback0 addressed {rid_ip}/32 as the router-id source"
              if style != 2 else f"Loopback0 {rid_ip}/32")
        parts.insert(1, lb)
    for g in groups:
        parts.extend(g)
    intent = "; ".join(parts) + "."
    cfg = [f"hostname {cfgname}", "!"]
    if use_loopback:
        cfg += ["interface Loopback0",
                " description OSPF router-id source",
                f" ip address {rid_ip} 255.255.255.255", "!"]
    for iface, ip, p, p2p in mode_ifaces:
        cfg += [f"interface {iface}",
                f" description OSPF link area {area}",
                f" ip address {ip} {mask_of(p)}",
                f" ip ospf {pid} area {area}"]
        if p2p:
            cfg.append(" ip ospf network point-to-point")
        cfg += [" no shutdown", "!"]
    cfg.append(f"router ospf {pid}")
    cfg.append(f" router-id {rid_ip}")
    for net, prefix in nets:
        cfg.append(f" network {net} {wild_of(prefix)} area {area}")
    if passive_mode == "list":
        for iface in passives:
            cfg.append(f" passive-interface {iface}")
    elif passive_mode == "default":
        cfg.append(" passive-interface default")
        for iface in no_pass:
            cfg.append(f" no passive-interface {iface}")
    return row(rid, "ospf", intent, None, dev, "\n".join(cfg) + "\n")


# ---------------------------------------------------------------- bgp
PEER_DESCS = ["Primary transit", "Backup transit", "ISP uplink",
              "Upstream provider", "Peering partner", "Regional transit",
              "Wholesale carrier"]


def _asn(rng, exclude=()):
    pool = list(range(64496, 64512)) + list(range(64512, 65535))
    while True:
        a = rng.choice(pool)
        if a not in exclude:
            return a


def _peer_ip(rng):
    return f"{rng.choice(WAN_NH)}.{rng.randint(1, 254)}"


def gen_bgp(rng, rid):
    dev, cfgname = device(rng, ROUTER_HOSTS, rng.choice(
        ["edge_router", "edge_router", "router", "branch_router"]))
    local_as = _asn(rng)
    rid_ip = f"10.255.{rng.randint(0, 250)}.{rng.randint(1, 254)}"
    n_peers = rng.choices([2, 3], weights=[11, 9])[0]
    peers = []  # dicts: ip, asn, desc, shut
    used_as = {local_as}
    used_ips = set()
    for i in range(n_peers):
        asn = _asn(rng, used_as)
        used_as.add(asn)
        ip = _peer_ip(rng)
        while ip in used_ips:
            ip = _peer_ip(rng)
        used_ips.add(ip)
        desc = rng.choice(PEER_DESCS) if rng.random() < 0.45 else None
        shut = (i == n_peers - 1) and rng.random() < 0.25
        peers.append({"ip": ip, "asn": asn, "desc": desc, "shut": shut})
    n_net = rng.choices([2, 3, 4], weights=[3, 4, 2])[0]
    nets, seen = [], set()
    tries = 0
    while len(nets) < n_net and tries < 60:
        tries += 1
        prefix = rng.choice([16, 16, 24, 20, 22])
        if prefix == 16:
            base = (f"10.{rng.randint(1, 250)}.0.0" if rng.random() < 0.7
                    else f"172.{rng.randint(16, 31)}.0.0")
        elif prefix == 24:
            base = f"192.168.{rng.randint(0, 250)}.0"
        else:
            base = f"10.{rng.randint(1, 250)}.{rng.randint(0, 250)}.0"
        net = net_of(base, prefix)
        if net not in seen:
            seen.add(net)
            nets.append((net, prefix))
    # policies: 0-2, distinct kinds, distinct peers, prefer active peers
    n_pol = rng.choices([0, 1, 2], weights=[3, 9, 8])[0]
    policies = []  # dicts: kind, peer_idx, value
    if n_pol:
        kinds = rng.sample(["maxprefix", "localpref", "defaultorig"], n_pol)
        active = [i for i, p in enumerate(peers) if not p["shut"]]
        cand = active if len(active) >= n_pol else list(range(n_peers))
        peer_idxs = rng.sample(cand, n_pol)
        for kind, pi in zip(kinds, peer_idxs):
            if kind == "maxprefix":
                val = rng.choice([500, 1000, 5000, 10000, 100000])
            elif kind == "localpref":
                val = rng.choice([150, 200, 250, 300])
            else:
                val = None
            policies.append({"kind": kind, "peer": pi, "value": val})
    lp_names = {}
    lp_count = 0
    for pol in policies:
        if pol["kind"] == "localpref":
            lp_count += 1
            lp_names[pol["peer"]] = "PREF-IN" if lp_count == 1 \
                else f"PREF-IN{lp_count}"
    style = rng.randint(1, 5)
    host = dev["hostname"]
    peer_txt = []
    for p in peers:
        if style == 2:
            t = f"peer {p['ip']} in AS {p['asn']}"
        else:
            t = f"peer with {p['ip']} in AS {p['asn']}"
        if p["desc"]:
            t += f" ('{p['desc']}')"
        if p["shut"]:
            t += " kept administratively shut down"
        peer_txt.append(t)
    net_txt = join_list([f"{n}/{p}" for n, p in nets])
    pol_txt = ""
    for pol in policies:
        pip = peers[pol["peer"]]["ip"]
        if pol["kind"] == "maxprefix":
            pol_txt += f", and set maximum-prefix {pol['value']} on peer {pip}"
        elif pol["kind"] == "localpref":
            pol_txt += (f", and prefer routes from peer {pip} with "
                        f"local-preference {pol['value']}")
        else:
            pol_txt += f", and originate a default route to peer {pip}"
    if style == 1:
        intent = (f"Configure eBGP AS {local_as} on {host} with router-id "
                  f"{rid_ip}: " + join_list(peer_txt) +
                  f", advertising {net_txt}{pol_txt}.")
    elif style == 2:
        intent = (f"{host}: eBGP AS {local_as}, router-id {rid_ip}; " +
                  "; ".join(peer_txt) + f"; advertise {net_txt}{pol_txt}.")
    elif style == 3:
        intent = (f"Set up eBGP on {host} in AS {local_as} (router-id "
                  f"{rid_ip}) — " + join_list(peer_txt) +
                  f" — advertising {net_txt}{pol_txt}.")
    elif style == 4:
        intent = (f"Bring up eBGP in AS {local_as} on {host} (router-id "
                  f"{rid_ip}): " + "; ".join(peer_txt) +
                  f"; advertising {net_txt}{pol_txt}.")
    else:
        intent = (f"{host} peers via eBGP in AS {local_as} (router-id "
                  f"{rid_ip}): " + "; ".join(peer_txt) +
                  f"; advertising {net_txt}{pol_txt}.")
    cfg = [f"hostname {cfgname}", "!", f"router bgp {local_as}",
           f" bgp router-id {rid_ip}", " bgp log-neighbor-changes"]
    for p in peers:
        cfg.append(f" neighbor {p['ip']} remote-as {p['asn']}")
        if p["desc"]:
            cfg.append(f" neighbor {p['ip']} description {p['desc']}")
        if p["shut"]:
            cfg.append(f" neighbor {p['ip']} shutdown")
    cfg += [" !", " address-family ipv4"]
    for n, p in nets:
        cfg.append(f"  network {n} mask {mask_of(p)}")
    pol_by_peer = {}
    for pol in policies:
        pol_by_peer.setdefault(pol["peer"], []).append(pol)
    for i, p in enumerate(peers):
        if not p["shut"]:
            cfg.append(f"  neighbor {p['ip']} activate")
        for pol in pol_by_peer.get(i, []):
            if pol["kind"] == "maxprefix":
                cfg.append(f"  neighbor {p['ip']} maximum-prefix "
                           f"{pol['value']}")
            elif pol["kind"] == "localpref":
                cfg.append(f"  neighbor {p['ip']} route-map "
                           f"{lp_names[i]} in")
            else:
                cfg.append(f"  neighbor {p['ip']} default-originate")
    cfg.append(" exit-address-family")
    for pol in policies:
        if pol["kind"] == "localpref":
            cfg += ["!", f"route-map {lp_names[pol['peer']]} permit 10",
                    f" set local-preference {pol['value']}"]
    return row(rid, "bgp", intent, None, dev, "\n".join(cfg) + "\n")


# ---------------------------------------------------------------- interface_mgmt
DOMAINS = ["example.com", "example.org", "example.net", "lab.example.com",
           "corp.example.com", "hq.example.org"]
ROUTED_DESCS = ["WAN uplink", "LAN gateway", "Link to core switch",
                "Branch connection", "DMZ interface", "Server segment",
                "Internet edge", "Metro link", "Campus uplink",
                "DC interconnect"]
PORT_LABELS = ["Office workstations", "IP phones", "Printer row",
               "Access points", "User desks", "Lab benches",
               "Conference rooms", "Security cameras", "Reception",
               "Training room", "Branch office desks", "Library terminals"]
BANNERS = ["Authorized access only",
           "Authorized users only - all activity is logged",
           "Authorized access only - sessions are monitored"]


def _ssh_clause_cfg(rng, cfg, clauses):
    domain = rng.choice(DOMAINS)
    et = rng.choice([5, 10, 15, 20])
    vty_hi = rng.choice([4, 15])
    cfg += [f"ip domain-name {domain}", "ip ssh version 2", "!",
            f"line vty 0 {vty_hi}", f" exec-timeout {et} 0",
            " login local", " transport input ssh"]
    clauses.append(f"Enable SSH management: domain name {domain}, SSH "
                   f"version 2, and VTY lines 0 to {vty_hi} for SSH-only "
                   f"login with a {et}-minute timeout")
    return cfg, clauses


def _console_clause_cfg(rng, cfg, clauses):
    et = rng.choice([5, 10, 15])
    cfg += ["line con 0", f" exec-timeout {et} 0",
            " logging synchronous", "!"]
    clauses.append(f"set the console timeout to {et} minutes with "
                   "logging synchronous")
    return cfg, clauses


def _routed_ifaces(rng, cfg, clauses, count_choices):
    ifaces = rng.sample(["GigabitEthernet0/0/0", "GigabitEthernet0/0/1",
                         "GigabitEthernet0/0/2", "GigabitEthernet0/1/0",
                         "GigabitEthernet0/1/1", "GigabitEthernet0/1/2"],
                        rng.choices(count_choices[0],
                                    weights=count_choices[1])[0])
    used3 = set()
    for i, iface in enumerate(ifaces):
        desc = rng.choice(ROUTED_DESCS)
        while True:
            o3 = rng.randint(0, 250)
            if o3 not in used3:
                used3.add(o3)
                break
        ip = f"10.{rng.randint(0, 200)}.{o3}.1"
        prefix = rng.choice([30, 30, 24, 25, 26])
        ip = str(ipaddress.ip_network(f"{ip}/{prefix}",
                                      strict=False).network_address + 1)
        secondary = None
        if i == 0 and rng.random() < 0.3:
            secondary = (f"192.168.{rng.randint(0, 250)}.1", 24)
        cfg.append(f"interface {iface}")
        cfg.append(f" description {desc}")
        cfg.append(f" ip address {ip} {mask_of(prefix)}")
        if secondary:
            cfg.append(f" ip address {secondary[0]} "
                       f"{mask_of(secondary[1])} secondary")
        cfg += [" no shutdown", "!"]
        c = (f"Configure {iface} with description '{desc}' and IP "
             f"{ip}/{prefix}")
        if secondary:
            c += f" plus secondary IP {secondary[0]}/{secondary[1]}"
        c += ", bring it up"
        clauses.append(c)
    return cfg, clauses


def gen_iface(rng, rid):
    structure = rng.choices(
        ["routed", "routed_ssh", "ports_console", "loopback_ssh", "mixed",
         "routed_banner", "ssh_acl", "ports_ssh", "loopback_console",
         "routed_ssh_console", "loopback_ssh_console"],
        weights=[16, 18, 12, 12, 9, 8, 8, 7, 5, 6, 5])[0]
    cfg = []
    clauses = []
    if structure in ("routed", "routed_ssh", "mixed", "routed_banner",
                     "routed_ssh_console"):
        dev, cfgname = device(rng, ROUTER_HOSTS, rng.choice(
            ["router", "edge_router", "branch_router", "soho_router"]))
        cfg, clauses = _routed_ifaces(rng, cfg, clauses,
                                      ([2, 3, 4, 5], [3, 4, 2, 1]))
        if structure == "mixed":
            extra = rng.sample(["GigabitEthernet0/2/0",
                                "GigabitEthernet0/2/1"], 1)[0]
            cfg += [f"interface {extra}", " description Unused port",
                    " shutdown", "!"]
            clauses.append(f"leave {extra} shut down as an unused port")
        if structure == "routed_banner":
            banner = rng.choice(BANNERS)
            cfg += ["banner motd ^", f"*** {banner} ***", "^"]
            clauses.append("set a message-of-the-day banner reading "
                           f"'{banner}'")
        if structure == "routed_ssh_console":
            cfg, clauses = _ssh_clause_cfg(rng, cfg, clauses)
            cfg, clauses = _console_clause_cfg(rng, cfg, clauses)
    elif structure in ("ports_console", "ports_ssh"):
        dev, cfgname = device(rng, SWITCH_HOSTS, rng.choice(
            ["access_switch", "branch_switch", "campus_switch"]))
        ports = rng.sample([f"GigabitEthernet1/0/{i}" for i in range(1, 25)],
                           rng.choices([2, 3, 4], weights=[3, 4, 2])[0])
        label = rng.choice(PORT_LABELS)
        for p in ports:
            cfg += [f"interface {p}", f" description {label}",
                    " no shutdown", "!"]
        clauses.append("Label " + join_list(ports) + f" as '{label}' and "
                       "bring them up")
        if structure == "ports_console":
            cfg, clauses = _console_clause_cfg(rng, cfg, clauses)
        else:
            cfg, clauses = _ssh_clause_cfg(rng, cfg, clauses)
            if rng.random() < 0.4:
                cfg, clauses = _console_clause_cfg(rng, cfg, clauses)
    elif structure == "loopback_console":
        dev, cfgname = device(rng, ROUTER_HOSTS + SWITCH_HOSTS,
                              rng.choice(["router", "core_switch"]))
        lo_ip = f"10.255.{rng.randint(0, 250)}.{rng.randint(1, 254)}"
        cfg += ["interface Loopback0", " description Management loopback",
                f" ip address {lo_ip} 255.255.255.255", "!"]
        clauses.append(f"Create Loopback0 as the management interface with "
                       f"IP {lo_ip}/32")
        cfg, clauses = _console_clause_cfg(rng, cfg, clauses)
    else:  # loopback_ssh, ssh_acl
        dev, cfgname = device(rng, ROUTER_HOSTS + SWITCH_HOSTS,
                              rng.choice(["router", "core_switch",
                                          "branch_router"]))
        lo_ip = f"10.255.{rng.randint(0, 250)}.{rng.randint(1, 254)}"
        cfg += ["interface Loopback0", " description Management loopback",
                f" ip address {lo_ip} 255.255.255.255", "!"]
        clauses.append(f"Create Loopback0 as the management interface with "
                       f"IP {lo_ip}/32")
        if structure == "ssh_acl":
            acl_no = rng.choice([10, 11, 15, 20])
            kind = rng.random()
            if kind < 0.5:
                mgmt_net, mgmt_p = f"10.99.{rng.randint(0, 200)}.0", 24
            elif kind < 0.75:
                mgmt_net, mgmt_p = f"192.168.{rng.randint(0, 250)}.0", 24
            else:
                mgmt_net, mgmt_p = f"172.20.{rng.randint(0, 200)}.0", 24
            domain = rng.choice(DOMAINS)
            et = rng.choice([5, 10, 15])
            vty_hi = rng.choice([4, 15])
            cfg += [f"access-list {acl_no} permit {mgmt_net} "
                    f"{wild_of(mgmt_p)}",
                    f"ip domain-name {domain}", "ip ssh version 2", "!",
                    f"line vty 0 {vty_hi}", f" access-class {acl_no} in",
                    f" exec-timeout {et} 0", " login local",
                    " transport input ssh"]
            clauses.append(f"Enable SSH management: domain name {domain}, "
                           f"SSH version 2, and VTY lines 0 to {vty_hi} for "
                           f"SSH-only login with a {et}-minute timeout")
            clauses.append(f"restrict VTY access with access-list {acl_no} "
                           f"permitting the {mgmt_net}/{mgmt_p} management "
                           "network")
        else:
            cfg, clauses = _ssh_clause_cfg(rng, cfg, clauses)
        if structure == "loopback_ssh_console":
            cfg, clauses = _console_clause_cfg(rng, cfg, clauses)
    host = dev["hostname"]
    style = rng.randint(1, 4)
    if style == 1:
        intent = f"On {host}: " + "; ".join(clauses) + "."
    elif style == 2:
        intent = "; ".join(clauses) + f" — all on {host}."
    elif style == 3:
        intent = f"Configure {host} as follows: " + "; ".join(clauses) + "."
    else:
        intent = f"Work on {host}: " + "; ".join(clauses) + "."
    full = [f"hostname {cfgname}", "!"] + cfg
    return row(rid, "interface_mgmt", intent, None, dev,
               "\n".join(full) + "\n")


# ---------------------------------------------------------------- checker
IP_RE = r"\d{1,3}(?:\.\d{1,3}){3}"
IFACE_RE = r"(?:GigabitEthernet|TenGigabitEthernet|FastEthernet|Loopback)\d+(?:/\d+)*"


def blocks_of(config):
    blocks, cur = {}, None
    for line in config.splitlines():
        if line and not line.startswith((" ", "!")):
            cur = line.strip()
            blocks.setdefault(cur, [])
        elif cur is not None:
            blocks[cur].append(line)
    return blocks


def block_text(blocks, header):
    if header not in blocks:
        return None
    return header + "\n" + "\n".join(blocks[header])


def check_common(r):
    errs = []
    intent, config = r["intent_nl"], r["config"]
    if not config.startswith("hostname "):
        errs.append("config does not start with hostname")
    for ip in set(re.findall(IP_RE, intent)):
        if not ip_ok(ip):
            errs.append(f"intent IP {ip} outside allowed ranges")
        if ip not in config:
            errs.append(f"intent IP {ip} missing from config")
    mask_set = {str(ipaddress.ip_network(f"0.0.0.0/{p}").netmask)
                for p in range(33)} | \
               {str(ipaddress.ip_network(f"0.0.0.0/{p}").hostmask)
                for p in range(33)}
    for ip in set(re.findall(IP_RE, config)):
        if ip in mask_set:
            continue  # dotted mask / wildcard, not an address
        if not ip_ok(ip):
            errs.append(f"config IP {ip} outside allowed ranges")
    for iface in set(re.findall(IFACE_RE, intent)):
        if iface not in config:
            errs.append(f"intent interface {iface} missing from config")
    for net, pfx in re.findall(rf"({IP_RE})/(\d+)", intent):
        if net not in config:
            errs.append(f"intent network {net}/{pfx} missing from config")
    return errs


def check_vlan(r):
    errs = check_common(r)
    intent, config = r["intent_nl"], r["config"]
    blocks = blocks_of(config)
    for vid, name in re.findall(r"VLAN (\d+) named ([\w-]+)", intent):
        body = block_text(blocks, f"vlan {vid}")
        if body is None:
            errs.append(f"vlan {vid} block missing")
        elif f" name {name}" not in body:
            errs.append(f"vlan {vid} name {name} missing")
    assoc = {}
    pats = [r"Assign (.+?) to VLAN (\d+)",
            r"(.+?) go in VLAN (\d+)",
            r"VLAN (\d+) gets ports (.+?)(?:\.|;|$)",
            r"ports (.+?) are assigned to VLAN (\d+)",
            r"(.+?) belong to VLAN (\d+)",
            r"Put (.+?) in VLAN (\d+)",
            r"(.+?) are access ports in VLAN (\d+)"]
    for pat in pats:
        for m in re.finditer(pat, intent):
            if pat.startswith("VLAN"):
                vid, plist = m.group(1), m.group(2)
            else:
                plist, vid = m.group(1), m.group(2)
            for p in re.findall(IFACE_RE, plist):
                assoc[p] = int(vid)
    cfg_assoc = {}
    for hdr, body in blocks.items():
        if hdr.startswith("interface "):
            m = re.search(r"switchport access vlan (\d+)", "\n".join(body))
            if m:
                cfg_assoc[hdr.split(" ", 1)[1]] = int(m.group(1))
    if assoc != cfg_assoc:
        errs.append(f"port->vlan mismatch intent={assoc} config={cfg_assoc}")
    return errs


def check_trunk(r):
    errs = check_common(r)
    intent, config = r["intent_nl"], r["config"]
    blocks = blocks_of(config)
    tpat = (r"(GigabitEthernet[\d/]+|TenGigabitEthernet[\d/]+)"
            r"(?: as a trunk allowing| trunk allowing"
            r"| an 802\.1Q trunk, allowed| as an 802\.1Q trunk allowing"
            r"| is a trunk permitting)"
            r" VLANs ([\d,\- ]+?)"
            r"(?= with native|;|\.|, and | on |$)")
    found = False
    for m in re.finditer(tpat, intent):
        found = True
        port, expr = m.group(1), m.group(2)
        body = block_text(blocks, f"interface {port}")
        if body is None:
            errs.append(f"trunk port {port} missing")
            continue
        cm = re.search(r"switchport trunk allowed vlan ([\d,\-]+)", body)
        if not cm:
            errs.append(f"trunk {port} has no allowed vlan line")
        elif expand_vlan_expr(cm.group(1)) != expand_vlan_expr(expr):
            errs.append(f"trunk {port} allowed set mismatch: intent "
                        f"{sorted(expand_vlan_expr(expr))} vs config "
                        f"{sorted(expand_vlan_expr(cm.group(1)))}")
        if cm and cm.group(1) != expr:
            errs.append(f"trunk {port} expression drift: intent '{expr}' "
                        f"vs config '{cm.group(1)}'")
        if "switchport mode trunk" not in body:
            errs.append(f"{port} not a trunk in config")
    if not found:
        errs.append("no trunk clause parsed from intent")
    for m in re.finditer(r"with native VLAN (\d+)", intent):
        pre = intent[:m.start()]
        ports = re.findall(IFACE_RE, pre)
        if ports:
            body = block_text(blocks, f"interface {ports[-1]}") or ""
            if f"switchport trunk native vlan {m.group(1)}" not in body:
                errs.append(f"native vlan {m.group(1)} missing on "
                            f"{ports[-1]}")
    svipats = [rf"SVI for VLAN (\d+) with IP ({IP_RE})/(\d+)",
               rf"gateway for VLAN (\d+) at ({IP_RE})/(\d+)",
               rf"gateway for VLAN (\d+) is ({IP_RE})/(\d+)"]
    for pat in svipats:
        for vid, ip, pfx in re.findall(pat, intent):
            body = block_text(blocks, f"interface Vlan{vid}")
            if body is None:
                errs.append(f"SVI Vlan{vid} missing")
            elif f"ip address {ip} {mask_of(int(pfx))}" not in body:
                errs.append(f"SVI Vlan{vid} ip {ip}/{pfx} mismatch")
    return errs


def check_static(r):
    errs = check_common(r)
    intent, config = r["intent_nl"], r["config"]
    route_lines = [l.strip() for l in config.splitlines()
                   if l.strip().startswith("ip route ")]

    def has_route(net, pfx, *must):
        mask = mask_of(int(pfx))
        return any(l.startswith(f"ip route {net} {mask}")
                   and all(x in l for x in must) for l in route_lines)

    for net, pfx, iface, nh in re.findall(
            rf"({IP_RE})/(\d+) via (GigabitEthernet[\d/]+) ({IP_RE})",
            intent):
        if not has_route(net, pfx, iface, nh):
            errs.append(f"route {net}/{pfx} via {iface}+{nh} missing")
    for net, pfx, iface in re.findall(
            rf"({IP_RE})/(\d+) (?:out|via) (GigabitEthernet[\d/]+) \(",
            intent):
        if not has_route(net, pfx, iface):
            errs.append(f"exit route {net}/{pfx} out {iface} missing")
    for net, pfx, nh in re.findall(
            rf"({IP_RE})/(\d+) via ({IP_RE})", intent):
        if not has_route(net, pfx, nh):
            errs.append(f"route {net}/{pfx} via {nh} missing")
    for iface, ip, pfx in re.findall(
            rf"(GigabitEthernet[\d/]+) .*?\(interface IP ({IP_RE})/(\d+)\)",
            intent):
        body = block_text(blocks_of(config), f"interface {iface}") or ""
        if f"ip address {ip} {mask_of(int(pfx))}" not in body:
            errs.append(f"exit iface {iface} ip {ip}/{pfx} mismatch")
    for ad in re.findall(r"(?:AD|administrative distance) (\d+)", intent):
        if not any(l.rsplit(" ", 1)[-1] == ad for l in route_lines):
            errs.append(f"floating AD {ad} not on any route line")
    dm = re.search(rf"default (?:route )?via ({IP_RE})", intent)
    if dm and f"ip route 0.0.0.0 0.0.0.0 {dm.group(1)}" not in route_lines:
        errs.append("default route missing")
    dmx = re.search(rf"default route out (GigabitEthernet[\d/]+)", intent)
    if dmx and f"ip route 0.0.0.0 0.0.0.0 {dmx.group(1)}" not in route_lines:
        errs.append("default route via exit interface missing")
    if "default" in intent and not dm and not dmx:
        errs.append("default route mentioned but unparseable")
    return errs


def check_ospf(r):
    errs = check_common(r)
    intent, config = r["intent_nl"], r["config"]
    blocks = blocks_of(config)
    pm = re.search(r"OSPF process (\d+)", intent) or \
        re.search(r"OSPF (\d+),", intent)
    if not pm:
        errs.append("ospf pid not parsed")
        return errs
    pid = pm.group(1)
    rtr = block_text(blocks, f"router ospf {pid}")
    if rtr is None:
        errs.append(f"router ospf {pid} block missing")
        return errs
    rm = re.search(rf"router[- ]ID ({IP_RE})", intent, re.I)
    if rm and f"router-id {rm.group(1)}" not in rtr:
        errs.append("router-id mismatch")
    for pat in (rf"network (?:statement )?({IP_RE})/(\d+) in area (\d+)",
                rf"advertise ({IP_RE})/(\d+) in area (\d+)"):
        for net, pfx, area in re.findall(pat, intent):
            want = f"network {net} {wild_of(int(pfx))} area {area}"
            if want not in rtr:
                errs.append(f"network stmt {want} missing")
    for iface, ip, pfx, area in re.findall(
            rf"({IFACE_RE}) \(({IP_RE})/(\d+)\) in area (\d+)", intent):
        body = block_text(blocks, f"interface {iface}") or ""
        if f"ip address {ip} {mask_of(int(pfx))}" not in body:
            errs.append(f"{iface} ip mismatch")
        if f"ip ospf {pid} area {area}" not in body:
            errs.append(f"{iface} not in ospf {pid} area {area}")
    for iface in re.findall(
            rf"({IFACE_RE}) \({IP_RE}/\d+\) in area \d+ as point-to-point",
            intent):
        body = block_text(blocks, f"interface {iface}") or ""
        if "ip ospf network point-to-point" not in body:
            errs.append(f"{iface} point-to-point missing")
    for seg in intent.split(";"):
        s = seg.strip().rstrip(".")
        mm = re.fullmatch(r"make (.+?) passive", s) or \
            re.fullmatch(r"with (.+?) passive", s)
        if mm:
            for iface in re.findall(IFACE_RE, mm.group(1)):
                if f"passive-interface {iface}" not in rtr:
                    errs.append(f"{iface} not passive")
        dm = re.fullmatch(r"all interfaces passive except (.+)", s)
        if dm:
            if "passive-interface default" not in rtr:
                errs.append("passive-interface default missing")
            for iface in re.findall(IFACE_RE, dm.group(1)):
                if f"no passive-interface {iface}" not in rtr:
                    errs.append(f"{iface} not exempted from passive default")
    return errs


def check_bgp(r):
    errs = check_common(r)
    intent, config = r["intent_nl"], r["config"]
    blocks = blocks_of(config)
    lm = re.search(r"eBGP AS (\d+)", intent) or \
        re.search(r"\bin AS (\d+)", intent)
    if not lm:
        errs.append("local AS not parsed")
        return errs
    local = lm.group(1)
    bgp = block_text(blocks, f"router bgp {local}")
    if bgp is None:
        errs.append(f"router bgp {local} block missing")
        return errs
    rm = re.search(rf"router-id ({IP_RE})", intent)
    if rm and f"bgp router-id {rm.group(1)}" not in bgp:
        errs.append("bgp router-id mismatch")
    peers = list(re.finditer(
        rf"peer (?:with )?({IP_RE}) in AS (\d+)", intent))
    if not 2 <= len(peers) <= 3:
        errs.append(f"expected 2-3 peers parsed, got {len(peers)}")
    for i, m in enumerate(peers):
        ip, asn = m.group(1), m.group(2)
        if f"neighbor {ip} remote-as {asn}" not in bgp:
            errs.append(f"peer {ip} AS {asn} missing")
        tail = intent[m.end(): peers[i + 1].start()
                      if i + 1 < len(peers) else len(intent)]
        if "shut down" in tail:
            if f"neighbor {ip} shutdown" not in bgp:
                errs.append(f"peer {ip} should be shut down")
            if f"neighbor {ip} activate" in bgp:
                errs.append(f"shutdown peer {ip} is activated")
    for desc in re.findall(r"\('([A-Za-z][^']*)'\)", intent):
        if f"description {desc}" not in config:
            errs.append(f"peer description '{desc}' missing")
    for net, pfx in re.findall(rf"({IP_RE})/(\d+)", intent):
        want = f"network {net} mask {mask_of(int(pfx))}"
        if want not in bgp:
            errs.append(f"advertised {want} missing")
    for v, ip in re.findall(
            rf"set maximum-prefix (\d+) on peer ({IP_RE})", intent):
        if f"neighbor {ip} maximum-prefix {v}" not in bgp:
            errs.append(f"maximum-prefix {v} on {ip} missing")
    for ip, v in re.findall(
            rf"prefer routes from peer ({IP_RE}) with local-preference "
            rf"(\d+)", intent):
        if f"set local-preference {v}" not in config:
            errs.append(f"local-preference {v} missing")
        if not re.search(rf"neighbor {re.escape(ip)} route-map \S+ in",
                         bgp):
            errs.append(f"route-map not applied to peer {ip}")
    for ip in re.findall(
            rf"originate a default route to peer ({IP_RE})", intent):
        if f"neighbor {ip} default-originate" not in bgp:
            errs.append(f"default-originate to {ip} missing")
    return errs


def check_iface(r):
    errs = check_common(r)
    intent, config = r["intent_nl"], r["config"]
    blocks = blocks_of(config)
    for iface, desc, ip, pfx in re.findall(
            rf"({IFACE_RE}) with description '([^']+)' and IP "
            rf"({IP_RE})/(\d+)", intent):
        body = block_text(blocks, f"interface {iface}") or ""
        if f"description {desc}" not in body:
            errs.append(f"{iface} description mismatch")
        if f"ip address {ip} {mask_of(int(pfx))}" not in body:
            errs.append(f"{iface} ip mismatch")
        if "no shutdown" not in body:
            errs.append(f"{iface} not brought up")
    for ip, pfx in re.findall(rf"plus secondary IP ({IP_RE})/(\d+)",
                              intent):
        if f"ip address {ip} {mask_of(int(pfx))} secondary" not in config:
            errs.append(f"secondary {ip}/{pfx} missing")
    lm = re.search(r"Label (.+?) as '([^']+)'", intent)
    if lm:
        for p in re.findall(IFACE_RE, lm.group(1)):
            body = block_text(blocks, f"interface {p}") or ""
            if f"description {lm.group(2)}" not in body:
                errs.append(f"{p} label mismatch")
    if "console" in intent:
        body = block_text(blocks, "line con 0")
        if body is None:
            errs.append("console line missing")
        else:
            tm = re.search(r"console timeout to (\d+) minutes", intent)
            if tm and f"exec-timeout {tm.group(1)} 0" not in body:
                errs.append("console timeout mismatch")
    sm = re.search(rf"leave ({IFACE_RE}) shut down", intent)
    if sm:
        body = block_text(blocks, f"interface {sm.group(1)}") or ""
        if not re.search(r"^ shutdown$", body, re.M) or \
                "no shutdown" in body:
            errs.append(f"{sm.group(1)} should be shutdown")
    if "Loopback0" in intent:
        body = block_text(blocks, "interface Loopback0")
        if body is None:
            errs.append("Loopback0 missing")
    if "message-of-the-day banner" in intent:
        if "banner motd ^" not in config:
            errs.append("banner motd missing")
        bm = re.search(r"banner reading '([^']+)'", intent)
        if bm and bm.group(1) not in config:
            errs.append("banner text mismatch")
    am = re.search(rf"access-list (\d+) permitting the ({IP_RE})/(\d+)",
                   intent)
    if am:
        n, net, p = am.group(1), am.group(2), int(am.group(3))
        if f"access-list {n} permit {net_of(net, p)} {wild_of(p)}" \
                not in config:
            errs.append(f"access-list {n} permit line missing")
        if f"access-class {n} in" not in config:
            errs.append(f"access-class {n} not applied")
    if "SSH" in intent:
        if "ip ssh version 2" not in config:
            errs.append("ssh version 2 missing")
        if "transport input ssh" not in config:
            errs.append("vty transport ssh missing")
        dm = re.search(r"domain name ([\w.]+)", intent)
        if dm and f"ip domain-name {dm.group(1)}" not in config:
            errs.append("domain name mismatch")
        vm = re.search(r"VTY lines (\d+) to (\d+)", intent)
        if vm and block_text(blocks,
                             f"line vty {vm.group(1)} {vm.group(2)}") is None:
            errs.append("vty range mismatch")
        tm = re.search(r"(\d+)-minute timeout", intent)
        if tm and f"exec-timeout {tm.group(1)} 0" not in config:
            errs.append("vty timeout mismatch")
    return errs


CHECKERS = {"vlan_l2": check_vlan, "trunk_svi": check_trunk,
            "static_route": check_static, "ospf": check_ospf,
            "bgp": check_bgp, "interface_mgmt": check_iface}
GENS = {"vlan_l2": gen_vlan, "trunk_svi": gen_trunk,
        "static_route": gen_static, "ospf": gen_ospf, "bgp": gen_bgp,
        "interface_mgmt": gen_iface}


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
        rng = random.Random(f"batch6-{fam}")
        out, sigs = [], set()
        caught, dupes, attempts = 0, 0, 0
        while len(out) < TARGET:
            attempts += 1
            if attempts > 40000:
                raise SystemExit(f"{fam}: could not reach {TARGET} rows "
                                 f"(got {len(out)})")
            rid = f"{PREFIX[fam]}-{START_ID[fam] + len(out):04d}"
            cand = GENS[fam](rng, rid)
            if cand is None:
                continue
            if rng.random() < 0.22:
                cand["intent_nl"] = rng.choice(TICKET_PREFIXES) + \
                    cand["intent_nl"]
            if (cand["intent_nl"], cand["config"]) in corpus_exact:
                dupes += 1
                continue
            sig = signature(cand["intent_nl"], cand["config"])
            if sig in corpus_sigs or sig in sigs:
                dupes += 1
                continue
            errs = CHECKERS[fam](cand)
            if errs:
                caught += 1
                if caught <= 3:
                    print(f"  [{fam}] self-check caught {cand['id']}: "
                          f"{errs[:2]}")
                continue
            sigs.add(sig)
            out.append(cand)
        path = BASE / f"data/generated/{fam}_b6.jsonl"
        with open(path, "w") as f:
            for r in out:
                f.write(json.dumps(r) + "\n")
        stats[fam] = {"written": len(out), "selfcheck_caught": caught,
                      "dupes_rejected": dupes, "attempts": attempts}
        print(f"{fam}: wrote {len(out)} "
              f"(self-check caught {caught}, dupes rejected {dupes}, "
              f"{attempts} attempts)")
    print(json.dumps(stats, indent=1))


if __name__ == "__main__":
    main()
