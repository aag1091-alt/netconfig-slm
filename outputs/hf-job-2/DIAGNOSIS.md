# Diagnosis: why run 2 still scored stopped_cleanly 0.0%

**Run:** HF job `6b04b9c9ca8b51043c3a7f19` (a100-large, 2026-11-23), QLoRA Qwen3-4B-Base on dataset v1.0 (4,496 train / 252 test), script `scripts/job_train_eval_v2.py`.
**Outcome:** train loss 0.0447, in-job key-token coverage 0.986, parse 100% — but `stopped_cleanly` **0.0%** (base model: 20.2%), home ladder on raw generations: Batfish 2.8%, intent-match 73.4% raw / 78.6% triaged → publish bar missed.
**Status of this document:** analysis only. No jobs submitted, nothing published. Includes a zero-cost local probe of the shipped GGUF weights (§5, Step 0) executed as part of this diagnosis.

---

## 1. What actually happens at the end of the first config (evidence)

The saved generations (`generations_ft.jsonl`) were decoded with `skip_special_tokens=True`, so real special tokens are invisible in the file — but the failure is fully visible anyway:

- **204/252** generations contain a **second `hostname` line** — a second, hallucinated config. In **172** of those, the second config is for the **same hostname** as the first (a near-repeat of the same device's config); 32 use a different hostname.
- **143/252** generations reproduce the reference config **verbatim** as a prefix; char-level diffing shows the common prefix extends to the *exact end* of the reference config, and the generation then continues with junk. The model reaches the precise context that, in every one of the 4,496 training examples, is followed by `<|im_end|>` — and does not emit it.
- **0/252** generations contain a literal `<|im_end|>`, `<|im_start|>`, or `<|endoftext|>` string. (If the model were spelling the markers out as ordinary text tokens, they would appear here, because `skip_special_tokens=True` only strips true special tokens. It isn't.)
- No generation contains a clean ChatML continuation either: there are **no** `user` turns with an intent and no `assistant` markers after the first config. What follows the config is one of:
  - a few garbage tokens, then a fresh config (`hostname …`),
  - base-model-style prose ("Intialized router soho-rtr1 with the following routing table: …"),
  - degenerate repetition loops ("(reordered and formatted)" interleaved with stray CJK/byte-fragment tokens).

Representative boundary excerpts (text immediately after the reference config's final line; `�` is U+FFFD, i.e. byte-fragment tokens):

```text
ospf-0249:  '��取\n�user\nhostname wan-rtr1\n!\ninterface GigabitEthernet0/0/0\n description OSPF link area 0\n…'
bgp-0763:   '\n�\n\tNdrFchostname\nhostname router4\n!\nrouter bgp 64607\n bgp router-id 10.255.25.153\n…'
ospf-0478:  '\n\tNdrFc\nspNet\nhostname spnet1\n!\ninterface GigabitEthernet0/1/1\n…'
ospf-0763 (no 2nd hostname): '\n��取\n(reordered and formatted)\n 오�\n(reordered and formatted)\n…'
```

### How `stopped_cleanly` is computed, and the eval's generation settings

From `run_eval()` in `job_train_eval_v2.py`:

```python
out = model.generate(**inputs, max_new_tokens=512, do_sample=False,
                     eos_token_id=[151645, 151643], pad_token_id=151643)
stopped_via_eos = any(t in [151645, 151643] for t in gen_ids)
stopped_cleanly = stopped_via_eos and "<|im_start|>" not in raw  # raw decoded with skip_special_tokens=False
```

So a generation counts as clean only if it emitted 151645/151643 (which halts `generate()` immediately, so it can only be the last token) and never emitted 151644 before that. The metric cannot manufacture the observed second configs: the run-on is real model behavior, not a scoring artifact. The **base model, run through the identical harness, stopped cleanly 20.2%** of the time — the harness registers stops when the model emits an EOS id.

## 2. The training path: everything checkable checks out

The v2 hypothesis was "TRL appended the wrong EOS in v1; v2 fixed it." The v2 data path was re-verified four independent ways:

**(a) Local self-check (pre-launch, `docs/train-v2-selfcheck.txt`)** — real TRL 0.25.1 pipeline on the real tokenizer: processed row ends with exactly one 151645; prompt labels all −100; completion (including the final 151645) fully supervised; omitting `processing_class` reproduces the v1 defect ([…, 151645, 151643]).

**(b) Independent re-tokenization of the full train set** (this diagnosis, using the cached Qwen3-4B-Base `tokenizer.json`): all **4,496/4,496** completions end with exactly one token 151645, none with a double. Prompt+completion lengths: p50 = 440 tokens, p95 = 792, max = 1,421.

**(c) TRL 0.25.1 source, read directly** (`trl/trainer/sft_trainer.py`):
- `add_eos`: appends the processing tokenizer's eos to the completion only if it doesn't already end with it → no-op here (completions already end with `<|im_end|>`).
- `tokenize_fn` (non-conversational prompt-completion): tokenizes `prompt + completion` as one string; `completion_mask = [0]*len(prompt) + [1]*len(completion)` — the final 151645 is inside the mask.
- `DataCollatorForLanguageModeling.torch_call`: right-pads, builds labels from `input_ids`, then `labels[completion_mask == 0] = -100` — position-aligned, **no shift**; the EOS position keeps label 151645.
- `SFTConfig` defaults: `packing=False`, `padding_free=False`; the job script sets neither → the plain padded path above is what ran.

**(d) The job's own logs** (retrieved from the HF Jobs log stream for `6b04b9c9ca8b51043c3a7f19`):
- `Adding EOS to train dataset: 4496/4496`, `Tokenizing train dataset: 4496/4496`, `Truncating train dataset: 4496/4496` — the same three steps, on all rows.
- `The tokenizer has new PAD/BOS/EOS tokens … Updated tokens: {'eos_token_id': 151645, 'bos_token_id': None, 'pad_token_id': 151643}` — proof the eos-overridden tokenizer (`processing_class`) was in effect in-job; the training model's config/generation config were aligned to eos 151645.
- Training ran **843 steps** = ⌈4496/16⌉ × 3 epochs exactly, and `num_tokens` ≈ 438/example — the dataset was **not packed** (packing to 1024 would give ~299 steps).
- No prompt/completion tokenization-mismatch warning was logged.

### Ruled out, with numbers

| Suspect | Verdict | Evidence |
|---|---|---|
| Wrong EOS appended (v1 defect) | **Ruled out** | Self-check (b2) reproduces it only when `processing_class` is omitted; job log event above proves the override was active; completions end in exactly one 151645 |
| Packing concatenating examples | **Ruled out** | `packing=False` default; 843 steps = unpacked row count |
| `max_length=1024` truncation eating the EOS | **Ruled out as primary cause** | Only **50/4,496 rows (1.11%)** exceed 1024 tokens (p95 = 792). A 1% effect cannot produce a 100% failure. (Still worth fixing — see §4.) |
| Completion mask excluding the EOS | **Ruled out** | TRL 0.25.1 source (above) + self-check labels (`last non-masked label: 151645`) |
| EOS spelled as text tokens instead of id 151645 | **Ruled out** | Tokenizer encodes the string as id 151645 (verified on all 4,496 rows); generations contain zero literal `<|im_end|>` strings |
| Multi-config training rows teaching config→config | **Ruled out** | Every train/test config contains exactly one `hostname` line; all end with a single trailing `\n` |
| Eval harness / generation settings | **Ruled out** | Greedy, `eos_token_id=[151645, 151643]` passed explicitly; base model stopped 20.2% under the identical harness |
| Library version divergence in-job | **No evidence** | RUN_INFO/train_log don't record versions (a process gap — see §4), but the in-job log steps match TRL 0.25.1's exactly, and the launch pins were resolved to trl 0.25.1 / transformers 4.57.6 / peft 0.18.1 / datasets 3.6.0 in the same-day self-check |
| Dataset not the intended one | **Ruled out** | RUN_INFO + job log: sha `ecf3d5a9a67a…` = v1.0, 4,496 train rows (guard passed) |

## 3. Ranked hypotheses for what actually failed

The failure is **total (252/252), uniform across all six families, and localized to a single token position**. The local GGUF probe (§5, Step 0) measured the shipped weights directly and found the boundary distribution is **flat and context-independent**: top-1 candidate probability 0.00048, the top 50 candidates all within 0.00043–0.00048, `<|im_end|>` absent from the top 50 (P ≲ 4×10⁻⁴, vs ≈ 1.0 expected), and the *identical* top-10 junk tokens for two unrelated configs — while a mid-config position in the same model has top-1 probability **1.0000**. Aggregate train loss cannot exonerate the EOS position: at ~1 supervised EOS per ~300 completion tokens, even a flat EOS distribution (loss ≈ 5) would add only ≈ 0.017 to the average — inside the observed 0.045.

**H1 (top) — In the training that actually executed, the EOS position contributed no effective gradient, despite the verified-correct pipeline.**
The probe's signature — a boundary distribution with *zero* residual EOS elevation and no dependence on the preceding config — is what a never-trained position looks like: the model's output at "end of config" collapses to a context-free junk attractor (the same tokens, `\tNdrFc`, `��取`, `useRalative`, … in the same order, for any config). Something in the executed stack nullified the EOS supervision at scale, in a way invisible to (a) the TRL 0.25.1 source, (b) the single-row self-check, and (c) the job logs. Candidate loci, unresolved post hoc: a batched-collator/labels divergence that only manifests in real multi-row batches; or an in-job library-version behavior differing from the self-check environment (the run logged no versions, so this can never be settled retroactively — itself a finding, addressed in §4.3).
*Confirm/refute:* the probe job (§5, Step 1) asserts, in-job and pre-training, that a real collated batch has `labels[last non-pad position] == 151645` on every row, logs `pip freeze`, and then evaluates the trained adapter. If the assertion passes and the adapter still doesn't stop, the loss is between labels and gradients in the executed stack, and the recipe must change formulation (§4.2), not plumbing. If the assertion *fails* in-job, the divergence is found and can be fixed directly.

**H2 — The EOS behavior was learned by the adapter but annihilated when the adapter was merged into the bf16 base** (the merged model is the only artifact ever evaluated, in both failed runs; the GGUF inherits the merge).
Demoted by the probe: bf16 merging of LoRA adapters is the standard GGUF pipeline worldwide and routinely preserves stopping behavior; and rounding would be expected to leave a *trace* — 151645 attenuated but still elevated above the flat background. Observed P(151645) is indistinguishable from rank-50 junk (≲ 4×10⁻⁴). Not excluded — the adapter was never evaluated — and the probe job bisects it at no extra cost.
*Confirm:* probe job evaluates the adapter on its 4-bit base and the merged model side by side; adapter stops + merged doesn't ⇒ H2 proven, fix is §4.1. *Refute:* adapter also ~0%.

**H3 — EOS supervision reached the loss but was too weak to learn in this configuration** (single token per ~440-token sequence; LoRA r16, frozen embeddings/lm_head, base model).
Demoted: a partially learned boundary would still be context-dependent and would place 151645 somewhere in the top ranks; observed is a context-independent flat distribution. Also, standard QLoRA fine-tunes learn EOS under near-identical budgets. Retained because it is the fallback if H1's in-job assertions all pass and H2 is refuted — in that case the remedy is §4.2 (oversample/shorten the boundary signal), applied blind.

## 4. The fix

Do not re-run the same recipe with another token-level tweak — v1→v2 already spent one full run on that. Change the formulation so stopping is over-determined, and never evaluate only the merged artifact:

1. **Evaluate the adapter (on its 4-bit base) AND the merged model in every future run**, and report both. If H2 (merge loss) is confirmed, merge in **float32** (load base in fp32 on CPU for the merge, save bf16) or ship the adapter + base instead of a merged model; the GGUF must then be rebuilt from the corrected merge and re-probed.
2. **Make the EOS signal unmissable in training:**
   - Keep prompt/completion + `processing_class` + eos override (verified correct), and add a **pre-training assertion** (in-job): after dataset processing and collation of a real batch, assert every row's last non-pad label is 151645 — fail the run loudly if not, instead of discovering it after 4 hours.
   - **Oversample the boundary:** add a small auxiliary set (e.g. 10–15% of rows) of *short* completions (single-block configs) so EOS occurs at varied, frequent positions; and/or append a plain-text terminator line that Cisco configs naturally end with — `end` — as the final config line before `<|im_end|>`, giving the model a high-frequency text token as a stepping stone to the special token.
   - Raise `max_length` to **1536** so the 50 truncated rows (1.11%) keep their EOS too.
3. **Log the environment**: `pip freeze` + TRL/transformers versions into RUN_INFO.json at job start; log the in-job processed-dataset sample (last 5 token ids + last label) for row 0.
4. **Gate on stopping, in-job, before scoring**: after training, run the 252-row eval on the adapter model first; if `stopped_cleanly` < 90%, abort before merge/scoring and say so — don't spend the wrap-up budget rediscovering it at home.

## 5. Verification probe (run this BEFORE any full retrain)

**Step 0 (zero cost — DONE as part of this diagnosis): teacher-forced boundary probe on the existing GGUF.**
The run-2 GGUF (2,497,280,288 bytes, converted from the merged weights) was downloaded from the bucket and run locally under llama.cpp (b12669, CPU, greedy). Prompts were fed as exact token ids (cached Qwen3 tokenizer), so there is no tokenization ambiguity. Results (raw outputs in `outputs/hf-job-2/diag/probeB_result.json` and `probe_more.json`):

- **Boundary distribution** (eval prompt + full reference config of held-out row `iface-0061`, i.e. the exact context followed by 151645 in all 4,496 training rows): top-1 = token 70564 `'\tNdrFc'` at **p = 0.00048**; top-50 candidates all within **0.00043–0.00048**; **151645 not in the top 50** → P(`<|im_end|>`) ≲ 4×10⁻⁴ (a trained boundary would be ≈ 1.0).
- **Same probe on `ospf-0249`** (different family, longer config): the top-10 is the *identical token list in the identical order* (70564 `'\tNdrFc'`, 27487 `'��取'`, 88372 `'useRalative'`, …) — the boundary distribution does not depend on the config at all.
- **Control, mid-config position** (prompt + first half of the same config): top-1 probability **1.0000**. The weights are sharp everywhere except the boundary.
- **Free generation** from the `iface-0061` eval prompt reproduces the failure end-to-end on CPU: the exact reference config, then `'\tNdrFc\nIntialized router2\nhostname router2\n…'` degenerating into `'(Initialized router2)'` loops. The junk tokens are the *same ones* seen in the in-job A100 generations (`'\tNdrFc'` in bgp-0763/ospf-0478, `'Intialized …'` in static-0220) — the merged weights, the GGUF, and the in-job eval all agree; there is no eval-harness or conversion artifact left to blame.

**Step 1 (the cheap training probe): one HF Job, A10G-small ($1.00/hr).**
A stripped-down script (`job_probe_v3.py`) that:
- loads **256 train rows** (stratified across families, all ≤1024 tokens) + 24 held-out prompts;
- dumps one real collated batch and asserts the EOS label in-job (tests H1's loci directly, with `pip freeze` logged);
- trains the identical QLoRA recipe for **1 epoch** (~16 steps — minutes of GPU time);
- evaluates **adapter-on-4-bit-base** and **fp32-merged** model side by side: greedy generation on the 24 prompts, `stopped_cleanly` for each (bisects H2);
- writes results to the bucket and exits. No merge to bucket, no GGUF, no scoring ladder.

Expected wall time ≈ 20–30 min including pip install and model downloads → **≈ $0.35–0.50**. Even with a retry, it stays under $1. (For reference: T4-small at $0.40/hr would also work and cost ≈ $0.15–0.25, but 4-bit 4B training + two eval passes is materially slower there; A10G-small is the safer sub-$1 choice. The full retrain it gates cost $10.28.)

**Decision rule:** proceed to a full retrain only if the probe's chosen artifact (adapter or fp32-merge) stops cleanly on ≥ 90% of the 24 probe prompts. If neither stops, the boundary-oversampling/terminator changes in §4.2 get folded in and the probe repeats — still cheaper than one more blind full run.

---

*Evidence files: `outputs/hf-job-2/generations_ft.jsonl`, `eval_ft.json`, `train_log.json`, `RUN_INFO.json`, `SCORED.json`; `docs/train-v2-selfcheck.txt`; TRL 0.25.1 source read from the official sdist (copy under `outputs/hf-job-2/diag/trl-0.25.1/`); job log stream for `6b04b9c9ca8b51043c3a7f19` (HF Jobs API); GGUF probe raw outputs under `outputs/hf-job-2/diag/` (`probeB_result.json`, `probe_more.json`, `probe_ids.json`, `probe_pick.json`). The downloaded GGUF and llama.cpp binaries were removed after probing; the GGUF remains in the bucket at `run2/gguf/`.*
