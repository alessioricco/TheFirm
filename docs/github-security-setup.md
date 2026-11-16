# GitHub security setup (owner checklist)

This repo is public. These steps protect it against leaked secrets and
unreviewed changes to `main`. Do them **before** (or right when) the repo
becomes public. Each step has the GitHub web UI route and a `gh` command;
GitHub renames menus from time to time, so if a label differs, look for the
nearest match under the same settings page.

Run the `gh` commands from inside the repo: `gh` fills in `{owner}/{repo}`
by itself. Check you're logged in first: `gh auth status`.

---

## 1. Secret scanning and push protection

**What they do.** *Secret scanning* finds known credential formats (cloud
keys, GitHub tokens, Telegram bot tokens, ...) anywhere in the repo and its
history and raises an alert. *Push protection* goes further: it **rejects a
push** that contains a detected secret, before it becomes public. For public
repositories both are free.

### Web UI

1. Repo page → **Settings** → **Security** section in the left sidebar →
   **Advanced Security** (older name: *Code security and analysis*).
2. Under **Secret Protection** (older: *Secret scanning*), click **Enable**.
3. Under it, enable **Push protection**.
4. Optional but recommended, if offered: **Non-provider patterns**
   (generic passwords, private keys) and **Validity checks** (GitHub tests
   whether a found token is still live).

### gh

```bash
gh api -X PATCH repos/{owner}/{repo} --input - <<'JSON'
{
  "security_and_analysis": {
    "secret_scanning": { "status": "enabled" },
    "secret_scanning_push_protection": { "status": "enabled" }
  }
}
JSON

# verify
gh api repos/{owner}/{repo} --jq .security_and_analysis
```

If the API answers that a feature isn't available, the repo is probably
still private on a plan without it: make it public first, then rerun.

### Your account too

Settings (your profile, not the repo) → **Code security** → **Push
protection for yourself**: enable it. It protects pushes to *any* public
repo you contribute to.

### Test it (optional, safe)

GitHub documents dummy test tokens for push protection; search the docs for
"test push protection". Never test with a real key.

---

## 2. Require CI on `main` (branch protection)

**What it does.** Nobody (including you, by accident) can put a commit on
`main` unless CI passed: the repo guard, lint, types, firm-rule tests, all
tests, and the gitleaks scan.

**Do this after the first CI run.** GitHub only offers checks it has seen
run. Push a branch or open a PR first, let `ci` run once, then:

### Web UI (rulesets)

1. **Settings** → **Rules** → **Rulesets** → **New ruleset** → **New
   branch ruleset**.
2. Name: `main`. Enforcement status: **Active**.
3. Target branches → **Add target** → **Include default branch**.
4. Rules, tick:
   - **Restrict deletions**
   - **Block force pushes**
   - **Require status checks to pass**, then **Add checks** and pick all
     of these (names as CI reports them):
     - `test (ubuntu-24.04-arm, py3.12)`
     - `test (ubuntu-24.04-arm, py3.14)`
     - `test (macos-14, py3.12)`
     - `test (macos-14, py3.14)`
     - `secret scan (full history)`
   - Optional: **Require a pull request before merging** (with 0 required
     approvals if you work alone: it still forces CI to run on the branch).
5. **Create**.

### gh (classic branch protection, same effect)

```bash
gh api -X PUT repos/{owner}/{repo}/branches/main/protection --input - <<'JSON'
{
  "required_status_checks": {
    "strict": true,
    "contexts": [
      "test (ubuntu-24.04-arm, py3.12)",
      "test (ubuntu-24.04-arm, py3.14)",
      "test (macos-14, py3.12)",
      "test (macos-14, py3.14)",
      "secret scan (full history)"
    ]
  },
  "enforce_admins": true,
  "required_pull_request_reviews": null,
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}
JSON
```

If you later change the CI matrix (OS or Python versions), the check names
change: update this list, or `main` will wait forever for a check that no
longer exists.

---

## 3. Pin GitHub Actions to commit SHAs

**Why.** `uses: actions/checkout@v4` follows a movable tag; if an action's
repo were compromised, the tag could point at malicious code that runs in
our CI. A full commit SHA can't move.

For each `uses:` line in `.github/workflows/ci.yml`, find the commit of the
release you want and replace the tag with the SHA, keeping the version as a
comment:

```bash
# list releases, pick one (e.g. v4.2.2), then resolve its commit
gh release list -R actions/checkout --limit 5
gh api repos/actions/checkout/commits/v4.2.2 --jq .sha
```

```yaml
- uses: actions/checkout@<40-hex-sha>  # v4.2.2
```

Do the same for `astral-sh/setup-uv` and `gitleaks/gitleaks-action`. To
keep the pins fresh, add Dependabot for actions only (it opens PRs that bump
the SHAs; CI must pass before you merge them):

```yaml
# .github/dependabot.yml
version: 2
updates:
  - package-ecosystem: github-actions
    directory: /
    schedule:
      interval: weekly
```

---

## 4. Other repo settings worth flipping

- **Settings → Advanced Security → Dependabot alerts**: enable (free; warns
  about known-vulnerable dependencies in `uv.lock`).
- **Settings → Actions → General → Workflow permissions**: *Read repository
  contents* (CI only needs read; our workflow also declares
  `permissions: contents: read`).
- **Settings → Actions → General → Fork pull request workflows**: require
  approval for first-time contributors.
- **Settings → General → Features**: switch off Wiki/Projects if unused.

---

## 5. Local protection (already in the repo)

- Pre-commit guard, once per clone:
  ```bash
  git config core.hooksPath .githooks
  ```
  It runs `scripts/check_repo.py --staged` (data, secrets, machine paths,
  big files) and ruff on staged Python files.
- Optional deeper local scan with gitleaks (same tool CI uses):
  ```bash
  brew install gitleaks
  gitleaks git -v        # whole history
  gitleaks dir -v .      # working tree
  ```

## 6. Claude Code permissions

`settings.json` in the repo root is meant for `.claude/settings.json`
(the session that wrote it can't write inside `.claude/`). Move it:

```bash
mv settings.json .claude/settings.json
git add .claude/settings.json && git commit -m "Claude Code: deny secrets, ask before risky edits"
```

What it does:

- **deny**: reading or editing `.env`, `.env.local`, `.envrc`, `secrets/`;
  `cat .env*`; force pushes. (`.env.example` stays readable.)
- **ask**: edits to risk, jurisdiction and verifier-threshold configs, to
  CI, to the repo guard and git hooks, and any `git push`, so the owner
  approves each one.

Restart Claude Code (or start a new session) for it to take effect, then
check with `/permissions`.

---

## 7. If a secret ever leaks

Order matters:

1. **Revoke / rotate the secret at the provider first.** Once pushed to a
   public repo, assume it was copied within minutes; deleting it from git
   does not un-leak it.
2. Then remove it from history (`git filter-repo --replace-text`, or
   GitHub's guide "Removing sensitive data from a repository"), force-push
   with branch protection temporarily relaxed, and ask collaborators to
   re-clone.
3. Close the secret-scanning alert as *revoked*.
4. Note what happened and the fix in DECISIONS.md.
