# Run configuration — frozen

- **Checkpoint:** `Qwen/Qwen3-4B-Base` (the base checkpoint the Phase 1
  fine-tune started from; the pilot measures prompting, not the
  fine-tuned model).
- **Revision:** `906bfd4b4dc7f14ee4320094d8b41684abff8539`
  (HF Hub, verified via the Hub API on 2026-11-26).
- **Arms:** A = no schema packet; B = `schema_packet.txt` inserted
  verbatim. Identical system prompt, item text, and output contract.
- **Decoding:** greedy (`do_sample=False`), no temperature/top-p.
  Chat template applied (`apply_chat_template`, generation prompt on).
- **Token budgets (max_new_tokens):** R 256, E 384, P 512.
- **Items:** 34 × 2 arms = 68 generations. Prompts are content-hashed
  per (item, arm) into each raw-output record (`prompt_sha256`).
- **Runtime plan:** one HF Job (GPU, bfloat16) that clones this repo at
  the freeze commit and runs `run_pilot.py`; outputs land in
  `pilot/results/raw_outputs.jsonl`. Estimated job cost: under $2
  (68 short generations + model load).
- **Scoring:** `score_pilot.py`, offline, deterministic, no model calls.
- **Hold compliance:** no dataset generation, no training, no other
  repo changes are part of this pilot.
