#!/usr/bin/env python3
"""Negative controls for the intent-match checker (validation round 1+).

A checker that passes everything is worthless unless it also FAILS wrong
configs. This script rebuilds the hand mutations named in
docs/validation-round-1.md against v0.1 rows (wrong VLAN name, wrong
next-hop, wrong remote-as, wrong OSPF wildcard, wrong subnet mask,
dropped trunk VLAN, wrong advertised network, wrong SVI IP, flipped
shutdown, wrong aggregate, deleted passive-interface, telnet instead of
SSH, deleted passive-default, plus wrong OSPF router-id as the 14th) and
four batch-4-style mutations against batch-4 rows (swapped static
next-hops, maximum-prefix moved to the other peer, one access port
reassigned to a different VLAN in a multi-VLAN row, one word of a quoted
description altered).

Every control searches for the first row (dataset order) whose original
config passes and whose mutated config FAILS, and requires the failure
to include an expected check type. Exit code 0 iff every control is
caught.

Usage:
  python3 scripts/intent_match_controls.py
"""

import copy
import importlib.util
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "intent_match", ROOT / "scripts" / "intent_match.py")
im = importlib.util.module_from_spec(spec)
spec.loader.exec_module(im)

IP = im.IP


def load(path):
    return [json.loads(ln) for ln in Path(path).read_text().splitlines()
            if ln.strip()]


def run(row):
    c = im.Checker(row)
    c.assert_hostname()
    im.FAMILY_CHECKS[row["family"]](c)
    return c


def find_control(rows, mutator, expected_checks, family=None):
    """First row whose original passes and mutation fails as expected."""
    for row in rows:
        if family and row["family"] != family:
            continue
        mutated_cfg = mutator(row)
        if mutated_cfg is None or mutated_cfg == row["config"]:
            continue
        if run(row).failures:
            continue  # original must pass for the control to mean anything
        mrow = copy.deepcopy(row)
        mrow["config"] = mutated_cfg
        mc = run(mrow)
        if not mc.failures:
            continue
        if expected_checks and not any(
                f["check"] in expected_checks for f in mc.failures):
            continue
        return row["id"], mc.failures
    return None, None


def sub_once(pattern, repl, text, flags=0):
    new, n = re.subn(pattern, repl, text, count=1, flags=flags)
    return new if n else None


# --------------------------------------------------------------------------
# v0.1 mutation builders (each returns a mutated config or None)
# --------------------------------------------------------------------------

def mut_wrong_vlan_name(row):
    return sub_once(r"(?m)^(\s*name )\S+", r"\g<1>WrongName", row["config"])


def mut_wrong_next_hop(row):
    m = re.search(rf"(?m)^(ip route \S+ \S+ )({IP})$", row["config"])
    if not m:
        return None
    nh = m.group(2)
    wrong = nh.rsplit(".", 1)[0] + "." + str((int(nh.rsplit(".", 1)[1]) + 7) % 254 + 1)
    return row["config"].replace(m.group(0), m.group(1) + wrong, 1)


def mut_wrong_remote_as(row):
    m = re.search(r"(?m)^( neighbor \S+ remote-as )(\d+)$", row["config"])
    if not m:
        return None
    return row["config"].replace(
        m.group(0), m.group(1) + str(int(m.group(2)) + 111), 1)


def mut_wrong_ospf_wildcard(row):
    m = re.search(r"(?m)^( network \S+ )(\S+)( area \d+)$", row["config"])
    if not m:
        return None
    wrong = "0.0.255.255" if m.group(2) != "0.0.255.255" else "0.0.0.255"
    return row["config"].replace(m.group(0), m.group(1) + wrong + m.group(3), 1)


def mut_wrong_subnet_mask(row):
    for m in re.finditer(rf"(?m)^(\s*ip address )({IP}) (\S+)$", row["config"]):
        ip, mask = m.group(2), m.group(3)
        pm = re.search(rf"{re.escape(ip)}/(\d+)", row["intent_nl"])
        if not pm or im.mask_of(pm.group(1)) != mask:
            continue
        plen = int(pm.group(1))
        wrong = im.mask_of(plen + 1) if plen < 32 else im.mask_of(plen - 1)
        return row["config"].replace(m.group(0), m.group(1) + ip + " " + wrong, 1)
    return None


def mut_dropped_trunk_vlan(row):
    m = re.search(r"(?m)^( switchport trunk allowed vlan )(\S+)$", row["config"])
    if not m or "," not in m.group(2):
        return None
    kept = m.group(2).split(",")[1:]
    return row["config"].replace(m.group(0), m.group(1) + ",".join(kept), 1)


def mut_wrong_advertised_network(row):
    m = re.search(rf"(?m)^(  network )({IP})( mask \S+)$", row["config"])
    if not m:
        return None
    parts = m.group(2).split(".")
    parts[1] = str((int(parts[1]) + 5) % 250 + 1)
    return row["config"].replace(m.group(0), m.group(1) + ".".join(parts) + m.group(3), 1)


def mut_wrong_svi_ip(row):
    blocks = row["config"].split("!")
    for i, b in enumerate(blocks):
        if re.search(r"(?m)^interface Vlan\d+$", b):
            m = re.search(rf"(?m)^(\s*ip address )({IP})( \S+)$", b)
            if not m:
                return None
            parts = m.group(2).split(".")
            parts[2] = str((int(parts[2]) + 9) % 250 + 1)
            blocks[i] = b.replace(
                m.group(0), m.group(1) + ".".join(parts) + m.group(3), 1)
            return "!".join(blocks)
    return None


def mut_flipped_shutdown(row):
    if "bring" not in row["intent_nl"].lower():
        return None
    return sub_once(r"(?m)^ no shutdown$", " shutdown", row["config"])


def mut_wrong_aggregate(row):
    m = re.search(rf"(?m)^(  aggregate-address )({IP})( \S+.*)$", row["config"])
    if not m:
        return None
    parts = m.group(2).split(".")
    parts[1] = str((int(parts[1]) + 6) % 250 + 1)
    return row["config"].replace(m.group(0), m.group(1) + ".".join(parts) + m.group(3), 1)


def mut_deleted_passive(row):
    return sub_once(r"(?m)^ passive-interface (?!default)\S+\n", "", row["config"])


def mut_telnet_instead_of_ssh(row):
    return sub_once(r"transport input ssh", "transport input telnet",
                    row["config"])


def mut_deleted_passive_default(row):
    return sub_once(r"(?m)^ passive-interface default\n", "", row["config"])


def mut_wrong_router_id(row):
    m = re.search(rf"(?m)^( router-id )({IP})$", row["config"])
    if not m:
        return None
    wrong = "10.255.254.254" if m.group(2) != "10.255.254.254" else "10.255.253.253"
    return row["config"].replace(m.group(0), m.group(1) + wrong, 1)


# --------------------------------------------------------------------------
# batch-4 mutation builders
# --------------------------------------------------------------------------

def mut_swap_static_next_hops(row):
    lines = row["config"].splitlines()
    idx = [i for i, ln in enumerate(lines)
           if re.fullmatch(rf"ip route \S+ \S+ {IP}", ln)]
    if len(idx) < 2:
        return None
    a, b = idx[0], idx[1]
    nh_a = lines[a].rsplit(" ", 1)[1]
    nh_b = lines[b].rsplit(" ", 1)[1]
    if nh_a == nh_b:
        return None
    lines[a] = lines[a].rsplit(" ", 1)[0] + " " + nh_b
    lines[b] = lines[b].rsplit(" ", 1)[0] + " " + nh_a
    return "\n".join(lines) + "\n"


def mut_move_maximum_prefix(row):
    m = re.search(rf"(?m)^(  neighbor )({IP})( maximum-prefix \d+)$",
                  row["config"])
    if not m:
        return None
    peers = re.findall(rf"(?m)^ neighbor ({IP}) remote-as \d+$", row["config"])
    others = [p for p in peers if p != m.group(2)]
    if not others:
        return None
    return row["config"].replace(
        m.group(0), m.group(1) + others[0] + m.group(3), 1)


def mut_reassign_access_port(row):
    vids = re.findall(r"(?m)^vlan (\d+)$", row["config"])
    if len(vids) < 2:
        return None
    m = re.search(r"(?m)^( switchport access vlan )(\d+)$", row["config"])
    if not m:
        return None
    other = next(v for v in vids if v != m.group(2))
    return row["config"].replace(m.group(0), m.group(1) + other, 1)


def mut_alter_quoted_description(row):
    # intent quotes the description ('WAN uplink'); alter one word of the
    # matching config description line
    for desc in re.findall(r"description '([^']+)'", row["intent_nl"]):
        line = f" description {desc}"
        if line in row["config"]:
            words = desc.split()
            words[-1] = "Changed" if words[-1] != "Changed" else "Altered"
            return row["config"].replace(
                line, " description " + " ".join(words), 1)
    return None


# --------------------------------------------------------------------------

def main():
    v01 = load(ROOT / "data/v0.1/dataset.jsonl")
    v02 = load(ROOT / "data/v0.2/dataset.jsonl")
    v01_ids = {r["id"] for r in v01}
    b4 = [r for r in v02 if r["id"] not in v01_ids]

    controls = [
        # label, rows, mutator, family, expected failure checks
        ("v01 wrong VLAN name", v01, mut_wrong_vlan_name, "vlan_l2",
         {"vlan_name"}),
        ("v01 wrong next-hop", v01, mut_wrong_next_hop, "static_route",
         {"static_route_next_hop", "static_route_line"}),
        ("v01 wrong remote-as", v01, mut_wrong_remote_as, "bgp",
         {"bgp_neighbor"}),
        ("v01 wrong OSPF wildcard", v01, mut_wrong_ospf_wildcard, "ospf",
         {"ospf_network"}),
        ("v01 wrong subnet mask", v01, mut_wrong_subnet_mask, None,
         {"interface_ip", "svi_ip", "loopback_ip", "ospf_interface_ip",
          "static_interface_ip", "bgp_link_ip", "bgp_loopback_ip"}),
        ("v01 dropped trunk VLAN", v01, mut_dropped_trunk_vlan, "trunk_svi",
         {"trunk_allowed_vlans"}),
        ("v01 wrong advertised network", v01, mut_wrong_advertised_network,
         "bgp", {"bgp_network"}),
        ("v01 wrong SVI IP", v01, mut_wrong_svi_ip, "trunk_svi", {"svi_ip"}),
        ("v01 flipped shutdown", v01, mut_flipped_shutdown, "interface_mgmt",
         {"interface_no_shutdown"}),
        ("v01 wrong aggregate", v01, mut_wrong_aggregate, "bgp",
         {"bgp_aggregate"}),
        ("v01 deleted passive-interface", v01, mut_deleted_passive, "ospf",
         {"ospf_passive"}),
        ("v01 telnet instead of SSH", v01, mut_telnet_instead_of_ssh,
         "interface_mgmt", {"vty_transport"}),
        ("v01 deleted passive-default", v01, mut_deleted_passive_default,
         "ospf", {"ospf_passive_default"}),
        ("v01 wrong OSPF router-id", v01, mut_wrong_router_id, "ospf",
         {"ospf_router_id"}),
        ("b4 swap two static routes' next-hops", b4,
         mut_swap_static_next_hops, "static_route",
         {"static_route_next_hop", "static_route_line"}),
        ("b4 move maximum-prefix to the other peer", b4,
         mut_move_maximum_prefix, "bgp", {"bgp_maximum_prefix"}),
        ("b4 reassign one port to a different VLAN", b4,
         mut_reassign_access_port, "vlan_l2", {"access_port_vlan"}),
        ("b4 alter one quoted description word", b4,
         mut_alter_quoted_description, "interface_mgmt",
         {"interface_description"}),
    ]
    caught = 0
    for label, rows, mutator, family, expected in controls:
        rid, failures = find_control(rows, mutator, expected, family)
        if rid:
            caught += 1
            kinds = sorted({f["check"] for f in failures})
            print(f"CAUGHT  {label}: row {rid} fails ({', '.join(kinds)})")
        else:
            print(f"MISSED  {label}: no mutated row was caught")
    print(f"\ncontrols caught: {caught}/{len(controls)}")
    return 0 if caught == len(controls) else 1


if __name__ == "__main__":
    sys.exit(main())
