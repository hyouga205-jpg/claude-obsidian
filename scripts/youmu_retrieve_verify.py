#!/usr/bin/env python3
"""youmu branch: verification command for the wiki-retrieve capability.

``contracts --verify`` runs this from the product root without a vault
argument, so the vault is resolved the same way as the bridge (``YOUMU_VAULT``,
else the sibling ``claude-obsidian`` directory).

The check is the path wiki-query actually uses: one real BM25 query through the
bridged ``retrieve.py`` with ``--no-rerank``. It must exit 0 and print JSON with
at least one candidate. When the query fails, its exit code is returned as is;
an empty or unreadable result returns 1.

Why recall_health.py is not part of this check (measured 2026-09-13): it
returned exit 3 only because ollama did not answer, while the BM25 index was
healthy. wiki-query uses retrieval only when this verifier passes, so failing
on the reranker would turn off the BM25 retrieval that still works. The
reranker and index freshness are checked by the nightly recall_health run.

Why the upstream verifier is not used on this branch: it runs
``tests/test_retrieve.py``, which builds synthetic vaults (refused on native
Windows) and checks upstream's retrieval implementation, which this branch
replaces with the Youmu one.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from claude_obsidian import youmu_bridge as bridge


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if any(arg in ("-h", "--help") for arg in argv):
        print(__doc__)
        return 0
    try:
        vault = bridge.resolve_vault(None, product_root=HERE.parent, environ=os.environ)
    except bridge.BridgeError as exc:
        print(f"ERR {exc.code}: {exc}", file=sys.stderr)
        return 2

    query = subprocess.run(
        [sys.executable, str(HERE / "retrieve.py"), "--vault", str(vault), "wiki", "--top", "1", "--no-rerank"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(vault),
    )
    if query.returncode != 0:
        print(f"ERR YOUMU_QUERY_FAILED: retrieve.py exited {query.returncode}: {query.stderr.strip()[-300:]}", file=sys.stderr)
        return query.returncode
    text = query.stdout
    try:
        data = json.loads(text[text.index("{"):])
    except ValueError:
        print("ERR YOUMU_QUERY_UNREADABLE: retrieve.py did not print JSON", file=sys.stderr)
        return 1
    candidates = data.get("candidates") if isinstance(data, dict) else None
    if not isinstance(candidates, list):
        print("ERR YOUMU_QUERY_UNREADABLE: the JSON has no candidates list", file=sys.stderr)
        return 1
    if not candidates:
        print("ERR YOUMU_QUERY_EMPTY: the query returned no candidates; the index may be empty", file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, "vault": str(vault), "candidates": len(candidates)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
