"""The repo is public: no market data, datasets or model files in the tree.

Data lives in the git-ignored store (`$FIRM_DATA_ROOT`, default `<repo>/data`).
Test fixtures are generated in code (synthetic bars), never copied from a
provider. This walks the working tree, so it catches a file before it is
committed, not only after.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SKIP_DIRS = {
    ".git",
    ".venv",
    "data",
    "__pycache__",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    "node_modules",
}
DATA_SUFFIXES = {
    ".csv",
    ".parquet",
    ".feather",
    ".arrow",
    ".duckdb",
    ".sqlite",
    ".db",
    ".gz",
    ".zip",
    ".h5",
    ".hdf5",
    ".pkl",
    ".pickle",
    ".npy",
    ".npz",
    ".gguf",
    ".safetensors",
    ".pt",
    ".onnx",
    ".xlsx",
}
MAX_BYTES = 200_000  # anything larger than this in the tree needs a deliberate exception


def _files() -> list[Path]:
    out = []
    for p in REPO.rglob("*"):
        rel = p.relative_to(REPO)
        if rel.parts and rel.parts[0] in SKIP_DIRS:
            continue
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if p.is_file():
            out.append(p)
    return out


def test_no_data_files_in_repo_tree() -> None:
    bad = [str(p.relative_to(REPO)) for p in _files() if p.suffix.lower() in DATA_SUFFIXES]
    assert not bad, f"data files must live in the data store, not the repo: {bad}"


def test_no_large_files_in_repo_tree() -> None:
    big = [
        str(p.relative_to(REPO))
        for p in _files()
        if p.stat().st_size > MAX_BYTES and p.name != "uv.lock"
    ]
    assert not big, f"unexpectedly large files (data?): {big}"
