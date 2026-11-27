#!/usr/bin/env python3
"""Tokenize the yang-learn corpus with the pinned Qwen3 tokenizer.

Tokenizer: Qwen/Qwen3-4B-Base @ 906bfd4b4dc7f14ee4320094d8b41684abff8539
(tokenizer.json fetched from the Hub at that revision). Reports
prompt/completion tokens per split, length maxima, and truncation
counts at max_len 1024 (the run-3 training limit). Writes the stats
into corpus/manifest.json under "tokens" and prints a summary.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from tokenizers import Tokenizer

HERE = Path(__file__).resolve().parent
TOK_FILE = Path("/tmp/qwen-tok/tokenizer.json")
MAX_LENS = (1024, 1536)  # 1536 = run-3 training cap; 1024 for reference


def main() -> int:
    tok = Tokenizer.from_file(str(TOK_FILE))
    corpus_dir = HERE / "corpus"
    stats = {"tokenizer": "Qwen/Qwen3-4B-Base @ 906bfd4b",
             "max_lens": list(MAX_LENS), "splits": {}}
    for name in ("train", "dev", "test", "benchmark"):
        path = corpus_dir / f"{name}.jsonl"
        if not path.exists():
            continue
        recs = [json.loads(l) for l in path.read_text().splitlines()]
        p_tok = c_tok = 0
        max_total = 0
        truncated = {ml: 0 for ml in MAX_LENS}
        per_family = {}
        for r in recs:
            p = len(tok.encode(r["prompt"]).tokens)
            c = len(tok.encode(r["completion"]).tokens)
            p_tok += p
            c_tok += c
            max_total = max(max_total, p + c)
            for ml in MAX_LENS:
                if p + c > ml:
                    truncated[ml] += 1
            f = per_family.setdefault(r["family"], [0, 0, 0])
            f[0] += 1
            f[1] += p
            f[2] += c
        stats["splits"][name] = {
            "records": len(recs), "prompt_tokens": p_tok,
            "completion_tokens": c_tok,
            "total_tokens": p_tok + c_tok,
            "max_record_tokens": max_total,
            "records_over_max_len": {str(k): v
                                     for k, v in truncated.items()},
            "per_family": {k: {"records": v[0], "prompt_tokens": v[1],
                               "completion_tokens": v[2]}
                           for k, v in sorted(per_family.items())}}
        s = stats["splits"][name]
        print(f"{name}: {s['records']} records, "
              f"{s['total_tokens']} tokens "
              f"(prompt {p_tok} + completion {c_tok}), "
              f"max {max_total}, over 1536: {truncated[1536]}, "
              f"over 1024: {truncated[1024]}")
    manifest_path = corpus_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["tokens"] = stats
    manifest_path.write_text(json.dumps(manifest, indent=1) + "\n")
    print("manifest updated with token stats")
    return 0


if __name__ == "__main__":
    sys.exit(main())
