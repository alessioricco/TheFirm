"""A fake `certo mcp` for the tests: MCP over stdio, Certo's documented shapes.

Run as a program. Settings from the environment:
- FAKE_CERTO_LOG: a file each tools/call is appended to (JSON lines);
- FAKE_CERTO_VERSION: the version it reports (default 0.2.5);
- FAKE_CERTO_POLLS: run_status says "running" this many times first (default 1);
- FAKE_CERTO_VERDICT: the run's verdict (default verified);
- FAKE_CERTO_DIE_ON: a tool name that makes the server exit (a crash).
"""

from __future__ import annotations

import json
import os
import sys

VERSION = os.environ.get("FAKE_CERTO_VERSION", "0.2.5")
POLLS = int(os.environ.get("FAKE_CERTO_POLLS", "1"))
VERDICT = os.environ.get("FAKE_CERTO_VERDICT", "verified")
LOG = os.environ.get("FAKE_CERTO_LOG")
DIE_ON = os.environ.get("FAKE_CERTO_DIE_ON")

runs: dict[str, dict] = {}


def reply(msg_id, result=None, error=None) -> None:
    out = {"jsonrpc": "2.0", "id": msg_id}
    out["result" if error is None else "error"] = result if error is None else error
    sys.stdout.write(json.dumps(out) + "\n")
    sys.stdout.flush()


def tool_result(data) -> dict:
    structured = data if isinstance(data, dict) else {"result": data}
    return {
        "content": [{"type": "text", "text": json.dumps(data)}],
        "structuredContent": structured,
        "isError": False,
    }


def tool_error(text: str) -> dict:
    return {"content": [{"type": "text", "text": f"Error executing tool: {text}"}], "isError": True}


def result_v1(run: dict) -> dict:
    return {
        "result_version": 1,
        "certo_version": VERSION,
        "task": run["task"],
        "project": run["project"],
        "workspace": f"/ws/{run['project']}",
        "verdict": VERDICT,
        "checks": [{"name": "pytest", "status": "passed", "summary": "pytest passed"}],
        "files_changed": {
            "created": ["strategy.py", "test_strategy.py"],
            "modified": [],
            "deleted": [],
        },
        "usage": {
            "duration_seconds": 42.5,
            "total_tokens": 12000,
            "prompt_tokens": 10000,
            "completion_tokens": 2000,
            "cost": 0.0,
            "cost_known": True,
            "models": [{"model": run["model"]}],
        },
        "error": None,
    }


def call(name: str, args: dict):
    if LOG:
        with open(LOG, "a") as fh:
            fh.write(json.dumps({"tool": name, "args": args}) + "\n")
    if name == DIE_ON:
        sys.exit(3)
    if name == "run_task":
        run_id = f"r{len(runs) + 1}"
        runs[run_id] = {
            "task": args.get("task"),
            "project": args.get("project"),
            "model": args.get("model"),
            "polls": 0,
            "state": "running",
        }
        return tool_result(
            {
                "run_id": run_id,
                "state": "running",
                "task": args.get("task"),
                "project": args.get("project"),
                "workspace": "/ws",
            }
        )
    if name in ("run_status", "run_events", "run_cancel"):
        run = runs.get(args.get("run_id"))
        if run is None:
            return tool_error(f"no run with id {args.get('run_id')!r}")
        if name == "run_cancel":
            run["state"] = "cancelled"
        if name == "run_events":
            since = int(args.get("since", 0))
            events = [
                {"seq": i, "kind": "message", "role": "agent", "text": f"step {i}", "at": 0}
                for i in range(since, min(since + 2, 3))
            ]
            return tool_result(
                {
                    "run_id": args["run_id"],
                    "state": run["state"],
                    "events": events,
                    "next": since + len(events),
                }
            )
        if run["state"] == "running":
            run["polls"] += 1
            if run["polls"] > POLLS:
                run["state"] = "finished"
        done = run["state"] != "running"
        result = result_v1(run) if done and run["state"] == "finished" else None
        return tool_result(
            {
                "run_id": args["run_id"],
                "state": run["state"],
                "verdict": result["verdict"] if result else None,
                "passed": (result["verdict"] == "verified") if result else None,
                "result": result,
            }
        )
    if name == "run_resume":
        return tool_result(
            {"run_id": None, "state": "nothing_to_do", "message": "nothing to resume"}
        )
    return tool_error(f"unknown tool {name}")


def main() -> None:
    for line in sys.stdin:
        if not line.strip():
            continue
        msg = json.loads(line)
        method, msg_id = msg.get("method"), msg.get("id")
        if method == "initialize":
            reply(
                msg_id,
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "certo", "version": VERSION},
                },
            )
        elif method == "tools/call":
            reply(msg_id, call(msg["params"]["name"], msg["params"].get("arguments", {})))
        elif msg_id is not None:
            reply(msg_id, error={"code": -32601, "message": f"no method {method}"})


if __name__ == "__main__":
    print("fake certo MCP server on stdio", file=sys.stderr)
    main()
