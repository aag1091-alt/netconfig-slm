#!/usr/bin/env bash
# Regenerate coverage/data/coverage.json — the one documented
# refresh command (see coverage/DEPLOYMENT.md). Deterministic:
# same inputs -> same JSON, apart from meta.generated_at
# (pass --no-timestamp for a byte-stable output). Extra arguments
# are forwarded to generate_coverage.py.
#
# The generator needs pyang; this script finds a python that has it:
# $PYTHON, the repo venv (.venv), the sibling main checkout's venv
# (../netconfig-slm/.venv — the layout of the coverage worktree), or
# python3, in that order.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PY=""
for cand in "${PYTHON:-}" "$ROOT/.venv/bin/python" \
            "$ROOT/../netconfig-slm/.venv/bin/python" python3; do
  [ -n "$cand" ] || continue
  if "$cand" -c "import pyang" >/dev/null 2>&1; then
    PY="$cand"
    break
  fi
done
if [ -z "$PY" ]; then
  echo "error: no python with pyang found (tried \$PYTHON, .venv," \
       "../netconfig-slm/.venv, python3)." >&2
  echo "Create the project venv or set PYTHON=/path/to/python." >&2
  exit 1
fi
exec "$PY" coverage/generate_coverage.py "$@"
