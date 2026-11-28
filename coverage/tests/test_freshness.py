#!/usr/bin/env python3
"""Fixture tests for the coverage freshness fingerprints: the
recorded-vs-current comparison that keeps a stale committed
coverage.json from being republished as if it were current.

Run:  python3 -m unittest discover -s coverage/tests -v
(No pyang needed — fingerprints hash files, nothing more.)
"""
import importlib.util
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gc = _load("generate_coverage", HERE.parent / "generate_coverage.py")
cf = _load("check_freshness", HERE.parent / "check_freshness.py")

SPLIT_RELS = list(gc.SPLIT_FILES.values())


def make_repo(tmp: Path):
    """A minimal repo + bundle layout with every fingerprinted
    input present."""
    repo = tmp / "repo"
    bundle = tmp / "bundles" / "xe-2621"
    for rel in SPLIT_RELS:
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('{"id": "r1"}\n{"id": "r2"}\n', encoding="utf-8")
    (repo / gc.SCORES_FILE).parent.mkdir(parents=True, exist_ok=True)
    (repo / gc.SCORES_FILE).write_text("{}", encoding="utf-8")
    (repo / "coverage" / "priorities.yaml").write_text(
        "- id: x\n", encoding="utf-8")
    bundle.mkdir(parents=True)
    (bundle / "yang-set-cat9k.xml").write_text("<xml/>\n",
                                               encoding="utf-8")
    (bundle / "Cisco-IOS-XE-foo.yang").write_text("module foo {}\n",
                                                  encoding="utf-8")
    return repo, bundle


class Freshness(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo, self.bundle = make_repo(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def test_fingerprints_deterministic(self):
        a = gc.compute_fingerprints(self.repo, self.bundle)
        b = gc.compute_fingerprints(self.repo, self.bundle)
        self.assertEqual(a, b)
        self.assertEqual(a["split_counts"]["train"], 2)

    def test_clean_compare_has_no_problems(self):
        fp = gc.compute_fingerprints(self.repo, self.bundle)
        self.assertEqual(cf.compare_fingerprints(fp, fp), [])

    def test_corpus_change_detected(self):
        recorded = gc.compute_fingerprints(self.repo, self.bundle)
        train = self.repo / gc.SPLIT_FILES["train"]
        train.write_text(train.read_text(encoding="utf-8")
                         + '{"id": "r3"}\n', encoding="utf-8")
        current = gc.compute_fingerprints(self.repo, self.bundle)
        problems = cf.compare_fingerprints(recorded, current)
        self.assertTrue(any("train.jsonl" in p for p in problems),
                        problems)
        self.assertTrue(any("split record counts" in p
                            for p in problems), problems)

    def test_bundle_change_detected(self):
        recorded = gc.compute_fingerprints(self.repo, self.bundle)
        (self.bundle / "Cisco-IOS-XE-foo.yang").write_text(
            "module foo { // changed\n}\n", encoding="utf-8")
        current = gc.compute_fingerprints(self.repo, self.bundle)
        problems = cf.compare_fingerprints(recorded, current)
        self.assertTrue(any("bundle_digest" in p for p in problems),
                        problems)

    def test_scores_change_detected(self):
        recorded = gc.compute_fingerprints(self.repo, self.bundle)
        (self.repo / gc.SCORES_FILE).write_text('{"x": 1}',
                                               encoding="utf-8")
        current = gc.compute_fingerprints(self.repo, self.bundle)
        problems = cf.compare_fingerprints(recorded, current)
        self.assertTrue(any("run1_scores.json" in p for p in problems),
                        problems)

    def test_missing_fingerprint_block_is_stale(self):
        current = gc.compute_fingerprints(self.repo, self.bundle)
        problems = cf.compare_fingerprints(None, current)
        self.assertEqual(len(problems), 1)
        self.assertIn("no meta.fingerprint", problems[0])


if __name__ == "__main__":
    unittest.main()
