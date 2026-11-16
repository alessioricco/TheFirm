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
| `/certo-task WHAT` | Writes a Certo task file with script-checkable acceptance criteria. |

## Skills from Anthropic (downloaded, git-ignored)

From `anthropics/skills`, at a pinned commit, into `.claude/skills/`,
the same way Certo does it (`scripts/fetch_claude_skills.py`, not yet
copied into this repo; see TODO.md):

| Skill | License | Why, and when |
|---|---|---|
| `skill-creator` | Apache-2.0 | Writing and testing skills for the firm's research agents. Milestone 3. |
| `webapp-testing` | Apache-2.0 | Testing the web UI in a browser with Playwright. Milestone 4. |
| `mcp-builder` | Apache-2.0 | Building the read-only MCP endpoint on the control API. Milestone 4. |
| `frontend-design` | check license | A clean, non-generic look for the web UI. Milestone 4. |

Add a skill only when current work needs it. Check each skill's license at
the pinned commit before adding it.
