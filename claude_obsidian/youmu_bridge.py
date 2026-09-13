"""Hand upstream retrieval entry points over to the Youmu vault scripts (youmu branch).

Why (user ruling 2026-09-13, "retrieval stays Youmu's"): upstream v2.2.0's
retrieval scripts cannot read the Youmu index (a different schema version and a
different page-body hash convention), while the wiki-query / wiki-retrieve
skills call ``$PRODUCT_ROOT/scripts/<name>.py --vault VAULT ...``. On this
branch those four entry points hand the call over to the same-named script in
the Youmu vault.

The Youmu scripts find their vault from their own location and do not take
``--vault``, so the option is validated and removed. Youmu's ``rerank.py``
chooses its embedding model itself, so ``--model`` is dropped with a notice on
stderr. Everything else passes through unchanged, and the Youmu script's exit
code is returned as is.

Vault resolution: an explicit ``--vault``; else ``YOUMU_VAULT``; else the sibling
directory ``claude-obsidian`` next to this product tree (the convention
``co.sh`` uses in the other direction). A directory is accepted only when it
has ``.vault-meta/``, and never when it is this product tree itself, which would
hand the call straight back to this bridge.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, Mapping, Sequence, TextIO

DEFAULT_VAULT_NAME = "claude-obsidian"
VAULT_ENV = "YOUMU_VAULT"
_DROPPED_OPTIONS = {"rerank": ("--model",)}
HELP = """usage: {name}.py [--vault VAULT] [arguments for the Youmu script]

youmu branch: this upstream entry point hands the call over to the Youmu
vault's scripts/{name}.py. The vault is --vault, else YOUMU_VAULT, else the
directory claude-obsidian next to this product tree; it must have .vault-meta/.
Run --help with a vault to see the Youmu script's own options."""


class BridgeError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def split_args(argv: Sequence[str], *, name: str) -> tuple[str | None, list[str], list[str]]:
    """Return ``(vault, arguments to pass on, dropped arguments)``."""

    vault: str | None = None
    rest: list[str] = []
    dropped: list[str] = []
    drop = _DROPPED_OPTIONS.get(name, ())
    items = list(argv)
    index = 0
    while index < len(items):
        arg = items[index]
        if arg == "--vault" and index + 1 < len(items):
            vault = items[index + 1]
            index += 2
            continue
        if arg.startswith("--vault="):
            vault = arg.split("=", 1)[1]
            index += 1
            continue
        if arg.split("=", 1)[0] in drop:
            if "=" in arg:
                dropped.append(arg)
                index += 1
            else:
                dropped.extend(items[index:index + 2])
                index += 2
            continue
        rest.append(arg)
        index += 1
    return vault, rest, dropped


def resolve_vault(
    vault_arg: str | None,
    *,
    product_root: Path | str,
    environ: Mapping[str, str],
) -> Path:
    """Resolve and check the Youmu vault this call should go to."""

    product = Path(product_root).resolve()
    candidate = vault_arg or environ.get(VAULT_ENV) or str(product.parent / DEFAULT_VAULT_NAME)
    vault = Path(candidate).resolve()
    if vault == product:
        raise BridgeError(
            "YOUMU_VAULT_INVALID",
            f"{vault} is this product tree; handing over would call this bridge again",
        )
    if not (vault / ".vault-meta").is_dir():
        raise BridgeError("YOUMU_VAULT_INVALID", f"{vault} has no .vault-meta directory")
    return vault


def run(
    name: str,
    argv: Sequence[str],
    *,
    product_root: Path | str,
    environ: Mapping[str, str] | None = None,
    runner: Callable[..., subprocess.CompletedProcess] | None = None,
    python: str | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run the Youmu script ``name`` for this command line and return its exit code."""

    environ = os.environ if environ is None else environ
    runner = subprocess.run if runner is None else runner
    python = sys.executable if python is None else python
    stdout = sys.stdout if stdout is None else stdout
    stderr = sys.stderr if stderr is None else stderr
    vault_arg, rest, dropped = split_args(argv, name=name)
    try:
        vault = resolve_vault(vault_arg, product_root=product_root, environ=environ)
        script = vault / "scripts" / f"{name}.py"
        if not script.is_file():
            raise BridgeError("YOUMU_VAULT_INVALID", f"{script} does not exist")
    except BridgeError as exc:
        # Asking for usage must not require a vault (an installed product tree has none).
        if any(arg in ("-h", "--help") for arg in rest):
            print(HELP.format(name=name), file=stdout)
            print(f"NOTE: no Youmu vault here ({exc}); the options above are the bridge's only", file=stderr)
            return 0
        print(f"ERR {exc.code}: {exc} (Youmu retrieval bridge, claude_obsidian/youmu_bridge.py)", file=stderr)
        return 2
    if dropped:
        print(
            f"NOTE: dropped {' '.join(dropped)}; the Youmu {name}.py decides this itself",
            file=stderr,
        )
    completed = runner([python, str(script), *rest], cwd=str(vault))
    return completed.returncode
