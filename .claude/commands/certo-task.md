---
description: Turn a piece of work into a Certo task file with acceptance checks Certo can verify.
argument-hint: "<what to build>"
---

Write a Certo task file for: $ARGUMENTS

Save it as `docs/tasks/<short-slug>.md`. The repo is public: a task file
holds no strategy code, parameters, results, data or private details
(firm rule 8); private work starts from the firm's local agents instead.
It must contain:

1. **Goal** in one or two sentences.
2. **Context**: the files and interfaces involved, with paths, and the
   golden rules from `CLAUDE.md` that apply.
3. **Acceptance criteria** that a script can check, never "looks good":
   test names that must exist and pass, commands that must exit 0, files
   that must exist.
4. **Out of scope**: what the agent must not touch (always: risk config,
   `tests/invariants/`, anything enabling live trading).
5. **Suggested ladder**: local model first, cloud only if the task is about
   building the harness and contains no strategy data.

Keep it short enough for a local model's context. Then show me the command
to run it, for example:

```bash
certo docs/tasks/<short-slug>.md --project firm --escalate
```
