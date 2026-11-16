"""Freeze the golden baseline of the UNCHANGED ari_signals Level 0 generator.

Step 0 of docs/tasks/extract-ari-signals-features.md. Run once, before the
old repo is touched or archived, with the OLD repo's own interpreter:

    PYTHONDONTWRITEBYTECODE=1 ~/Documents/GitHub/ari_signals/.venv/bin/python \\
        scripts/ari_golden/generate_baseline.py \\
        --source ~/Documents/GitHub/ari_signals --out data/golden/ari_signals-<sha7>-run

It imports the old code read-only (nothing in the source repo is written)
and records, for each case, the exact input slice and the canonical JSON
output, plus everything needed to reproduce it (commit, library versions,
params, preprocessing). Two modes mirror the two old call paths:

- `sim`:  run_simulation_fast: 201-row window ending at bar t
          (min_history=200), dates floored to the day, normalized=True.
- `live`: OHLCVDownloaderService: every row up to bar t, normalized=False.

`--out` must be inside the git-ignored data store (`<repo>/data/` or
`$FIRM_DATA_ROOT`) or outside the repo: this repo is public and provider
data is never committed. History: the 2026-11-16 run wrote to
tests/fixtures/ari_golden; its files were then moved into the data store
(raw CSVs under `raw/`, outputs under `golden/ari_signals-03a8b8d/`) and
only MANIFEST.json, rewritten with store paths, stayed in git.

This file is not part of the firm package and is never imported by it.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import gzip
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SYMBOLS = {
    # symbol: (source csv, end-of-week weekday: bars on other days are mid-week)
    "BTCUSDT": ("data/simulation/BTCUSDT-1d.csv", 6),  # 24x7, weeks close Sunday
    "NVDA": ("data/simulation/NVDA-1d.csv", 4),  # US share, weeks close Friday
}
TIMEFRAME = "1d"
MIN_HISTORY = 200  # run_simulation_fast default
SEED = 20261116
N_RANDOM = 40
EXTRACTED_PATHS = [  # files the firm will copy; must be clean at the source commit
    "src/analysis/level_0_generation.py",
    "src/analysis/document_cleanup.py",
    "src/analysis/level_0.schema.json",
    "src/analysis/validate_level0.py",
    "src/analysis/strategy_params.py",
    "src/technicals",
    "src/utils/analysis_science.py",
    "config/strategy_params/default.json",
]


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def git(source: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(source), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def canonical_json(doc: Any) -> bytes:
    """The serialisation the parity test must reproduce byte for byte."""
    import numpy as np
    import pandas as pd

    def default(o: Any) -> Any:
        if isinstance(o, pd.Timestamp):
            return o.isoformat()
        if isinstance(o, np.generic):
            return o.item()
        raise TypeError(f"not JSON serialisable: {type(o).__name__}")

    return json.dumps(
        doc,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=True,
        default=default,
    ).encode("utf-8")


def normalize(df):  # copy of run_simulation_fast._normalize_csv at the source commit
    import pandas as pd

    df = df.copy()
    df.columns = df.columns.str.lower()
    if "date" not in df.columns:
        df = df.rename(columns={"timestamp": "date"})
    if "volume" not in df.columns:
        df["volume"] = 0.0
    df["date"] = pd.to_datetime(df["date"], utc=True, errors="coerce")
    df = df.dropna(subset=["date"])
    return df.sort_values("date").reset_index(drop=True)


def pick_cases(df, week_end: int) -> list[int]:
    import numpy as np

    n = len(df)
    # weekly bars available at bar i = distinct W-SUN weeks among rows 0..i
    weeks = df["date"].dt.tz_convert(None).dt.to_period("W")
    cum_weeks = (~weeks.duplicated()).cumsum()
    hits = cum_weeks[cum_weeks >= 200]
    first_200w = int(hits.index[0]) if len(hits) else None
    fixed = {MIN_HISTORY, MIN_HISTORY + 1, 365, 366, 367, 400, n - 1}
    if first_200w is not None:
        fixed |= {first_200w - 1, first_200w, first_200w + 1}
    # mid-week bars in the last year (unfinished week at the bar's close)
    recent = [i for i in range(n - 260, n) if df["date"].iloc[i].weekday() != week_end]
    fixed |= set(recent[-5:]) | set(recent[:5])
    rng = np.random.default_rng(SEED)
    pool = sorted(set(range(MIN_HISTORY, n)) - fixed)
    fixed |= {int(i) for i in rng.choice(pool, size=N_RANDOM, replace=False)}
    return sorted(i for i in fixed if MIN_HISTORY <= i < n)


EARLY_LIVE = [30, 60, 120, 199]  # live mode only: near the start of history


async def run_case(timeseries_info, df, idx: int, mode: str, symbol: str, params):
    import pandas as pd

    if mode == "sim":
        window = df.iloc[idx - MIN_HISTORY : idx + 1].copy()
        bar_date = window.iloc[-1]["date"]
        desc = f"Simulation for {symbol} {TIMEFRAME} on {bar_date.strftime('%Y%m%d')}"
        return await timeseries_info(
            window, symbol, desc, tf=TIMEFRAME, params=params, normalized=True
        )
    window = df.iloc[: idx + 1].copy()
    assert isinstance(window["date"].dtype, pd.DatetimeTZDtype)
    return await timeseries_info(
        window,
        symbol,
        f"{symbol} {TIMEFRAME}",
        tf=TIMEFRAME,
        params=params,
        normalized=False,
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    source: Path = args.source.expanduser().resolve()
    out: Path = args.out.resolve()
    repo = Path(__file__).resolve().parents[2]
    store = Path(os.environ.get("FIRM_DATA_ROOT", repo / "data")).resolve()
    if out.is_relative_to(repo) and not out.is_relative_to(store):
        print(f"refusing: {out} is in the public repo tree; use the data store", file=sys.stderr)
        return 2

    commit = git(source, "rev-parse", "HEAD")
    dirty = git(source, "status", "--porcelain", "--", *EXTRACTED_PATHS)
    if dirty:
        print(f"refusing: extracted files differ from {commit}:\n{dirty}", file=sys.stderr)
        return 2

    sys.path.insert(0, str(source / "src"))
    import jsonschema
    import numpy as np
    import pandas as pd
    import scipy
    from analysis.level_0_generation import timeseries_info
    from analysis.validate_level0 import validate_level0_document

    params_bytes = (source / "config/strategy_params/default.json").read_bytes()
    params = json.loads(params_bytes)
    schema_bytes = (source / "src/analysis/level_0.schema.json").read_bytes()
    schema = json.loads(schema_bytes)

    (out / "inputs").mkdir(parents=True, exist_ok=True)
    cases_out: list[dict[str, Any]] = []

    for symbol, (rel, week_end) in SYMBOLS.items():
        raw_bytes = (source / rel).read_bytes()
        (out / "inputs" / f"{symbol}-{TIMEFRAME}.csv").write_bytes(raw_bytes)
        df = normalize(pd.read_csv(source / rel))
        sim_df = df.copy()
        sim_df["date"] = sim_df["date"].dt.floor("D")  # as run_simulation_fast for 1d

        idxs = pick_cases(df, week_end)
        plan = [("sim", i) for i in idxs] + [("live", i) for i in EARLY_LIVE + idxs]
        for mode, idx in plan:
            frame = sim_df if mode == "sim" else df
            bar = frame["date"].iloc[idx]
            case: dict[str, Any] = {
                "symbol": symbol,
                "mode": mode,
                "bar_index": idx,
                "bar_date": bar.isoformat(),
                "weekday": bar.weekday(),
                "mid_week": bar.weekday() != week_end,
                "input_rows": [idx - MIN_HISTORY, idx] if mode == "sim" else [0, idx],
            }
            rel_out = Path("outputs") / symbol / mode / f"{idx:05d}.json.gz"
            try:
                doc = asyncio.run(run_case(timeseries_info, frame, idx, mode, symbol, params))
            except Exception as exc:  # recorded: the baseline includes failures
                case["error"] = f"{type(exc).__name__}: {exc}"
                cases_out.append(case)
                continue
            payload = canonical_json(doc)
            (out / rel_out).parent.mkdir(parents=True, exist_ok=True)
            # mtime=0 so the .gz bytes are reproducible too
            (out / rel_out).write_bytes(gzip.compress(payload, mtime=0))
            errs = sorted(
                e.message[:200]
                for e in jsonschema.Draft202012Validator(schema).iter_errors(json.loads(payload))
            )
            v = validate_level0_document(copy.deepcopy(doc))
            case.update(
                {
                    "output": rel_out.as_posix(),
                    "output_sha256": sha256_bytes(payload),
                    "has_nan": any(t in payload for t in (b"NaN", b"Infinity")),
                    "schema_valid": not errs,
                    "schema_errors": errs[:5],
                    "validator_passed": bool(v.get("meta", {}).get("validation_passed")),
                }
            )
            cases_out.append(case)
            print(f"{symbol} {mode} {idx} {case['bar_date'][:10]} ok", flush=True)

    manifest = {
        "purpose": "Golden baseline of the unchanged ari_signals Level 0 generator "
        "(docs/tasks/extract-ari-signals-features.md, step 0).",
        "source": {
            "repo": "ari_signals",
            "commit": commit,
            "commit_date": git(source, "show", "-s", "--format=%cI", commit),
            "extracted_paths_clean": True,
            "license": "Apache-2.0",
        },
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "pandas": pd.__version__,
            "numpy": np.__version__,
            "scipy": scipy.__version__,
            "jsonschema": importlib.metadata.version("jsonschema"),
        },
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "params": {
            "file": "config/strategy_params/default.json",
            "sha256": sha256_bytes(params_bytes),
            "passed_as": "whole file",
        },
        "schema_sha256": sha256_bytes(schema_bytes),
        "inputs": {
            s: {
                "file": f"inputs/{s}-{TIMEFRAME}.csv",
                "source": rel,
                "sha256": sha256_bytes((out / "inputs" / f"{s}-{TIMEFRAME}.csv").read_bytes()),
            }
            for s, (rel, _) in SYMBOLS.items()
        },
        "preprocessing": [
            "pd.read_csv(file) with default options",
            "normalize(): lower-case columns; timestamp->date; "
            "pd.to_datetime(date, utc=True, errors='coerce'); dropna(date); "
            "sort by date; reset_index (copy of run_simulation_fast._normalize_csv)",
            "sim mode only: date = date.dt.floor('D')",
        ],
        "modes": {
            "sim": "timeseries_info(df.iloc[t-200:t+1].copy(), symbol, "
            "'Simulation for {symbol} 1d on {YYYYMMDD}', tf='1d', params, normalized=True)",
            "live": "timeseries_info(df.iloc[:t+1].copy(), symbol, '{symbol} 1d', tf='1d', "
            "params, normalized=False)",
        },
        "serialisation": "json.dumps(doc, sort_keys=True, separators=(',', ':'), "
        "ensure_ascii=False, allow_nan=True; pd.Timestamp->isoformat(), "
        "numpy scalar->.item()), UTF-8, gzip(mtime=0). "
        "output_sha256 is of the uncompressed JSON bytes.",
        "case_selection": f"fixed edges (200, 201, 365-367, 400, last, first bar with "
        f"200 weekly bars +-1, 5+5 mid-week bars in the last year) + "
        f"{N_RANDOM} random bars (numpy default_rng({SEED})); live mode "
        f"also bars {EARLY_LIVE}",
        "cases": cases_out,
    }
    (out / "MANIFEST.json").write_text(json.dumps(manifest, indent=1) + "\n")
    bad = [c for c in cases_out if "error" in c]
    print(f"{len(cases_out)} cases, {len(bad)} errors")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
