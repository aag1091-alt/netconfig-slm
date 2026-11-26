#!/usr/bin/env python3
"""Batch 4 generator: 100 harder multi-part rows per family (600 total).

- Dedupe vs full corpus + within batch: exact pair + shape signature
  (intent+config with numbers/names/descriptions normalized).
- Intent-match self-check gate (check_row) parses the intent text and
  asserts every load-bearing entity appears in the config in the right
  role; flagged rows are dropped and regenerated.
- IPs asserted valid and inside RFC1918 / documentation ranges.
Writes data/generated/{family}_b4.jsonl with validation flags null;
fill them from the validator run afterwards.
"""
import ipaddress
import json
import random
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
FAMILIES = ["vlan_l2", "trunk_svi", "static_route", "ospf", "bgp", "interface_mgmt"]
PREFIX = {"vlan_l2": "vlan", "trunk_svi": "trunk", "static_route": "static",
          "ospf": "ospf", "bgp": "bgp", "interface_mgmt": "iface"}
START_ID = {"vlan_l2": 98, "trunk_svi": 98, "static_route": 98,
            "ospf": 98, "bgp": 98, "interface_mgmt": 96}

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
    return str(ipaddress.ip_network(f"{ip}/{prefix}", strict=False).network_address)


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
    for p in [BASE / "data/examples/seed.jsonl"] + \
             sorted((BASE / "data/generated").glob("*.jsonl")) + \
             [BASE / "data/v0.1/dataset.jsonl"]:
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
                ("dist1", "dist1"), ("dist2", "dist2")]
ROUTER_HOSTS = [("r1", "router1"), ("r2", "router2"), ("r3", "router3"),
                ("r4", "router4"), ("r5", "router5"), ("r6", "router6"),
                ("edge1", "edge1"), ("edge2", "edge2")]
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
            "notes": "Generated batch 004"}


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
              "PointOfSale", "DigitalSignage", "BuildingMgmt"]
PORT_DESCS = ["Desk port", "Office workstation", "IP phone and PC",
              "Printer drop", "Access point", "Camera drop", "Wall jack",
              "Lab bench", "Conference room", "Hot desk"]


def gen_vlan(rng, rid):
    dev, cfgname = device(rng, SWITCH_HOSTS,
                          rng.choice(["access_switch", "access_switch",
                                      "distribution_switch"]))
    n = rng.choices([2, 3, 4], weights=[2, 5, 3])[0]
    vids = rng.sample(range(10, 1000), n)
    names = rng.sample(VLAN_NAMES, n)
    vlans = list(zip(vids, names))
    module = rng.choice(["1/0", "1/0", "1/0", "1/1", "2/0"])
    used_ports = set()
    assign = []  # (vid, [ports], desc_or_None)
    for vid, _ in vlans:
        k = rng.choices([0, 1, 2, 3, 4], weights=[1, 2, 4, 3, 2])[0]
        ports = []
        for _ in range(k):
            for _try in range(20):
                p = f"GigabitEthernet{module}/{rng.randint(1, 24)}"
                if p not in used_ports:
                    used_ports.add(p)
                    ports.append(p)
                    break
        desc = rng.choice(PORT_DESCS) if ports and rng.random() < 0.35 else None
        assign.append((vid, ports, desc))
    if sum(len(p) for _, p, _ in assign) < 3:  # keep it multi-part
        return None
    style = rng.randint(1, 4)
    vtxt = join_list([f"VLAN {v} named {nm}" for v, nm in vlans])
    clauses = []
    for vid, ports, desc in assign:
        if not ports:
            continue
        plist = join_list(ports)
        if style == 1:
            clauses.append(f"Assign {plist} to VLAN {vid}")
        elif style == 2:
            clauses.append(f"{plist} go in VLAN {vid}")
        elif style == 3:
            clauses.append(f"VLAN {vid} gets ports {plist}")
        else:
            clauses.append(f"ports {plist} are assigned to VLAN {vid}")
    host = dev["hostname"]
    if style == 1:
        intent = f"On {host}, create {vtxt}. " + ". ".join(clauses) + "."
    elif style == 2:
        intent = (f"Create {vtxt} on {host}. " + ". ".join(clauses) +
                  ". Bring all access ports up.")
    elif style == 3:
        intent = f"{host}: create {vtxt}; " + "; ".join(clauses) + "."
    else:
        intent = (f"Set up {vtxt} on {host}, with " +
                  "; ".join(clauses) + ".")
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
def vlan_list_expr(rng, vids):
    """Render an explicit allowed-vlan expression (list / range / mixed)."""
    vids = sorted(vids)
    style = rng.random()
    if len(vids) >= 4 and vids == list(range(vids[0], vids[-1] + 1)) and style < 0.6:
        return f"{vids[0]}-{vids[-1]}"
    if len(vids) >= 5 and style < 0.5 and \
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
               "Trunk to distribution", "Uplink trunk", "Trunk to closet switch"]
SVI_DESCS = ["Gateway for users", "SVI gateway", "Voice gateway",
             "Server segment gateway", "Management SVI"]


def gen_trunk(rng, rid):
    role = rng.choice(["core_switch", "distribution_switch",
                       "distribution_switch"])
    dev, cfgname = device(rng, SWITCH_HOSTS, role)
    n_trunk = rng.choices([1, 2], weights=[3, 2])[0]
    n_svi = rng.choices([1, 2, 3], weights=[2, 4, 3])[0]
    base = rng.randrange(10, 400, 10)
    svi_vids = sorted(rng.sample(range(base, base + 60), n_svi))
    ports = rng.sample(["GigabitEthernet1/0/1", "GigabitEthernet1/0/2",
                        "GigabitEthernet1/0/3", "TenGigabitEthernet1/0/1",
                        "TenGigabitEthernet1/0/2", "GigabitEthernet2/0/1"],
                       n_trunk)
    trunks = []
    for i, p in enumerate(ports):
        if i == 0:
            allowed = sorted(set(svi_vids) |
                             set(rng.sample(range(base, base + 60),
                                            rng.randint(0, 3))))
        else:
            allowed = sorted(rng.sample(range(base, base + 60),
                                        rng.randint(3, 7)))
        native = rng.choice(allowed) if rng.random() < 0.35 else None
        desc = rng.choice(TRUNK_DESCS) if rng.random() < 0.5 else None
        trunks.append((p, allowed, native, desc))
    svis = []
    used_subnets = set()
    for vid in svi_vids:
        for _try in range(30):
            net3 = rng.randint(0, 200)
            prefix = rng.choice([24, 24, 23, 26, 25])
            subnet = f"10.{vid % 250}.{net3}"
            if subnet not in used_subnets:
                used_subnets.add(subnet)
                break
        ip = str(ipaddress.ip_network(f"{subnet}.0/{prefix}",
                                      strict=False).network_address + 1)
        desc = rng.choice(SVI_DESCS) if rng.random() < 0.4 else None
        svis.append((vid, ip, prefix, desc))
    style = rng.randint(1, 3)
    host = dev["hostname"]
    tclauses, sclauses = [], []
    for p, allowed, native, desc in trunks:
        expr = vlan_list_expr(rng, allowed)
        nat = f" with native VLAN {native}" if native else ""
        if style == 1:
            tclauses.append(
                f"Configure {p} as a trunk allowing VLANs {expr}{nat}")
        elif style == 2:
            tclauses.append(f"{p} trunk allowing VLANs {expr}{nat}")
        else:
            tclauses.append(
                f"Make {p} an 802.1Q trunk, allowed VLANs {expr}{nat}")
    for vid, ip, prefix, desc in svis:
        if style == 2:
            sclauses.append(f"gateway for VLAN {vid} at {ip}/{prefix}")
        else:
            sclauses.append(f"create the SVI for VLAN {vid} with IP "
                            f"{ip}/{prefix}")
    if style == 1:
        intent = (f"On {host}, " + "; ".join(tclauses) + ", and " +
                  join_list(sclauses) + ".")
    elif style == 2:
        intent = f"{host}: " + "; ".join(tclauses) + "; " + \
            join_list(sclauses) + "."
    else:
        intent = ("Configure " + "; ".join(tclauses) + " on " + host +
                  ", and " + join_list(sclauses) + ".")
    cfg = [f"hostname {cfgname}", "!"]
    for p, allowed, native, desc in trunks:
        cfg.append(f"interface {p}")
        if desc:
            cfg.append(f" description {desc}")
        cfg.append(" switchport mode trunk")
        if native:
            cfg.append(f" switchport trunk native vlan {native}")
        cfg.append(f" switchport trunk allowed vlan "
                   f"{vlan_list_expr(rng, allowed)}")
        cfg += [" no shutdown", "!"]
    # NOTE: intent and config render the allowed expression independently;
    # the self-check compares the expanded sets.
    for vid, ip, prefix, desc in svis:
        cfg.append(f"interface Vlan{vid}")
        if desc:
            cfg.append(f" description {desc}")
        cfg += [f" ip address {ip} {mask_of(prefix)}", " no shutdown", "!"]
    return row(rid, "trunk_svi", intent, None, dev, "\n".join(cfg) + "\n")


# ---------------------------------------------------------------- static_route
WAN_NH = ["192.0.2", "198.51.100", "203.0.113"]
IFACE_DESCS = ["WAN uplink", "Link to core", "Branch link", "Internet edge",
               "Metro Ethernet handoff"]


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
    dev, cfgname = device(rng, ROUTER_HOSTS,
                          rng.choice(["router", "router", "edge_router"]))
    n = rng.choices([3, 4, 5], weights=[3, 4, 2])[0]
    routes = []  # dicts: net, prefix, kind(nh|exit|exitnh), nh, iface, ad
    seen_nets = set()
    use_exit = rng.random() < 0.45
    exit_ifaces = rng.sample(["GigabitEthernet0/0/1", "GigabitEthernet0/0/2",
                              "GigabitEthernet0/1/0"], 2)
    iface_ip = {}
    for i in range(n):
        net, prefix = _dest_net(rng)
        if net in seen_nets:
            continue
        seen_nets.add(net)
        r = {"net": net, "prefix": prefix, "ad": None}
        if use_exit and i == 0:
            iface = exit_ifaces[0]
            link = f"10.0.{rng.randint(1, 200)}"
            iface_ip[iface] = (f"{link}.1", 30)
            r["kind"] = rng.choice(["exit", "exitnh"])
            r["iface"] = iface
            if r["kind"] == "exitnh":
                r["nh"] = f"{link}.2"
        else:
            r["kind"] = "nh"
            r["nh"] = _nh_ip(rng)
        routes.append(r)
    # floating backup for the first route
    if rng.random() < 0.55 and routes:
        base = routes[0]
        ad = rng.choice([100, 150, 200, 220, 250])
        fb = {"net": base["net"], "prefix": base["prefix"], "kind": "nh",
              "nh": _nh_ip(rng), "ad": ad}
        while fb["nh"] == base.get("nh"):
            fb["nh"] = _nh_ip(rng)
        routes.append(fb)
    has_default = rng.random() < 0.6
    def_nh = _nh_ip(rng) if has_default else None
    style = rng.randint(1, 3)
    host = dev["hostname"]
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
        clauses.append(f"a default route via {def_nh}" if style != 2
                       else f"default via {def_nh}")
    if style == 1:
        intent = (f"On {host}, add static routes: " +
                  "; ".join(clauses) + ".")
    elif style == 2:
        intent = f"{host}: static routes — " + "; ".join(clauses) + "."
    else:
        intent = ("Add these static routes on " + host + ": " +
                  "; ".join(clauses) + ".")
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
        cfg.append(f"ip route 0.0.0.0 0.0.0.0 {def_nh}")
    return row(rid, "static_route", intent, None, dev, "\n".join(cfg) + "\n")


# ---------------------------------------------------------------- ospf
def gen_ospf(rng, rid):
    dev, cfgname = device(rng, ROUTER_HOSTS, "router")
    pid = rng.choice([1, 10, 20, 50, 64, 100, 110, 200, 300, 400, 1000])
    area = rng.choices([0, 0, 0, 0, 5, 10, 20, 100],
                       weights=[6, 6, 6, 6, 1, 1, 1, 1])[0]
    rid_ip = f"10.255.{rng.randint(0, 250)}.{rng.randint(1, 254)}"
    use_loopback = rng.random() < 0.3
    n_net = rng.choices([1, 2, 3, 4], weights=[2, 4, 3, 2])[0]
    n_iface = rng.choices([0, 1, 2, 3], weights=[2, 3, 4, 2])[0]
    if n_net == 0 and n_iface == 0:
        n_net = 2
    nets, seen = [], set()
    for _ in range(n_net):
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
    ifaces = rng.sample(["GigabitEthernet0/0/0", "GigabitEthernet0/0/1",
                         "GigabitEthernet0/0/2", "GigabitEthernet0/0/3",
                         "GigabitEthernet0/1/0", "GigabitEthernet0/1/1"],
                        n_iface + 3)
    mode_ifaces = []
    for i in range(n_iface):
        link = f"10.0.{rng.randint(1, 250)}"
        mode_ifaces.append((ifaces[i], f"{link}.1", 30))
    passive_mode = rng.choice(["list", "list", "default"])
    if passive_mode == "list":
        n_pass = rng.choices([0, 1, 2, 3], weights=[1, 3, 3, 2])[0]
        passives = ifaces[n_iface:n_iface + n_pass]
        no_pass = []
    else:
        passives = []
        no_pass = ifaces[:max(1, n_iface)]
        if not no_pass:
            no_pass = [ifaces[0]]
    style = rng.randint(1, 3)
    host = dev["hostname"]
    parts = []
    if style == 1:
        head = f"Configure OSPF process {pid} on {host} with router-id {rid_ip}"
    elif style == 2:
        head = f"{host}: OSPF {pid}, router-id {rid_ip}"
    else:
        head = f"Set up OSPF process {pid} on {host} (router ID {rid_ip})"
    parts.append(head)
    if use_loopback:
        parts.append(f"Loopback0 addressed {rid_ip}/32 as the router-id source"
                     if style != 2 else f"Loopback0 {rid_ip}/32")
    for net, prefix in nets:
        if style == 2:
            parts.append(f"advertise {net}/{prefix} in area {area}")
        else:
            parts.append(f"network {net}/{prefix} in area {area}")
    for iface, ip, p in mode_ifaces:
        parts.append(f"{iface} ({ip}/{p}) in area {area}")
    if passive_mode == "list" and passives:
        parts.append("make " + join_list(passives) + " passive")
    if passive_mode == "default":
        parts.append("all interfaces passive except " +
                     join_list(no_pass))
    intent = "; ".join(parts) + "."
    if style == 3:
        intent = intent.replace("network ", "add network statement ")
    cfg = [f"hostname {cfgname}", "!"]
    if use_loopback:
        cfg += ["interface Loopback0",
                " description OSPF router-id source",
                f" ip address {rid_ip} 255.255.255.255", "!"]
    for iface, ip, p in mode_ifaces:
        cfg += [f"interface {iface}",
                f" description OSPF link area {area}",
                f" ip address {ip} {mask_of(p)}",
                f" ip ospf {pid} area {area}", " no shutdown", "!"]
    cfg.append(f"router ospf {pid}")
    cfg.append(f" router-id {rid_ip}")
    for net, prefix in nets:
        cfg.append(f" network {net} {wild_of(prefix)} area {area}")
    if passive_mode == "list":
        for iface in passives:
            cfg.append(f" passive-interface {iface}")
    else:
        cfg.append(" passive-interface default")
        for iface in no_pass:
            cfg.append(f" no passive-interface {iface}")
    return row(rid, "ospf", intent, None, dev, "\n".join(cfg) + "\n")


# ---------------------------------------------------------------- bgp
PEER_DESCS = ["Primary transit", "Backup transit", "ISP uplink",
              "Upstream provider", "Peering partner"]


def _asn(rng, exclude=()):
    pool = list(range(64496, 64512)) + list(range(64512, 65535))
    while True:
        a = rng.choice(pool)
        if a not in exclude:
            return a


def _peer_ip(rng):
    return f"{rng.choice(WAN_NH)}.{rng.randint(1, 254)}"


def gen_bgp(rng, rid):
    dev, cfgname = device(rng, ROUTER_HOSTS, "edge_router")
    local_as = _asn(rng)
    rid_ip = f"10.255.{rng.randint(0, 250)}.{rng.randint(1, 254)}"
    peers = []  # (ip, asn, desc, shutdown)
    used_as = {local_as}
    for i in range(2):
        asn = _asn(rng, used_as)
        used_as.add(asn)
        desc = rng.choice(PEER_DESCS) if rng.random() < 0.4 else None
        peers.append([_peer_ip(rng), asn, desc, i == 1 and rng.random() < 0.3])
    n_net = rng.choices([2, 3, 4], weights=[3, 4, 2])[0]
    nets, seen = [], set()
    for _ in range(n_net):
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
    policy = rng.choice(["none", "maxprefix", "localpref", "defaultorig",
                         "maxprefix"])
    pol_peer = rng.randrange(2)
    pol_val = {"maxprefix": rng.choice([500, 1000, 5000, 10000, 100000]),
               "localpref": rng.choice([150, 200, 250, 300])}.get(policy)
    style = rng.randint(1, 3)
    host = dev["hostname"]
    peer_txt = []
    for ip, asn, desc, shut in peers:
        if style == 2:
            t = f"peer {ip} in AS {asn}"
        else:
            t = f"peer with {ip} in AS {asn}"
        if desc:
            t += f" ('{desc}')"
        if shut:
            t += " kept administratively shut down"
        peer_txt.append(t)
    net_txt = join_list([f"{n}/{p}" for n, p in nets])
    pol_txt = ""
    if policy == "maxprefix":
        pol_txt = (f", and set maximum-prefix {pol_val} on peer "
                   f"{peers[pol_peer][0]}")
    elif policy == "localpref":
        pol_txt = (f", and prefer routes from peer {peers[pol_peer][0]} "
                   f"with local-preference {pol_val}")
    elif policy == "defaultorig":
        pol_txt = (f", and originate a default route to peer "
                   f"{peers[pol_peer][0]}")
    if style == 1:
        intent = (f"Configure eBGP AS {local_as} on {host} with router-id "
                  f"{rid_ip}: " + join_list(peer_txt) +
                  f", advertising {net_txt}{pol_txt}.")
    elif style == 2:
        intent = (f"{host}: eBGP AS {local_as}, router-id {rid_ip}; " +
                  "; ".join(peer_txt) + f"; advertise {net_txt}{pol_txt}.")
    else:
        intent = (f"Set up eBGP on {host} in AS {local_as} (router-id "
                  f"{rid_ip}) — " + join_list(peer_txt) +
                  f" — advertising {net_txt}{pol_txt}.")
    cfg = [f"hostname {cfgname}", "!", f"router bgp {local_as}",
           f" bgp router-id {rid_ip}", " bgp log-neighbor-changes"]
    for ip, asn, desc, shut in peers:
        cfg.append(f" neighbor {ip} remote-as {asn}")
        if desc:
            cfg.append(f" neighbor {ip} description {desc}")
        if shut:
            cfg.append(f" neighbor {ip} shutdown")
    cfg += [" !", " address-family ipv4"]
    for n, p in nets:
        cfg.append(f"  network {n} mask {mask_of(p)}")
    for i, (ip, asn, desc, shut) in enumerate(peers):
        if not shut:
            cfg.append(f"  neighbor {ip} activate")
        if policy == "maxprefix" and i == pol_peer:
            cfg.append(f"  neighbor {ip} maximum-prefix {pol_val}")
        if policy == "localpref" and i == pol_peer:
            cfg.append(f"  neighbor {ip} route-map PREF-IN in")
        if policy == "defaultorig" and i == pol_peer:
            cfg.append(f"  neighbor {ip} default-originate")
    cfg.append(" exit-address-family")
    if policy == "localpref":
        cfg += ["!", "route-map PREF-IN permit 10",
                f" set local-preference {pol_val}"]
    return row(rid, "bgp", intent, None, dev, "\n".join(cfg) + "\n")


# ---------------------------------------------------------------- interface_mgmt
DOMAINS = ["example.com", "example.org", "example.net", "lab.example.com",
           "corp.example.com", "hq.example.org"]
ROUTED_DESCS = ["WAN uplink", "LAN gateway", "Link to core switch",
                "Branch connection", "DMZ interface", "Server segment",
                "Internet edge", "Metro link"]
PORT_LABELS = ["Office workstations", "IP phones", "Printer row",
               "Access points", "User desks", "Lab benches",
               "Conference rooms", "Security cameras", "Reception",
               "Training room"]


def gen_iface(rng, rid):
    structure = rng.choice(["routed", "routed_ssh", "ports_console",
                            "loopback_ssh", "mixed"])
    cfg = []
    clauses = []
    if structure in ("routed", "routed_ssh", "mixed"):
        dev, cfgname = device(rng, ROUTER_HOSTS,
                              rng.choice(["router", "edge_router"]))
        ifaces = rng.sample(["GigabitEthernet0/0/0", "GigabitEthernet0/0/1",
                             "GigabitEthernet0/0/2", "GigabitEthernet0/1/0",
                             "GigabitEthernet0/1/1"],
                            rng.choices([2, 3, 4], weights=[3, 4, 2])[0])
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
            if i == 0 and rng.random() < 0.25:
                s_ip = f"192.168.{rng.randint(0, 250)}.1"
                secondary = (s_ip, 24)
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
        if structure == "mixed":
            extra = rng.sample(["GigabitEthernet0/2/0",
                                "GigabitEthernet0/2/1"], 1)[0]
            cfg += [f"interface {extra}", " description Unused port",
                    " shutdown", "!"]
            clauses.append(f"leave {extra} shut down as an unused port")
    elif structure == "ports_console":
        dev, cfgname = device(rng, SWITCH_HOSTS, "access_switch")
        ports = rng.sample([f"GigabitEthernet1/0/{i}" for i in range(1, 25)],
                           rng.choices([2, 3, 4], weights=[3, 4, 2])[0])
        label = rng.choice(PORT_LABELS)
        for p in ports:
            cfg += [f"interface {p}", f" description {label}",
                    " no shutdown", "!"]
        clauses.append("Label " + join_list(ports) + f" as '{label}' and "
                       "bring them up")
        et = rng.choice([5, 10, 15])
        cfg += ["line con 0", f" exec-timeout {et} 0",
                " logging synchronous", "!"]
        clauses.append(f"set the console timeout to {et} minutes with "
                       "logging synchronous")
    else:  # loopback_ssh
        dev, cfgname = device(rng, ROUTER_HOSTS + SWITCH_HOSTS,
                              rng.choice(["router", "core_switch"]))
        lo_ip = f"10.255.{rng.randint(0, 250)}.{rng.randint(1, 254)}"
        cfg += ["interface Loopback0", " description Management loopback",
                f" ip address {lo_ip} 255.255.255.255", "!"]
        clauses.append(f"Create Loopback0 as the management interface with "
                       f"IP {lo_ip}/32")
    if structure in ("routed_ssh", "loopback_ssh"):
        domain = rng.choice(DOMAINS)
        et = rng.choice([5, 10, 15])
        vty_hi = rng.choice([4, 15])
        cfg += [f"ip domain-name {domain}", "ip ssh version 2", "!",
                f"line vty 0 {vty_hi}", f" exec-timeout {et} 0",
                " login local", " transport input ssh"]
        clauses.append(f"Enable SSH management: domain name {domain}, SSH "
                       f"version 2, and VTY lines 0 to {vty_hi} for SSH-only "
                       f"login with a {et}-minute timeout")
    host = dev["hostname"]
    style = rng.randint(1, 2)
    if style == 1:
        intent = f"On {host}: " + "; ".join(clauses) + "."
    else:
        intent = "; ".join(clauses) + f" — all on {host}."
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
        if net_of(net, int(pfx)) != net and r["family"] != "ospf":
            pass  # host-with-prefix forms are fine (SVI/iface gateways)
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
    # intent port->vlan associations
    assoc = {}
    pats = [r"Assign (.+?) to VLAN (\d+)",
            r"(.+?) go in VLAN (\d+)",
            r"VLAN (\d+) gets ports (.+?)(?:\.|;|$)",
            r"ports (.+?) are assigned to VLAN (\d+)"]
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
            r"| an 802\.1Q trunk, allowed) VLANs ([\d,\- ]+?)"
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
               rf"gateway for VLAN (\d+) at ({IP_RE})/(\d+)"]
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
    if dm and f"ip route 0.0.0.0 0.0.0.0 {dm.group(1)}" not in config:
        errs.append("default route missing")
    if "default" in intent and not dm:
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
    mm = re.search(r"make (.+?) passive", intent)
    if mm:
        for iface in re.findall(IFACE_RE, mm.group(1)):
            if f"passive-interface {iface}" not in rtr:
                errs.append(f"{iface} not passive")
    dm = re.search(r"all interfaces passive except (.+?)(?:\.|$)", intent)
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
    if len(peers) != 2:
        errs.append(f"expected 2 peers parsed, got {len(peers)}")
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
    mm = re.search(r"maximum-prefix (\d+)", intent)
    if mm and f"maximum-prefix {mm.group(1)}" not in bgp:
        errs.append("maximum-prefix missing")
    lp = re.search(r"local-preference (\d+)", intent)
    if lp and f"set local-preference {lp.group(1)}" not in config:
        errs.append("local-preference missing")
    if "originate a default route" in intent and \
            "default-originate" not in bgp:
        errs.append("default-originate missing")
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
    corpus = load_corpus()
    corpus_exact = {(r["intent_nl"], r["config"]) for r in corpus}
    corpus_sigs = {signature(r["intent_nl"], r["config"]) for r in corpus}
    print(f"corpus: {len(corpus)} rows, {len(corpus_sigs)} signatures")
    stats = {}
    for fam in FAMILIES:
        rng = random.Random(hash(f"batch4-{fam}") & 0xFFFFFFFF)
        out, sigs = [], set()
        caught, dupes, attempts = 0, 0, 0
        while len(out) < 100:
            attempts += 1
            if attempts > 4000:
                raise SystemExit(f"{fam}: could not reach 100 rows")
            rid = f"{PREFIX[fam]}-{START_ID[fam] + len(out):04d}"
            cand = GENS[fam](rng, rid)
            if cand is None:
                continue
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
        path = BASE / f"data/generated/{fam}_b4.jsonl"
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
