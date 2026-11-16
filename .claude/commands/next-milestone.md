---
description: Work on the next unfinished milestone of docs/SPEC.md, then stop and report.
argument-hint: "[milestone number]"
---

Work on milestone $ARGUMENTS of `docs/SPEC.md` (if no number is given, the
first one whose exit check is not yet met according to `TODO.md`).

1. Read `CLAUDE.md`, the milestone in `docs/SPEC.md`, `TODO.md`, and the
   relevant parts of `ARCHITECTURE.md` and `DECISIONS.md`.
2. Write a short plan: the modules you will touch, the tests you will add
   first, and how you will show the exit check is met. Show it and wait for
   my go-ahead.
3. Build it test-first. Keep `tests/invariants/` green; never weaken one.
4. Run the exit check exactly as written in the SPEC. Report each condition
   as met, not met, or unverified, with the command and its real output.
5. Update `TODO.md`, `ARCHITECTURE.md` and `DECISIONS.md`.
6. Stop. Do not start the next milestone.
