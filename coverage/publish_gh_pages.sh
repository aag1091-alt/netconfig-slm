#!/usr/bin/env bash
# Fallback publisher: push the coverage dashboard to a `gh-pages`
# branch for GitHub Pages branch publishing — the path that works
# while the gh token lacks the `workflow` scope needed to land
# .github/workflows/coverage-pages.yml. See coverage/DEPLOYMENT.md.
#
# The site is assembled by coverage/build_site.sh — the same script
# the Actions workflow uses — so both paths publish identical bytes.
#
# DEFAULT IS A DRY RUN: builds the site, creates the would-be commit
# on a temporary ref, prints its tree and the exact push command,
# then deletes the temp ref. Nothing is pushed and refs/heads/gh-pages
# is not touched. With --push it creates/updates the real gh-pages
# branch and pushes it to origin. (Switching the repo's Pages source
# to that branch is a separate one-time step — DEPLOYMENT.md.)
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PUSH=0
[ "${1:-}" = "--push" ] && PUSH=1

BUILD_DIR="$(mktemp -d)"
TMP_INDEX="$(mktemp)"
# git wants to create the index itself; a pre-created 0-byte file
# makes it print "index file smaller than expected".
rm -f "$TMP_INDEX"
DRY_REF="refs/dry-run/coverage-gh-pages"
cleanup() {
  rm -rf "$BUILD_DIR" "$TMP_INDEX"
  if [ "$PUSH" = "0" ]; then
    git update-ref -d "$DRY_REF" 2>/dev/null || true
  fi
}
trap cleanup EXIT

bash coverage/build_site.sh "$BUILD_DIR" >/dev/null
# Pages serves Jekyll by default; the dashboard is plain static
# files, so opt out.
: > "$BUILD_DIR/.nojekyll"

GENERATED_AT="$(python3 -c "
import json
print(json.load(open('coverage/data/coverage.json'))['meta'].get('generated_at', 'unknown'))
")"

export GIT_INDEX_FILE="$TMP_INDEX"
git read-tree --empty
add_file() {
  local hash
  hash="$(git hash-object -w "$1")"
  git update-index --add --cacheinfo "100644,$hash,$2"
}
add_file "$BUILD_DIR/index.html" index.html
add_file "$BUILD_DIR/style.css" style.css
add_file "$BUILD_DIR/app.js" app.js
add_file "$BUILD_DIR/data/coverage.json" data/coverage.json
add_file "$BUILD_DIR/.nojekyll" .nojekyll
TREE="$(git write-tree)"
unset GIT_INDEX_FILE

PARENT_ARGS=()
# Parent on the REMOTE gh-pages tip when it exists (whoever published
# last may have done so from another machine, so the local branch can
# be stale or absent); fall back to the local branch, else root commit.
git fetch origin gh-pages 2>/dev/null || true
REMOTE_TIP="$(git rev-parse -q --verify refs/remotes/origin/gh-pages 2>/dev/null || true)"
LOCAL_TIP="$(git rev-parse -q --verify refs/heads/gh-pages 2>/dev/null || true)"
if [ -n "$REMOTE_TIP" ]; then
  PARENT_ARGS=(-p "$REMOTE_TIP")
elif [ -n "$LOCAL_TIP" ]; then
  PARENT_ARGS=(-p "$LOCAL_TIP")
fi
# NB: never expand a possibly-empty array under `set -u` — macOS ships
# bash 3.2, where "${ARR[@]}" on an empty array is an unbound-variable
# error. Branch on the count instead.
if [ "${#PARENT_ARGS[@]}" -gt 0 ]; then
  COMMIT="$(git commit-tree "$TREE" "${PARENT_ARGS[@]}" \
    -m "Publish YANG coverage dashboard (data generated $GENERATED_AT)")"
else
  COMMIT="$(git commit-tree "$TREE" \
    -m "Publish YANG coverage dashboard (data generated $GENERATED_AT)")"
fi

echo "Would-be gh-pages tree (commit $COMMIT):"
git ls-tree -r "$COMMIT"
echo
echo "File sizes / sha256 of the assembled site:"
# Portable size/hash: GNU stat/sha256sum vs macOS stat -f / shasum.
(cd "$BUILD_DIR" && find . -type f | sort | \
  xargs -I{} sh -c '
    sz="$(stat -c%s "{}" 2>/dev/null || stat -f%z "{}")"
    hs="$(sha256sum "{}" 2>/dev/null | cut -c1-16 || shasum -a 256 "{}" | cut -c1-16)"
    printf "  %10s  %s…  %s\n" "$sz" "$hs" "{}"')

if [ "$PUSH" = "0" ]; then
  git update-ref "$DRY_REF" "$COMMIT"
  echo
  echo "DRY RUN — nothing pushed, refs/heads/gh-pages untouched."
  echo "To publish for real:"
  echo "  bash coverage/publish_gh_pages.sh --push"
  echo "(equivalently: git update-ref refs/heads/gh-pages $COMMIT && git push origin gh-pages)"
  echo "Then switch the Pages source to the gh-pages branch — one-time"
  echo "step in DEPLOYMENT.md (repo Settings → Pages, owner's action)."
else
  git update-ref refs/heads/gh-pages "$COMMIT"
  git push origin gh-pages
  echo "Pushed gh-pages @ $COMMIT to origin."
fi
