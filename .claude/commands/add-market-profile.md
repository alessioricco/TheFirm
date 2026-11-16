---
description: Add a new market profile (calendar, data, costs, broker) without changing core code.
argument-hint: "<market name, e.g. uk_equities>"
---

Add the market profile `$ARGUMENTS`.

1. Copy the shape of an existing file in `config/markets/`. Fill in:
   calendar, currency, universe source (with delistings), history and
   real-time data sources, cost model, corporate actions or funding rates,
   paper broker (`simulator`) and an inactive live broker.
2. If a needed adapter does not exist, add it under `src/firm/data/` or
   `src/firm/execution/` behind the existing adapter interface, with a fake
   for tests. Do not change core code to special-case this market; if you
   must, stop and explain why.
3. Check the venue is allowed in the active jurisdiction profile; if not,
   say so and stop.
4. Add tests with synthetic fixtures in the provider's format (never real
   data: the repo is public; see `firm.data.synthetic`): calendar edges
   (holidays, early closes, clock changes), cost model, and the verifier's
   checks running on this profile. Fee schedules go in `config/fees/`,
   risk limits in `config/risk/`, never in the market profile.
5. Add a DECISIONS.md entry for any vendor or cost assumption. Mark every
   number you could not confirm as a placeholder.
