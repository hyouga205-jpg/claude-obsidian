#!/usr/bin/env python3
"""Compatibility entry point for plugin hooks and legacy script callers."""

from __future__ import annotations

import sys
from pathlib import Path

sys.dont_write_bytecode = True

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

# youmu branch: on native Windows, vault-writing subcommands run inside WSL
# (claude_obsidian/wsl_route.py). This must happen before the CLI import.
from claude_obsidian.wsl_route import maybe_reexec

_routed = maybe_reexec(sys.argv[1:], script=str(Path(__file__).resolve()))
if _routed is not None:
    raise SystemExit(_routed)

from claude_obsidian.cli import main


raise SystemExit(main())
