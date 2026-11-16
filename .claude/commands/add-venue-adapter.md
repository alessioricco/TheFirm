---
description: Add a broker or exchange adapter behind the VenueAdapter interface, certified by the conformance suite.
argument-hint: "<venue name, e.g. kraken>"
---

Add a venue adapter for `$ARGUMENTS`.

1. Read the SPEC "Integration layer" section and `src/firm/execution/`
   (the `VenueAdapter` interface and the paper simulator).
2. Implement the adapter in `paper` and `sandbox` modes only. `live` mode
   must exist as code but stay refused by config while `live_trading` is
   false. Never use real-money credentials.
3. Declare the venue's real capabilities (order types, time in force,
   instrument rules). Do not fake support the venue lacks.
4. Make it pass `tests/conformance/` with recorded fixtures (no network in
   tests). Add a separate script, run by me, for the venue's sandbox.
5. Credentials come from the secrets store, never from code or logs.
   Recommend trade-only keys with withdrawals disabled.
6. Add a DECISIONS.md entry: API used, rate limits, and anything the
   conformance suite could not check.
