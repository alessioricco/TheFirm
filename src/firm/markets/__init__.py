"""Market workers: one asyncio worker per enabled market (golden rule 10).

Each has its own feeds, calendar and venue adapter; a failure halts only
that market. Milestone 2.
"""
