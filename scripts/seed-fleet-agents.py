#!/usr/bin/env python3
"""Compatibility shim: use scripts/seed-agents.py (folder contract + legacy briefs)."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

if __name__ == "__main__":
    target = Path(__file__).with_name("seed-agents.py")
    sys.argv[0] = str(target)
    runpy.run_path(str(target), run_name="__main__")
