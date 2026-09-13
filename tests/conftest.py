"""youmu branch: skip the upstream tests whose target this branch replaces on purpose.

Why: scripts/retrieve.py, bm25-index.py, contextual-prefix.py and rerank.py hand the
call over to the Youmu vault scripts (claude_obsidian/youmu_bridge.py, user ruling
2026-09-13), and the wiki-retrieve verifier is scripts/youmu_retrieve_verify.py.
The upstream tests below check the upstream implementation, so they cannot pass
here. Left running, they add 99 failures that hide real regressions.
The replacement is tested by tests/test_youmu_bridge.py; the Youmu scripts are
tested in the vault.

Skips are listed by exact file or test id, never by pattern, so a new upstream
test still runs. When a listed file is collected but a listed test id in it is
gone (upstream renamed or removed it), collection fails so the list cannot rot.
"""
from __future__ import annotations

import pytest

REPLACED_FILES = {
    "tests/test_retrieve.py": "retrieve.py and rerank.py hand over to the Youmu vault",
    "tests/test_bm25_index.py": "bm25-index.py hands over to the Youmu vault",
    "tests/test_contextual_prefix.py": "contextual-prefix.py hands over to the Youmu vault",
}

REPLACED_TESTS = {
    "tests/test_contracts.py::CanonicalContractTests::test_canonical_verifiers_are_behavioral_or_explain_their_absence":
        "the wiki-retrieve verifier is scripts/youmu_retrieve_verify.py (tests/test_youmu_bridge.py checks it)",
}


def pytest_collection_modifyitems(config, items):
    collected = {item.nodeid for item in items}
    collected_files = {nodeid.split("::", 1)[0] for nodeid in collected}
    missing = [
        nodeid for nodeid in REPLACED_TESTS
        if nodeid.split("::", 1)[0] in collected_files and nodeid not in collected
    ]
    if missing:
        raise pytest.UsageError(
            "tests/conftest.py lists upstream tests that no longer exist; update the list: " + ", ".join(missing)
        )
    for item in items:
        path = item.nodeid.split("::", 1)[0]
        reason = REPLACED_FILES.get(path) or REPLACED_TESTS.get(item.nodeid)
        if reason:
            item.add_marker(pytest.mark.skip(reason=f"youmu branch: {reason}"))
