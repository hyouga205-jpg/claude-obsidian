"""Route vault-writing subcommands from native Windows into WSL (youmu branch).

Why (measured 2026-09-13): claude-obsidian v2 refuses vault writes on native
Windows with ``UNSUPPORTED_PLATFORM`` (init, transaction, mode set and
checkpoint alike), and the skills call the launcher directly
(``python3 "$CORE" transaction apply ...``). The launcher is the one place both
skills and people pass through, so the routing lives there.

Approval hashes bind to the environment that produced them, so a routed group
runs its review step (``inspect`` / dry-run) in WSL as well as its apply step.

Only native Windows routes. Inside WSL ``os.name`` is ``posix``, so the same
launcher never routes a second time. ``CLAUDE_OBSIDIAN_WSL=0`` turns routing off
and restores the native refusal.

Path translation is deliberately pure (no ``wslpath`` call per argument): a
drive path such as ``C:/x`` (or the same path written with backslashes) becomes ``/mnt/c/x``, and the Git Bash
spelling ``/c/x`` becomes ``/mnt/c/x``. Anything else, including relative paths,
is passed unchanged; the WSL process starts in the translated working directory.
A genuine WSL path whose first component is a single letter (``/a/b``) would be
misread as a drive path; callers on Windows do not produce those.
"""
from __future__ import annotations

import os
import subprocess
import sys
from typing import Callable, Mapping, Sequence, TextIO

ROUTED_GROUPS = frozenset(
    {"transaction", "capture", "extension", "migrate", "adopt", "init", "checkpoint"}
)
# Subcommands that stay native. ``mode`` is native except ``mode set`` (see needs_wsl).
# ``hook`` only writes to stdout; ``release`` writes to the product tree, not a vault.
# Every CLI subcommand must be in exactly one of the two sets
# (tests/test_wsl_route.py), so a subcommand added upstream fails a test until
# someone decides where it runs.
NATIVE_GROUPS = frozenset({"doctor", "hook", "lint", "contracts", "package", "release", "mode"})
DISABLE_ENV = "CLAUDE_OBSIDIAN_WSL"
_BACKSLASH = chr(92)


def needs_wsl(
    argv: Sequence[str],
    *,
    os_name: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Return whether this command line must run inside WSL."""

    os_name = os.name if os_name is None else os_name
    environ = os.environ if environ is None else environ
    if os_name != "nt" or environ.get(DISABLE_ENV) == "0":
        return False
    positional = [arg for arg in argv if not arg.startswith("-")]
    if not positional:
        return False
    head = positional[0]
    if head in ROUTED_GROUPS:
        return True
    return head == "mode" and len(positional) > 1 and positional[1] == "set"


def _is_drive_path(path: str) -> bool:
    return len(path) >= 3 and path[0].isalpha() and path[1] == ":" and path[2] in ("/", _BACKSLASH)


def _is_git_bash_path(path: str) -> bool:
    return len(path) >= 3 and path[0] == "/" and path[1].isalpha() and path[2] == "/"


def to_wsl_path(path: str) -> str:
    """Translate a Windows absolute path to its DrvFs mount; leave others alone."""

    if _is_drive_path(path):
        return "/mnt/" + path[0].lower() + path[2:].replace(_BACKSLASH, "/")
    if _is_git_bash_path(path):
        return "/mnt/" + path[1].lower() + path[2:]
    return path


def translate_arg(arg: str) -> str:
    """Translate one argument, including the value of ``--option=value``."""

    if arg.startswith("--") and "=" in arg:
        key, value = arg.split("=", 1)
        translated = to_wsl_path(value)
        return arg if translated == value else key + "=" + translated
    return to_wsl_path(arg)


def build_command(argv: Sequence[str], *, script: str, cwd: str) -> list[str]:
    """Build the WSL command that re-runs this launcher with translated paths."""

    return [
        "wsl.exe",
        "--cd",
        to_wsl_path(cwd),
        "-e",
        "python3",
        to_wsl_path(script),
        *(translate_arg(arg) for arg in argv),
    ]


def maybe_reexec(
    argv: Sequence[str],
    *,
    script: str,
    os_name: str | None = None,
    environ: Mapping[str, str] | None = None,
    runner: Callable[[list[str]], subprocess.CompletedProcess] | None = None,
    cwd: str | None = None,
    stderr: TextIO | None = None,
) -> int | None:
    """Run a routed command in WSL and return its exit code, or ``None`` to stay native."""

    if not needs_wsl(argv, os_name=os_name, environ=environ):
        return None
    runner = subprocess.run if runner is None else runner
    stderr = sys.stderr if stderr is None else stderr
    command = build_command(argv, script=script, cwd=os.getcwd() if cwd is None else cwd)
    try:
        completed = runner(command)
    except OSError as exc:
        print(
            "ERR WSL_UNAVAILABLE: cannot start WSL for a vault-writing command "
            f"({exc}); set {DISABLE_ENV}=0 to get the native refusal instead",
            file=stderr,
        )
        return 2
    return completed.returncode
