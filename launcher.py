#!/usr/bin/env python3
"""Homecoming Launcher entry point. The real work lives in the launcher package."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from launcher.app import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
