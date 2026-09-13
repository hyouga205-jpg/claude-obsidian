#!/usr/bin/env python3
"""youmu branch: hand this upstream entry point over to the Youmu vault's retrieve.py.

Why and how: claude_obsidian/youmu_bridge.py. The upstream implementation is in tag v2.2.0.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.dont_write_bytecode = True

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from claude_obsidian.youmu_bridge import run

# Importing this file (upstream tests do) must not run the Youmu script with the importer's argv.
if __name__ == "__main__":
    raise SystemExit(run("retrieve", sys.argv[1:], product_root=PLUGIN_ROOT))
