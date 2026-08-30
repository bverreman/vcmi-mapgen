---
name: vcmi-mapgen-python-pypi-release
description: "Releasing Python distributions from a repo with release-please + PyPI Trusted Publishing (OIDC) — no API token anywhere. Covers the propose-in-a-PR/ship-by-merging shape, the .release-please-config.json traps that silently release nothing (changelog-sections replacing the defaults, sticky release-as, component/tag mismatch, a manifest that disagrees with pyproject), monorepo publish ordering and cross-package pins that no lockfile validates, the verify gate, and a bring-up checklist for a new repo. Load when setting up releasing, changing release config, or diagnosing a release that produced an empty or incomplete PR."
metadata:
  generated_by: farrier
  source: library/skills/stacks/python/python-pypi-release/SKILL.md
  resolve: "farrier source .claude/skills/vcmi-mapgen-python-pypi-release/SKILL.md"
  do_not_edit: "generated — run the `resolve` command below for this machine's editable source path, edit that, then `make agent-install` to regenerate"
  tags: [release, ci]
---

# Python releases — release-please + PyPI Trusted Publishing

The target shape, and every rule below exists to keep one of its two halves true:

- **The version is reviewable before it exists.** release-please reads the
  Conventional-Commit history since each package's last tag and opens **one pull request**
  carrying the computed bumps and the generated `CHANGELOG.md`. You read what would ship
  before it ships.
- **There is no PyPI token.** The upload runs in GitHub Actions under
  [Trusted Publishing](https://docs.pypi.org/trusted-publishers/): GitHub mints a
  short-lived OIDC token that PyPI verifies against a registered publisher. No long-lived
  secret exists to leak — not in the repo, not in CI secrets, not in `~/.pypirc`.

```
make release (workflow_dispatch)        the release PR merging
        │                                        │
        └────────────────┬───────────────────────┘
                         ▼
                 job: release-please      dispatch → creates/refreshes the PR
                         │                merge   → creates tags + GitHub releases
                         ▼
                 job: verify              only if releases_created — runs the full suite
                         ▼
                 job: publish             environment: pypi, id-token: write
                                          one ordered build+publish step per package
```

**Reference implementation:** the `stablemate` monorepo — `.release-please-config.json`,
`.release-please-manifest.json`, `.github/workflows/release.yml`, the root `Makefile`'s
`release` target, and `.githooks/commit-msg`. Read those five together before adapting
this to a new repo.

---

## The commit type is the input, not a style preference

release-please reads commit **subjects** and nothing else. The type decides whether a
package ships at all:

| Subject | Effect on the package owning the touched paths |
| --- | --- |
| `feat:` | minor |
| `fix:` / `perf:` / `refactor:` | patch |
| `<type>!:` or a `BREAKING CHANGE:` body paragraph | major |
| `docs:` `test:` `build:` `ci:` `chore:` / anything unparseable | **none — no release** |

A repaired defect labelled `chore:` ships to nobody, and the omission surfaces weeks later
as a bug report against a version that never contained the fix. That is the whole reason
to enforce the format mechanically rather than by prompt: a generated message (an agent's,
an editor's "generate commit message") can only *bias* toward the convention.

**Install a `commit-msg` hook.** Derive the valid scopes from the tracked top-level
directories so adding a package needs no edit to the hook, and reject the two failures
that actually recur: a capitalized description (the built-in "capitalize the subject line"
instinct winning over the convention) and a body starting on line 2 with no blank line —
invisible to `git log --oneline` and to the changelog, which reads the subject alone.

**One concern per commit.** A commit spanning four unrelated changes cannot be labelled
correctly by any type, so whichever type is chosen withholds a release from the other
three. Split first, then label.

---

## `.release-please-config.json` — the four traps

```jsonc
{
  "$schema": "https://raw.githubusercontent.com/googleapis/release-please/main/schemas/config.json",
  "release-type": "python",
  "separate-pull-requests": false,          // ONE PR for the workspace, not one per package
  "include-component-in-tag": true,
  "bootstrap-sha": "<sha>",
  "changelog-sections": [ /* see below — write it out in full */ ],
  "packages": {
    "workhorse": { "component": "workhorse-agent" },   // dir → distribution name
    "farrier":   { "component": "farrier" }
  }
}
```

### 1. `changelog-sections` replaces the defaults, and a hidden type bumps nothing

This is the expensive one, because it fails **silently and in the direction of "nothing
happened"**. release-please hides `refactor` (and `docs`/`style`/`test`/`build`/`ci`/
`chore`) by default, and a hidden type produces **no version bump**. A commit that rewrote
an entire package therefore shipped to nobody, while the release read as successful — the
PR simply did not mention that package.

Declaring `changelog-sections` **replaces** the default list rather than extending it, so
write out every type you care about, including the hidden ones you want to stay hidden:

```jsonc
"changelog-sections": [
  { "type": "feat",     "section": "Features" },
  { "type": "feature",  "section": "Features" },
  { "type": "fix",      "section": "Bug Fixes" },
  { "type": "perf",     "section": "Performance Improvements" },
  { "type": "refactor", "section": "Code Refactoring" },
  { "type": "revert",   "section": "Reverts" },
  { "type": "docs",  "section": "Documentation",         "hidden": true },
  { "type": "style", "section": "Styles",                "hidden": true },
  { "type": "test",  "section": "Tests",                 "hidden": true },
  { "type": "build", "section": "Build System",          "hidden": true },
  { "type": "ci",    "section": "Continuous Integration","hidden": true },
  { "type": "chore", "section": "Miscellaneous Chores",  "hidden": true }
]
```

A type left off the list is a type that silently stops releasing. Whatever bump table your
`CLAUDE.md`/README states, this array is what makes it true — if the two disagree, the
array wins and the document is a lie.

**Symptom to recognize:** a release PR that omits a package you know had commits. Confirm
by checking whether that package's `CHANGELOG.md` is missing an entry you can point at in
`git log`; the type of that commit is the answer.

### 2. `release-as` is sticky — delete it the moment it lands

`"release-as": "1.0.0"` is the way to declare a version the history cannot compute (a 1.0
line earned by work that predates `bootstrap-sha`). It is **not consumed** by the release
it causes. Left in place it pins *every subsequent* release of that package to the same
version, and the second one fails on the index as a duplicate upload rather than saying
why.

Write the removal down somewhere durable (the README, not the PR description) at the same
time you add the key.

### 3. `component` must reproduce the tags that already exist

With `include-component-in-tag`, the tag is `<component>-v<version>`. Set `component` to
the **distribution name**, not the directory name, whenever a repo already has tags like
`farrier-v1.5.1` — otherwise release-please looks for a tag that does not exist, finds
nothing, and falls back to `bootstrap-sha`, re-releasing history.

### 4. The manifest, `pyproject.toml`, and the last real tag must agree

```json
{ "workhorse": "0.8.0", "farrier": "1.5.1", "ostler": "0.1.0" }
```

The classic mismatch: a `pyproject.toml` says `1.6.0` because a version was stamped
locally and never shipped, while the last tag and the last PyPI release are `1.5.1`. Set
**both** the manifest and the `pyproject.toml` to what actually shipped (`1.5.1`) and let
release-please compute forward. Putting the phantom `1.6.0` in the manifest sends it
hunting for a tag that does not exist.

`bootstrap-sha` bounds the scan for packages with **no** tag. Without it they scan the
entire history, which predates the convention and produces nonsense. Set it to the `HEAD`
at which you adopted release-please; the first PR then reflects only work after that
point.

---

## The workflow — one file, two triggers

```yaml
on:
  workflow_dispatch:          # `make release`
  pull_request:
    types: [closed]           # the release PR merging is what ships
```

- **One file.** PyPI's trusted publisher pins the *workflow filename*. Splitting "open the
  PR" and "upload" into two files means two registrations per project, and one of them is
  the one you forget.
- **`pull_request: closed`, not `push: main`.** A release is something you ask for and then
  approve, not something that happens because a commit landed. Guard the job so an
  ordinary PR merging cannot start a release:

  ```yaml
  if: >-
    github.event_name == 'workflow_dispatch' ||
    (github.event.pull_request.merged == true &&
     startsWith(github.event.pull_request.head.ref, 'release-please--'))
  ```

- **Publish in the *same run* as the release.** Releases created with `GITHUB_TOKEN` do not
  trigger further workflows, so a separate `on: release` workflow never fires. The publish
  job consumes this run's `releases_created` / `paths_released` outputs instead.
- **`GITHUB_TOKEN` is sufficient throughout.** No PAT.

### Refresh the lockfile on the release branch

`uv.lock` records every workspace member's own version, so release-please's bump stales it
the moment it writes the pyprojects. Re-lock and commit on the release branch, so the tree
a human reviews is one `uv sync --locked` accepts:

```yaml
- uses: actions/checkout@v4
  if: steps.rp.outputs.pr
  with: { ref: "${{ fromJSON(steps.rp.outputs.pr).headBranchName }}" }
- uses: astral-sh/setup-uv@v5
  if: steps.rp.outputs.pr
- run: |
    uv lock
    if ! git diff --quiet uv.lock; then
      git config user.name  "github-actions[bot]"
      git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
      git commit -m "chore: refresh uv.lock for the release" uv.lock
      git push
    fi
```

### The verify gate is not optional

```yaml
verify:
  needs: release-please
  if: needs.release-please.outputs.releases_created == 'true'
  steps:
    - uses: actions/checkout@v4
      with: { ref: main }        # the commit release-please just tagged
    - run: uv sync --all-packages
    - run: make test
```

An upload to PyPI cannot be taken back or re-uploaded under the same version, so the suite
runs against the merged tree before anything leaves the runner.

**But note the failure mode this creates, and check for it before merging:** tags and
GitHub releases are created *by the merge*, before `verify` runs. A red suite therefore
leaves you with packages tagged and released on GitHub, nothing on PyPI, and release-please
unwilling to retry — it believes those versions shipped. **`make test` must be green on
`main` before you merge the release PR.** A pre-existing failure inherited from someone
else is still a release blocker.

### Publish steps, in dependency order

```yaml
publish:
  needs: [release-please, verify]
  if: needs.release-please.outputs.releases_created == 'true'
  environment: pypi              # so PyPI can require it, and a required reviewer here
  permissions:                   # turns "merge the PR" into "approve the upload"
    id-token: write              # the OIDC token — this is the whole no-API-key mechanism
  steps:
    - name: Build ostler
      if: contains(fromJSON(needs.release-please.outputs.paths_released), 'ostler')
      run: uv build --package ostler --out-dir dist/ostler
    - name: Publish ostler
      if: contains(fromJSON(needs.release-please.outputs.paths_released), 'ostler')
      uses: pypa/gh-action-pypi-publish@release/v1
      with: { packages-dir: dist/ostler }
    # …then each dependent, in an order where an install of a release resolves
```

Ordering is expressed as **sequential steps inside one job**: steps in a job are guaranteed
to run in order, a matrix is not, and a `needs:` chain across jobs would need
`always() && !failure()` guards to survive skipped predecessors. `paths_released` is a JSON
array of the released **directories** — guard each step with `contains(fromJSON(...), '<dir>')`.

---

## Monorepo hazards no lockfile catches

### A cross-package pin is never validated locally

With `[tool.uv.sources] foo = { workspace = true }`, `uv.lock` records the member as
`{ name = "foo", editable = "packages/foo" }` — **with no version specifier**, because the
workspace source overrides the constraint. A wrong `"foo>=0.8,<1"` in a sibling's
`dependencies` is therefore invisible in the repo, produces no `uv lock` diff, and binds
only the people installing from PyPI.

**So: any time a package takes a major bump, hand-edit every dependent's pin in the same
release.** Publishing `foo` 1.0.0 alongside `bar` 0.2.0 that still pins `foo>=0.8,<1`
ships a `bar` that refuses to install with the `foo` released beside it. Grep the
dependents' `pyproject.toml`s — nothing else will tell you.

### An unpublished internal library must be vendored, not just imported

release-please decides what to ship **from the paths a commit touched**. A shared internal
package that is not itself a distribution therefore has a hole: a fix committed only under
`core/` touches no released package and reaches nobody.

The fix is to vendor it — copy it into each consumer under a private namespace
(`workhorse/_vendor/stablemate_core/`), commit the copies, and guard them with a
byte-for-byte check in `make test`. The copy is committed rather than synthesized at build
time precisely so that a core change *is* a change to both tools' trees, which is what
makes release-please bump them. Mark the source package `Private :: Do Not Upload`.

### Packages that are versioned but not published

List them in `packages` so they get versions and changelogs, and simply give them no
publish step (a taken PyPI name, out of scope, whatever). Say why in a comment next to the
last publish step, since the absence is otherwise indistinguishable from an oversight.

---

## Bring-up checklist for a new repo

In-repo:

1. `.release-please-config.json` — `release-type: python`, `separate-pull-requests: false`,
   `include-component-in-tag: true`, `bootstrap-sha` at today's `HEAD`, the **full**
   `changelog-sections` array, one `packages` entry per directory with `component` set to
   the distribution name.
2. `.release-please-manifest.json` — one entry per package, each equal to what actually
   shipped. Reconcile any `pyproject.toml` that disagrees.
3. `.github/workflows/release.yml` — the three jobs above.
4. A `make release` target that *only* dispatches (`gh workflow run release.yml --ref main`).
   It must build nothing and upload nothing; if it can publish from a laptop, eventually it
   will.
5. `.githooks/commit-msg` + a `make hooks` that sets `core.hooksPath`.
6. Delete any older release machinery entirely (`scripts/release.sh`, `uv publish` targets,
   a token read out of `~/.pypirc`). Keeping it "as a fallback" keeps the token.

Out of repo — none of it verifiable from a checkout, and all of it blocks the upload
rather than the PR:

7. **PyPI trusted publisher per project**, at *pypi.org → the project → Publishing*:
   owner, repository, workflow filename, environment name. A project that does not exist
   on the index yet gets a **pending publisher** with the same four fields; the first
   upload converts it into the project.
8. **The GitHub environment** (`pypi`) in repo settings. Adding yourself as a required
   reviewer is the cheapest safety net available.
9. **Settings → Actions → General → "Allow GitHub Actions to create and approve pull
   requests"**, or release-please cannot open the PR with the default token.

First real run: merge a release PR while only a **leaf** package (no dependents, already on
PyPI, low blast radius) has pending commits, so the OIDC path is proven on one package
before anything's first-ever upload.

---

## Diagnosing a release that did nothing

| Symptom | Cause |
| --- | --- |
| `make release` → `HTTP 403: Resource not accessible by personal access token` | `gh` is authenticated with a fine-grained PAT lacking **Actions: write**. (`GET .../actions/permissions/workflow` separately needs **Administration: read**.) Pushing over SSH bypasses PAT scope entirely, which is why pushes work and dispatch does not. |
| No PR at all | Check `gh pr list --label 'autorelease: pending'` and the branch `release-please--branches--main` before believing it. Then: no conventional commits since the last tag; or "allow Actions to create PRs" is off. |
| PR exists but a package is missing | Its commits are a type that bumps nothing — see trap 1. Corroborate against that package's `CHANGELOG.md`. |
| Second release of a package fails on the index as a duplicate | A `release-as` left in the config — trap 2. |
| Tags and GitHub releases exist, PyPI has nothing | `verify` failed after the merge, or a trusted publisher is unregistered. Both leave release-please believing the version shipped. |
| Released package won't install | A stale cross-package pin — see "A cross-package pin is never validated locally". |

Before merging, read the PR's `.release-please-manifest.json` diff. It is the whole
proposal in one hunk: every package that is about to ship, at the version it will ship at.
