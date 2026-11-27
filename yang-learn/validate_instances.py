#!/usr/bin/env python3
"""Independent instance validation with yangson.

Verdicts here come ONLY from yangson (an implementation independent of
the pyang-based extraction in schema_tools.py). The data model is built
from the source .yang modules + the scoped library on every run — no
cached or serialized model is loaded.

Usage:
  validate_instances.py --bundle DIR --library FILE instance.json [...]
Prints one JSON line per instance: {file, verdict, detail}.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from yangson.datamodel import DataModel


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", required=True)
    ap.add_argument("--library", required=True)
    ap.add_argument("instances", nargs="+")
    args = ap.parse_args()

    dm = DataModel.from_file(args.library, mod_path=[args.bundle])
    for path in args.instances:
        raw = json.loads(Path(path).read_text())
        try:
            inst = dm.from_raw(raw)
            inst.validate()
            print(json.dumps({"file": Path(path).name, "verdict": "valid",
                              "detail": None}))
        except Exception as e:  # yangson raises typed validation errors
            msg = f"{type(e).__name__}: {e}"
            print(json.dumps({"file": Path(path).name,
                              "verdict": "invalid", "detail": msg[:400]}))
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
