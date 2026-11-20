---
description: Start the firm's dashboard in the background; you get the login link in your own terminal.
argument-hint: "[stop]"
---

Start (or, with `stop`, stop) the dashboard (docs/CLAUDE_CODE.md, section 5;
docs/DASHBOARD.md). Use `FIRM_DOTENV=0 .venv/bin/python -m firm.control.cli`
for `firm`.

1. If this session already started `firm dashboard serve` and it is still
   running, say so and skip to step 3. Otherwise check whether something
   answers on `http://127.0.0.1:8765/` (`curl -s -o /dev/null -w '%{http_code}'`);
   if it does, it was started elsewhere: don't start another, go to step 3.
   The sandbox blocks local connections and binding ports ("Operation not
   permitted"): run the check and the server outside the sandbox, and say so.
2. Start `firm dashboard serve` as a background job. Wait for it to answer
   (a few seconds), and report its address. If it fails (for example the
   sandbox refuses to bind the port), show the error and give the owner the
   command for their own terminal: `uv run firm dashboard serve`.
3. Tell the owner to get the login link **in their own terminal**, not
   here: `uv run firm dashboard url`. The link carries the login token, a
   secret: never run that command yourself, never read the token file, and
   ask the owner not to paste the link into the conversation.
4. Remind them it opens in their browser (this sandbox can't open one) and
   that a dashboard started here stops when the session ends.

With `stop`: stop the background job this session started. Don't kill a
dashboard this session didn't start; tell the owner where it runs instead.
