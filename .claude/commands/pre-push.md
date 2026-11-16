---
description: Check everything that is about to be pushed to the public repo. Changes no file; never pushes.
argument-hint: "[remote/branch, default origin/main]"
---

Check what would be pushed to `${ARGUMENTS:-origin/main}`. Change no file and
do not push: report, then wait for me.

1. List the outgoing commits: `git log --oneline <target>..HEAD` (if the
   remote branch doesn't exist, compare with `origin/main`).
2. Run and show the real output of:
   - `python3 scripts/check_repo.py --all`
   - `uv run ruff check . && uv run ruff format --check .`
   - `uv run mypy`
   - `uv run pytest`
3. Scan the outgoing changes (`git diff <target>..HEAD`) and every file
   they touch for: data or derived prices, dataset/verdict IDs tied to
   private results, strategy parameters or results, personal details,
   machine-specific paths, secrets. Report each hit with file and line.
4. Check the commit metadata that becomes public: author names and emails
   on the outgoing commits (`git log --format='%an <%ae>' <target>..HEAD |
   sort -u`). Don't print them in the report; say only whether they are a
   GitHub no-reply address. If not, suggest setting one:
   `git config user.email <id>+<user>@users.noreply.github.com`.
5. Check `.gitignore` still covers `/data/`, `/state/`, `/data-*/`, `.env`.
6. Verdict: **safe to push**, or **not safe** with the list of blockers.
   Pushing stays my decision.
