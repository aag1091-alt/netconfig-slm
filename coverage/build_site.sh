#!/usr/bin/env bash
# Assemble the static dashboard site into a directory. This is the
# ONLY site assembly: the GitHub Actions workflow and the gh-pages
# fallback publisher both call it, so the two deployment paths can
# never publish different output.
#
#   usage: build_site.sh <output-dir>
#
# Layout produced (what GitHub Pages serves):
#   <output-dir>/index.html, style.css, app.js
#   <output-dir>/data/coverage.json
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${1:?usage: build_site.sh <output-dir>}"
mkdir -p "$OUT/data"
cp "$ROOT/coverage/site/index.html" "$ROOT/coverage/site/style.css" \
   "$ROOT/coverage/site/app.js" "$OUT/"
cp "$ROOT/coverage/data/coverage.json" "$OUT/data/coverage.json"
echo "site assembled in $OUT"
