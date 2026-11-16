"""Data plane: ingests, versions and serves point-in-time market data.

Every read takes an `as_of` time and returns only what was known then
(golden rule 3). Raw data is stored as received with a dataset version and
checksum. Delisted instruments stay forever. Also home of the feature
library (ari_signals Level 0 behind a fixed-lookback, closed-bar wrapper;
see docs/tasks/extract-ari-signals-features.md). Milestone 1.
"""
