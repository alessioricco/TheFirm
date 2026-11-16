"""The `firm` command line. Grows into a client of the control API (milestone 4)."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from firm import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="firm", description="The firm: paper trading, v1.")
    parser.add_argument("--version", action="version", version=f"firm {__version__}")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("version", help="print the version")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command in (None, "version"):
        print(f"firm {__version__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
