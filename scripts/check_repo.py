"""Guard the public repo: no data, no secrets, no machine paths, no big files.

Standard library only, so it runs in a git hook before any environment exists.

    python3 scripts/check_repo.py --staged   # pre-commit hook: staged content
    python3 scripts/check_repo.py --all      # CI / tests: tracked + untracked files

Exit 0 if clean, 1 with a list of problems otherwise. The data store
(`/data/`) and verifier state (`/state/`) must never be committed, and
strategy code (a `def signals(` method) is allowed only in
`examples/strategies/`, `src/` and `tests/`: agent-written strategies live in
the private store.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import PurePosixPath

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
    ".jsonl",
}
FORBIDDEN_PREFIXES = ("data/", "state/", "ledger/", "models/", "private/")
FORBIDDEN_NAMES = {".env", ".envrc", ".mcp.json"}
SIZE_LIMIT = 200_000
SIZE_EXEMPT = {"uv.lock"}

SECRET_PATTERNS = {
    "a private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "an AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "a GitHub token": re.compile(
        r"\b(ghp|gho|ghs|ghr)_[A-Za-z0-9]{36}\b|\bgithub_pat_[A-Za-z0-9_]{40,}"
    ),
    "an Anthropic key": re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}"),
    "an OpenAI-style key": re.compile(r"\bsk-(proj-)?[A-Za-z0-9]{32,}\b"),
    "a Telegram bot token": re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b"),
    "an assigned secret": re.compile(
        r"(?i)\b[A-Z0-9_]*(api_?key|secret|token|password|passwd)[A-Z0-9_]*[ \t]*[:=][ \t]*"
        r"['\"]?[A-Za-z0-9/+_\-.]{16,}"
    ),
}
HOME_PATH = re.compile(r"(/Users/|/home/)[A-Za-z0-9._-]+/")
# Real dataset versions and verdict IDs point at private results: they belong
# in private/TODO.md, never in committed files (placeholders like ds-xxxx are fine).
PRIVATE_ID = re.compile(r"(?<![A-Za-z0-9])(ds-[0-9a-f]{12}|v-[0-9a-f]{16})(?![0-9a-f])")
SKIP_CONTENT = {"scripts/check_repo.py", "uv.lock"}  # this file holds the patterns
# Agent-written strategies live in the private store, never in git (M3 plan, R1).
# Only hand-written examples, the firm's own code and tests may hold strategy code.
STRATEGY_CODE = re.compile(r"\bdef\s+signals\s*\(")
STRATEGY_SUFFIXES = {".py", ".ipynb"}
STRATEGY_HOMES = ("examples/strategies/", "src/", "tests/")


def git(*args: str) -> bytes:
    return subprocess.run(["git", *args], check=True, capture_output=True).stdout


def staged() -> list[tuple[str, bytes]]:
    names = git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z").split(b"\0")
    return [(n.decode(), git("show", f":{n.decode()}")) for n in names if n]


def working_tree() -> list[tuple[str, bytes]]:
    tracked = git("ls-files", "-z").split(b"\0")
    untracked = git("ls-files", "--others", "--exclude-standard", "-z").split(b"\0")
    out = []
    for n in sorted({*tracked, *untracked} - {b""}):
        name = n.decode()
        try:
            with open(name, "rb") as fh:
                out.append((name, fh.read()))
        except FileNotFoundError:  # deleted but not yet staged
            continue
    return out


def problems(files: list[tuple[str, bytes]]) -> list[str]:
    found = []
    for name, content in files:
        path = PurePosixPath(name)
        if name.startswith(FORBIDDEN_PREFIXES):
            found.append(f"{name}: inside the data store / state (never commit)")
        if path.name in FORBIDDEN_NAMES:
            found.append(f"{name}: secrets or machine config file")
        if path.suffix.lower() in DATA_SUFFIXES:
            found.append(f"{name}: data file type {path.suffix} (data lives in the store)")
        if len(content) > SIZE_LIMIT and name not in SIZE_EXEMPT:
            found.append(f"{name}: {len(content)} bytes (> {SIZE_LIMIT}); data?")
        if name in SKIP_CONTENT or b"\0" in content[:8000]:
            continue
        text = content.decode("utf-8", errors="replace")
        for label, pattern in SECRET_PATTERNS.items():
            if pattern.search(text):
                found.append(f"{name}: looks like {label}")
        m = HOME_PATH.search(text)
        if m:
            found.append(f"{name}: machine-specific path {m.group(0)!r}")
        if (
            path.suffix in STRATEGY_SUFFIXES
            and not name.startswith(STRATEGY_HOMES)
            and STRATEGY_CODE.search(text)
        ):
            found.append(f"{name}: strategy code outside {STRATEGY_HOMES} (private store)")
        m = PRIVATE_ID.search(text)
        if m:
            found.append(f"{name}: real dataset/verdict ID {m.group(0)} (private/TODO.md)")
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--staged", action="store_true")
    mode.add_argument("--all", action="store_true")
    args = ap.parse_args()
    found = problems(staged() if args.staged else working_tree())
    if found:
        print("check_repo: refusing (this repo is public):", file=sys.stderr)
        for p in found:
            print(f"  - {p}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
