# Skills for developing the firm

Claude Code commands and skills used to build this repository. They help the
coding session; they are not part of the firm. (The firm's own agents get
their skills through Certo.)

## Project commands (in git, `.claude/commands/`)

| Command | What it does |
|---|---|
| `/next-milestone [N]` | Plans, builds and checks one SPEC milestone, then stops and reports. |
| `/check-point-in-time PATH` | Audits code for lookahead and survivorship leaks. Changes no file. |
| `/add-market-profile NAME` | Adds a market (calendar, data, costs, broker) without touching core code. |
| `/add-venue-adapter VENUE` | Adds a broker or exchange behind the adapter interface; must pass the conformance suite. |
| `/certo-task WHAT` | Writes a Certo task file (`docs/tasks/`) with script-checkable acceptance criteria; no private data. |
| `/add-strategy NAME HYPOTHESIS` | Writes a strategy from the template, verifies it on synthetic then real data, reports the verdict as is; warns that every run is a trial. |
| `/update-data VENUE INSTRUMENT SYMBOL TF` | Downloads or refreshes a series into the data store, builds a dataset version, reports gaps and revisions. |
| `/pre-push [TARGET]` | Checks everything about to be pushed to the public repo (guard, tests, lint, types, outgoing diff, commit emails). Never pushes. |
| `/senior-quant [FOCUS]` | Asks the senior quant subagent (`.claude/agents/senior-quant.md`, web tools only) for strategy proposals, saves them under `private/briefs/proposals/` and imports them for the owner to approve. |
| `/review-docs` | Reviews the docs against the code: fixes stale facts, lists SPEC deltas and the rest in TODO.md. |

## Skills from Anthropic (downloaded, git-ignored)

From `anthropics/skills`, at a pinned commit, into `.claude/skills/`,
with `uv run python scripts/fetch_claude_skills.py` (the same script as
Certo's; the session sandbox can't write `.claude/`, so the owner runs it):

| Skill | License | Why, and when |
|---|---|---|
| `skill-creator` | Apache-2.0 | Writing and testing skills for the firm's research agents. Milestone 3. |
| `webapp-testing` | Apache-2.0 | Testing the web UI in a browser with Playwright. Milestone 4. |
| `mcp-builder` | Apache-2.0 | Building the read-only MCP endpoint on the control API. Milestone 4. |
| `frontend-design` | check license | A clean, non-generic look for the web UI. Milestone 4. |

Add a skill only when current work needs it. Check each skill's license at
the pinned commit before adding it.
