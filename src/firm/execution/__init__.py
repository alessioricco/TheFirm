"""Execution desk: sizes, places and reconciles orders through a `VenueAdapter`.

Deterministic. Never trades without a verdict ID and a risk approval; every
venue (the paper simulator included) sits behind `adapter.VenueAdapter`.
"""
