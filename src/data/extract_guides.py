"""Extractor for Cisco IOS XE configuration guides -> candidate intent/config pairs.

Recipe follows arXiv 2512.02861:
1. Parse config-guide PDFs / HTML for CLI blocks (lines starting with known IOS verbs inside <pre>/code or PDF text blocks).
2. Group contiguous CLI lines into one config snippet.
3. (Separate step) Use an LLM to write the natural-language intent for each snippet - see generate_synthetic.py.
4. Scrub + validate before anything lands in processed/.

This module does step 1-2 only. It does NOT call an LLM and does NOT write to processed/ directly.
Output is JSONL candidates in data/raw/candidates.jsonl for human review.

Usage:
    python -m src.data.extract_guides --input path/to/guide.pdf --family vlan_l2
    python -m src.data.extract_guides --input path/to/guide.html --family ospf
"""
import argparse, json, re
from pathlib import Path

CLI_STARTERS = (
    "vlan ", "interface ", "router ospf", "router bgp", "ip route",
    "switchport", "ip address", "network ", "neighbor ",
    "hostname ", "line vty", "spanning-tree",
)

def extract_from_text(text: str) -> list[str]:
    """Heuristic: contiguous lines that look like IOS config form a block."""
    blocks: list[str] = []
    current: list[str] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        is_cli = any(stripped.startswith(s) for s in CLI_STARTERS) or stripped in ("!", "exit-address-family") or (current and line.startswith(" "))
        if is_cli:
            current.append(line)
        else:
            if len(current) >= 2:
                blocks.append("\n".join(current) + "\n")
            current = []
    if len(current) >= 2:
        blocks.append("\n".join(current) + "\n")
    return blocks

def read_input(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError:
            raise SystemExit("PDF input needs pypdf: pip install pypdf")
        reader = PdfReader(str(path))
        return "\n".join((p.extract_text() or "") for p in reader.pages)
    return path.read_text(errors="ignore")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="PDF or text/HTML guide file")
    ap.add_argument("--family", required=True)
    ap.add_argument("--out", default="data/raw/candidates.jsonl")
    args = ap.parse_args()

    from src.data.scrub import scrub_config
    text = read_input(Path(args.input))
    blocks = extract_from_text(text)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("a") as f:
        for i, cfg in enumerate(blocks):
            cleaned, warns = scrub_config(cfg)
            row = {
                "family": args.family,
                "config_candidate": cleaned,
                "source_ref": str(args.input),
                "scrub_warnings": warns,
                "intent_nl": None,  # filled by generate_synthetic or human
                "needs_review": True,
            }
            f.write(json.dumps(row) + "\n")
    print(f"Extracted {len(blocks)} candidate blocks -> {out_path}")

if __name__ == "__main__":
    main()
