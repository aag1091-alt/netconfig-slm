#!/usr/bin/env python3
"""Fixture tests for the coverage generator's pure logic: rollup
math, leaf dedupe, evidence extraction, and unknown/empty handling.

Run:  python3 -m unittest discover -s coverage/tests -v
(No pyang needed — these exercise the pure functions only.)
"""
import importlib.util
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "generate_coverage", HERE.parent / "generate_coverage.py")
gc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gc)


def leaf(path, eligible=True, splits=(), status="current", config=True):
    return {"path": path, "kind": "leaf", "config": config,
            "status": status, "file": "M.yang", "eligible": eligible,
            "splits": set(splits), "pass_n": 0, "pass_p": 0,
            "evidence": {}, "priority": None, "split_mask": 0}


def tree_of(leaves):
    root = gc.build_tree({l["path"]: l for l in leaves})
    gc.rollup(root)
    return root


class RollupMath(unittest.TestCase):
    def test_parent_pct_from_descendant_counts_not_average(self):
        # Child A: 1/2 = 50%. Child B: 9/10 = 90%. Averaging child
        # percentages gives 70%; the correct count-based rollup is
        # 10/12 = 83.33%.
        leaves = [leaf("/n/a/x", splits={"train"})]
        leaves += [leaf(f"/n/a/y{i}") for i in range(1)]
        leaves += [leaf(f"/n/b/z{i}", splits={"train"}) for i in range(9)]
        leaves += [leaf("/n/b/w")]
        root = tree_of(leaves)
        n = root.children["n"]
        self.assertEqual(n.total, 12)
        self.assertEqual(n.covered["train"], 10)
        self.assertEqual(gc.pct(n.covered["train"], n.total), 83.33)
        self.assertNotEqual(gc.pct(n.covered["train"], n.total), 70.0)

    def test_counts_sum_through_levels(self):
        leaves = [leaf("/a/b/c/d", splits={"train", "benchmark"}),
                  leaf("/a/b/e", splits={"dev"}),
                  leaf("/a/f", splits=set())]
        root = tree_of(leaves)
        a = root.children["a"]
        self.assertEqual((a.total, a.covered["train"],
                          a.covered["benchmark"], a.covered["dev"]),
                         (3, 1, 1, 1))
        self.assertEqual(a.covered["corpus"], 2)  # train + dev leaves

    def test_excluded_leaves_not_in_denominator(self):
        leaves = [leaf("/a/x", splits={"train"}),
                  leaf("/a/y", eligible=False, status="obsolete",
                       splits={"train"}),
                  leaf("/a/z", eligible=False, config=False)]
        root = tree_of(leaves)
        a = root.children["a"]
        self.assertEqual(a.total, 1)
        self.assertEqual(a.excluded, 2)
        self.assertEqual(a.covered["train"], 1)

    def test_empty_subtree_pct_is_none_not_zero(self):
        # A subtree whose leaves are all excluded has total 0: its
        # percentage is unknown/empty (None), never 0%.
        leaves = [leaf("/a/x", eligible=False, status="obsolete")]
        root = tree_of(leaves)
        a = root.children["a"]
        self.assertEqual(a.total, 0)
        self.assertIsNone(gc.pct(a.covered["train"], a.total))
        # ...while a genuinely uncovered eligible leaf is a real 0%.
        leaves2 = [leaf("/a/x")]
        a2 = tree_of(leaves2).children["a"]
        self.assertEqual(gc.pct(a2.covered["train"], a2.total), 0.0)


class Dedupe(unittest.TestCase):
    def test_duplicate_leaf_path_counted_once(self):
        # build_tree keys leaves by path: a second record for the
        # same canonical path (e.g. two choice cases collapsing to
        # one transparent path) replaces, never double-counts.
        d = {"/a/x": leaf("/a/x", splits={"train"})}
        d["/a/x"] = leaf("/a/x", splits={"train"})
        root = gc.build_tree(d)
        gc.rollup(root)
        self.assertEqual(root.children["a"].total, 1)

    def test_sr_overlapping_paths_dedupe(self):
        rec = {"family": "state_reconstruction", "fact": {
            "changed_paths": ["/Cisco-IOS-XE-native:native/interface/"
                              "GigabitEthernet[0]/description"],
            "preserved_paths": ["/Cisco-IOS-XE-native:native/interface/"
                                "GigabitEthernet[0]/description",
                                "/Cisco-IOS-XE-native:native/interface/"
                                "GigabitEthernet[0]/name"]}}
        paths, _ = gc.extract_record_paths(rec)
        self.assertEqual(paths, {
            "/native/interface/GigabitEthernet/description",
            "/native/interface/GigabitEthernet/name"})


class EvidenceRules(unittest.TestCase):
    def test_typed_edit_covers_only_changed_path(self):
        rec = {"family": "typed_edit", "fact": {
            "edits": [{"path": "/native/interface/GigabitEthernet"
                               "/switchport-config/switchport/access"
                               "/vlan/vlan", "old": 10, "new": 20}],
            "edited_path": "/native/interface/GigabitEthernet"
                           "/switchport-config/switchport/access"
                           "/vlan/vlan",
            "before_file": "instances/x-before.json"}}
        paths, _ = gc.extract_record_paths(rec)
        self.assertEqual(paths, {"/native/interface/GigabitEthernet"
                                 "/switchport-config/switchport/access"
                                 "/vlan/vlan"})

    def test_selector_negative_uses_attempted_path(self):
        rec = {"family": "typed_edit", "fact": {
            "error": "target_not_found",
            "attempted_edits": [{"path": "/native/vlan"
                                         "/configuration-entry/vlan-id"}]}}
        paths, _ = gc.extract_record_paths(rec)
        self.assertEqual(paths, {"/native/vlan/configuration-entry"
                                 "/vlan-id"})

    def test_iv_uses_judged_node_not_bystanders(self):
        rec = {"family": "instance_validity",
               "fact": {"expected_node": "x"},
               "source": {"modules": [{"paths": [
                   "/native/interface/GigabitEthernet/switchport-config"
                   "/switchport/access/vlan/vlan"]}]}}
        paths, _ = gc.extract_record_paths(rec)
        self.assertEqual(len(paths), 1)

    def test_canon_instance_path(self):
        self.assertEqual(
            gc.canon_instance_path(
                "/Cisco-IOS-XE-native:native/interface/"
                "GigabitEthernet[12]/switchport-config/switchport/"
                "Cisco-IOS-XE-switch:access/Cisco-IOS-XE-switch:vlan/"
                "Cisco-IOS-XE-switch:vlan"),
            "/native/interface/GigabitEthernet/switchport-config"
            "/switchport/access/vlan/vlan")
        self.assertEqual(
            gc.canon_instance_path(
                "/interface/GigabitEthernet[1]/switchport-config"
                "/switchport/trunk/native/vlan/vlan-id (added)"),
            "/native/interface/GigabitEthernet/switchport-config"
            "/switchport/trunk/native/vlan/vlan-id")


class Priorities(unittest.TestCase):
    def test_glob_and_first_match(self):
        groups = [
            {"id": "a", "patterns": ["/native/interface/*/description"]},
            {"id": "b", "patterns": ["/native/interface/**"]},
        ]
        self.assertEqual(gc.assign_priority(
            "/native/interface/GigabitEthernet/description", groups), "a")
        self.assertEqual(gc.assign_priority(
            "/native/interface/GigabitEthernet/switchport-config/x",
            groups), "b")
        self.assertIsNone(gc.assign_priority("/native/vlan/x", groups))
        self.assertFalse(gc.glob_match(
            "/native/interface/*/description",
            "/native/interface/GigabitEthernet/a/b"))


if __name__ == "__main__":
    unittest.main()
