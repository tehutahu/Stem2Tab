"""Run the minimum local Web/API pair. Ctrl-C stops only owned children.

No package installation, Redis, Celery, model download or OS configuration.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request


def stop_owned_group(child: subprocess.Popen) -> None:
    """Stop a session created by this launcher, even if its leader exited first."""
    try:
        os.killpg(child.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        child.wait(timeout=15)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    child.wait(timeout=5)


def main() -> int:
    """Keep both foreground services together, including startup failure cleanup."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--api-port", type=int, default=8421)
    parser.add_argument("--web-port", type=int, default=4291)
    parser.add_argument("--data", type=Path, default=Path(".local-data"))
    parser.add_argument("--check", action="store_true", help="Read existing pair health; never starts processes")
    args = parser.parse_args()
    if not 1 <= args.api_port <= 65535 or not 1 <= args.web_port <= 65535 or args.api_port == args.web_port:
        parser.error("Use distinct valid ports")
    root = Path(__file__).resolve().parents[1]
    if args.check:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{args.web_port}/api/v1/transcriptions/health", timeout=3) as response:
                print(json.dumps(json.load(response), ensure_ascii=False))
            return 0
        except (OSError, ValueError, urllib.error.URLError) as exc:
            print(f"Web/API pair unavailable: {type(exc).__name__}", file=sys.stderr)
            return 1
    for port in (args.api_port, args.web_port):
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                parser.error(f"Port {port} is already in use; its owner was not stopped")
    if not (root/"frontend/dist/index.html").is_file():
        parser.error("Build first: cd frontend && npm run build")
    data = args.data.resolve()
    data.mkdir(parents=True, exist_ok=True)
    children: list[subprocess.Popen] = []
    stopping = False
    def stop(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        env = {**os.environ, "FILE_BUCKET_PATH": str(data), "PYTHONPATH": str(root/"backend"),
               "API_PROXY_TARGET": f"http://127.0.0.1:{args.api_port}", "WEB_PORT": str(args.web_port)}
        children.append(subprocess.Popen([str(args.python.absolute()), "-m", "uvicorn", "src.api.main:app", "--host", "127.0.0.1", "--port", str(args.api_port)], cwd=root/"backend", env=env, start_new_session=True))
        children.append(subprocess.Popen(["npm", "run", "preview", "--", "--host", "127.0.0.1", "--strictPort"], cwd=root/"frontend", env=env, start_new_session=True))
        print(f"Web http://127.0.0.1:{args.web_port} / API http://127.0.0.1:{args.api_port}; Ctrl-C stops this pair. Data: {data}", flush=True)
        while not stopping:
            if any(child.poll() is not None for child in children):
                return 1
            time.sleep(.2)
        return 0
    finally:
        for child in reversed(children):
            stop_owned_group(child)


if __name__ == "__main__":
    raise SystemExit(main())
