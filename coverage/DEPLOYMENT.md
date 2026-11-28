# Deployment — YANG coverage dashboard

How the dashboard gets to GitHub Pages, how it stays current, and
how staleness is prevented. Two publishing paths exist; **exactly
one is active at a time**, because GitHub Pages has a single source
per repo. Both assemble the site with `coverage/build_site.sh`, so
they publish byte-identical output — switching paths never changes
what visitors see.

| | Path A — Actions workflow | Path B — `gh-pages` branch (fallback) |
|---|---|---|
| Status | **Blocked**: the environment's gh token lacks the `workflow` scope, so the workflow file cannot be pushed to `.github/workflows/` yet. It is staged at `coverage/workflows/coverage-pages.yml`. | **Ready**: script implemented and dry-run verified; needs one push + one settings change (below). |
| Publish trigger | Push to `main` touching `coverage/**` or `yang-learn/corpus/**`, or manual `workflow_dispatch` | Manual: re-run the publish script |
| Auto-refresh | Automatic on push (after the freshness gate) | **Manual** — nothing republishes until someone runs the script again |
| Canonical when? | Becomes canonical as soon as the token gains `workflow` scope | Canonical until then |

## The freshness contract (both paths)

Publishers only ever republish the committed
`coverage/data/coverage.json` — CI never generates. So the JSON
carries `meta.fingerprint`: sha256 hashes of every input that
determines it (the five corpus split files, the vendored run-1
scores snapshot, `priorities.yaml`, a digest of the YANG bundle
files, and the generator's own hash), plus per-split record counts.

- `coverage/check_freshness.py` (fast, seconds, no pyang)
  recomputes the fingerprints and fails if anything changed since
  generation. The page footer displays the fingerprint and the
  generation time, so a stale page is visibly stale.
- `coverage/check_freshness.py --full` additionally re-runs the
  generator and requires the committed JSON to equal the fresh
  output exactly (ignoring only `meta.generated_at`).
- The Actions workflow runs the fast check **before** publishing
  and fails the deploy on staleness. Consequence: a corpus push to
  `main` turns the Pages workflow red until someone regenerates and
  commits the JSON — old numbers are never silently served as
  current.
- On the gh-pages path there is no CI gate; run
  `make -C coverage check` before publishing (the publish script
  prints the data's `generated_at` in its commit message for the
  same reason).

Regeneration is one command and is deterministic (same inputs →
same JSON apart from `generated_at`; verified byte-identical across
consecutive `--no-timestamp` runs):

```
bash coverage/regen.sh        # or: make -C coverage regen
```

## Path B — first deployment (gh-pages branch)

One-time steps, in order:

1. **Publish the branch** (anyone with push access):
   ```
   bash coverage/publish_gh_pages.sh --push
   ```
   This builds the site via `build_site.sh`, commits it to a new
   `gh-pages` branch (root = the site files + `.nojekyll`), and
   pushes it. Without `--push` the script is a dry run: it prints
   the would-be tree and pushes nothing.
2. **Point Pages at the branch** — *repo owner's action*
   (Avinash), or an API call with a repo-admin token:
   GitHub repo → **Settings → Pages → Build and deployment →
   Source: “Deploy from a branch” → Branch: `gh-pages` / `(root)`**.
   This environment cannot do it: Pages is currently configured
   for workflow deployment, and changing the source is an admin
   setting, not a file. (API equivalent:
   `gh api -X PUT repos/{owner}/{repo}/pages -f build_type=legacy`
   with `source.branch=gh-pages`, `source.path=/` — requires admin
   on the repo.)
3. Verify https://aag1091-alt.github.io/netconfig-slm/ serves the
   dashboard (first branch publish can take a few minutes).

After that, refreshes are: regenerate if the corpus changed
(`bash coverage/regen.sh`, commit, push `main`) → re-run
`bash coverage/publish_gh_pages.sh --push`.

## Path A — activation, and the switch back

1. Refresh the gh token once, interactively (owner's action):
   `gh auth refresh -h github.com -s workflow`
2. Move the staged workflow into place and push:
   `git mv coverage/workflows/coverage-pages.yml .github/workflows/coverage-pages.yml`
   (or create it via the web UI).
3. Switch **Settings → Pages → Source back to “GitHub Actions”**
   (owner/admin, same as Path B step 2).
4. Push any `coverage/**` change (or run the workflow manually) —
   it freshness-checks, assembles via `build_site.sh`, and deploys.
5. Retire the fallback: delete the `gh-pages` branch
   (`git push origin --delete gh-pages`). `publish_gh_pages.sh`
   stays in the repo, unused, in case the branch path is ever
   needed again.
