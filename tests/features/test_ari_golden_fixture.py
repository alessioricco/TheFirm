"""The ari_signals golden baseline is present, complete and untampered.

Only `MANIFEST.json` (checksums, dates, no prices) is in git. The inputs and
outputs live in the git-ignored data store (`$FIRM_DATA_ROOT`, default
`<repo>/data`): this repo is public and provider data is never committed.
Without the store, the data checks are skipped and reported as UNVERIFIED.

Standard library only. The parity test (tests/features/test_ari_parity.py)
arrives with step 2 of docs/tasks/extract-ari-signals-features.md.
"""

import gzip
import hashlib
import json
import os
from collections import Counter
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
MANIFEST = json.loads((REPO / "tests/fixtures/ari_golden/MANIFEST.json").read_text())
CASES = MANIFEST["cases"]
STORE = Path(os.environ.get("FIRM_DATA_ROOT", REPO / "data"))
OUTPUTS = STORE / MANIFEST["store"]["outputs_root"]

needs_store = pytest.mark.skipif(
    not OUTPUTS.is_dir(),
    reason=f"UNVERIFIED: ari golden data not found under {STORE} (data store absent)",
)


def test_records_source_commit_and_environment() -> None:
    src = MANIFEST["source"]
    assert len(src["commit"]) == 40
    assert src["extracted_paths_clean"] is True
    assert {"python", "pandas", "numpy", "scipy"} <= set(MANIFEST["environment"])


@pytest.mark.parametrize("symbol", sorted(MANIFEST["inputs"]))
def test_enough_cases_per_symbol(symbol: str) -> None:
    ok = [c for c in CASES if c["symbol"] == symbol and "output" in c]
    per_mode = Counter(c["mode"] for c in ok)
    assert per_mode["sim"] >= 50 and per_mode["live"] >= 50
    assert sum(c["mid_week"] for c in ok) >= 10  # inside an unfinished week
    assert min(c["bar_index"] for c in CASES if c["symbol"] == symbol) < 200  # near start


def test_every_case_has_output_or_recorded_error() -> None:
    for c in CASES:
        assert ("output" in c) != ("error" in c)
        if "output" in c:
            assert c["schema_valid"], c["output"]


@needs_store
def test_input_files_match_their_checksums() -> None:
    for spec in MANIFEST["inputs"].values():
        data = (STORE / spec["store_path"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == spec["sha256"], spec["store_path"]


@needs_store
def test_every_output_matches_its_checksum() -> None:
    for c in CASES:
        if "output" in c:
            payload = gzip.decompress((OUTPUTS / c["output"]).read_bytes())
            assert hashlib.sha256(payload).hexdigest() == c["output_sha256"], c["output"]
            json.loads(payload)


@needs_store
def test_no_unreferenced_output_files() -> None:
    referenced = {c["output"] for c in CASES if "output" in c}
    on_disk = {p.relative_to(OUTPUTS).as_posix() for p in (OUTPUTS / "outputs").rglob("*.gz")}
    assert on_disk == referenced
