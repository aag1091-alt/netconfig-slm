"""Eval harness - the numbers that go in the model card.

Metrics per family + overall:
- parse_ok %   (ciscoconfparse2 rung 1 on generated config)
- batfish_ok % (rung 2, when harness is wired; reported as null until then - never faked)
- intent keyword coverage: fraction of key tokens from the reference (VLAN ids, IPs, interfaces, AS) present in generation - a cheap proxy until Batfish lands

Compares fine-tuned model vs base model on the SAME held-out test split.

Usage:
    python -m src.eval.evaluate --model outputs/qwen3-4b-netconfig-v0 --split data/processed/test.jsonl --out outputs/eval_4b.json
    python -m src.eval.evaluate --model Qwen/Qwen3-4B-Base --split data/processed/test.jsonl --out outputs/eval_base.json
"""
import argparse, json, re
from collections import defaultdict
from pathlib import Path

KEY_TOKEN = re.compile(r"\b(?:vlan\s*\d+|\d{1,3}(?:\.\d{1,3}){3}(?:/\d+)?|GigabitEthernet\S+|TenGigabitEthernet\S+|AS\s*\d+|\d{5})\b", re.I)

def coverage(ref_config: str, gen_config: str) -> float:
    ref_tokens = set(t.lower() for t in KEY_TOKEN.findall(ref_config))
    if not ref_tokens:
        return 1.0
    gen = gen_config.lower()
    hit = sum(1 for t in ref_tokens if t in gen)
    return hit / len(ref_tokens)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--split", default="data/processed/test.jsonl")
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-new-tokens", type=int, default=512)
    args = ap.parse_args()

    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from src.validate.validate import parse_check

    rows = [json.loads(l) for l in Path(args.split).read_text().splitlines() if l.strip()]
    tok = AutoTokenizer.from_pretrained(args.model)
    model = AutoModelForCausalLM.from_pretrained(args.model, torch_dtype=torch.bfloat16, device_map="auto")
    model.eval()

    per_family = defaultdict(lambda: {"n": 0, "parse": 0, "cov": 0.0})
    details = []
    for row in rows:
        prompt = f"<|im_start|>user\n{row['intent_nl']}<|im_end|>\n<|im_start|>assistant\n"
        inputs = tok(prompt, return_tensors="pt").to(model.device)
        with torch.no_grad():
            out = model.generate(**inputs, max_new_tokens=args.max_new_tokens, do_sample=False)
        gen = tok.decode(out[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True)
        p_ok, _ = parse_check(gen)
        cov = coverage(row["config"], gen)
        fam = per_family[row["family"]]
        fam["n"] += 1; fam["parse"] += int(p_ok); fam["cov"] += cov
        details.append({"id": row["id"], "family": row["family"], "parse_ok": p_ok, "coverage": round(cov, 3), "generated": gen})

    summary = {}
    for fam, s in per_family.items():
        summary[fam] = {"n": s["n"], "parse_ok_pct": round(100 * s["parse"] / s["n"], 1), "mean_coverage": round(s["cov"] / s["n"], 3), "batfish_ok_pct": None}
    total_n = sum(s["n"] for s in per_family.values())
    overall = {
        "n": total_n,
        "parse_ok_pct": round(100 * sum(s["parse"] for s in per_family.values()) / total_n, 1) if total_n else 0,
        "mean_coverage": round(sum(s["cov"] for s in per_family.values()) / total_n, 3) if total_n else 0,
        "batfish_ok_pct": None,
    }
    report = {"model": args.model, "split": args.split, "overall": overall, "per_family": summary, "details": details}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, indent=2))
    print(json.dumps({"overall": overall, "per_family": summary}, indent=2))
    print(f"Full report -> {args.out}")

if __name__ == "__main__":
    main()
