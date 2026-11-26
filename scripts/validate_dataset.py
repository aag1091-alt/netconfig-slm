#!/usr/bin/env python3
"""Promote candidates -> processed/ only if they pass validation rung 1.
Thin wrapper around src.validate.validate for pipeline use."""
import subprocess, sys
if __name__ == "__main__":
    sys.exit(subprocess.call([sys.executable, "-m", "src.validate.validate"] + sys.argv[1:]))
