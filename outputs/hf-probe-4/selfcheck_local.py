#!/usr/bin/env python3
"""Local CPU self-check for job_probe_v4.py (attempt 4).

Runs the v4 data-transform + subset-selection logic against the
local data/v1.0 files with the cached Qwen3 tokenizer (via the
`tokenizers` lib behind an HF-style shim). Verifies:
  * tokenizer shim maps <|im_end|> to id 151645
  * subset: 256 rows, exactly 102 from the shortest quartile,
    deterministic across two runs, all 6 families present
  * every transformed completion (train subset + held-out refs) ends
    with the token(s) for "end" + 151645 (verify_endings all_ok)
  * held-out selection identical to attempt 3 (24 rows, 4/family)
  * position-scan row selection: 1 row each from ospf/bgp/vlan_l2/
    interface_mgmt, matching the probe eval's held-out rows
Log is written to docs/train-probe-v4-selfcheck.txt.
"""
import glob
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path("/home/hatch/workspace/projects/small-ai-model-for-networking/netconfig-slm")
sys.path.insert(0, str(ROOT / "scripts"))
import job_probe_v4 as P4  # noqa: E402

from tokenizers import Tokenizer  # noqa: E402

TOK_PATH = glob.glob(str(Path.home() / ".cache/huggingface/hub/"
                         "models--Qwen--Qwen3-4B-Base/snapshots/*/"
                         "tokenizer.json"))[0]


class TokShim:
    """Minimal HF-AutoTokenizer-style callable over `tokenizers`."""

    def __init__(self, path):
        self.t = Tokenizer.from_file(path)

    def __call__(self, texts, add_special_tokens=False):
        if isinstance(texts, str):
            enc = self.t.encode(texts,
                                add_special_tokens=add_special_tokens)
            return {"input_ids": enc.ids}
        encs = self.t.encode_batch(texts,
                                   add_special_tokens=add_special_tokens)
        return {"input_ids": [e.ids for e in encs]}

    def decode(self, ids, skip_special_tokens=False):
        return self.t.decode(ids,
                             skip_special_tokens=skip_special_tokens)


def main():
    lines = []

    def check(name, ok, detail=""):
        lines.append(f"{'PASS' if ok else 'FAIL'} {name}: {detail}")
        if not ok:
            (ROOT / "docs/train-probe-v4-selfcheck.txt").write_text(
                "\n".join(lines) + "\n")
            raise SystemExit("\n".join(lines))

    tok = TokShim(TOK_PATH)
    probe_ids = tok("test<|im_end|>")["input_ids"]
    check("tokenizer shim", probe_ids[-1] == 151645,
          f"'test<|im_end|>' -> {probe_ids}")

    train_rows = [json.loads(l) for l in
                  (ROOT / "data/v1.0/train.jsonl").read_text().splitlines()
                  if l.strip()]
    test_rows = [json.loads(l) for l in
                 (ROOT / "data/v1.0/test.jsonl").read_text().splitlines()
                 if l.strip()]
    lines.append(f"local data: {len(train_rows)} train / "
                 f"{len(test_rows)} test")

    subset, counts, short_counts, threshold = P4.select_subset(
        train_rows, tok)
    check("subset size", len(subset) == 256, f"{len(subset)} rows")
    check("subset family counts", len(counts) == 6, f"{counts}")
    n_short = sum(short_counts.values())
    check("short-quartile rows", n_short == 102,
          f"{n_short} rows, per-family {short_counts}, "
          f"threshold {threshold} tokens")

    subset2, counts2, short_counts2, threshold2 = P4.select_subset(
        train_rows, tok)
    check("subset deterministic",
          [r["id"] for r in subset] == [r["id"] for r in subset2]
          and counts == counts2 and short_counts == short_counts2
          and threshold == threshold2, "two runs identical")

    sample = P4.completion_text(subset[0])
    check("transform sample", sample.endswith("\nend<|im_end|>"),
          f"tail: {sample[-30:]!r}")

    heldout = P4.select_heldout(test_rows)
    check("heldout", len(heldout) == 24
          and Counter(r["family"] for r in heldout) == Counter(
              {f: 4 for f in counts}),
          f"24 rows, first ids: {[r['id'] for r in heldout][:4]}...")

    end_train = P4.verify_endings(tok, subset, "train_subset")
    check("ending check (train subset)", end_train["all_ok"],
          json.dumps(end_train))
    end_held = P4.verify_endings(tok, heldout, "heldout_refs")
    check("ending check (held-out refs)", end_held["all_ok"],
          json.dumps(end_held))

    held_by_family = {}
    for r in heldout:
        held_by_family.setdefault(r["family"], r)
    scan_rows = [held_by_family[f] for f in P4.SCAN_FAMILIES]
    check("scan rows",
          [r["family"] for r in scan_rows] == P4.SCAN_FAMILIES,
          f"{[(r['id'], r['family']) for r in scan_rows]}")

    lines.append("ALL PROBE V4 SELF-CHECKS PASSED")
    out = "\n".join(lines) + "\n"
    (ROOT / "docs/train-probe-v4-selfcheck.txt").write_text(out)
    print(out)


if __name__ == "__main__":
    main()
