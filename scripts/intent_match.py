#!/usr/bin/env python3
"""Intent-match checker for the NetConfig SLM dataset (validation round 1).

The parse + Batfish ladder proves each config is valid IOS XE; it never
checks the config actually does what the intent asked. This checker closes
that gap: it extracts the load-bearing entities from each row's intent
(intent_nl, cross-checked against intent_oc where present) and asserts each
one appears in the config in the right role:

  vlan_l2        vlan id+name block; access ports in the right VLAN
  trunk_svi      trunk allowed-VLAN set + native VLAN; SVI id+IP/mask
  static_route   destination+mask with the stated next-hop / exit interface,
                 administrative distance, name and tag
  ospf           process id, router-id, network+wildcard+area statements,
                 interface-mode `ip ospf` lines, passive interfaces
  bgp            local AS, neighbor IP+remote-as, advertised networks,
                 default-originate, prefix-list, update-source, multihop
  interface_mgmt interface description/IP/no-shutdown, loopbacks, SSH
                 domain/version/timeout/retries, VTY and console line settings
  etherchannel   member ports with channel-group number + mode, the
                 Port-channel block (trunk allowed-VLAN set / native /
                 access VLAN, min-links, description), load-balance method
  stp            spanning-tree mode, per-VLAN priorities, PortFast /
                 BPDU guard ports (per-port, list and by-default forms),
                 root / loop guard and PortFast-trunk uplinks
  port_security  per-port maximum count, violation mode, sticky / static
                 MAC, aging time + type
  acl            ACL name/number + type, every ACE (action, protocol,
                 src/dst as any / host / net+wildcard, port eq / range /
                 gt / lt, established, log), remark, interface application
                 (access-group name + direction, interface IP/description)
  nat            inside/outside interfaces (+IP, ip nat inside/outside),
                 source ACL entries, PAT interface-overload rule, pool
                 definition + source rule (+overload), static 1:1 mappings
  hsrp           interface + IP, HSRP version, per-group virtual IP,
                 priority, preempt, hello/hold timers (seconds or msec),
                 SVI VLAN block
  all            config hostname == expanded device hostname

Stdlib only. Read-only against the dataset: writes a results JSONL.

Usage:
  python3 scripts/intent_match.py [dataset.jsonl] [results.jsonl]
"""

import ipaddress
import json
import re
import sys
from pathlib import Path

IP = r"\d{1,3}(?:\.\d{1,3}){3}"
IFACE = r"(?:GigabitEthernet|TenGigabitEthernet|FastEthernet|Ethernet)"
IFACE_NUM = IFACE + r"\d+(?:/\d+)*(?:\.\d+)?"
ANYIFACE = r"(?:" + IFACE + r"\d+(?:/\d+)*(?:\.\d+)?|Loopback\d+|Vlan\d+)"


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def mask_of(plen):
    return str(ipaddress.IPv4Network(f"0.0.0.0/{int(plen)}").netmask)


def wildcard_of(plen):
    return str(ipaddress.IPv4Network(f"0.0.0.0/{int(plen)}").hostmask)


def net_of(ip, plen):
    return str(ipaddress.ip_network(f"{ip}/{int(plen)}", strict=False).network_address)


def expand_hostname(short):
    m = re.fullmatch(r"r(\d+)", short or "")
    if m:
        return f"router{m.group(1)}"
    m = re.fullmatch(r"sw(\d+)", short or "")
    if m:
        return f"switch{m.group(1)}"
    return short


def norm_iface(name):
    return re.sub(r"\s+", "", name)


def unquote(text):
    """Strip surrounding quotation marks from an extracted phrase.

    Batch-4 intents quote descriptions ('WAN uplink') while configs carry
    them unquoted; the comparison is on the description text itself.
    """
    text = text.strip()
    while len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        text = text[1:-1].strip()
    return text


# --------------------------------------------------------------------------
# config parsing
# --------------------------------------------------------------------------

def parse_config(text):
    """Split a config into hostname, global lines and named blocks.

    Blocks: interface / vlan / router ospf / router bgp / line. Children are
    stored stripped of indentation. A trailing '!' glued to a content line
    (a known corpus quirk, e.g. 'ip ssh version 2!') is removed.
    """
    out = {"hostname": None, "globals": [], "interfaces": {}, "vlans": {},
           "ospf": {}, "bgp": {}, "lines": {}, "acls": {}, "raw_lines": []}
    current = None  # (kind, key)
    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped or stripped == "!":
            continue
        if stripped.endswith("!") and len(stripped) > 1:
            stripped = stripped[:-1].rstrip()
        out["raw_lines"].append(stripped)
        if line[0] not in (" ", "\t"):  # top-level statement
            current = None
            m = re.match(r"hostname (\S+)$", stripped)
            if m:
                out["hostname"] = m.group(1)
                continue
            m = re.match(r"interface (\S.*)$", stripped)
            if m:
                key = norm_iface(m.group(1))
                out["interfaces"].setdefault(key, [])
                current = ("interfaces", key)
                continue
            m = re.match(r"vlan (\d+)$", stripped)
            if m:
                out["vlans"].setdefault(m.group(1), [])
                current = ("vlans", m.group(1))
                continue
            m = re.match(r"router ospf (\d+)$", stripped)
            if m:
                out["ospf"].setdefault(m.group(1), [])
                current = ("ospf", m.group(1))
                continue
            m = re.match(r"router bgp (\d+)$", stripped)
            if m:
                out["bgp"].setdefault(m.group(1), [])
                current = ("bgp", m.group(1))
                continue
            m = re.match(r"line (\S.*)$", stripped)
            if m:
                key = m.group(1)  # keep spaces: "vty 0 4" vs "vty 5 15"
                out["lines"].setdefault(key, [])
                current = ("lines", key)
                continue
            m = re.match(r"ip access-list (?:standard|extended) (\S+)$",
                         stripped)
            if m:
                key = m.group(1)  # ACL name or number, as written
                out["acls"].setdefault(key, [])
                current = ("acls", key)
                continue
            out["globals"].append(stripped)
        elif current is not None:
            kind, key = current
            out[kind][key].append(stripped)
    return out


def iface_block(cfg, name):
    """Return child lines for an interface, honouring `interface range`."""
    name = norm_iface(name)
    if name in cfg["interfaces"]:
        return cfg["interfaces"][name]
    for key, children in cfg["interfaces"].items():
        m = re.match(r"range(.*?)(\d+)-(\d+)$", key)
        if m:
            prefix, start, end = m.group(1), int(m.group(2)), int(m.group(3))
            mm = re.match(r"(.*?)(\d+)$", name)
            if mm and mm.group(1) == prefix and start <= int(mm.group(2)) <= end:
                return children
    return None


def has_line(lines, pattern):
    return any(re.search(pattern, ln) for ln in lines or [])


def find_line(lines, pattern):
    for ln in lines or []:
        if re.search(pattern, ln):
            return ln
    return None


# --------------------------------------------------------------------------
# check bookkeeping
# --------------------------------------------------------------------------

class Checker:
    def __init__(self, row):
        self.row = row
        self.cfg = parse_config(row["config"])
        self.failures = []
        self.n_checks = 0

    def check(self, ok, check_type, entity, expected, found):
        self.n_checks += 1
        if not ok:
            self.failures.append({
                "check": check_type, "entity": entity,
                "expected": expected, "found": found,
            })
        return ok

    def expect_line(self, lines, pattern, check_type, entity, expected_desc):
        found = find_line(lines, pattern)
        return self.check(found is not None, check_type, entity,
                          expected_desc, found if found else "missing")

    # -- shared assertions -------------------------------------------------
    def assert_iface_ip(self, iface, ip, plen, check_type="interface_ip"):
        block = iface_block(self.cfg, iface)
        if block is None:
            return self.check(False, check_type, iface,
                              f"interface block with ip address {ip} {mask_of(plen)}",
                              "interface block missing")
        pat = rf"^ip address {re.escape(ip)} {re.escape(mask_of(plen))}$"
        found = find_line(block, pat)
        if found:
            return self.check(True, check_type, iface, "", "")
        actual = find_line(block, r"^ip address ")
        return self.check(False, check_type, iface,
                          f"ip address {ip} {mask_of(plen)}",
                          actual if actual else "no ip address line")

    def assert_hostname(self):
        intent = self.row["intent_nl"]
        dev_host = (self.row.get("device") or {}).get("hostname")
        m = re.match(r"On ([A-Za-z0-9_-]+),", intent)
        if m:
            self.check(m.group(1) == dev_host, "intent_device_hostname",
                       m.group(1), f"device.hostname {dev_host}",
                       f"intent addresses {m.group(1)}")
        expected = expand_hostname(dev_host)
        self.check(self.cfg["hostname"] == expected, "hostname",
                   dev_host or "?", f"hostname {expected}",
                   f"hostname {self.cfg['hostname']}")


# --------------------------------------------------------------------------
# family: vlan_l2
# --------------------------------------------------------------------------

def check_vlan_l2(c):
    intent = c.row["intent_nl"]
    segments = re.split(r";\s*", intent)
    specs = []  # (vlan_id, name, [ports])
    for seg in segments:
        parts = re.split(r"(?:,?\s+and\s+|,\s+)(?=VLAN \d+ (?:named|called|\())", seg)
        for part in parts:
            m = re.search(r"VLAN (\d+)", part)
            if not m:
                continue
            vid = m.group(1)
            name = None
            for pat in (r"VLAN \d+ named ([A-Za-z0-9_-]+)",
                        r"VLAN \d+ called ([A-Za-z0-9_-]+)",
                        r"VLAN \d+ \(([^)]+)\)"):
                mm = re.search(pat, part)
                if mm:
                    name = mm.group(1)
                    break
            ports = []
            for rm in re.finditer(
                    rf"({IFACE_NUM}) through ({IFACE_NUM})", part):
                start, end = rm.group(1), rm.group(2)
                sm = re.match(r"(.*?)(\d+)$", start)
                em = re.match(r"(.*?)(\d+)$", end)
                if sm and em and sm.group(1) == em.group(1):
                    for n in range(int(sm.group(2)), int(em.group(2)) + 1):
                        ports.append(sm.group(1) + str(n))
            rest = re.sub(rf"{IFACE_NUM} through {IFACE_NUM}", "", part)
            ports += re.findall(rf"({IFACE_NUM})", rest)
            specs.append((vid, name, ports))
    # ports assigned by VLAN name ("assign Gi1/0/3 to Finance")
    name_to_vid = {name: vid for vid, name, _ in specs if name}
    if name_to_vid:
        assigned = {}
        for m in re.finditer(rf"({IFACE_NUM}) to ([A-Za-z][A-Za-z0-9_-]*)",
                             intent):
            if m.group(2) in name_to_vid:
                assigned[m.group(1)] = name_to_vid[m.group(2)]
        for s in specs:
            # a name assignment wins over clause position: drop ports this
            # clause merely mentions but assigns to another VLAN by name
            s[2][:] = [p for p in s[2] if assigned.get(p, s[0]) == s[0]]
            for port, vid in assigned.items():
                if vid == s[0] and port not in s[2]:
                    s[2].append(port)
    # explicit port->VLAN assignments in their own sentences (batch-4
    # styles): "Assign A and B to VLAN 647." / "A and B go in VLAN 647." /
    # "VLAN 647 gets ports A and B" / "ports A and B are assigned to
    # VLAN 647". These pair each port with its stated VLAN directly; the
    # clause-position pairing above would otherwise attach every port in
    # the final sentence group to the last VLAN spec.
    assigned_vid = {}
    for m in re.finditer(r"[Aa]ssign (.+?) to VLAN (\d+)", intent):
        for p in re.findall(rf"({IFACE_NUM})", m.group(1)):
            assigned_vid[p] = m.group(2)
    for m in re.finditer(r"(.+?) go in VLAN (\d+)", intent):
        for p in re.findall(rf"({IFACE_NUM})", m.group(1)):
            assigned_vid[p] = m.group(2)
    for m in re.finditer(r"VLAN (\d+) gets ports (.+?)(?:;|\.|$)", intent):
        for p in re.findall(rf"({IFACE_NUM})", m.group(2)):
            assigned_vid[p] = m.group(1)
    for m in re.finditer(r"ports (.+?) are assigned to VLAN (\d+)", intent):
        for p in re.findall(rf"({IFACE_NUM})", m.group(1)):
            assigned_vid[p] = m.group(2)
    if assigned_vid:
        known_vids = {s[0] for s in specs}
        for vid in assigned_vid.values():
            if vid not in known_vids:
                specs.append((vid, None, []))
                known_vids.add(vid)
        for s in specs:
            # an explicit assignment wins over clause position
            s[2][:] = [p for p in s[2] if assigned_vid.get(p, s[0]) == s[0]]
            for port, vid in assigned_vid.items():
                if vid == s[0] and port not in s[2]:
                    s[2].append(port)
    # list form without the word VLAN per item: "Create three VLANs:
    # 11 named Floor1, 12 named Floor2 and 13 named Floor3 (no ports yet)"
    known = {s[0] for s in specs}
    for m in re.finditer(r"(\d+) named ([A-Za-z0-9_-]+)", intent):
        if m.group(1) not in known:
            specs.append((m.group(1), m.group(2), []))
            known.add(m.group(1))
    c.check(bool(specs), "vlan_coverage", "vlan",
            "at least one VLAN spec extracted from the intent",
            "none extracted")
    # cross-check structured intent
    oc = c.row.get("intent_oc") or {}
    try:
        for v in oc["openconfig-vlan:vlans"]["vlan"]:
            vid = str(v["vlan-id"])
            name = (v.get("config") or {}).get("name")
            if not any(s[0] == vid for s in specs):
                specs.append((vid, name, []))
    except (KeyError, TypeError):
        pass
    for vid, name, ports in specs:
        block = c.cfg["vlans"].get(vid)
        c.check(block is not None, "vlan_block", f"vlan {vid}",
                f"vlan {vid} block", "missing")
        if name is not None and block is not None:
            c.expect_line(block, rf"^name {re.escape(name)}$", "vlan_name",
                          f"vlan {vid}", f"name {name}")
        for port in ports:
            block = iface_block(c.cfg, port)
            if block is None:
                c.check(False, "access_port", port,
                        f"access port in vlan {vid}", "interface block missing")
                continue
            c.expect_line(block, r"^switchport mode access$",
                          "access_port_mode", port, "switchport mode access")
            c.expect_line(block, rf"^switchport access vlan {vid}$",
                          "access_port_vlan", port,
                          f"switchport access vlan {vid}")


# --------------------------------------------------------------------------
# family: trunk_svi
# --------------------------------------------------------------------------

def parse_vlan_list(text):
    m = re.fullmatch(r"\s*(\d+)\s+through\s+(\d+)\s*", text)
    if m:
        return list(range(int(m.group(1)), int(m.group(2)) + 1))
    out = []
    for tok in re.split(r"\s*(?:,|\band\b)\s*", text):
        rm = re.fullmatch(r"(\d+)-(\d+)", tok.strip())
        if rm:
            out.extend(range(int(rm.group(1)), int(rm.group(2)) + 1))
        elif tok.strip().isdigit():
            out.append(int(tok.strip()))
    return out


VLAN_LIST = (r"\d+\s+through\s+\d+|\d+(?:-\d+)?(?:\s*,\s*\d+(?:-\d+)?)*"
             r"(?:\s+and\s+\d+(?:-\d+)?)?")


def check_trunk_svi(c):
    intent = c.row["intent_nl"]
    trunk_specs = []  # (iface, [vlans], native)
    for m in re.finditer(
            rf"({IFACE_NUM}) as an? (?:802\.1Q )?trunk allowing VLANs "
            rf"({VLAN_LIST})(?: with native VLAN (\d+))?", intent):
        trunk_specs.append((m.group(1), parse_vlan_list(m.group(2)),
                            m.group(3)))
    for m in re.finditer(
            rf"[Mm]ake ({IFACE_NUM}) a trunk allowing VLANs "
            rf"({VLAN_LIST})(?: with native VLAN (\d+))?", intent):
        trunk_specs.append((m.group(1), parse_vlan_list(m.group(2)),
                            m.group(3)))
    for m in re.finditer(
            rf"({IFACE_NUM}) as an? (?:802\.1Q )?trunk with native VLAN "
            rf"(\d+) allowing VLANs ({VLAN_LIST})", intent):
        trunk_specs.append((m.group(1), parse_vlan_list(m.group(3)),
                            m.group(2)))
    for m in re.finditer(
            rf"({IFACE_NUM}) and ({IFACE_NUM}) as trunks allowing VLANs "
            rf"({VLAN_LIST})(?: with native VLAN (\d+))?", intent):
        for iface in (m.group(1), m.group(2)):
            trunk_specs.append((iface, parse_vlan_list(m.group(3)),
                                m.group(4)))
    # batch-4 word orders: "Make X an 802.1Q trunk, allowed VLANs ..."
    # (sometimes preceded by a stray "Configure") and the bare
    # "X trunk allowing VLANs ..." clause form
    for m in re.finditer(
            rf"[Mm]ake ({IFACE_NUM}) an 802\.1Q trunk, allowed VLANs "
            rf"({VLAN_LIST})(?: with native VLAN (\d+))?", intent):
        trunk_specs.append((m.group(1), parse_vlan_list(m.group(2)),
                            m.group(3)))
    for m in re.finditer(
            rf"({IFACE_NUM}) trunk allowing VLANs "
            rf"({VLAN_LIST})(?: with native VLAN (\d+))?", intent):
        trunk_specs.append((m.group(1), parse_vlan_list(m.group(2)),
                            m.group(3)))
    if "trunk" in intent.lower():
        c.check(bool(trunk_specs), "trunk_coverage", "trunk",
                "at least one trunk spec extracted from the intent",
                "none extracted")
    seen = set()
    for iface, vlans, native in trunk_specs:
        if iface in seen:
            continue
        seen.add(iface)
        block = iface_block(c.cfg, iface)
        if block is None:
            c.check(False, "trunk_block", iface, "trunk interface block",
                    "missing")
            continue
        c.expect_line(block, r"^switchport mode trunk$", "trunk_mode",
                      iface, "switchport mode trunk")
        allowed = set()
        for ln in block:
            mm = re.match(r"switchport trunk allowed vlan (?:add )?(.+)$", ln)
            if mm:
                for tok in mm.group(1).split(","):
                    tok = tok.strip()
                    if "-" in tok:
                        a, b = tok.split("-", 1)
                        allowed.update(range(int(a), int(b) + 1))
                    elif tok.isdigit():
                        allowed.add(int(tok))
        c.check(allowed == set(vlans), "trunk_allowed_vlans", iface,
                f"allowed vlans {sorted(vlans)}",
                f"allowed vlans {sorted(allowed)}" if allowed else "missing")
        if native:
            c.expect_line(block, rf"^switchport trunk native vlan {native}$",
                          "trunk_native_vlan", iface,
                          f"switchport trunk native vlan {native}")
    svis = {}
    for m in re.finditer(rf"SVI for VLAN (\d+)(?: on \S+)? with IP "
                         rf"({IP})/(\d+)", intent):
        svis[m.group(1)] = (m.group(2), m.group(3))
    for m in re.finditer(rf"SVI Vlan(\d+) with IP ({IP})/(\d+)", intent):
        svis[m.group(1)] = (m.group(2), m.group(3))
    for m in re.finditer(rf"VLAN (\d+) \(({IP})/(\d+)\)", intent):
        svis[m.group(1)] = (m.group(2), m.group(3))
    # "create VLAN 60 named Printers and its SVI with IP 192.168.60.1/24"
    for m in re.finditer(rf"VLAN (\d+) named ([A-Za-z0-9_-]+) and its SVI "
                         rf"with IP ({IP})/(\d+)", intent):
        svis[m.group(1)] = (m.group(3), m.group(4))
    for m in re.finditer(rf"VLAN (\d+) SVI with IP ({IP})/(\d+)", intent):
        svis[m.group(1)] = (m.group(2), m.group(3))
    # batch-4 gateway form: "gateway for VLAN 42 at 10.42.39.1/24"
    for m in re.finditer(rf"gateway for VLAN (\d+) at ({IP})/(\d+)", intent):
        svis[m.group(1)] = (m.group(2), m.group(3))
    # "create SVIs for all three VLANs: 10.10.0.1/24, 10.20.0.1/24 and
    # 10.30.0.1/24" — IPs pair in order with the trunk's allowed VLANs
    m = re.search(r"SVIs for all three VLANs: ([^;]+)", intent)
    if m and trunk_specs:
        ips = re.findall(rf"({IP})/(\d+)", m.group(1))
        allowed = trunk_specs[0][1]
        if len(ips) == len(allowed):
            for vid, (ip, plen) in zip(allowed, ips):
                svis[str(vid)] = (ip, plen)
    if "svi" in intent.lower():
        c.check(bool(svis), "svi_coverage", "SVI",
                "at least one SVI spec extracted from the intent",
                "none extracted")
    for vid, (ip, plen) in svis.items():
        c.assert_iface_ip(f"Vlan{vid}", ip, plen, "svi_ip")
    # a VLAN named in a trunk_svi intent still needs its vlan block + name
    for m in re.finditer(r"VLAN (\d+) named ([A-Za-z0-9_-]+)", intent):
        block = c.cfg["vlans"].get(m.group(1))
        c.check(block is not None, "vlan_block", f"vlan {m.group(1)}",
                f"vlan {m.group(1)} block", "missing")
        if block is not None:
            c.expect_line(block, rf"^name {re.escape(m.group(2))}$",
                          "vlan_name", f"vlan {m.group(1)}",
                          f"name {m.group(2)}")


# --------------------------------------------------------------------------
# family: etherchannel
# --------------------------------------------------------------------------

LOAD_BALANCE_METHODS = ("src-dst-ip", "src-dst-mac", "src-dst-port",
                        "src-ip", "dst-ip", "src-mac", "dst-mac")


def check_etherchannel(c):
    intent = c.row["intent_nl"]
    # bundle clauses: "{port list} into Port-channelN with LACP
    # active/passive mode" or "... with channel-group mode on"
    bundles = {}  # group -> (mode, [member ifaces])
    for m in re.finditer(
            r"(.+?) into Port-channel(\d+) with (?:LACP (active|passive) "
            r"mode|channel-group mode (on))", intent):
        ports = re.findall(rf"({IFACE_NUM})", m.group(1))
        bundles[m.group(2)] = (m.group(3) or m.group(4), ports)
    c.check(bool(bundles), "etherchannel_coverage", "bundle",
            "at least one bundle spec extracted from the intent",
            "none extracted")
    # Port-channel role clauses (trunk / access), with optional native
    # VLAN and min-links in that order
    roles = {}  # group -> ("trunk", [vlans], native) | ("access", vid)
    min_links = {}
    for m in re.finditer(
            rf"Port-channel(\d+)(?: \(description '[^']+'\))? "
            rf"(?:as a trunk allowing|is a trunk permitting) VLANs "
            rf"({VLAN_LIST})(?: with native VLAN (\d+))?"
            rf"(?: with min-links (\d+))?", intent):
        roles[m.group(1)] = ("trunk", parse_vlan_list(m.group(2)),
                             m.group(3))
        if m.group(4):
            min_links[m.group(1)] = m.group(4)
    for m in re.finditer(
            rf"Port-channel(\d+)(?: \(description '[^']+'\))? "
            rf"(?:as an access port-channel|is an access port-channel) "
            rf"in VLAN (\d+)(?: with min-links (\d+))?", intent):
        roles[m.group(1)] = ("access", int(m.group(2)), None)
        if m.group(3):
            min_links[m.group(1)] = m.group(3)
    for group, (mode, ports) in bundles.items():
        po_name = f"Port-channel{group}"
        po = iface_block(c.cfg, po_name)
        c.check(po is not None, "port_channel_block", po_name,
                f"interface {po_name} block", "missing")
        role = roles.get(group)
        c.check(role is not None, "port_channel_role", po_name,
                "a trunk/access role clause for the Port-channel",
                "none extracted")
        for port in ports:
            block = iface_block(c.cfg, port)
            if block is None:
                c.check(False, "etherchannel_member", port,
                        f"member of channel-group {group}",
                        "interface block missing")
                continue
            c.expect_line(block, rf"^channel-group {group} mode {mode}$",
                          "etherchannel_member_mode", port,
                          f"channel-group {group} mode {mode}")
            if role is not None and role[0] == "trunk":
                c.expect_line(block, r"^switchport mode trunk$",
                              "etherchannel_member_trunk", port,
                              "switchport mode trunk")
            elif role is not None:
                c.expect_line(block, r"^switchport mode access$",
                              "etherchannel_member_access", port,
                              "switchport mode access")
                c.expect_line(block,
                              rf"^switchport access vlan {role[1]}$",
                              "etherchannel_member_vlan", port,
                              f"switchport access vlan {role[1]}")
        if po is None or role is None:
            continue
        if role[0] == "trunk":
            c.expect_line(po, r"^switchport mode trunk$",
                          "port_channel_mode", po_name,
                          "switchport mode trunk")
            allowed = set()
            for ln in po:
                mm = re.match(r"switchport trunk allowed vlan (?:add )?"
                              r"(.+)$", ln)
                if mm:
                    for tok in mm.group(1).split(","):
                        tok = tok.strip()
                        if "-" in tok:
                            a, b = tok.split("-", 1)
                            allowed.update(range(int(a), int(b) + 1))
                        elif tok.isdigit():
                            allowed.add(int(tok))
            c.check(allowed == set(role[1]), "port_channel_allowed_vlans",
                    po_name, f"allowed vlans {sorted(role[1])}",
                    f"allowed vlans {sorted(allowed)}" if allowed
                    else "missing")
            if role[2]:
                c.expect_line(po,
                              rf"^switchport trunk native vlan {role[2]}$",
                              "port_channel_native_vlan", po_name,
                              f"switchport trunk native vlan {role[2]}")
        else:
            c.expect_line(po, r"^switchport mode access$",
                          "port_channel_mode", po_name,
                          "switchport mode access")
            c.expect_line(po, rf"^switchport access vlan {role[1]}$",
                          "port_channel_access_vlan", po_name,
                          f"switchport access vlan {role[1]}")
        if group in min_links:
            c.expect_line(po, rf"^port-channel min-links {min_links[group]}$",
                          "port_channel_min_links", po_name,
                          f"port-channel min-links {min_links[group]}")
    for m in re.finditer(r"Port-channel(\d+) \(description '([^']+)'\)",
                         intent):
        po = iface_block(c.cfg, f"Port-channel{m.group(1)}")
        if po is not None:
            c.expect_line(po, rf"^description {re.escape(m.group(2))}$",
                          "port_channel_description",
                          f"Port-channel{m.group(1)}",
                          f"description {m.group(2)}")
    m = re.search(r"port-channel load-balance (?:to )?"
                  r"(" + "|".join(LOAD_BALANCE_METHODS) + r")\b", intent)
    if m:
        c.check(find_line(c.cfg["globals"],
                          rf"^port-channel load-balance {m.group(1)}$")
                is not None,
                "load_balance", m.group(1),
                f"port-channel load-balance {m.group(1)}", "missing")


# --------------------------------------------------------------------------
# family: stp
# --------------------------------------------------------------------------

def _stp_access_port(c, port, vid, portfast=False, bpduguard=False):
    block = iface_block(c.cfg, port)
    if block is None:
        c.check(False, "stp_edge_port", port,
                f"access port in vlan {vid}", "interface block missing")
        return
    c.expect_line(block, r"^switchport mode access$", "stp_edge_mode",
                  port, "switchport mode access")
    c.expect_line(block, rf"^switchport access vlan {vid}$",
                  "stp_edge_vlan", port, f"switchport access vlan {vid}")
    if portfast:
        c.expect_line(block, r"^spanning-tree portfast$",
                      "stp_portfast", port, "spanning-tree portfast")
    if bpduguard:
        c.expect_line(block, r"^spanning-tree bpduguard enable$",
                      "stp_bpduguard", port,
                      "spanning-tree bpduguard enable")


def check_stp(c):
    intent = c.row["intent_nl"]
    m = re.search(r"spanning-tree mode (?:to )?(rapid-pvst|pvst|mst)\b",
                  intent)
    c.check(m is not None, "stp_mode_coverage", "spanning-tree mode",
            "a spanning-tree mode stated in the intent", "none extracted")
    if m:
        c.check(find_line(c.cfg["globals"],
                          rf"^spanning-tree mode {m.group(1)}$")
                is not None,
                "stp_mode", m.group(1),
                f"spanning-tree mode {m.group(1)}", "missing")
    for vid, prio in re.findall(r"priority for VLAN (\d+) to (\d+)",
                                intent):
        c.check(find_line(c.cfg["globals"],
                          rf"^spanning-tree vlan {vid} priority {prio}$")
                is not None,
                "stp_vlan_priority", f"vlan {vid}",
                f"spanning-tree vlan {vid} priority {prio}", "missing")
    for vid, prio in re.findall(r"VLAN (\d+) root priority (\d+)", intent):
        c.check(find_line(c.cfg["globals"],
                          rf"^spanning-tree vlan {vid} priority {prio}$")
                is not None,
                "stp_vlan_priority", f"vlan {vid}",
                f"spanning-tree vlan {vid} priority {prio}", "missing")
    if "PortFast on all access ports by default" in intent:
        c.check(find_line(c.cfg["globals"],
                          r"^spanning-tree portfast default$") is not None,
                "stp_portfast_default", "portfast default",
                "spanning-tree portfast default", "missing")
    if "BPDU guard by default" in intent:
        c.check(find_line(c.cfg["globals"],
                          r"^spanning-tree portfast bpduguard default$")
                is not None,
                "stp_bpduguard_default", "bpduguard default",
                "spanning-tree portfast bpduguard default", "missing")
    handled = set()
    # per-port edge clauses: "Gi1/0/1 in VLAN 10 with PortFast [and
    # BPDU guard]"
    for m in re.finditer(
            rf"({IFACE_NUM}) in VLAN (\d+) with PortFast"
            rf"( and BPDU guard)?", intent):
        handled.add(m.group(1))
        _stp_access_port(c, m.group(1), m.group(2), portfast=True,
                         bpduguard=bool(m.group(3)))
    # list form: "PortFast on Gi1/0/1, Gi1/0/2 and Gi1/0/3, all in
    # VLAN 10"
    for m in re.finditer(r"PortFast on (.+?), all in VLAN (\d+)", intent):
        for port in re.findall(rf"({IFACE_NUM})", m.group(1)):
            handled.add(port)
            _stp_access_port(c, port, m.group(2), portfast=True)
    # standalone BPDU guard list: "BPDU guard on Gi1/0/1 and Gi1/0/2"
    for m in re.finditer(r"BPDU guard on (.+?)(?=;|\.|$)", intent):
        for port in re.findall(rf"({IFACE_NUM})", m.group(1)):
            block = iface_block(c.cfg, port)
            if block is not None:
                c.expect_line(block, r"^spanning-tree bpduguard enable$",
                              "stp_bpduguard", port,
                              "spanning-tree bpduguard enable")
    # remaining "{iface} in VLAN n" mentions (by-default rows state their
    # access ports without per-port PortFast): assert access role + VLAN
    for m in re.finditer(rf"({IFACE_NUM}) in VLAN (\d+)", intent):
        if m.group(1) not in handled:
            handled.add(m.group(1))
            _stp_access_port(c, m.group(1), m.group(2))
    # uplinks: "{iface} as a trunk allowing VLANs X [with root guard /
    # loop guard / PortFast trunk]"
    for m in re.finditer(
            rf"({IFACE_NUM}) as a trunk allowing VLANs ({VLAN_LIST})"
            rf"(?: with (root guard|loop guard|PortFast trunk))?", intent):
        port, expr, suffix = m.group(1), m.group(2), m.group(3)
        block = iface_block(c.cfg, port)
        if block is None:
            c.check(False, "stp_uplink", port, "trunk uplink block",
                    "missing")
            continue
        c.expect_line(block, r"^switchport mode trunk$", "stp_uplink_mode",
                      port, "switchport mode trunk")
        allowed = set()
        for ln in block:
            mm = re.match(r"switchport trunk allowed vlan (?:add )?(.+)$",
                          ln)
            if mm:
                for tok in mm.group(1).split(","):
                    tok = tok.strip()
                    if "-" in tok:
                        a, b = tok.split("-", 1)
                        allowed.update(range(int(a), int(b) + 1))
                    elif tok.isdigit():
                        allowed.add(int(tok))
        want = set(parse_vlan_list(expr))
        c.check(allowed == want, "stp_uplink_vlans", port,
                f"allowed vlans {sorted(want)}",
                f"allowed vlans {sorted(allowed)}" if allowed
                else "missing")
        if suffix == "root guard":
            c.expect_line(block, r"^spanning-tree guard root$",
                          "stp_root_guard", port, "spanning-tree guard root")
        elif suffix == "loop guard":
            c.expect_line(block, r"^spanning-tree guard loop$",
                          "stp_loop_guard", port, "spanning-tree guard loop")
        elif suffix == "PortFast trunk":
            c.expect_line(block, r"^spanning-tree portfast trunk$",
                          "stp_portfast_trunk", port,
                          "spanning-tree portfast trunk")


# --------------------------------------------------------------------------
# family: port_security
# --------------------------------------------------------------------------

MAC_RE = r"[0-9a-f]{4}\.[0-9a-f]{4}\.[0-9a-f]{4}"


def check_port_security(c):
    intent = c.row["intent_nl"]
    specs = []  # per-port dicts from the strict clause grammar
    for m in re.finditer(
            rf"({IFACE_NUM}) in VLAN (\d+): port-security maximum (\d+), "
            rf"violation (shutdown|restrict|protect)"
            rf"(, sticky MAC learning)?"
            rf"(, static MAC ({MAC_RE}))?"
            rf"(, aging time (\d+) minutes \((absolute|inactivity)\))?",
            intent):
        specs.append({
            "port": m.group(1), "vlan": m.group(2), "maximum": m.group(3),
            "violation": m.group(4), "sticky": bool(m.group(5)),
            "static_mac": m.group(7), "aging_time": m.group(9),
            "aging_type": m.group(10),
        })
    c.check(bool(specs), "port_security_coverage", "port-security",
            "at least one port-security spec extracted from the intent",
            "none extracted")
    for s in specs:
        port = s["port"]
        block = iface_block(c.cfg, port)
        if block is None:
            c.check(False, "psec_port", port, "interface block",
                    "missing")
            continue
        c.expect_line(block, r"^switchport mode access$", "psec_mode",
                      port, "switchport mode access")
        c.expect_line(block, rf"^switchport access vlan {s['vlan']}$",
                      "psec_vlan", port,
                      f"switchport access vlan {s['vlan']}")
        c.expect_line(block, r"^switchport port-security$",
                      "psec_enable", port, "switchport port-security")
        c.expect_line(block,
                      rf"^switchport port-security maximum {s['maximum']}$",
                      "psec_maximum", port,
                      f"switchport port-security maximum {s['maximum']}")
        c.expect_line(
            block,
            rf"^switchport port-security violation {s['violation']}$",
            "psec_violation", port,
            f"switchport port-security violation {s['violation']}")
        if s["sticky"]:
            c.expect_line(block,
                          r"^switchport port-security mac-address sticky$",
                          "psec_sticky", port,
                          "switchport port-security mac-address sticky")
        if s["static_mac"]:
            c.expect_line(
                block,
                rf"^switchport port-security mac-address "
                rf"{re.escape(s['static_mac'])}$",
                "psec_static_mac", port,
                f"switchport port-security mac-address {s['static_mac']}")
        if s["aging_time"]:
            c.expect_line(
                block,
                rf"^switchport port-security aging time {s['aging_time']}$",
                "psec_aging_time", port,
                f"switchport port-security aging time {s['aging_time']}")
            c.expect_line(
                block,
                rf"^switchport port-security aging type {s['aging_type']}$",
                "psec_aging_type", port,
                f"switchport port-security aging type {s['aging_type']}")


# --------------------------------------------------------------------------
# family: static_route
# --------------------------------------------------------------------------

def parse_static_clauses(intent):
    """Parse ';'-separated route clause lists (batch-4 style).

    Each clause states exactly one route: a destination prefix (or a
    default route), its next-hop and/or exit interface, and any floating
    AD. Parsing per clause keeps each route paired with its own next-hop;
    the comma/'and' fragment parser below collapses these lists (every
    route ends up paired with the last next-hop seen).
    Returns [] when the intent is not in this form.
    """
    if ";" not in intent:
        return []
    specs = []
    for clause in re.split(r";\s*", intent):
        clause = clause.strip().rstrip(".")
        iface_ip = None
        m = re.search(rf"\(interface IP ({IP})/(\d+)\)", clause)
        if m:
            iface_ip = (m.group(1), m.group(2))
        scan = re.sub(rf"\(interface IP {IP}/\d+\)", "", clause)
        if re.search(r"\bdefault\b", scan):
            dest = ("0.0.0.0", "0")
        else:
            m = re.search(rf"({IP})/(\d+)", scan)
            if not m:
                continue
            dest = (m.group(1), m.group(2))
        nh = iface = None
        m = re.search(rf"via ({IFACE_NUM}) ({IP})", scan)
        if m:
            iface, nh = m.group(1), m.group(2)
        else:
            m = re.search(rf"(?:via|out) ({IFACE_NUM})\b", scan)
            if m:
                iface = m.group(1)
            else:
                m = re.search(rf"via ({IP})", scan)
                if m:
                    nh = m.group(1)
        if not (nh or iface):
            continue
        ad = None
        m = re.search(r"(?:\bAD |administrative distance )(\d+)", clause)
        if m:
            ad = m.group(1)
        specs.append({"dest": dest, "nh": nh, "iface": iface, "ad": ad,
                      "name": None, "tag": None, "iface_ip": iface_ip})
    return specs


def check_static_route(c):
    intent = c.row["intent_nl"]
    specs = parse_static_clauses(intent)
    # when the clause parser handled the intent, the fragment parser must
    # not run: it would re-pair destinations with the wrong next-hops
    fragments = [] if specs else re.split(
        r"\s*,\s*and\s+|\s+and\s+|\s*,\s*", intent)
    pending = []      # dests waiting for a via
    last_dest = None
    last_nh = None
    for frag in fragments:
        # the exit interface's own IP is not a destination: blank it out
        # before scanning the fragment for destination prefixes
        frag_dests = []
        scan = re.sub(rf"\(IP {IP}/\d+\)", "(IP)", frag)
        scan = re.sub(rf"which has IP {IP}/\d+", "which has IP", scan)
        scan = re.sub(rf"\bIP {IP}/\d+", "IP", scan)
        for m in re.finditer(rf"({IP})/(\d+)", scan):
            frag_dests.append((m.group(1), m.group(2)))
        if "same prefix" in frag and last_dest:
            frag_dests.append(last_dest)
        if "default route" in frag:
            frag_dests.append(("0.0.0.0", "0"))
        # via information in this fragment
        nh = None
        m = re.search(rf"via (?:next-hop )?({IP})", frag)
        if m:
            nh = m.group(1)
        if "same gateway" in frag:
            nh = last_nh
        m = re.search(r"with next-hop (" + IP + ")", frag)
        if m:
            nh = m.group(1)
        exit_iface = None
        m = re.search(rf"exit interface ({IFACE_NUM})", frag)
        if m:
            exit_iface = m.group(1)
        iface_ip = None
        m = re.search(rf"\(IP ({IP})/(\d+)\)", frag) or \
            re.search(rf"which has IP ({IP})/(\d+)", frag)
        if m:
            iface_ip = (m.group(1), m.group(2))
        ad = name = tag = None
        m = re.search(r"administrative distance (\d+)", frag)
        if m:
            ad = m.group(1)
        m = re.search(r"with tag (\d+)", frag)
        if m:
            tag = m.group(1)
        m = re.search(r"(?:name it|named) ([A-Za-z0-9_-]+)", frag)
        if m:
            name = m.group(1)
        if frag_dests:
            pending.extend(frag_dests)
            last_dest = frag_dests[-1]
        # "via both 192.168.155.1 and 10.17.41.2": a fragment that is just
        # a bare next-hop IP continues the previous via for the last dest
        if not frag_dests and not nh and not exit_iface and last_dest:
            m = re.match(rf"\s*({IP})\b", frag)
            if m:
                nh = m.group(1)
        if nh or exit_iface:
            targets = pending if pending else ([last_dest] if last_dest else [])
            for dest in targets:
                specs.append({"dest": dest, "nh": nh, "iface": exit_iface,
                              "ad": ad, "name": name, "tag": tag,
                              "iface_ip": iface_ip})
            if nh:
                last_nh = nh
            pending = []
        elif specs and (ad or name or tag or iface_ip):
            # modifiers trailing the via clause belong to the last spec(s)
            for s in specs:
                if s["dest"] == last_dest:
                    if ad and not s["ad"]:
                        s["ad"] = ad
                    if name and not s["name"]:
                        s["name"] = name
                    if tag and not s["tag"]:
                        s["tag"] = tag
                    if iface_ip and not s["iface_ip"]:
                        s["iface_ip"] = iface_ip
    c.check(bool(specs), "static_route_coverage", "routes",
            "at least one route spec extracted from the intent",
            "none extracted")
    # an interface address configured in the same breath is load-bearing too
    for m in re.finditer(rf"({IFACE_NUM}) with IP ({IP})/(\d+)", intent):
        c.assert_iface_ip(m.group(1), m.group(2), m.group(3),
                          "static_interface_ip")
    route_lines = [ln for ln in c.cfg["globals"] if ln.startswith("ip route ")]

    def line_satisfies(line, s):
        """Score how many of the spec's stated attributes a line matches."""
        toks = line.split()
        score = 0
        if s["iface"] and s["iface"] in toks:
            score += 1
        if s["nh"] and s["nh"] in toks:
            score += 1
        if s["ad"]:
            bare = [t for t in toks[4:] if t.isdigit()
                    and t != (s["tag"] or "")]
            if s["ad"] in bare:
                score += 1
        if s["name"] and f"name {s['name']}" in line:
            score += 1
        if s["tag"] and f"tag {s['tag']}" in line:
            score += 1
        return score

    for s in specs:
        dip, dplen = s["dest"]
        head = f"ip route {net_of(dip, dplen)} {mask_of(dplen)}"
        entity = f"route {dip}/{dplen}"
        candidates = [ln for ln in route_lines if ln.startswith(head + " ")
                      or ln == head]
        n_attrs = sum(1 for k in ("iface", "nh", "ad", "name", "tag") if s[k])
        if not candidates:
            c.check(False, "static_route_line", entity,
                    head + " ...", "missing")
            continue
        best = max(candidates, key=lambda ln: line_satisfies(ln, s))
        if line_satisfies(best, s) < n_attrs:
            # report per-attribute against the closest line
            toks = best.split()
            rest = best[len(head):].strip()
            if s["iface"]:
                c.check(s["iface"] in toks, "static_route_exit_interface",
                        entity, f"exit interface {s['iface']}",
                        f"line: {best}")
            if s["nh"]:
                c.check(s["nh"] in toks, "static_route_next_hop", entity,
                        f"next-hop {s['nh']}", f"line: {best}")
            if s["ad"]:
                bare = [t for t in toks[4:] if t.isdigit()
                        and t != (s["tag"] or "")]
                c.check(s["ad"] in bare, "static_route_ad", entity,
                        f"administrative distance {s['ad']}",
                        f"line: {best}")
            if s["name"]:
                c.check(f"name {s['name']}" in rest, "static_route_name",
                        entity, f"name {s['name']}", f"line: {best}")
            if s["tag"]:
                c.check(f"tag {s['tag']}" in rest, "static_route_tag",
                        entity, f"tag {s['tag']}", f"line: {best}")
        else:
            c.n_checks += n_attrs  # all attributes verified on one line
        if s["iface"] and s["iface_ip"]:
            c.assert_iface_ip(s["iface"], s["iface_ip"][0], s["iface_ip"][1],
                              "exit_interface_ip")


# --------------------------------------------------------------------------
# family: ospf
# --------------------------------------------------------------------------

def check_ospf(c):
    intent = c.row["intent_nl"]
    # process id phrasings: "OSPF process 100", "OSPF 20, router-id ..."
    m = re.search(r"OSPF process (\d+)", intent) or \
        re.search(r"OSPF (\d+)\s*,", intent) or \
        re.search(r"\bOSPF (\d+)\b", intent)
    pid = m.group(1) if m else None
    block = c.cfg["ospf"].get(pid) if pid else None
    c.check(block is not None, "ospf_process", f"process {pid}",
            f"router ospf {pid}", "missing")
    if block is None:
        block = []
    m = re.search(rf"router[- ]id ({IP})", intent, re.I)
    if m:
        c.expect_line(block, rf"^router-id {re.escape(m.group(1))}$",
                      "ospf_router_id", f"process {pid}",
                      f"router-id {m.group(1)}")
    area_m = re.search(r"in area (\d+)", intent)
    area = area_m.group(1) if area_m else "0"
    # interface-mode specs: IFACE (ip/plen) / IFACE has IP ip/plen
    iface_specs = []
    for m in re.finditer(rf"({ANYIFACE}) \(({IP})/(\d+)\)", intent):
        iface_specs.append((m.group(1), m.group(2), m.group(3)))
    for m in re.finditer(rf"({IFACE_NUM}) has IP ({IP})/(\d+)", intent):
        iface_specs.append((m.group(1), m.group(2), m.group(3)))
    consumed = [(ip, plen) for _, ip, plen in iface_specs]
    # a Loopback0 stated as the router-id source ("Loopback0 addressed
    # 10.255.0.1/32 as the router-id source" / "Loopback0 10.255.0.1/32")
    # is an interface address, NOT a network statement: assert its IP on
    # the loopback block and consume the prefix so the network scan below
    # does not demand a `network .../32` line for it
    for m in re.finditer(rf"Loopback0 addressed ({IP})/(\d+)", intent):
        c.assert_iface_ip("Loopback0", m.group(1), m.group(2),
                          "ospf_loopback_ip")
        consumed.append((m.group(1), m.group(2)))
    for m in re.finditer(rf"Loopback0 ({IP})/(\d+)", intent):
        c.assert_iface_ip("Loopback0", m.group(1), m.group(2),
                          "ospf_loopback_ip")
        consumed.append((m.group(1), m.group(2)))
    # interface-mode specs are asserted whenever they are stated, even
    # when the same intent also carries network statements (batch-4 rows
    # mix both); the old condition skipped them whenever the word
    # "network" appeared anywhere in the intent
    if iface_specs:
        for iface, ip, plen in iface_specs:
            c.assert_iface_ip(iface, ip, plen, "ospf_interface_ip")
            iblock = iface_block(c.cfg, iface)
            if iblock is not None and pid:
                c.expect_line(iblock, rf"^ip ospf {pid} area {area}$",
                              "ospf_interface_area", iface,
                              f"ip ospf {pid} area {area}")
    # SVI context statements: "the VLAN 10 SVI is 192.168.64.1/24" or
    # "the VLAN 20 SVI (192.168.20.1/24)"
    for m in re.finditer(rf"VLAN (\d+) SVI is ({IP})/(\d+)", intent):
        c.assert_iface_ip(f"Vlan{m.group(1)}", m.group(2), m.group(3),
                          "ospf_svi_ip")
        consumed.append((m.group(2), m.group(3)))
    for m in re.finditer(rf"VLAN (\d+) SVI \(({IP})/(\d+)\)", intent):
        c.assert_iface_ip(f"Vlan{m.group(1)}", m.group(2), m.group(3),
                          "ospf_svi_ip")
        consumed.append((m.group(2), m.group(3)))
    for m in re.finditer(r"make the VLAN (\d+) SVI[^;]*?passive", intent):
        c.expect_line(block, rf"^passive-interface Vlan{m.group(1)}$",
                      "ospf_passive", f"Vlan{m.group(1)}",
                      f"passive-interface Vlan{m.group(1)}")
    # network statements: every remaining prefix in the intent (also in
    # interface-mode rows when a network statement is explicitly requested)
    if "interface mode" not in intent or "network statement" in intent:
        for m in re.finditer(rf"({IP})/(\d+)", intent):
            if (m.group(1), m.group(2)) in consumed:
                continue
            base, plen = m.group(1), m.group(2)
            c.expect_line(
                block,
                rf"^network {re.escape(base)} {re.escape(wildcard_of(plen))} area {area}$",
                "ospf_network", f"{base}/{plen}",
                f"network {base} {wildcard_of(plen)} area {area}")
    # passive interfaces
    # literal form: "with passive-interface default", exceptions stated as
    # "X as the only non-passive interface" / "X excepted" / "no exceptions"
    if "passive-interface default" in intent:
        c.expect_line(block, r"^passive-interface default$",
                      "ospf_passive_default", "all interfaces",
                      "passive-interface default")
        for m in re.finditer(rf"({ANYIFACE}) as the only non-passive "
                              r"interfaces?", intent):
            c.expect_line(block,
                          rf"^no passive-interface {re.escape(norm_iface(m.group(1)))}$",
                          "ospf_passive_exception", m.group(1),
                          f"no passive-interface {m.group(1)}")
        for m in re.finditer(rf"({ANYIFACE}) excepted", intent):
            c.expect_line(block,
                          rf"^no passive-interface {re.escape(norm_iface(m.group(1)))}$",
                          "ospf_passive_exception", m.group(1),
                          f"no passive-interface {m.group(1)}")
    # list forms: "with X and Y as passive interfaces", "with X passive"
    for m in re.finditer(r"with ((?:" + ANYIFACE +
                         r"(?:\s+and\s+|\s*,\s*)?)+?) as (?:a )?passive "
                         r"interfaces?", intent):
        for iface in re.findall(rf"({ANYIFACE})", m.group(1)):
            c.expect_line(block,
                          rf"^passive-interface {re.escape(norm_iface(iface))}$",
                          "ospf_passive", iface,
                          f"passive-interface {iface}")
    for m in re.finditer(r"with ((?:" + ANYIFACE +
                         r"(?:\s+and\s+|\s*,\s*)?)+?) passive\b", intent):
        for iface in re.findall(rf"({ANYIFACE})", m.group(1)):
            c.expect_line(block,
                          rf"^passive-interface {re.escape(norm_iface(iface))}$",
                          "ospf_passive", iface,
                          f"passive-interface {iface}")
    # trailing form: "..., and GigabitEthernet0/0/2 passive, router-id ..."
    for m in re.finditer(rf"and ({ANYIFACE}) passive\b", intent):
        c.expect_line(block,
                      rf"^passive-interface {re.escape(norm_iface(m.group(1)))}$",
                      "ospf_passive", m.group(1),
                      f"passive-interface {m.group(1)}")
    # "make it passive" — the interface-mode interface just described
    if "make it passive" in intent and iface_specs:
        iface = iface_specs[0][0]
        c.expect_line(block,
                      rf"^passive-interface {re.escape(norm_iface(iface))}$",
                      "ospf_passive", iface, f"passive-interface {iface}")
    # "all interfaces passive except X, Y, and Z" (batch-4 default mode):
    # passive-interface default plus one no-passive exception per interface
    m = re.search(r"all interfaces passive except (.+?)(?:;|\.$|$)", intent)
    if m:
        c.expect_line(block, r"^passive-interface default$",
                      "ospf_passive_default", "all interfaces",
                      "passive-interface default")
        for iface in re.findall(rf"({ANYIFACE})", m.group(1)):
            c.expect_line(block,
                          rf"^no passive-interface {re.escape(norm_iface(iface))}$",
                          "ospf_passive_exception", iface,
                          f"no passive-interface {iface}")
    m = re.search(rf"passive by default except ({ANYIFACE})", intent)
    if m:
        c.expect_line(block, r"^passive-interface default$",
                      "ospf_passive_default", "all interfaces",
                      "passive-interface default")
        c.expect_line(block,
                      rf"^no passive-interface {re.escape(norm_iface(m.group(1)))}$",
                      "ospf_passive_exception", m.group(1),
                      f"no passive-interface {m.group(1)}")
    else:
        # "make X passive" and the list form "make X, Y, and Z passive":
        # every interface named between "make" and "passive" is asserted
        for m in re.finditer(r"make (.+?) passive\b", intent):
            for iface in re.findall(rf"({ANYIFACE})", m.group(1)):
                c.expect_line(block,
                              rf"^passive-interface {re.escape(norm_iface(iface))}$",
                              "ospf_passive", iface,
                              f"passive-interface {iface}")
        for m in re.finditer(rf"({ANYIFACE}) is passive", intent):
            c.expect_line(block,
                          rf"^passive-interface {re.escape(norm_iface(m.group(1)))}$",
                          "ospf_passive", m.group(1),
                          f"passive-interface {m.group(1)}")


# --------------------------------------------------------------------------
# family: bgp
# --------------------------------------------------------------------------

def check_bgp(c):
    intent = c.row["intent_nl"]
    # local AS phrasings: "eBGP AS 65000", "Set up eBGP on edge1 in
    # AS 64792" (the first "in AS" in the intent is the local AS; peer
    # ASes only appear after their peer IPs)
    m = re.search(r"(?:eBGP|BGP) AS (\d+)", intent) or \
        re.search(r"eBGP on \S+ in AS (\d+)", intent) or \
        re.search(r"\bin AS (\d+)", intent)
    local_as = m.group(1) if m else None
    block = c.cfg["bgp"].get(local_as) if local_as else None
    c.check(block is not None, "bgp_local_as", f"AS {local_as}",
            f"router bgp {local_as}", "missing")
    if block is None:
        block = []
    neighbors = re.findall(rf"({IP}) in AS (\d+)", intent)
    # shared-AS form: "two peers in the same upstream AS 64541 (192.0.2.34
    # and 192.0.2.38)"
    for m in re.finditer(r"AS (\d+) \(([^)]+)\)", intent):
        # "(router-id 10.255.0.1)" after the local AS is not a shared-AS
        # peer list (batch-4 style-3 head) — never mine it for neighbors
        if "router-id" in m.group(2):
            continue
        for ip in re.findall(rf"({IP})", m.group(2)):
            if (ip, m.group(1)) not in neighbors:
                neighbors.append((ip, m.group(1)))
    c.check(bool(neighbors), "bgp_neighbor_coverage", "neighbors",
            "at least one neighbor extracted from the intent",
            "none extracted")
    for nip, ras in neighbors:
        c.expect_line(block, rf"^neighbor {re.escape(nip)} remote-as {ras}$",
                      "bgp_neighbor", nip, f"neighbor {nip} remote-as {ras}")
    # neighbor descriptions: "peer X in AS N with description T" /
    # "peer X in AS N described as T"
    for m in re.finditer(rf"({IP}) in AS \d+ with description ([^,;]+)", intent):
        c.expect_line(block,
                      rf"^neighbor {re.escape(m.group(1))} description "
                      rf"{re.escape(m.group(2).strip())}$",
                      "bgp_neighbor_description", m.group(1),
                      f"neighbor {m.group(1)} description {m.group(2).strip()}")
    for m in re.finditer(rf"({IP}) in AS \d+ described as ([^,;]+)", intent):
        c.expect_line(block,
                      rf"^neighbor {re.escape(m.group(1))} description "
                      rf"{re.escape(m.group(2).strip())}$",
                      "bgp_neighbor_description", m.group(1),
                      f"neighbor {m.group(1)} description {m.group(2).strip()}")
    # batch-4 quoted parenthetical description: "peer X in AS N ('ISP
    # uplink')" — the config carries the text unquoted
    for m in re.finditer(rf"({IP}) in AS \d+ \('([^']+)'\)", intent):
        c.expect_line(block,
                      rf"^neighbor {re.escape(m.group(1))} description "
                      rf"{re.escape(m.group(2).strip())}$",
                      "bgp_neighbor_description", m.group(1),
                      f"neighbor {m.group(1)} description {m.group(2).strip()}")
    # a peer "kept administratively shut down" needs its shutdown line
    for m in re.finditer(
            rf"peer (?:with )?({IP}) in AS \d+(?: \('[^']*'\))? kept "
            r"administratively shut down", intent):
        c.expect_line(block,
                      rf"^neighbor {re.escape(m.group(1))} shutdown$",
                      "bgp_neighbor_shutdown", m.group(1),
                      f"neighbor {m.group(1)} shutdown")
    m = re.search(rf"router-id ({IP})", intent)
    if m:
        c.expect_line(block, rf"^bgp router-id {re.escape(m.group(1))}$",
                      "bgp_router_id", m.group(1),
                      f"bgp router-id {m.group(1)}")
    # advertised networks: span runs from 'advertis...' to the next
    # ', and <verb>' clause (originate/apply/aggregate/set/filter) or the
    # end of the intent; [^.;] would break on the dots inside IPs
    advertised = []
    m = re.search(r"advertis\w* (.+)", intent)
    if m:
        span = re.split(r",?\s+and\s+(?:originate|apply|aggregate|set|filter"
                        r"|create|prefer)\b|;", m.group(1))[0]
        advertised = re.findall(rf"({IP})/(\d+)", span)
    for ip, plen in advertised:
        c.expect_line(block,
                      rf"^network {net_of(ip, plen)} mask {mask_of(plen)}$",
                      "bgp_network", f"{ip}/{plen}",
                      f"network {net_of(ip, plen)} mask {mask_of(plen)}")
    # maximum-prefix scoped to a named peer ("set maximum-prefix 500 on
    # peer X") applies to that peer only; the unscoped form ("... on the
    # peer") applies to every neighbor
    m = re.search(rf"maximum-prefix (\d+) on peer ({IP})", intent)
    if m:
        c.expect_line(block,
                      rf"^neighbor {re.escape(m.group(2))} maximum-prefix "
                      rf"{m.group(1)}$",
                      "bgp_maximum_prefix", m.group(2),
                      f"neighbor {m.group(2)} maximum-prefix {m.group(1)}")
    else:
        m = re.search(r"maximum-prefix (\d+)", intent)
        if m:
            for nip, _ in neighbors:
                c.expect_line(block,
                              rf"^neighbor {re.escape(nip)} maximum-prefix {m.group(1)}$",
                              "bgp_maximum_prefix", nip,
                              f"neighbor {nip} maximum-prefix {m.group(1)}")
    m = re.search(r"keepalive (\d+) and hold (\d+)", intent)
    if m:
        for nip, _ in neighbors:
            c.expect_line(block,
                          rf"^neighbor {re.escape(nip)} timers {m.group(1)} {m.group(2)}$",
                          "bgp_timers", nip,
                          f"neighbor {nip} timers {m.group(1)} {m.group(2)}")
    m = re.search(rf"aggregate them as ({IP})/(\d+)", intent) or \
        re.search(rf"aggregate-address ({IP})/(\d+)", intent)
    if m:
        # compare the base address literally as stated: two corpus rows
        # state a non-canonical aggregate base (10.43.0.0/15) and the
        # config mirrors it verbatim, so normalising would false-fail
        suffix = " summary-only" if "summary-only" in intent else ""
        c.expect_line(block,
                      rf"^aggregate-address {re.escape(m.group(1))} "
                      rf"{mask_of(m.group(2))}{suffix}$",
                      "bgp_aggregate", f"{m.group(1)}/{m.group(2)}",
                      f"aggregate-address {m.group(1)} "
                      f"{mask_of(m.group(2))}{suffix}")
    # default-originate scoped to a named peer ("originate a default
    # route to peer X") applies to that peer only; the unscoped form
    # ("... to the peer") applies to every neighbor
    m = re.search(rf"originate a default route to peer ({IP})", intent)
    if m:
        c.expect_line(block,
                      rf"^neighbor {re.escape(m.group(1))} default-originate$",
                      "bgp_default_originate", m.group(1),
                      f"neighbor {m.group(1)} default-originate")
    elif "originate a default route" in intent:
        for nip, _ in neighbors:
            c.expect_line(block, rf"^neighbor {re.escape(nip)} default-originate$",
                          "bgp_default_originate", nip,
                          f"neighbor {nip} default-originate")
    m = re.search(r"prefix-list ([A-Za-z0-9_-]+)", intent)
    if m:
        name = m.group(1)
        direction = "in" if "inbound" in intent else "out"
        for nip, _ in neighbors:
            c.expect_line(block,
                          rf"^neighbor {re.escape(nip)} prefix-list {name} {direction}$",
                          "bgp_prefix_list_apply", nip,
                          f"neighbor {nip} prefix-list {name} {direction}")
        permitted = list(advertised)
        pm = re.search(r"permitting ([^;]+)", intent)
        if pm and "those prefixes" not in pm.group(1):
            permitted = re.findall(rf"({IP})/(\d+)", pm.group(1))
        for ip, plen in permitted:
            pat = (rf"^ip prefix-list {name} seq \d+ permit "
                   rf"{net_of(ip, plen)}/{plen}$")
            c.check(find_line(c.cfg["globals"], pat) is not None,
                    "bgp_prefix_list_entry", f"{ip}/{plen}",
                    f"ip prefix-list {name} permit {net_of(ip, plen)}/{plen}",
                    "missing")
    m = re.search(r"using (Loopback\d+) as the update source", intent)
    if m:
        for nip, _ in neighbors:
            c.expect_line(block,
                          rf"^neighbor {re.escape(nip)} update-source {m.group(1)}$",
                          "bgp_update_source", nip,
                          f"neighbor {nip} update-source {m.group(1)}")
    m = re.search(r"ebgp-multihop (\d+)", intent)
    if m:
        for nip, _ in neighbors:
            c.expect_line(block,
                          rf"^neighbor {re.escape(nip)} ebgp-multihop {m.group(1)}$",
                          "bgp_multihop", nip,
                          f"neighbor {nip} ebgp-multihop {m.group(1)}")
    for m in re.finditer(rf"[Cc]onfigure interface ({IFACE_NUM}) as the eBGP link "
                          rf"\(({IP})/(\d+)\)", intent):
        c.assert_iface_ip(m.group(1), m.group(2), m.group(3), "bgp_link_ip")
    for m in re.finditer(rf"[Cc]reate (Loopback\d+) \(({IP})/(\d+)\)", intent):
        c.assert_iface_ip(m.group(1), m.group(2), m.group(3), "bgp_loopback_ip")


# --------------------------------------------------------------------------
# family: interface_mgmt
# --------------------------------------------------------------------------

def vty_covering_blocks(cfg, first, last):
    """All `line vty` blocks whose ranges jointly cover [first, last].

    Configs sometimes split the range (e.g. `line vty 0 4` + `line vty 5 15`
    for VTY 0-15); that is a full match if coverage is contiguous and every
    covering block carries the required settings.
    """
    ranges = []
    for key, block in cfg["lines"].items():
        m = re.fullmatch(r"vty (\d+) (\d+)", key)
        if m:
            ranges.append((int(m.group(1)), int(m.group(2)), block))
    ranges.sort()
    covered, blocks = int(first), []
    for a, b, block in ranges:
        if a <= covered <= b:
            blocks.append(block)
            covered = b + 1
            if covered > int(last):
                return blocks
    return None


def check_interface_mgmt(c):
    intent = c.row["intent_nl"]
    low = intent.lower()
    start_checks = c.n_checks
    # --- loopbacks ---------------------------------------------------------
    # non-parenthesised form: "create Loopback0 with description X and IP a/b"
    for m in re.finditer(rf"(Loopback\d+) with description (.+?) and IP "
                         rf"({IP})/(\d+)", intent):
        c.assert_iface_ip(m.group(1), m.group(3), m.group(4), "loopback_ip")
        block = iface_block(c.cfg, m.group(1))
        if block is not None:
            c.expect_line(block,
                          rf"^description {re.escape(unquote(m.group(2)))}$",
                          "loopback_description", m.group(1),
                          f"description {unquote(m.group(2))}")
    for m in re.finditer(rf"(Loopback\d+) \(({IP})/(\d+)"
                          r"(?:, description ([^)]+))?\)", intent):
        c.assert_iface_ip(m.group(1), m.group(2), m.group(3), "loopback_ip")
        if m.group(4):
            block = iface_block(c.cfg, m.group(1))
            if block is not None:
                c.expect_line(block,
                              rf"^description {re.escape(unquote(m.group(4)))}$",
                              "loopback_description", m.group(1),
                              f"description {unquote(m.group(4))}")
    # --- batch-4 management loopback: "Create Loopback0 as the
    #     management interface with IP 10.255.0.1/32"
    for m in re.finditer(rf"(Loopback\d+) as the management interface "
                         rf"with IP ({IP})/(\d+)", intent):
        c.assert_iface_ip(m.group(1), m.group(2), m.group(3), "loopback_ip")
    # --- "Set the description of Gi0/0/4 to X (without changing its state)"
    desc_done = set()
    m = re.search(rf"[Ss]et the description of ({IFACE_NUM}) to "
                  r"(.+?)(?: without changing its state)?$", intent)
    if m:
        desc_done.add(m.group(1))
        block = iface_block(c.cfg, m.group(1))
        if block is not None:
            c.expect_line(block,
                          rf"^description {re.escape(unquote(m.group(2)))}$",
                          "interface_description", m.group(1),
                          f"description {unquote(m.group(2))}")
    # --- parenthesised interface specs: Gi0/0/0 (description X, 10.0.0.1/30)
    #     or Gi0/0/2 (description X) with IP 10.0.0.1/30
    paren_done = set(desc_done)
    for m in re.finditer(rf"({IFACE_NUM}) \(([^)]+)\)", intent):
        iface, inner = m.group(1), m.group(2)
        dm = re.search(r"description ([^,]+)", inner)
        im = re.search(rf"({IP})/(\d+)", inner)
        if im is None:
            tail = intent[m.end():m.end() + 40]
            im = re.match(rf"\s*with IP ({IP})/(\d+)", tail)
        if dm is None and im is None:
            continue
        paren_done.add(iface)
        if im:
            c.assert_iface_ip(iface, im.group(1), im.group(2), "interface_ip")
        if dm:
            block = iface_block(c.cfg, iface)
            if block is not None:
                c.expect_line(block,
                              rf"^description {re.escape(unquote(dm.group(1)))}$",
                              "interface_description", iface,
                              f"description {unquote(dm.group(1))}")
    # --- "Label Gi1/0/5 and Gi1/0/6 as Office workstations" (pre-pass:
    #     the clause split on " and " would tear this apart)
    label_done = set()
    for m in re.finditer(r"[Ll]abel ((?:" + IFACE_NUM +
                         r"(?:\s+and\s+|\s*,\s*)?)+) as "
                         r"'?([A-Za-z0-9 \-]+?)'?(?= and bring| and shut"
                         r"| and set|,|$|;)", intent):
        desc = unquote(m.group(2))
        for iface in re.findall(rf"({IFACE_NUM})", m.group(1)):
            label_done.add(iface)
            block = iface_block(c.cfg, iface)
            if block is not None:
                c.expect_line(block, rf"^description {re.escape(desc)}$",
                              "interface_description", iface,
                              f"description {desc}")
    # --- physical / OOB interfaces ----------------------------------------
    clauses = re.split(r"\s*;\s*|\s*,\s*and\s+|\s+and\s+", intent)
    last_iface = None
    for clause in clauses:
        m = re.search(rf"({IFACE}(?:\d+(?:/\d+)*(?:\.\d+)?)?\d*)", clause)
        iface = m.group(1) if m and re.search(r"\d", m.group(1)) else None
        if iface:
            last_iface = iface
        target = iface or last_iface
        bring_up = re.search(
            r"bring (it|the interface|them|the port|both ports) up"
            r"|no shutdown|enable it|both up", clause)
        if re.search(r"\b[Dd]isable\b|\bshut (it )?down\b", clause) and target:
            block = iface_block(c.cfg, target)
            if block is not None:
                c.expect_line(block, r"^shutdown$", "interface_shutdown",
                              target, "shutdown")
        if "access mode" in clause and target:
            block = iface_block(c.cfg, target)
            if block is not None:
                c.expect_line(block, r"^switchport mode access$",
                              "interface_access_mode", target,
                              "switchport mode access")
        sm = re.search(r"set its description to (.+?)(?:,| and |$)", clause)
        if sm and target and target not in paren_done:
            block = iface_block(c.cfg, target)
            if block is not None:
                c.expect_line(block,
                              rf"^description {re.escape(unquote(sm.group(1)))}$",
                              "interface_description", target,
                              f"description {unquote(sm.group(1))}")
        lm = re.search(r"[Ll]abel it (.+?)(?: and |,|$)", clause)
        if lm and target and target not in paren_done:
            block = iface_block(c.cfg, target)
            if block is not None:
                c.expect_line(block,
                              rf"^description {re.escape(unquote(lm.group(1)))}$",
                              "interface_description", target,
                              f"description {unquote(lm.group(1))}")
        if iface and iface not in paren_done and iface not in label_done:
            dm = re.search(r"description (?:to )?(.+?)(?:, IP |, and IP | and IP |, give|, no shutdown|, bring| and bring|$)",
                           clause)
            if dm:
                desc = unquote(dm.group(1).rstrip(","))
                block = iface_block(c.cfg, iface)
                if block is not None:
                    c.expect_line(block, rf"^description {re.escape(desc)}$",
                                  "interface_description", iface,
                                  f"description {desc}")
        # a description in a clause with no interface of its own also
        # continues the previous interface's spec ("... with IP a/b and
        # description Point to point link")
        if not iface and target and target not in paren_done:
            dm2 = re.search(r"description (?:to )?(.+?)(?:, no shutdown"
                            r"|, bring|, enable|,|$)", clause)
            if dm2:
                block = iface_block(c.cfg, target)
                if block is not None:
                    c.expect_line(block,
                                  rf"^description {re.escape(unquote(dm2.group(1).rstrip(',')))}$",
                                  "interface_description", target,
                                  f"description {unquote(dm2.group(1))}")
        # an IP in a clause with no interface of its own continues the
        # previous interface's spec ("... with description X and IP a/b")
        im = re.search(rf"IP ({IP})/(\d+)", clause)
        if im and target and target not in paren_done:
            c.assert_iface_ip(target, im.group(1), im.group(2),
                              "interface_ip")
        # a stated secondary IP ("plus secondary IP 192.168.23.1/24") is
        # load-bearing too: it must appear as a `secondary` address line
        sim = re.search(rf"secondary IP ({IP})/(\d+)", clause)
        if sim and target:
            block = iface_block(c.cfg, target)
            if block is not None:
                c.expect_line(
                    block,
                    rf"^ip address {re.escape(sim.group(1))} "
                    rf"{re.escape(mask_of(sim.group(2)))} secondary$",
                    "interface_secondary_ip", target,
                    f"ip address {sim.group(1)} "
                    f"{mask_of(sim.group(2))} secondary")
        if bring_up:
            if target:
                block = iface_block(c.cfg, target)
                if block is not None:
                    c.expect_line(block, r"^no shutdown$",
                                  "interface_no_shutdown", target,
                                  "no shutdown")
    # --- SSH ---------------------------------------------------------------
    if "ssh" in low:
        m = re.search(r"domain name (?:to )?([\w.-]+)", intent)
        if m:
            c.check(find_line(c.cfg["globals"],
                              rf"^ip domain-name {re.escape(m.group(1))}$")
                    is not None,
                    "ssh_domain", m.group(1), f"ip domain-name {m.group(1)}",
                    "missing")
        m = re.search(r"SSH version (\d+)", intent)
        if m:
            c.check(find_line(c.cfg["globals"], rf"^ip ssh version {m.group(1)}$")
                    is not None,
                    "ssh_version", f"version {m.group(1)}",
                    f"ip ssh version {m.group(1)}", "missing")
        m = re.search(r"time-out of (\d+) seconds", intent)
        if m:
            c.check(find_line(c.cfg["globals"], rf"^ip ssh time-out {m.group(1)}$")
                    is not None,
                    "ssh_timeout", f"{m.group(1)}s",
                    f"ip ssh time-out {m.group(1)}", "missing")
        m = re.search(r"(\d+) authentication retries", intent)
        if m:
            c.check(find_line(c.cfg["globals"],
                              rf"^ip ssh authentication-retries {m.group(1)}$")
                    is not None,
                    "ssh_retries", m.group(1),
                    f"ip ssh authentication-retries {m.group(1)}", "missing")
        m = re.search(r"VTY lines (\d+) to (\d+)", intent)
        if m:
            key = f"vty {m.group(1)} {m.group(2)}"
            blocks = vty_covering_blocks(c.cfg, m.group(1), m.group(2))
            c.check(blocks is not None, "vty_block", key,
                    f"line vty {m.group(1)} {m.group(2)} "
                    "(possibly split into contiguous blocks)", "missing")
            if blocks is not None:
                for block in blocks:
                    c.expect_line(block, r"^transport input ssh$",
                                  "vty_transport", key, "transport input ssh")
                    if "local login" in low or "login local" in low \
                            or "local authentication" in low:
                        c.expect_line(block, r"^login local$", "vty_login",
                                      key, "login local")
                em = re.search(r"(\d+) minute exec timeout", intent)
                if em:
                    for block in blocks:
                        c.expect_line(block,
                                      rf"^exec-timeout {em.group(1)} 0$",
                                      "vty_exec_timeout", key,
                                      f"exec-timeout {em.group(1)} 0")
        # --- VTY access-class ACL ------------------------------------------
        am = re.search(r"access-list (\d+)", intent)
        if am:
            acl = am.group(1)
            nm = re.search(rf"to the ({IP})/(\d+) network", intent)
            if nm:
                net = net_of(nm.group(1), nm.group(2))
                c.check(find_line(
                    c.cfg["globals"],
                    rf"^access-list {acl} permit {re.escape(net)} "
                    rf"{re.escape(wildcard_of(nm.group(2)))}$") is not None,
                    "acl_permit", f"access-list {acl}",
                    f"access-list {acl} permit {net} "
                    f"{wildcard_of(nm.group(2))}", "missing")
            vm = re.search(r"VTY lines (\d+) to (\d+)", intent)
            if vm:
                blocks = vty_covering_blocks(c.cfg, vm.group(1), vm.group(2))
                if blocks is not None:
                    for block in blocks:
                        c.expect_line(block, rf"^access-class {acl} in$",
                                      "vty_access_class",
                                      f"vty {vm.group(1)} {vm.group(2)}",
                                      f"access-class {acl} in")
    # --- console -----------------------------------------------------------
    if "console" in low:
        block = c.cfg["lines"].get("con 0")
        c.check(block is not None, "console_block", "line con 0",
                "line con 0", "missing")
        if block is not None:
            if "logging synchronous" in low:
                c.expect_line(block, r"^logging synchronous$",
                              "console_logging_sync", "line con 0",
                              "logging synchronous")
            m = re.search(r"exec timeout of (\d+) minutes?", intent) or \
                re.search(r"(\d+) minute timeout", intent) or \
                re.search(r"console timeout to (\d+) minutes", intent)
            if m:
                c.expect_line(block, rf"^exec-timeout {m.group(1)} 0$",
                              "console_exec_timeout", "line con 0",
                              f"exec-timeout {m.group(1)} 0")
    c.check(c.n_checks > start_checks, "interface_coverage", "intent",
            "at least one interface/SSH/console assertion extracted",
            "none extracted")


# --------------------------------------------------------------------------
# family: acl (batch 9)
# --------------------------------------------------------------------------

def _acl_addr(text):
    """Render an intent address token as its config form."""
    if text == "any":
        return "any"
    m = re.fullmatch(rf"host ({IP})", text)
    if m:
        return f"host {m.group(1)}"
    m = re.fullmatch(rf"({IP})/(\d+)", text)
    return f"{net_of(m.group(1), m.group(2))} {wildcard_of(m.group(2))}"


def check_acl(c):
    intent = c.row["intent_nl"]
    start_checks = c.n_checks
    m = re.search(r"(extended|standard) ACL ([A-Za-z][\w-]*|\d+)", intent)
    c.check(m is not None, "acl_header", "intent",
            "an '(extended|standard) ACL <name>' header", "none extracted")
    if m is None:
        return
    acl_type, name = m.group(1), m.group(2)
    block = c.cfg["acls"].get(name)
    c.check(block is not None, "acl_block", name,
            f"ip access-list {acl_type} {name}", "missing")
    if block is None:
        return
    # Sequence numbers (10, 20, ...) are a rendering choice, not intent:
    # strip them before matching ACE lines.
    lines = [re.sub(r"^\d+\s+", "", ln) for ln in block]
    rm = re.search(r"remark '([^']+)'", intent)
    if rm:
        c.expect_line(lines, rf"^remark {re.escape(rm.group(1))}$",
                      "acl_remark", name, f"remark {rm.group(1)}")
    # --- application -------------------------------------------------------
    app = re.search(r"apply (?:extended|standard) ACL \S+ "
                    r"(inbound|outbound) on (" + IFACE_NUM + r")", intent) \
        or re.search(r"applied (inbound|outbound) on (" + IFACE_NUM + r")",
                     intent)
    if app:
        direction = "in" if app.group(1) == "inbound" else "out"
        iface = app.group(2)
        iblock = iface_block(c.cfg, iface)
        c.check(iblock is not None, "acl_app_iface", iface,
                "interface block", "missing")
        if iblock is not None:
            c.expect_line(iblock, rf"^ip access-group {re.escape(name)} "
                                  rf"{direction}$",
                          "acl_access_group", iface,
                          f"ip access-group {name} {direction}")
        ipm = re.search(r"on " + re.escape(iface) +
                        rf" \(({IP})/(\d+)(?:, description '[^']+')?\)",
                        intent)
        if ipm:
            c.assert_iface_ip(iface, ipm.group(1), ipm.group(2))
        dm = re.search(r"description '([^']+)'", intent)
        if dm and iblock is not None:
            c.expect_line(iblock, rf"^description {re.escape(dm.group(1))}$",
                          "acl_iface_description", iface,
                          f"description {dm.group(1)}")
    # --- ACEs ---------------------------------------------------------------
    if acl_type == "extended":
        ace_re = re.compile(
            r"(permit|deny) (tcp|udp|icmp|ip) from "
            rf"(any|host {IP}|{IP}/\d+) to (any|host {IP}|{IP}/\d+)"
            r"( port (\d+)| port range (\d+) to (\d+)| ports above (\d+)"
            r"| ports below (\d+)| established)?( with logging)?")
        n_ace = 0
        for am in ace_re.finditer(intent):
            n_ace += 1
            expected = (f"{am.group(1)} {am.group(2)} "
                        f"{_acl_addr(am.group(3))} {_acl_addr(am.group(4))}")
            if am.group(6):
                expected += f" eq {am.group(6)}"
            elif am.group(7):
                expected += f" range {am.group(7)} {am.group(8)}"
            elif am.group(9):
                expected += f" gt {am.group(9)}"
            elif am.group(10):
                expected += f" lt {am.group(10)}"
            elif " established" in (am.group(5) or ""):
                expected += " established"
            if am.group(11):
                expected += " log"
            c.expect_line(lines, rf"^{re.escape(expected)}$",
                          "acl_ace", name, expected)
        c.check(n_ace > 0, "acl_ace_coverage", name,
                "at least one extended ACE extracted", "none extracted")
    else:
        ace_re = re.compile(rf"(permit|deny) (any|host {IP}|{IP}/\d+)")
        n_ace = 0
        for am in ace_re.finditer(intent):
            n_ace += 1
            expected = f"{am.group(1)} {_acl_addr(am.group(2))}"
            c.expect_line(lines, rf"^{re.escape(expected)}$",
                          "acl_ace", name, expected)
        c.check(n_ace > 0, "acl_ace_coverage", name,
                "at least one standard ACE extracted", "none extracted")
    c.check(c.n_checks > start_checks, "acl_coverage", "intent",
            "at least one ACL assertion extracted", "none extracted")


# --------------------------------------------------------------------------
# family: nat (batch 9)
# --------------------------------------------------------------------------

def check_nat(c):
    intent = c.row["intent_nl"]
    start_checks = c.n_checks
    globals_ = c.cfg["globals"]
    in_iface = out_iface = None
    for role, pat in (("inside", r"as the inside interface"),
                      ("outside", r"as the outside interface")):
        m = re.search(r"(" + IFACE_NUM + rf") \(({IP})/(\d+)\) " + pat,
                      intent) or \
            re.search(role + r" is (" + IFACE_NUM + rf") \(({IP})/(\d+)\)",
                      intent)
        c.check(m is not None, f"nat_{role}_iface", "intent",
                f"a stated {role} interface with IP", "none extracted")
        if m is None:
            continue
        iface, ip, plen = m.group(1), m.group(2), m.group(3)
        if role == "inside":
            in_iface = iface
        else:
            out_iface = iface
        c.assert_iface_ip(iface, ip, plen)
        block = iface_block(c.cfg, iface)
        if block is not None:
            c.expect_line(block, rf"^ip nat {role}$", f"nat_{role}_line",
                          iface, f"ip nat {role}")

    def check_source_acl(acl, entries):
        block = c.cfg["acls"].get(acl)
        c.check(block is not None, "nat_acl_block", acl,
                f"ip access-list standard {acl}", "missing")
        if block is None:
            return
        lines = [re.sub(r"^\d+\s+", "", ln) for ln in block]
        for em in re.finditer(rf"permit ({IP})/(\d+)", entries):
            expected = (f"permit {net_of(em.group(1), em.group(2))} "
                        f"{wildcard_of(em.group(2))}")
            c.expect_line(lines, rf"^{re.escape(expected)}$",
                          "nat_acl_permit", acl, expected)

    for pm in re.finditer(
            r"matching standard ACL (\S+?) \((.*?)\) to the outside "
            r"interface address with overload", intent):
        acl, entries = pm.group(1), pm.group(2)
        check_source_acl(acl, entries)
        if out_iface:
            expected = (f"ip nat inside source list {acl} interface "
                        f"{out_iface} overload")
            c.expect_line(globals_, rf"^{re.escape(expected)}$",
                          "nat_pat_rule", acl, expected)
    for pm in re.finditer(
            rf"matching standard ACL (\S+?) \((.*?)\) to pool (\S+?) "
            rf"\(({IP}) to ({IP}), netmask ({IP})\)( with overload)?",
            intent):
        acl, entries, pool = pm.group(1), pm.group(2), pm.group(3)
        check_source_acl(acl, entries)
        pool_line = (f"ip nat pool {pool} {pm.group(4)} {pm.group(5)} "
                     f"netmask {pm.group(6)}")
        c.expect_line(globals_, rf"^{re.escape(pool_line)}$",
                      "nat_pool_def", pool, pool_line)
        rule = f"ip nat inside source list {acl} pool {pool}"
        if pm.group(7):
            rule += " overload"
        c.expect_line(globals_, rf"^{re.escape(rule)}$",
                      "nat_pool_rule", pool, rule)
    sm = re.search(r"static NAT mappings?: (.+?)\.(?:\s|$)", intent)
    if sm:
        for mm in re.finditer(rf"({IP}) to ({IP})", sm.group(1)):
            expected = (f"ip nat inside source static {mm.group(1)} "
                        f"{mm.group(2)}")
            c.expect_line(globals_, rf"^{re.escape(expected)}$",
                          "nat_static", mm.group(1), expected)
    c.check(c.n_checks > start_checks, "nat_coverage", "intent",
            "at least one NAT assertion extracted", "none extracted")


# --------------------------------------------------------------------------
# family: hsrp (batch 9)
# --------------------------------------------------------------------------

def check_hsrp(c):
    intent = c.row["intent_nl"]
    start_checks = c.n_checks
    m = re.search(r"HSRP on (" + ANYIFACE + rf") \(({IP})/(\d+)\)", intent)
    c.check(m is not None, "hsrp_iface", "intent",
            "an 'HSRP on <iface> (<ip>/<plen>)' header", "none extracted")
    if m is None:
        return
    iface, ip, plen = m.group(1), m.group(2), m.group(3)
    c.assert_iface_ip(iface, ip, plen)
    block = iface_block(c.cfg, iface)
    c.check(block is not None, "hsrp_block", iface,
            "interface block", "missing")
    if block is None:
        return
    vm = re.search(r"for VLAN (\d+)", intent)
    if vm:
        c.check(vm.group(1) in c.cfg["vlans"], "hsrp_vlan_block",
                f"vlan {vm.group(1)}", "vlan block present", "missing")
    if "HSRP version 2" in intent:
        c.expect_line(block, r"^standby version 2$",
                      "hsrp_version", iface, "standby version 2")
    grp_re = re.compile(
        rf"group (\d+) virtual IP ({IP}), priority (\d+)"
        r"(, preempt enabled)?(, hello (\d+) hold (\d+))?"
        r"(, hello (\d+) msec hold (\d+) msec)?")
    n_grp = 0
    for gm in grp_re.finditer(intent):
        n_grp += 1
        g, vip, prio = gm.group(1), gm.group(2), gm.group(3)
        c.expect_line(block, rf"^standby {g} ip {re.escape(vip)}$",
                      "hsrp_virtual_ip", f"group {g}",
                      f"standby {g} ip {vip}")
        c.expect_line(block, rf"^standby {g} priority {prio}$",
                      "hsrp_priority", f"group {g}",
                      f"standby {g} priority {prio}")
        if gm.group(4):
            c.expect_line(block, rf"^standby {g} preempt$",
                          "hsrp_preempt", f"group {g}",
                          f"standby {g} preempt")
        if gm.group(6):
            c.expect_line(block, rf"^standby {g} timers {gm.group(6)} "
                                 rf"{gm.group(7)}$",
                          "hsrp_timers", f"group {g}",
                          f"standby {g} timers {gm.group(6)} {gm.group(7)}")
        elif gm.group(9):
            c.expect_line(block, rf"^standby {g} timers msec {gm.group(9)} "
                                 rf"msec {gm.group(10)}$",
                          "hsrp_timers_msec", f"group {g}",
                          f"standby {g} timers msec {gm.group(9)} "
                          f"msec {gm.group(10)}")
    c.check(n_grp > 0, "hsrp_group_coverage", iface,
            "at least one HSRP group extracted", "none extracted")
    c.check(c.n_checks > start_checks, "hsrp_coverage", "intent",
            "at least one HSRP assertion extracted", "none extracted")


FAMILY_CHECKS = {
    "vlan_l2": check_vlan_l2,
    "trunk_svi": check_trunk_svi,
    "static_route": check_static_route,
    "ospf": check_ospf,
    "bgp": check_bgp,
    "interface_mgmt": check_interface_mgmt,
    "etherchannel": check_etherchannel,
    "stp": check_stp,
    "port_security": check_port_security,
    "acl": check_acl,
    "nat": check_nat,
    "hsrp": check_hsrp,
}


def main():
    root = Path(__file__).resolve().parent.parent
    dataset = Path(sys.argv[1]) if len(sys.argv) > 1 else root / "data/v0.1/dataset.jsonl"
    results = Path(sys.argv[2]) if len(sys.argv) > 2 else root / "data/v0.1/intent_match_results.jsonl"
    rows = [json.loads(ln) for ln in dataset.read_text().splitlines() if ln.strip()]
    n_pass = 0
    with results.open("w") as fh:
        for row in rows:
            c = Checker(row)
            c.assert_hostname()
            FAMILY_CHECKS[row["family"]](c)
            ok = not c.failures
            n_pass += ok
            fh.write(json.dumps({
                "id": row["id"], "family": row["family"], "pass": ok,
                "checks_run": c.n_checks, "failures": c.failures,
            }) + "\n")
    print(f"{n_pass}/{len(rows)} rows pass ({n_pass / len(rows):.1%})")
    print(f"results -> {results}")


if __name__ == "__main__":
    main()
