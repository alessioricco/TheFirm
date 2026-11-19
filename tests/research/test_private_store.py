"""Agent-written strategies live in the private store, never in git (M3 plan, R1).

They are saved under `<data root>/research/strategies/<dossier>/`, named by
their content and written once: a changed strategy is a new file (and a new
candidate). The verifier runs them from there, and its report cites the
code's SHA-256 and file name, never the code itself or where it lives.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from firm.research.store import StrategyStoreError, save_strategy, strategies_dir
from verify_helpers import EXAMPLES, NOW, REPO

CODE = (EXAMPLES / "ma_crossover.py").read_bytes()


def test_a_strategy_is_saved_by_content_under_its_dossier(tmp_path) -> None:
    saved = save_strategy(tmp_path, "d-001", CODE, stem="ma_cross")
    sha = hashlib.sha256(CODE).hexdigest()
    assert saved.sha256 == sha
    assert saved.path == strategies_dir(tmp_path, "d-001") / f"ma_cross-{sha[:12]}.py"
    assert saved.path.parent == tmp_path / "research" / "strategies" / "d-001"
    assert saved.path.read_bytes() == CODE


def test_saving_is_write_once_and_a_change_is_a_new_file(tmp_path) -> None:
    first = save_strategy(tmp_path, "d-001", CODE)
    assert save_strategy(tmp_path, "d-001", CODE).path == first.path  # same bytes: no-op
    changed = save_strategy(tmp_path, "d-001", CODE + b"\n# v2\n")
    assert changed.path != first.path
    assert first.path.read_bytes() == CODE  # the old version is untouched


@pytest.mark.parametrize("dossier", ["../escape", "a/b", "", ".hidden", "d 1"])
def test_dossier_ids_cannot_leave_the_store(tmp_path, dossier: str) -> None:
    with pytest.raises(StrategyStoreError):
        save_strategy(tmp_path, dossier, CODE)


@pytest.mark.parametrize("stem", ["../x", "a/b", "x.py", ""])
def test_file_stems_are_plain_names(tmp_path, stem: str) -> None:
    with pytest.raises(StrategyStoreError):
        save_strategy(tmp_path, "d-001", CODE, stem=stem)


def test_the_verifier_runs_a_private_strategy_and_cites_only_its_hash(dataset, tmp_path) -> None:
    from firm.verify.runner import verify_strategy_file

    store = tmp_path / "private-store"
    marker = b"# PRIVATE-MARKER-7f3a\n"
    saved = save_strategy(store, "d-042", marker + CODE, stem="agent_ma")
    root, version = dataset
    r = verify_strategy_file(
        strategy_path=saved.path,
        class_name="MovingAverageCrossover",
        params=None,
        data_root=root,
        dataset_version=version,
        venue="kraken",
        instrument="BTC-USD",
        tf="1d",
        config_root=REPO / "config",
        jurisdiction="uk",
        market="crypto",
        state=tmp_path / "state",
        now=NOW,
    )
    body = json.loads(r.report)
    assert body["candidate"]["code_sha256"] == saved.sha256
    report = r.report.decode()
    assert "PRIVATE-MARKER" not in report  # never the code
    assert str(store) not in report and "d-042" not in report  # never where it lives
