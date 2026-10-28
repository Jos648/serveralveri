#!/usr/bin/env python3
"""Start the IdlePool API for access from other computers on the local network."""

import argparse
import os
import sys
from pathlib import Path

from idlepool import run_app


def main():
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    os.chdir(Path(__file__).resolve().parent)
    parser = argparse.ArgumentParser(description="IdlePool local network server")
    parser.add_argument("--port", type=int, default=8000, help="server port (default: 8000)")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    run_app("0.0.0.0", args.port, desktop=False)


if __name__ == "__main__":
    main()
