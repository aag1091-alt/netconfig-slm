#!/usr/bin/env python3
"""Negative controls for the batch-9 intent-match checkers (acl / nat /
hsrp), in the style of scripts/intent_match_controls.py.

No batch-9 rows exist yet when the checkers are written, so the
controls run against three hand-built valid rows (one per family,
exercising every rule class) whose originals must PASS, plus a set of
single-fault mutations whose corrupted configs must FAIL with the
expected check type. Exit code 0 iff the originals pass and every
mutation is caught.

Usage:
  python3 scripts/intent_match_b9_controls.py
"""

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "intent_match", ROOT / "scripts" / "intent_match.py")
im = importlib.util.module_from_spec(spec)
spec.loader.exec_module(im)


def run(row):
    c = im.Checker(row)
    c.assert_hostname()
    im.FAMILY_CHECKS[row["family"]](c)
    return c


def dev(host):
    return {"vendor": "cisco", "os": "ios_xe", "version": "17.9",
            "role": "router", "hostname": host}


ACL_ROW = {
    "id": "acl-ctl", "family": "acl",
    "intent_nl": ("On r1, apply extended ACL EDGE-IN inbound on "
                  "GigabitEthernet0/0/1 (10.10.0.1/24, description "
                  "'LAN side'): remark 'Allow web and DNS'; permit tcp "
                  "from 10.10.0.0/24 to host 203.0.113.10 port 443; "
                  "permit udp from 10.10.0.0/24 to host 198.51.100.53 "
                  "port 53; permit tcp from any to any established; "
                  "deny tcp from any to any port 3389; deny ip from "
                  "any to any with logging."),
    "intent_oc": None, "device": dev("r1"),
    "config": ("hostname router1\n!\n"
               "interface GigabitEthernet0/0/1\n"
               " description LAN side\n"
               " ip address 10.10.0.1 255.255.255.0\n"
               " ip access-group EDGE-IN in\n"
               " no shutdown\n!\n"
               "ip access-list extended EDGE-IN\n"
               " remark Allow web and DNS\n"
               " permit tcp 10.10.0.0 0.0.0.255 host 203.0.113.10 eq 443\n"
               " permit udp 10.10.0.0 0.0.0.255 host 198.51.100.53 eq 53\n"
               " permit tcp any any established\n"
               " deny tcp any any eq 3389\n"
               " deny ip any any log\n!\n"),
    "source": "synthetic", "source_ref": None,
    "validation": {"parse_ok": None, "batfish_ok": None,
                   "emulation_ok": None},
    "notes": "control",
}

NAT_ROW = {
    "id": "nat-ctl", "family": "nat",
    "intent_nl": ("On edge1, configure NAT with GigabitEthernet0/0/1 "
                  "(10.1.1.1/24) as the inside interface and "
                  "GigabitEthernet0/0/0 (203.0.113.1/30) as the outside "
                  "interface. Translate inside sources matching standard "
                  "ACL NAT-SRC (permit 10.1.1.0/24) to pool PUBPOOL "
                  "(198.51.100.16 to 198.51.100.31, netmask "
                  "255.255.255.0) with overload. Add static NAT "
                  "mappings: 10.5.5.10 to 203.0.113.50."),
    "intent_oc": None, "device": dev("edge1"),
    "config": ("hostname edge1\n!\n"
               "interface GigabitEthernet0/0/1\n"
               " ip address 10.1.1.1 255.255.255.0\n"
               " ip nat inside\n"
               " no shutdown\n!\n"
               "interface GigabitEthernet0/0/0\n"
               " ip address 203.0.113.1 255.255.255.252\n"
               " ip nat outside\n"
               " no shutdown\n!\n"
               "ip access-list standard NAT-SRC\n"
               " permit 10.1.1.0 0.0.0.255\n!\n"
               "ip nat pool PUBPOOL 198.51.100.16 198.51.100.31 "
               "netmask 255.255.255.0\n"
               "ip nat inside source list NAT-SRC pool PUBPOOL overload\n"
               "ip nat inside source static 10.5.5.10 203.0.113.50\n!\n"),
    "source": "synthetic", "source_ref": None,
    "validation": {"parse_ok": None, "batfish_ok": None,
                   "emulation_ok": None},
    "notes": "control",
}

HSRP_ROW = {
    "id": "hsrp-ctl", "family": "hsrp",
    "intent_nl": ("On r1, configure HSRP on GigabitEthernet0/0/2 "
                  "(192.168.30.3/24): HSRP version 2; group 10 virtual "
                  "IP 192.168.30.1, priority 105, preempt enabled, "
                  "hello 1 hold 3; group 20 virtual IP 192.168.30.254, "
                  "priority 95."),
    "intent_oc": None, "device": dev("r1"),
    "config": ("hostname router1\n!\n"
               "interface GigabitEthernet0/0/2\n"
               " ip address 192.168.30.3 255.255.255.0\n"
               " standby version 2\n"
               " standby 10 ip 192.168.30.1\n"
               " standby 10 priority 105\n"
               " standby 10 preempt\n"
               " standby 10 timers 1 3\n"
               " standby 20 ip 192.168.30.254\n"
               " standby 20 priority 95\n"
               " no shutdown\n!\n"),
    "source": "synthetic", "source_ref": None,
    "validation": {"parse_ok": None, "batfish_ok": None,
                   "emulation_ok": None},
    "notes": "control",
}


def mut(row, old, new):
    r = json.loads(json.dumps(row))
    assert old in r["config"], f"mutation source not found: {old}"
    r["config"] = r["config"].replace(old, new, 1)
    return r


CONTROLS = [
    ("acl wrong ACE port", ACL_ROW,
     ("eq 443", "eq 444"), {"acl_ace"}),
    ("acl wrong ACE source net", ACL_ROW,
     ("permit udp 10.10.0.0 0.0.0.255", "permit udp 10.10.9.0 0.0.0.255"),
     {"acl_ace"}),
    ("acl flipped access-group direction", ACL_ROW,
     ("ip access-group EDGE-IN in", "ip access-group EDGE-IN out"),
     {"acl_access_group"}),
    ("acl dropped remark", ACL_ROW,
     (" remark Allow web and DNS\n", ""), {"acl_remark"}),
    ("acl wrong interface IP", ACL_ROW,
     ("ip address 10.10.0.1 255.255.255.0",
      "ip address 10.10.0.2 255.255.255.0"), {"interface_ip"}),
    ("nat wrong pool end", NAT_ROW,
     ("198.51.100.16 198.51.100.31", "198.51.100.16 198.51.100.30"),
     {"nat_pool_def"}),
    ("nat dropped pool overload", NAT_ROW,
     ("pool PUBPOOL overload", "pool PUBPOOL"), {"nat_pool_rule"}),
    ("nat wrong static outside IP", NAT_ROW,
     ("static 10.5.5.10 203.0.113.50", "static 10.5.5.10 203.0.113.51"),
     {"nat_static"}),
    ("nat wrong ACL permit net", NAT_ROW,
     ("permit 10.1.1.0 0.0.0.255", "permit 10.1.2.0 0.0.0.255"),
     {"nat_acl_permit"}),
    ("nat missing ip nat outside", NAT_ROW,
     (" ip nat outside\n", ""), {"nat_outside_line"}),
    ("hsrp wrong priority", HSRP_ROW,
     ("standby 10 priority 105", "standby 10 priority 106"),
     {"hsrp_priority"}),
    ("hsrp wrong virtual IP", HSRP_ROW,
     ("standby 20 ip 192.168.30.254", "standby 20 ip 192.168.30.253"),
     {"hsrp_virtual_ip"}),
    ("hsrp dropped preempt", HSRP_ROW,
     (" standby 10 preempt\n", ""), {"hsrp_preempt"}),
    ("hsrp wrong timers", HSRP_ROW,
     ("standby 10 timers 1 3", "standby 10 timers 2 6"),
     {"hsrp_timers"}),
    ("hsrp dropped version 2", HSRP_ROW,
     (" standby version 2\n", ""), {"hsrp_version"}),
]


def main():
    bad = 0
    for name, row in (("acl original", ACL_ROW), ("nat original", NAT_ROW),
                      ("hsrp original", HSRP_ROW)):
        c = run(row)
        ok = not c.failures
        print(f"{name}: {'PASS' if ok else 'FAIL ' + json.dumps(c.failures)}")
        bad += 0 if ok else 1
    for name, row, (old, new), expected in CONTROLS:
        c = run(mut(row, old, new))
        got = {f["check"] for f in c.failures}
        ok = bool(got & expected)
        print(f"{name}: {'caught' if ok else 'MISSED'} "
              f"(failures: {sorted(got)})")
        bad += 0 if ok else 1
    print(f"{'ALL CONTROLS OK' if not bad else f'{bad} CONTROL FAILURES'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
