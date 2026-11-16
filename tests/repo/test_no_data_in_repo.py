"""The repo is public: no market data, secrets, machine paths or big files.

Runs the same checker as the pre-commit hook and CI (`scripts/check_repo.py`)
over tracked and untracked files, so a bad file fails here before it is
committed.
"""

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def test_repo_check_passes() -> None:
    r = subprocess.run(
        [sys.executable, "scripts/check_repo.py", "--all"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr


def test_checker_catches_planted_problems(tmp_path: Path) -> None:
    sys.path.insert(0, str(REPO / "scripts"))
    try:
        from check_repo import problems
    finally:
        sys.path.pop(0)
    files = [
        ("data/raw/x.json", b"{}"),
        ("state/trials.jsonl", b"{}"),
        ("prices.csv", b"a,b"),
        (".env", b"X=1"),
        ("big.txt", b"x" * 300_000),
        # assembled at runtime so this file itself doesn't trip the checker
        ("leak.py", b"API_" + b'KEY = "abcdefghijklmnop1234"'),
        ("key.pem", b"-----BEGIN RSA " + b"PRIVATE KEY-----"),
        ("notes.md", b"see /Us" + b"ers/someone/file"),
        ("ok.env.example", b"TELEGRAM_BOT_TOKEN=\nOTHER_SETTING_NAME=1\n"),
    ]
    found = problems(files)
    flagged = {p.split(":")[0] for p in found}
    assert flagged == {
        "data/raw/x.json",
        "state/trials.jsonl",
        "prices.csv",
        ".env",
        "big.txt",
        "leak.py",
        "key.pem",
        "notes.md",
    }
