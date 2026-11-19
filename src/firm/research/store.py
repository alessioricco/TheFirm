"""The private strategy store: where agent-written strategy code lives.

The repo is public, so agent-written strategies never go in git (M3 plan,
R1). They live in the data store, which is git-ignored:

    <data root>/research/strategies/<dossier>/<stem>-<sha256[:12]>.py

Files are named by their content and written once. A changed strategy is a
new file, and a new candidate for the verifier; an old version is never
edited. Only hand-written examples stay public, in `examples/strategies/`.

Saving code here does not make it safe to run: agent code runs only in the
sandboxed runner (R2).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from firm.data.store import StoreError, sha256_bytes, write_once

ZONE = Path("research") / "strategies"
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class StrategyStoreError(ValueError):
    """A dossier ID or file name that could leave the store, or an overwrite."""


@dataclass(frozen=True)
class SavedStrategy:
    path: Path
    sha256: str


def _plain(kind: str, value: str) -> str:
    if not _NAME.match(value):
        raise StrategyStoreError(f"invalid {kind} {value!r}: letters, digits, '-' and '_' only")
    return value


def strategies_dir(root: Path, dossier: str) -> Path:
    """The folder holding one dossier's strategy files."""
    return Path(root) / ZONE / _plain("dossier ID", dossier)


def save_strategy(
    root: Path, dossier: str, code: bytes, *, stem: str = "strategy"
) -> SavedStrategy:
    """Write `code` once under the dossier, named by its content; same bytes again is a no-op."""
    sha = sha256_bytes(code)
    path = strategies_dir(root, dossier) / f"{_plain('file stem', stem)}-{sha[:12]}.py"
    try:
        write_once(path, code)
    except StoreError as e:  # a 12-hex prefix collision with different code
        raise StrategyStoreError(str(e)) from e
    return SavedStrategy(path, sha)
