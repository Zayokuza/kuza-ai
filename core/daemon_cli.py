#!/usr/bin/env python3
"""Small, injection-safe command-line client for the Kuza daemon."""

from __future__ import annotations

import argparse
import json
import socket
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.config import KUZA_STATE_DIR


SOCKET_FILE = KUZA_STATE_DIR / "kuza-v2.sock"
MAX_RESPONSE_BYTES = 2_000_000


def _port_healthy(port: int) -> bool:
    """Return whether a local llama-server has finished loading."""
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/health", timeout=2
        ) as response:
            if response.status != 200:
                return False
            body = json.loads(response.read(64_000).decode("utf-8"))
            return body.get("status") == "ok"
    except (OSError, ValueError, json.JSONDecodeError):
        return False


def _print_model_status() -> None:
    """Print coder and planner status without generating temporary scripts."""
    from utils.config import (
        KUZA_BACKEND,
        KUZA_PLANNER_BACKEND,
        OPENROUTER_MODEL,
        OPENROUTER_PLANNER_MODEL,
        QWEN_7B_MMAP,
        UNLIMITEDCLAUDE_MODEL,
        UNLIMITEDCLAUDE_PLANNER_MODEL,
        is_remote_backend,
        is_remote_planner_backend,
    )

    if is_remote_backend():
        model = (
            UNLIMITEDCLAUDE_MODEL
            if KUZA_BACKEND == "unlimitedclaude"
            else OPENROUTER_MODEL
        )
        print(f"7B model:       {KUZA_BACKEND} ({model})")
    elif _port_healthy(8080):
        mmap_status = "enabled" if QWEN_7B_MMAP else "disabled"
        print(f"7B model:       loaded   (mmap: {mmap_status})")
    else:
        print("7B model:       not loaded")

    if is_remote_planner_backend():
        model = (
            UNLIMITEDCLAUDE_PLANNER_MODEL
            if KUZA_PLANNER_BACKEND == "unlimitedclaude"
            else OPENROUTER_PLANNER_MODEL
        )
        suffix = f" [{KUZA_PLANNER_BACKEND}]" if KUZA_PLANNER_BACKEND != KUZA_BACKEND else ""
        print(f"Planner:        {KUZA_PLANNER_BACKEND} ({model}){suffix}")
    elif _port_healthy(8081):
        print("Planner:        0.5B local (port 8081)")
    else:
        print("Planner:        0.5B not loaded (port 8081)")


def _print_planner_paths() -> None:
    from utils.config import LLAMA_SERVER_BIN, PLANNER_MODEL_PATH

    print(PLANNER_MODEL_PATH)
    print(LLAMA_SERVER_BIN)


def _create_default_config() -> None:
    from core.daemon_config import create_default_config

    path = create_default_config()
    print(f"Default config created at: {path}")


def send_command(command: str, data: dict | None = None, timeout: float = 30.0) -> dict:
    """Send one JSON request over the local Unix socket."""
    if not SOCKET_FILE.is_socket():
        raise ConnectionError("Kuza daemon is not running")

    request = json.dumps({"cmd": command, "data": data or {}}).encode("utf-8")
    chunks = bytearray()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout)
        client.connect(str(SOCKET_FILE))
        client.sendall(request)
        while True:
            chunk = client.recv(65536)
            if not chunk:
                break
            chunks.extend(chunk)
            if len(chunks) > MAX_RESPONSE_BYTES:
                raise RuntimeError("Daemon response exceeded the safety limit")

    if not chunks:
        raise ConnectionError("Daemon closed the connection without a response")
    response = json.loads(chunks.decode("utf-8"))
    if response.get("status") == "error":
        raise RuntimeError(response.get("message", "Unknown daemon error"))
    return response


def _print_status() -> None:
    health = send_command("health", {}, timeout=5)
    tasks = health.get("tasks", {})
    stuck = tasks.get("stuck", [])
    print("Kuza-v2 Daemon Status")
    print("=" * 40)
    print(f"PID:            {health.get('pid', 'N/A')}")
    print("Status:         Running")
    print(f"Uptime:         {health.get('uptime_seconds', 0)} seconds")
    print(f"Memory:         {health.get('memory_mb', 0)} MB")
    print("\nTask Queue:")
    print(f"  Pending:      {tasks.get('pending', 0)}")
    print(f"  Stuck:        {', '.join(map(str, stuck)) if stuck else 'None'}")
    print(f"\nRecent Actions: {health.get('recent_actions', 0)} logged")
    print(f"State Database: {KUZA_STATE_DIR / 'state.db'}")


def _print_tasks(target: str) -> None:
    if target == "list":
        result = send_command("task", {"limit": 20}, timeout=10)
        print("Tasks:")
        for task in result.get("tasks", []):
            icon = {"done": "✓", "running": "⟳", "pending": "○", "failed": "✗"}.get(
                task.get("status"), "?"
            )
            description = str(task.get("description", ""))[:50].replace("\n", " ")
            print(f"  [{task.get('id')}] {icon} {description}")
        return

    try:
        task_id = int(target)
    except ValueError as exc:
        raise ValueError("Task ID must be an integer or 'list'") from exc
    result = send_command("task", {"id": task_id}, timeout=10)
    task = result.get("task", {})
    icon = {"done": "✓", "running": "⟳", "pending": "○", "failed": "✗"}.get(
        task.get("status"), "?"
    )
    print(f"Task {task.get('id')}: {task.get('description', '')}")
    print(f"Status: {icon} {task.get('status', 'unknown')}")
    if task.get("result"):
        print(f"Result: {str(task['result'])[:200]}")
    for field, label in (("created_at", "Created"), ("completed_at", "Completed")):
        if task.get(field):
            print(f"{label}: {datetime.fromtimestamp(task[field])}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Kuza daemon client")
    subparsers = parser.add_subparsers(dest="action", required=True)
    subparsers.add_parser("status")
    subparsers.add_parser("models")
    subparsers.add_parser("planner-paths")
    subparsers.add_parser("config")

    task = subparsers.add_parser("task")
    task.add_argument("target")

    cancel = subparsers.add_parser("cancel")
    cancel.add_argument("task_id", type=int)

    queue = subparsers.add_parser("queue")
    queue.add_argument("--prompt", required=True)
    queue.add_argument("--yolo", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.action == "status":
            _print_status()
        elif args.action == "models":
            _print_model_status()
        elif args.action == "planner-paths":
            _print_planner_paths()
        elif args.action == "config":
            _create_default_config()
        elif args.action == "task":
            _print_tasks(args.target)
        elif args.action == "cancel":
            result = send_command("cancel", {"id": args.task_id}, timeout=10)
            print(result.get("message", "Cancelled"))
        elif args.action == "queue":
            result = send_command(
                "command",
                {"prompt": args.prompt, "yolo": args.yolo},
                timeout=55,
            )
            print(json.dumps(result, indent=2))
        return 0
    except (ConnectionError, OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
