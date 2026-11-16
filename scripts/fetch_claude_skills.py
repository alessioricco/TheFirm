"""Download the Claude Code skills used to build the firm into .claude/skills/.

The skills come from Anthropic's official repository,
https://github.com/anthropics/skills, at the commit pinned below, so every
contributor gets the same files. .claude/skills/ is git-ignored: only this
list is in git.

    uv run python scripts/fetch_claude_skills.py            # the pinned commit
    uv run python scripts/fetch_claude_skills.py --ref main # the latest

To add a skill, put its folder name in SKILLS and a row in SKILLS.md; to
update them all, change COMMIT. These are skills for Claude Code sessions
working on this repo, not skills for the firm's own agents (those come
through Certo). Copied from Certo's script of the same name. Add a skill
only when current work needs it (SKILLS.md), and check its licence at the
pinned commit first.
"""

import argparse
import io
import os
import shutil
import sys
import tarfile
import urllib.request

REPO = "anthropics/skills"
COMMIT = "dbd4588f9e1033efb41dad4bef2f7947c8993d44"

SKILLS = {
    # Writing and testing skills for the firm's research agents (milestone 3).
    "skill-creator": "write and evaluate skills",
    # Testing the web UI in a browser with Playwright (milestone 4).
    "webapp-testing": "test a local web app in a browser",
    # Uncomment when the read-only MCP endpoint is built (milestone 4).
    # "mcp-builder": "build an MCP server",
    # A non-generic look for the web UI (milestone 4); check its licence first.
    # "frontend-design": "design a web UI",
}

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEST = os.path.join(ROOT, ".claude", "skills")


def download(ref: str) -> bytes:
    url = f"https://codeload.github.com/{REPO}/tar.gz/{ref}"
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read()


def extract(archive: bytes, names: list[str], dest: str) -> list[str]:
    """Extract skills/<name>/ for each name into dest/<name>/, replacing it."""
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        members = tar.getmembers()
        top = members[0].name.split("/")[0]
        found = []
        for name in names:
            prefix = f"{top}/skills/{name}/"
            mine = [m for m in members if m.name.startswith(prefix)]
            if not mine:
                continue
            target = os.path.join(dest, name)
            shutil.rmtree(target, ignore_errors=True)
            for member in mine:
                member.name = member.name[len(prefix) :]
                if member.name:
                    tar.extract(member, target, filter="data")
            found.append(name)
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ref", default=COMMIT, help="commit, tag or branch (default: pinned)")
    args = parser.parse_args(argv)

    print(f"Downloading {REPO} at {args.ref} ...")
    found = extract(download(args.ref), list(SKILLS), DEST)
    for name in SKILLS:
        status = "installed" if name in found else "NOT FOUND in the repository"
        print(f"  {name}: {status}")
    print(f"Skills are in {os.path.relpath(DEST, ROOT)}/ (git-ignored).")
    return 0 if len(found) == len(SKILLS) else 1


if __name__ == "__main__":
    sys.exit(main())
