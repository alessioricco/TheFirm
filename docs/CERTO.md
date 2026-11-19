# Certo in the firm

[Certo](https://github.com/alessioricco/certo) is the owner's coding-agent
harness: an agent works in a project on a local model, then Certo runs the
project's own tests and checks and gives a verdict. The firm's researcher
agent (milestone 3, M4) uses it to write strategy code.

How it runs (owner, 2026-11-19): **Certo's MCP server, started by the firm
itself** through the launcher `scripts/certo-mcp`, **on the Mac**, with the
**DGX's Ollama** as the model server. It speaks MCP over stdio, so only the
program that starts it can talk to it: no network port, no login needed,
and no Claude Code in between. Code stays on the Mac, in the firm's private
store, where the firm's sandbox applies; the models run on the DGX, at no
cloud cost.

## Contents

1. [Set it up](#1-set-it-up)
2. [Check that it answers](#2-check-that-it-answers)
3. [What the launcher does](#3-what-the-launcher-does)
4. [Rules](#4-rules)
5. [Models](#5-models)
6. [Cost and budgets](#6-cost-and-budgets)
7. [Certo on the DGX](#7-certo-on-the-dgx)
8. [Troubleshooting](#8-troubleshooting)

## 1. Set it up

Once per Mac:

1. **Certo 0.2.4 or later** on the Mac: an existing install
   (`certo --version`), or a checkout of its repository with its own
   environment.
2. **Your settings**, in a git-ignored file (the repository is public, so
   the DGX's address and your paths never go in it):
   ```bash
   cp scripts/certo-mcp.env.example private/certo-mcp.env
   ```
   and edit `private/certo-mcp.env`:
   ```bash
   CERTO_LLM_BASE_URL=http://<dgx-host>:11434   # required: the DGX's Ollama (as OLLAMA_HOST in .env)
   CERTO_BIN=/path/to/certo                     # if certo isn't on the PATH
   ```
   The other settings have defaults (below); set them here to change them.
3. Nothing else: the research manager starts `scripts/certo-mcp` when it
   needs Certo. (The launcher is executable; after a fresh clone, check with
   `ls -l scripts/certo-mcp`, or `chmod +x scripts/certo-mcp`.)

## 2. Check that it answers

The launcher waits for MCP messages on its input, so running it by hand
only prints its start line on stderr and waits (Ctrl-D or Ctrl-C ends it):

```bash
scripts/certo-mcp
#   Certo MCP server on stdio. Projects: <data root>/research/certo; root: <same>
```

If it prints an error instead, see [Troubleshooting](#8-troubleshooting).
A full check (Certo's version, its model, one small real call to the model
on the DGX) needs an MCP client speaking to it; the research manager's own
check comes with the researcher loop. Checked on 2026-11-19: Certo 0.2.5
answered, and `qwen3.8:27b` on the DGX replied in about 11 seconds at
36 tokens per second, cost £0.

## 3. What the launcher does

`scripts/certo-mcp` reads `private/certo-mcp.env` (or the file named by
`FIRM_CERTO_SETTINGS`, or the environment), then starts
`certo mcp --root <workspace>` with these settings, for this server only
(everything else comes from Certo's usual settings files):

| Setting | Default | Meaning |
| --- | --- | --- |
| `CERTO_LLM_BASE_URL` | required | the DGX's Ollama, `http://<dgx-host>:11434` |
| `CERTO_LLM_MODEL` | `ollama/qwen3.8:27b` | the model (`config/models.yaml`, role `research`; a test keeps them equal) |
| `CERTO_LLM_TIMEOUT_SECONDS` | `1200` | one model call |
| `CERTO_MAX_TASK_SECONDS` | `3600` | one task |
| `CERTO_BIN` | `certo` | the Certo executable |
| `FIRM_CERTO_WORKSPACE` | `<data root>/research/certo` | Certo's projects folder and its only root (also `CERTO_PROJECTS_DIR`) |

The workspace is in the private store: git-ignored, backed up with it, one
project folder per research dossier. Nothing goes to stdout but MCP
messages: errors go to stderr.

## How the firm talks to Certo

The research manager is the MCP client (`firm.models.certo.CertoServer`):

- **One server, always.** Before it starts `scripts/certo-mcp` it takes an
  exclusive lock on the workspace (`.firm-certo.lock`); a second server,
  from the same process or another, is refused with a message naming the
  process that holds it. The operating system releases the lock if that
  process dies, so a crash never leaves it stuck.
- **One task at a time**: a local Ollama serves one model at a time, so the
  floor queues Certo tasks even with several researchers.
- **Every task names the local model** and never lets Certo choose or
  escalate (`auto_model`, `escalate`), and runs no independent tests.
- **Every task is recorded** in the ledger when it ends, as a model call for
  its dossier: run ID, verdict, tokens, time. The dossier's budget counts it.
- **A run lives in the server's memory.** If the floor stops mid-task, the
  task stops with it; on restart the floor continues it (`run_resume`)
  rather than starting over.

The server's own messages (stderr) go to `<workspace>/.firm-certo.log`.

## 4. Rules

From [CLAUDE.md](../CLAUDE.md) and [SPEC.md](SPEC.md) ("Certo on the DGX"):

- **Never expose `certo mcp` or `certo serve` on a network port**: neither
  has a login. `certo mcp` speaks over stdio; from another machine, reach it
  over SSH.
- **The workspace holds no secrets**, no risk config, no `.env`, no ledger:
  Certo is not a sandbox (its agent runs commands as you, with the network
  open). The researcher loop runs Certo inside the firm's OS sandbox.
- **Local models only.** Certo has no switch forbidding cloud models, and it
  also reads your global `~/.config/certo/models.yaml`. The firm always names
  the local model for a task and never asks Certo to escalate up a ladder
  (the egress gate, firm rule 8).
- **Claude Code never sends Certo private strategy work.** Claude Code runs
  on a cloud model; strategy tasks start only from the firm's own research
  manager.
- **Certo's verdict is about the code** (its tests and build pass). The
  firm's verifier still judges the strategy.

## 5. Models

Pulled on the DGX (2026-11-19): `qwen3.8:27b`, `qwen3-coder:30b`,
`devstral-small-2:24b`, `gpt-oss:20b`, `nemotron-3.5-lightning:30b`,
`gemma4:31b`, `gemma3:4b`, `d3:27b`.

The research role uses **`qwen3.8:27b`** (owner, 2026-11-19). The SPEC's
candidate `gpt-oss:120b` isn't pulled; the SPEC also says Certo's benchmark
on the firm's own tasks should pick the final model, so others can be
compared later.

## 6. Cost and budgets

- Local models cost £0 in money, and Certo's accounting says so, which also
  means Certo's money limit (`CERTO_MAX_TASK_COST`) does nothing for them.
  Their real cost is DGX time and tokens.
- So runs are capped by time (`CERTO_MAX_TASK_SECONDS`, and
  `CERTO_LLM_TIMEOUT_SECONDS` per call), and each research dossier by its
  own budget in `config/research.yaml`: trials, hours, tokens, cloud spend
  (£0). Certo reports each run's tokens, which count against the dossier.
- Certo's independent tests roughly double a run's cost: off by default for
  the researcher.
- A local Ollama serves one model at a time: research tasks run one at a
  time.

## 7. Certo on the DGX

Certo 0.2.4 is also installed on the DGX, reachable over SSH with a key
that may only start `certo mcp` (stdio), the setup the SPEC describes. The
firm doesn't use it for research yet: Certo's MCP tools report which files
a run changed but can't return their contents, and the firm keeps agent
code in its own private store and runs it in its own sandbox. It becomes
the natural home of the research floor when the firm moves to the DGX.

## 8. Troubleshooting

| Symptom | What to do |
| --- | --- |
| `CERTO_LLM_BASE_URL: set CERTO_LLM_BASE_URL (Ollama on the DGX, ...) in <file>` | Create `private/certo-mcp.env` from `scripts/certo-mcp.env.example` and set the DGX's address. |
| `exec: certo: not found` | Set `CERTO_BIN` in `private/certo-mcp.env` to your Certo executable. |
| `permission denied: scripts/certo-mcp` | `chmod +x scripts/certo-mcp` |
| The model doesn't answer | Is Ollama on the DGX reachable? `curl http://<dgx-host>:11434/api/tags` lists its models; is `qwen3.8:27b` among them? |
| A task stops after an hour | `CERTO_MAX_TASK_SECONDS` (default 3600): split the task, or raise it in `private/certo-mcp.env`. |
