"""Typed device state for the NetConfig plan slice (Phase 2).

A device state S is a plain JSON dict:

    {
      "device":   "sw-access-1",          # short binding name used in plans
      "hostname": "switch1",              # expanded hostname rendered in CLI
      "vlans":    {"20": {"name": "Engineering"}, ...},
      "interfaces": {
        "GigabitEthernet1/0/48": {
          "mode": "trunk",                # "access" | "trunk"
          "access_vlan": 1,               # meaningful in access mode
          "trunk_allowed": "all",         # "all" | sorted list[int]; [] = none
          "native_vlan": 1,
          "admin": "up",                  # "up" | "down"
          "description": "Uplink to core",
          "aliases": ["uplink"]           # operator-facing names
        }, ...
      }
    }

VLAN ids are dict keys (strings, JSON-style) at rest and ints in plans.
`trunk_allowed` mirrors the IOS XE semantics of
`switchport trunk allowed vlan ...`: the literal "all", an explicit list,
or an empty list (rendered as `allowed vlan none`).
"""
import copy
import json

VLAN_MIN, VLAN_MAX = 1, 4094
RESERVED_VLANS = {1002, 1003, 1004, 1005}  # token-ring/fddi legacy, unusable


def new_interface(mode="access", access_vlan=1, trunk_allowed="all",
                  native_vlan=1, admin="up", description="", aliases=None):
    return {
        "mode": mode,
        "access_vlan": access_vlan,
        "trunk_allowed": trunk_allowed,
        "native_vlan": native_vlan,
        "admin": admin,
        "description": description,
        "aliases": list(aliases or []),
    }


def load_state(path):
    with open(path) as f:
        return json.load(f)


def save_state(state, path):
    with open(path, "w") as f:
        json.dump(state, f, indent=2, sort_keys=True)


def clone(state):
    return copy.deepcopy(state)


def vlan_ids(state):
    return sorted(int(v) for v in state["vlans"])


def find_by_alias(state, alias):
    """All interface names whose alias list contains `alias` (case-insensitive)."""
    a = alias.lower()
    return [name for name, iface in state["interfaces"].items()
            if a in [x.lower() for x in iface.get("aliases", [])]]


def resolve_interface(state, ref):
    """Resolve an operator reference to an interface name.

    Returns (name, None) on a unique hit, (None, reason) otherwise, where
    reason is "unknown_interface" or "ambiguous_alias".
    """
    if ref in state["interfaces"]:
        return ref, None
    hits = find_by_alias(state, ref)
    if len(hits) == 1:
        return hits[0], None
    if len(hits) > 1:
        return None, "ambiguous_alias"
    return None, "unknown_interface"
