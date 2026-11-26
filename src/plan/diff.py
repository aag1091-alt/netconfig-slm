"""Diff two device states into a sorted list of changed leaf paths.

Path grammar (used by the generator invariant, the eval set, and scorers):

    vlans.<id>                      VLAN created or deleted
    vlans.<id>.name                 VLAN renamed
    interfaces.<name>.<field>       one interface leaf changed
    interfaces.<name>               interface created or deleted

`trunk_allowed` is compared as a whole leaf ("all" vs list is one change).
"""
from .state import clone


def _diff_vlans(a, b, out):
    for vid in sorted(set(a) | set(b), key=lambda v: int(v)):
        if vid not in a or vid not in b:
            out.append(f"vlans.{vid}")
        elif a[vid].get("name") != b[vid].get("name"):
            out.append(f"vlans.{vid}.name")


def _diff_interfaces(a, b, out):
    for name in sorted(set(a) | set(b)):
        if name not in a or name not in b:
            out.append(f"interfaces.{name}")
            continue
        ia, ib = a[name], b[name]
        for field in sorted(set(ia) | set(ib)):
            if ia.get(field) != ib.get(field):
                out.append(f"interfaces.{name}.{field}")


def diff_states(before, after):
    """Sorted list of changed leaf paths between two states."""
    out = []
    if before.get("device") != after.get("device"):
        out.append("device")
    if before.get("hostname") != after.get("hostname"):
        out.append("hostname")
    _diff_vlans(before.get("vlans", {}), after.get("vlans", {}), out)
    _diff_interfaces(before.get("interfaces", {}), after.get("interfaces", {}), out)
    return sorted(out)


def path_touched(changed, path):
    """True if `path` or anything under it appears in the changed list."""
    return any(c == path or c.startswith(path + ".") for c in changed)


def snapshot(state):
    return clone(state)
