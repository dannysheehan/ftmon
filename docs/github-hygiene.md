# GitHub hygiene (personal maintainer notes)

Educational notes for running this repository safely on GitHub. Not end-user
documentation — it records *why* the settings and habits exist, and the
day-to-day workflow around them. FTMON-specific details are called out so the
same ideas transfer to other solo public repos.

Companion: contributor branch/release one-liner lives in `CONTRIBUTING.md`.

---

## Mental model

```text
feature|fix|docs/<slug> ──PR──► main (protected) ──v* tag──► PyPI + GitHub Release
                                      │
                                      └── pages deploy (Exchange only)
```

- **`main` is sacred.** It only advances via squash-merged PRs with green CI.
- **Releases are tags, not merges.** Bumping version files is ordinary work;
  publishing is pushing a matching `v*` tag.
- **Secrets stay out of the repo.** PyPI uses OIDC Trusted Publishing; no API
  tokens in Actions secrets for publish.
- **Dependencies update on a schedule.** Dependabot proposes; you review.
  Security fixes get priority; casual version bumps batch weekly.
- **Issues track the backlog.** GitHub issues are canonical; local scratch
  notes promote to issues when ready (see [Issues](#issues--backlog)).

---

## Branch management

### Why protect `main`

Without protection (the state this repo started in), anyone with push access —
including you on a tired Tuesday — can force-push history away, skip CI, or
land a broken commit that immediately deploys Pages / becomes the next release
base.

What we enforce on `main`:

| Rule | Why |
| ---- | --- |
| Pull request required | No direct pushes; every change gets a diff + CI |
| Required checks `test (3.11)`, `test (3.13)` | Gate matches what contributors run locally |
| Enforce for administrators | Solo maintainers still need the seatbelt |
| No force-push / no branch delete | History stays append-only |
| Conversation resolution | Review threads cannot be ignored by accident |
| Linear history + squash-only merges | Clean `main`; matches imperative commit subjects |

**Solo-maintainer nuance:** require **0** approving reviews. Forced self-review
adds ceremony without a second brain. Status checks are the real gate.

### Day-to-day branch habit

1. Branch from up-to-date `main`: `feature/…`, `fix/…`, `docs/…`, `chore/…`,
   `release/…`.
2. Open a PR; wait for CI.
3. Squash-merge; delete the head branch (repo setting does this automatically).
4. Never push commits straight to `main`; never `--force` to `main`.

If CI fails on something already broken on `main` (stale lint, lockfile drift),
fix it *on the feature branch* so the PR can merge — do not weaken protection
to “just land it.”

### Merge settings that help

- **Squash only** — one commit per PR on `main`; easier bisect and release notes.
- **Delete head branches on merge** — less stale remote clutter.
- **Suggest updating PR branches** — rebase-friendly against a moving `main`.
- **Wiki off** (if unused) — avoids a second, unprotected doc surface.

---

## Issues and backlog

GitHub issues are the tracked backlog. A local `BACKLOG.md` is optional
scratch — promote items to issues when they should survive outside your
machine.

### Labels

| Label | Meaning |
| ----- | ------- |
| `enhancement` | Feature or product improvement (GitHub default) |
| `backlog` | Agreed direction, not scheduled for the next PR |
| `bug` | Regression or broken behavior |
| `documentation` | Manual, install guide, CONTRIBUTING, etc. |
| `dependencies` | Dependabot only — created by automation |

Use **`enhancement` + `backlog`** for roadmap ideas (e.g. UI work filed before
SPEC work lands). Drop `backlog` when you branch to implement; keep `enhancement`
until shipped or closed.

Do **not** use issues for security reports — [`.github/SECURITY.md`](../.github/SECURITY.md)
and private vulnerability reporting.

### Maintainer habit

1. Capture ideas in issues (or promote from local notes).
2. Title clearly: area + outcome (`Web: baseline overlay on Metrics`).
3. Body: problem, proposal, constraints, likely touchpoints, related SPEC IDs.
4. When starting work: branch `feature/…`, PR with `Closes #N`, remove `backlog`.
5. Normative product requirements still live in `SPEC.md` — issues track
   implementation, not spec authority.

### What we skip for now

- Issue templates (add when external reporters need structure).
- Milestones / GitHub Projects (add when issue volume or collaborators grow).
- `good first issue` until tasks are explicitly scoped for newcomers.

```sh
gh issue list --label backlog
gh issue list --search "is:open sort:created-asc label:backlog"
```

---

## Releases (pre-1.0 / alphas)

While not production, keep SemVer pre-release suffixes: `2.0.0aN` → tags
`v2.0.0aN`. GitHub Release marks `a` / `b` / `rc` as pre-release automatically.

### Checklist

1. On a branch, bump **both**:
   - `pyproject.toml` → `version = "…"`
   - `src/ftmon/__init__.py` → `__version__ = "…"`
2. Run `uv lock` so `uv.lock` matches (CI uses `uv sync --locked`).
3. PR → squash-merge to `main`.
4. On `main`:

   ```sh
   git checkout main && git pull
   git tag -a v2.0.0a3 -m "FTMON 2.0.0a3 pre-release"
   git push origin v2.0.0a3
   ```

5. Watch `.github/workflows/release.yml`: test → build → **tag must equal**
   `v` + package version → PyPI (`pypi` environment) → GitHub Release.

### Guardrails around release

- **`pypi` environment** restricted to `v*` tags — a random branch cannot
  Trusted-Publish even if workflow YAML were confused.
- **`v*` tag ruleset** — tags cannot be force-updated or deleted (no silent
  retag of an already-published version).
- **No long-lived PyPI token** in repo secrets — OIDC only.
- Wrong tag / mismatched version fails the build job on purpose.

When you are ready for production: drop the `aN` suffix (`2.0.0`), same process;
omit pre-release in the tag message habit if you like.

---

## Security settings worth keeping on

| Control | Role |
| ------- | ---- |
| Secret scanning + push protection | Blocks committing known secret patterns |
| Dependabot alerts + security updates | Told about vulnerable deps; can open fix PRs |
| Private vulnerability reporting + `SECURITY.md` | Researchers report quietly; you control disclosure |
| Actions: selected allowlist + **require SHA pins** | Supply-chain: only known Action orgs; pins resist tag-move attacks |
| Default workflow `contents: read` | Escalate permissions per job only when needed |
| CodeQL on PRs / `main` | Extra static signal; **not** a substitute for pytest/SPEC |

Pages (`github-pages` env) may deploy only from `main`. Exchange workflow already
builds on PRs without deploy permissions.

### Action pinning (why SHA, not `@v4`)

Floating tags (`actions/checkout@v4`) can be moved. Pinned SHAs with a version
comment are what Dependabot updates:

```yaml
- uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2
```

Exchange workflows pioneered this; CI/release match. Repo setting *Require
actions to be pinned to a full-length commit SHA* enforces it.

---

## Dependabot: what to do when PRs appear

Dependabot is a **proposal engine**, not an auto-merge bot.

### Priority order

1. **Security update PRs** — review Changelog/advisory, CI green → merge soon.
2. **Grouped weekly version bumps** — one PR for Actions, one for pip (see
   `.github/dependabot.yml`). Skim major bumps’ release notes, merge if CI green.
3. **Noise / broken majors** — close with a note, or fix on a follow-up branch.
   Do not merge a red CodeQL / CI PR “to clear the queue.”

### Habits that scale for a solo repo

- Prefer **grouped** version updates (already configured) so Mondays are one
  review, not five major Action jumps.
- After merging one dep PR, let Dependabot rebase the others
  (`@dependabot rebase`) instead of hand-merging conflicts.
- Majors that skip several versions (e.g. `checkout` 4→7) deserve a changelog
  skim; patch/minor Actions bumps are usually low drama if CI is green.
- Closing a Dependabot PR is fine — it can recreate later. Better a clean queue
  than a graveyard of stale major bumps.

### What not to do

- Enable auto-merge for all Dependabot PRs on day one (especially majors).
- Ignore failing required checks to “just get it in.”
- Leave dozens of open dep PRs until they conflict with everything.

---

## Workflow files (authority map)

| Workflow | Trigger | Publishes? |
| -------- | ------- | ---------- |
| `ci.yml` | PR + push `main` | No — lint, reqindex, pytest, build smoke |
| `exchange.yml` | Path-filtered PR/`main` | Pages **only** on push to `main` |
| `release.yml` | `v*` tags | PyPI + GitHub Release |
| `codeql.yml` | PR + `main` + weekly | No — uploads Code Scanning alerts |

Permissions stay least-privilege at workflow top-level; jobs that need
`id-token` / `pages` / `contents: write` declare it locally.

---

## Quick recovery / audit commands

```sh
# Protection & merge hygiene
gh api repos/dannysheehan/ftmon/branches/main/protection --jq '{
  enforce_admins: .enforce_admins.enabled,
  checks: .required_status_checks.contexts,
  force: .allow_force_pushes.enabled
}'
gh api repos/dannysheehan/ftmon --jq '{
  delete_branch_on_merge, allow_squash_merge,
  allow_merge_commit, allow_rebase_merge
}'

# Environments & tags
gh api repos/dannysheehan/ftmon/environments/pypi/deployment-branch-policies
gh api repos/dannysheehan/ftmon/rulesets --jq '.[]|{name,target,enforcement}'

# Dependabot / scanning
gh api repos/dannysheehan/ftmon --jq .security_and_analysis
gh pr list --author 'app/dependabot'

# Release watch
gh run list --workflow=release.yml --limit 3
gh release list -L 5
```

---

## Personal checklist (copy for other repos)

When hardening a new public solo repo:

1. [ ] Protect default branch: PR + CI checks + enforce admins + no force-push
2. [ ] Squash-only + delete head branches
3. [ ] Secret scanning + push protection
4. [ ] Dependabot alerts + security updates + grouped `dependabot.yml`
5. [ ] `SECURITY.md` + private vulnerability reporting
6. [ ] Pin Actions by SHA; allowlist known publishers; require pinning
7. [ ] Lock deploy environments (Pages → `main`; PyPI → `v*` tags)
8. [ ] Tag rules for release tags (no delete / no force-update)
9. [ ] Document branch + release habit in CONTRIBUTING (one short section)
10. [ ] Document issue labels + backlog promotion in CONTRIBUTING / github-hygiene
11. [ ] Prefer OIDC / Trusted Publishing over long-lived tokens

This repository already follows the list; revisit after collaborators join
(then add required reviews / CODEOWNERS).

---

## Further reading

- GitHub: [About protected branches](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches)
- GitHub: [Dependabot version updates](https://docs.github.com/en/code-security/dependabot/dependabot-version-updates)
- GitHub: [Using OpenID Connect for PyPI](https://docs.github.com/en/actions/deployment/security-hardening-your-deployments/configuring-openid-connect-in-pypi) / PyPI Trusted Publishing
- In-repo: `CONTRIBUTING.md` (branch/release, issues), `.github/SECURITY.md`,
  `.github/workflows/release.yml`, `docs/github-hygiene.md`
