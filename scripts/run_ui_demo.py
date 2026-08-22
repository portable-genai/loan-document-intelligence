#!/usr/bin/env python3
"""Launch the real local demo server and presenter walkthrough as one command."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent.parent
ORIGIN = "http://127.0.0.1:8093"


def wait_ready() -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            with urlopen(ORIGIN, timeout=1) as response:
                if response.status == 200:
                    return
        except URLError:
            time.sleep(0.2)
    raise RuntimeError(f"demo server did not become ready at {ORIGIN}")


def main(argv: list[str] | None = None) -> int:
    env = dict(os.environ)
    env.update(LOAN_DOC_PROFILE="local", PYTHONPATH=str(ROOT / "src"))
    server = subprocess.Popen(
        [sys.executable, str(ROOT / "scripts" / "loan_doc_demo_server.py")],
        cwd=ROOT,
        env=env,
    )
    try:
        wait_ready()
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "loan_doc_demo_playwright.py"),
                "--base-url",
                ORIGIN,
                *(argv or []),
            ],
            cwd=ROOT,
            env=env,
            check=False,
        )
        return completed.returncode
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
