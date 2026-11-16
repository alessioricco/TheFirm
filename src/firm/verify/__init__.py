"""Verification desk: deterministic checks that turn a candidate into a `Verdict`.

Lookahead truncation, hidden out-of-sample (opened once, logged), parameter
sensitivity, cost realism and trial-count correction. Never uses an LLM to
decide. Anything not checked is reported as unverified. Milestone 1.
"""
