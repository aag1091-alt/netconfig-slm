"""Validation ladder rung 1 + 2.

Rung 1: ciscoconfparse2 syntax parse.
Rung 2: Batfish semantic checks via pybatfish against a Batfish server
        (Docker on Avinash's Mac, reached over Tailscale; Batfish is
        Docker-only upstream - no jar distribution anymore).

Usage:
    python -m src.validate.validate --input data/examples/seed.jsonl
    python -m src.validate.validate --input data/examples/seed.jsonl \
        --batfish-host <mac-tailnet-address>

Note: from the Muse VM, tailnet traffic goes through the runtime proxy,
so run with HTTP_PROXY set to the tunnel proxy (port 3130) - pybatfish
(requests) honors it.
"""
import argparse, json, sys
from pathlib import Path

def parse_check(config: str) -> tuple[bool, str]:
    try:
        from ciscoconfparse2 import CiscoConfParse
    except ImportError:
        return False, "ciscoconfparse2 not installed - pip install ciscoconfparse2"
    try:
        parse = CiscoConfParse(config.splitlines(), syntax="ios")
        objs = getattr(parse, "objs", None) or []
        real = [o for o in objs if getattr(o, "text", "").strip() not in ("", "!")]
        if not real:
            return False, "parse produced no config objects"
        return True, f"parse ok ({len(real)} config lines)"
    except Exception as e:
        return False, f"parse failed: {e}"

def make_batfish_session(host: str | None, proxy: str | None):
    """One shared pybatfish session (question-template fetch is slow, so
    create once). Returns None when rung 2 is skipped."""
    if not host:
        return None
    from pybatfish.client.session import Session
    proxies = {"http": proxy, "https": proxy} if proxy else None
    bf = Session(host=host, timeout=180, proxies=proxies)
    bf.set_network("netconfig-slm")
    return bf

def batfish_check(row: dict, bf) -> tuple[bool | None, str]:
    """Upload the row's config as a one-device Batfish snapshot and check
    Batfish's own parse status + init issues. Returns None if not run."""
    if bf is None:
        return None, "batfish not run (no --batfish-host)"
    try:
        # platform="cisco": we know the target is IOS XE, so tell Batfish
        # instead of relying on its vendor guesser - snippet-style configs
        # (e.g. routing-only) otherwise come back "File format is unknown".
        bf.init_snapshot_from_text(
            row["config"], filename=f"{row['id']}.cfg",
            snapshot_name=row["id"], overwrite=True, platform="cisco",
        )
        status = bf.q.fileParseStatus().answer().frame()
        issues = bf.q.initIssues().answer().frame()
        if status.empty:
            return False, "batfish returned no parse status"
        srow = status.iloc[0]
        pstat = str(srow.get("Status", srow.get("Parse_Status", "UNKNOWN")))
        fmt = str(srow.get("File_Format", "?"))
        n_issues = 0 if issues is None else len(issues)
        if pstat != "PASSED":
            return False, f"batfish status {pstat} ({fmt}), {n_issues} init issue(s)"
        if n_issues:
            types = "; ".join(sorted({str(t) for t in issues["Type"]})) if "Type" in issues else "?"
            return False, f"batfish PASSED ({fmt}) but {n_issues} init issue(s): {types}"
        return True, f"batfish PASSED ({fmt}), 0 init issues"
    except Exception as e:
        return None, f"batfish unreachable/error: {e}"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--batfish-host", default=None,
                    help="Batfish server host (e.g. Mac tailnet address); omit to skip rung 2")
    ap.add_argument("--batfish-proxy", default=None,
                    help="HTTP proxy for reaching the Batfish host (Muse VM: tunnel proxy :3130)")
    args = ap.parse_args()
    path = Path(args.input)
    bf = None
    if args.batfish_host:
        try:
            bf = make_batfish_session(args.batfish_host, args.batfish_proxy)
        except Exception as e:
            print(f"batfish session failed ({e}); rung 2 will report not-run")
    ok = fail = bok = 0
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        p_ok, p_msg = parse_check(row["config"])
        b_ok, b_msg = batfish_check(row, bf)
        status = "PASS" if p_ok else "FAIL"
        print(f"{row['id']:14} {row['family']:16} parse={status} ({p_msg}) batfish={b_ok} ({b_msg})")
        ok += int(p_ok); fail += int(not p_ok); bok += int(b_ok is True)
    print(f"\nTotal: {ok} parse-pass, {fail} parse-fail, {bok} batfish-pass")
    sys.exit(1 if fail else 0)

if __name__ == "__main__":
    main()
