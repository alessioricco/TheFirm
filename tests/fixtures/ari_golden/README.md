# ari_signals golden baseline (manifest only)

Outputs of the **unchanged** ari_signals Level 0 generator at the source
commit in `MANIFEST.json`, frozen before the code was copied into the firm
(step 0 of `docs/tasks/extract-ari-signals-features.md`). It is the only
proof that later changes to the copied code were intended.

**This repo is public: the data is not here.** Only `MANIFEST.json`
(checksums, dates, case list, no prices) is in git. The data lives in the
git-ignored data store (`$FIRM_DATA_ROOT`, default `<repo>/data/`):

- inputs: `raw/provider=binance/.../symbol=BTC-USDT/tf=1d/` and
  `raw/provider=yahoo/.../symbol=NVDA/tf=1d/` (legacy imports, byte for byte)
- outputs: `golden/ari_signals-03a8b8d/outputs/<symbol>/<mode>/<bar>.json.gz`

**Never regenerate or edit the baseline.** Intended differences are declared
in the parity test. Without the store, the tests that read it skip and
report UNVERIFIED.

Modes: `sim` = 201-row window as `run_simulation_fast`; `live` = all rows up
to the bar as `OHLCVDownloaderService`. Produced by
`scripts/ari_golden/generate_baseline.py` with the old repo's `.venv`; two
runs gave identical bytes.
