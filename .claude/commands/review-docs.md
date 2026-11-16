---
description: Review the planning docs against the code and each other; fix stale facts, list the rest in TODO.md.
---

Review the project's docs for drift. Read `CLAUDE.md`, `docs/SPEC.md`,
`TODO.md`, `ARCHITECTURE.md`, `DECISIONS.md`, `README.md`, `SKILLS.md`,
`docs/tasks/*.md`, `.claude/commands/*.md`, `.env.example`, and skim
`src/`, `tests/`, `config/`, `scripts/`.

Look for:

1. **Stale facts**: docs that describe code, files, commands or numbers
   that no longer exist or changed (test counts, paths, CLI flags, module
   names, milestone status).
2. **TODO.md hygiene**: items already done but unticked, done items whose
   follow-ups are missing, duplicates, contradictions with DECISIONS.md.
3. **SPEC deltas**: decisions in DECISIONS.md that differ from
   `docs/SPEC.md`. The SPEC is owner-only: list them for me, don't edit it.
4. **Missing decisions**: non-obvious behaviour in code with no
   DECISIONS.md entry.
5. **Public-repo hygiene**: anything in docs that reveals data, results,
   personal details or machine paths.
6. **Rules without tests**: golden rules or firm rules that tests don't
   enforce yet and that TODO.md doesn't track.

Fix plainly stale facts directly (one commit). Put everything else in
TODO.md under a dated "Docs review" heading. Files you can't write
(`.claude/`) go in TODO.md as owner edits. Report what you fixed and what
you listed.
