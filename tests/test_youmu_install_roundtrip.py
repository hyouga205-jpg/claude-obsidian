#!/usr/bin/env python3
"""Install -> test -> uninstall roundtrip check (PACKET W9, F25/F27/F28).

Everything happens inside ``tmp_path``:
clean checkout (git clone of this worktree) -> existing vault + fake HOME
snapshots -> init/adopt/setup-multi-agent -> doctor+lint -> uninstall ->
compare (a) pre-existing vault files unchanged, (b) HOME identical,
(c) added-files list, (d) upstream hardcoded paths present.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from claude_obsidian.capture import DEFAULT_INBOX, LEGACY_RAW  # noqa: E402
from claude_obsidian.ledgers import CLAIM_PATH, SOURCE_PATH  # noqa: E402

BRANCH = "youmu-w9-roundtrip"
GENERATED_AT = "2026-09-26T00:00:00Z"
INIT_OPERATION_ID = "w9-init-reviewed"
ADOPT_OPERATION_ID = "w9-adopt-reviewed"

GIT_BASH_CANDIDATE = Path("C:/Program Files/Git/bin/bash.exe")


def _git_bash() -> str:
    if os.name == "nt" and GIT_BASH_CANDIDATE.exists():
        return str(GIT_BASH_CANDIDATE)
    return "bash"


def _to_posix(path: Path) -> str:
    text = str(path)
    if os.name == "nt" and len(text) >= 2 and text[1] == ":":
        return "/" + text[0].lower() + text[2:].replace("\\", "/")
    return text.replace("\\", "/")


def _to_windows(posix_path: str) -> Path:
    if (
        os.name == "nt"
        and len(posix_path) >= 3
        and posix_path[0] == "/"
        and posix_path[1].isalpha()
        and posix_path[2] == "/"
    ):
        return Path(f"{posix_path[1].upper()}:/{posix_path[3:]}")
    return Path(posix_path)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot_files(root: Path) -> dict[str, tuple[str, str]]:
    """Map relative posix path -> (kind, sha256-or-link-target). No dir following."""
    found: dict[str, tuple[str, str]] = {}
    if not root.exists():
        return found

    def walk(current: Path) -> None:
        with os.scandir(current) as entries:
            for entry in entries:
                child = Path(entry.path)
                relative = child.relative_to(root).as_posix()
                if entry.is_symlink():
                    found[relative] = ("symlink", os.readlink(child))
                    continue
                if entry.is_dir(follow_symlinks=False):
                    walk(child)
                    continue
                if entry.is_file(follow_symlinks=False):
                    found[relative] = ("file", _sha256_file(child))

    walk(root)
    return found


def check_preexisting_unchanged(
    root: Path, before: dict[str, tuple[str, str]]
) -> list[str]:
    """Files present before install must exist with identical bytes (extras allowed)."""
    if not before:
        raise AssertionError("empty baseline: nothing was recorded before install")
    after = snapshot_files(root)
    problems: list[str] = []
    for relative in sorted(before):
        if relative not in after:
            problems.append(f"missing: {relative}")
        elif after[relative] != before[relative]:
            problems.append(f"changed: {relative}")
    if problems:
        raise AssertionError(
            f"{len(problems)} pre-existing vault file(s) changed:\n"
            + "\n".join(problems)
        )
    return problems


def check_home_identical(
    root: Path, before: dict[str, tuple[str, str]]
) -> dict[str, list[str]]:
    """HOME must match exactly: no missing, changed, or extra entries."""
    if not before:
        raise AssertionError("empty baseline: nothing was recorded before install")
    after = snapshot_files(root)
    detail = {
        "missing": sorted(set(before) - set(after)),
        "changed": sorted(
            key for key in set(before) & set(after) if before[key] != after[key]
        ),
        "extra": sorted(set(after) - set(before)),
    }
    if detail["missing"] or detail["changed"] or detail["extra"]:
        raise AssertionError(f"HOME differs after uninstall: {detail}")
    return detail


def list_added_files(root: Path, before_keys: set[str]) -> list[str]:
    return sorted(set(snapshot_files(root)) - before_keys)


def check_upstream_paths(vault: Path) -> list[str]:
    """Upstream hardcoded locations (taken from claude_obsidian constants)."""
    expected_files = [".claude-obsidian.json", SOURCE_PATH, CLAIM_PATH]
    expected_dirs = ["wiki", LEGACY_RAW, DEFAULT_INBOX]
    missing = [p for p in expected_files if not (vault / p).is_file()]
    missing += [p for p in expected_dirs if not (vault / p).is_dir()]
    if missing:
        raise AssertionError(f"upstream paths missing in vault: {missing}")
    return missing


def _run_cli(
    product_root: Path, *args: str, timeout: int = 180
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUTF8"] = "1"
    return subprocess.run(
        [sys.executable, str(product_root / "scripts" / "claude-obsidian.py"), *args],
        cwd=str(product_root),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def _reviewed_apply(
    product_root: Path, kind: str, vault: Path, operation_id: str
) -> None:
    dry = _run_cli(
        product_root,
        kind,
        str(vault),
        "--generated-at",
        GENERATED_AT,
        "--operation-id",
        operation_id,
    )
    if dry.returncode != 0:
        raise AssertionError(
            f"{kind} dry-run failed (rc={dry.returncode}): {dry.stderr[-2000:]}"
        )
    try:
        approval = json.loads(dry.stdout)["approved_plan_sha256"]
    except (json.JSONDecodeError, KeyError) as exc:
        raise AssertionError(
            f"{kind} dry-run emitted no approval hash: {dry.stdout[-1000:]}"
        ) from exc
    if not isinstance(approval, str) or not approval:
        raise AssertionError(
            f"{kind} dry-run approval hash is not a string: {approval!r}"
        )
    applied = _run_cli(
        product_root,
        kind,
        str(vault),
        "--generated-at",
        GENERATED_AT,
        "--operation-id",
        operation_id,
        "--approved-plan-sha256",
        approval,
        "--apply",
    )
    if applied.returncode != 0:
        raise AssertionError(
            f"{kind} apply failed, WSL is required and must not be skipped "
            f"(rc={applied.returncode}): {applied.stderr[-2000:]}"
        )


def _run_setup_multi_agent(
    product_root: Path, fake_home: Path, *args: str
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["HOME"] = _to_posix(fake_home)
    return subprocess.run(
        [
            _git_bash(),
            _to_posix(product_root / "scripts" / "setup-multi-agent.sh"),
            *args,
        ],
        cwd=str(product_root),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=120,
        check=False,
    )


def _uninstall_reported_links(
    fake_home: Path, apply_stdout: str, product_root: Path
) -> list[str]:
    removed: list[str] = []
    created = [
        line.split(None, 2)[2]
        for line in apply_stdout.splitlines()
        if line.startswith("CREATED ")
    ]
    if not created:
        raise AssertionError(
            "installer reported no CREATED links; nothing to uninstall"
        )
    for destination in created:
        target = _to_windows(destination)
        try:
            target.relative_to(fake_home)
        except ValueError as exc:
            raise AssertionError(
                f"installer link escapes fake HOME, refusing: {destination}"
            ) from exc
        if target.is_symlink() or target.is_file():
            target.unlink()
        elif target.is_dir():
            shutil.rmtree(target)
        else:
            raise AssertionError(
                f"installer link missing, cannot uninstall: {destination}"
            )
        removed.append(destination)
    leftover = [
        line.split(None, 2)[2]
        for line in apply_stdout.splitlines()
        if line.startswith("CREATED ") and _to_windows(line.split(None, 2)[2]).exists()
    ]
    if leftover:
        raise AssertionError(f"uninstall left links behind: {leftover}")
    return removed


def _make_existing_vault(vault: Path) -> None:
    (vault / ".obsidian" / "plugins" / "dataview").mkdir(parents=True)
    (vault / ".obsidian" / "app.json").write_text(
        json.dumps({"appearance": "obsidian", "newLinkFormat": "shortest"}, indent=2),
        encoding="utf-8",
    )
    (vault / ".obsidian" / "community-plugins.json").write_text(
        json.dumps(["dataview"]), encoding="utf-8"
    )
    (vault / ".obsidian" / "plugins" / "dataview" / "data.json").write_text(
        json.dumps({"query": "keep"}), encoding="utf-8"
    )
    (vault / "notes").mkdir()
    (vault / "notes" / "はじめに.md").write_text(
        "# はじめに\n\n既存のメモ。\n", encoding="utf-8"
    )
    (vault / "notes" / "todo.md").write_text("# todo\n\n- [ ] 残す\n", encoding="utf-8")
    (vault / "wiki").mkdir()
    (vault / "wiki" / "existing-note.md").write_text(
        "# existing\n\n導入前からある wiki のノート。\n", encoding="utf-8"
    )


def test_compare_vault_detects_content_change_and_rejects_empty_baseline(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    target = vault / "keep.md"
    target.write_text("original", encoding="utf-8")
    before = snapshot_files(vault)
    target.write_text("modified", encoding="utf-8")
    with pytest.raises(AssertionError, match="changed"):
        check_preexisting_unchanged(vault, before)
    with pytest.raises(AssertionError, match="empty baseline"):
        check_preexisting_unchanged(vault, {})


def test_compare_home_detects_leftover_and_rejects_empty_baseline(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / "keep.txt").write_text("keep", encoding="utf-8")
    before = snapshot_files(home)
    (home / "leftover.txt").write_text("leftover", encoding="utf-8")
    with pytest.raises(AssertionError, match="extra"):
        check_home_identical(home, before)
    (home / "leftover.txt").unlink()
    (home / "keep.txt").write_text("rewritten", encoding="utf-8")
    with pytest.raises(AssertionError, match="changed"):
        check_home_identical(home, before)
    (home / "keep.txt").unlink()
    with pytest.raises(AssertionError, match="missing"):
        check_home_identical(home, before)
    with pytest.raises(AssertionError, match="empty baseline"):
        check_home_identical(home, {})


def test_wsl_unavailable_must_fail_not_skip() -> None:
    source = Path(__file__).read_text(encoding="utf-8")
    assert "pytest" + ".skip" not in source
    assert "importor" + "skip" not in source


def test_install_test_uninstall_roundtrip(tmp_path: Path) -> None:
    started = time.monotonic()
    wsl_steps: list[str] = []
    clone = tmp_path / "clean-checkout"
    cloned = subprocess.run(
        ["git", "clone", "--branch", BRANCH, str(ROOT), str(clone)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=180,
        check=False,
    )
    assert cloned.returncode == 0, f"clean checkout failed: {cloned.stderr[-2000:]}"

    existing_vault = tmp_path / "existing-vault"
    existing_vault.mkdir()
    _make_existing_vault(existing_vault)
    fake_home = tmp_path / "fake-home"
    existing_skill = fake_home / ".config" / "opencode" / "skills" / "my-existing-skill"
    existing_skill.mkdir(parents=True)
    (existing_skill / "SKILL.md").write_text(
        "# my existing skill\n\n別人の skill。消さない。\n", encoding="utf-8"
    )
    vault_before = snapshot_files(existing_vault)
    home_before = snapshot_files(fake_home)
    assert vault_before and home_before, "baselines must not be empty"

    new_vault = tmp_path / "new-vault"
    _reviewed_apply(clone, "init", new_vault, INIT_OPERATION_ID)
    wsl_steps.append("init(new vault): dry-run + apply via scripts/claude-obsidian.py")
    assert (new_vault / ".claude-obsidian.json").is_file()
    _reviewed_apply(clone, "adopt", existing_vault, ADOPT_OPERATION_ID)
    wsl_steps.append(
        "adopt(existing vault): dry-run + apply via scripts/claude-obsidian.py"
    )

    preview = _run_setup_multi_agent(clone, fake_home, "--host", "opencode")
    assert preview.returncode == 0, f"setup preview failed: {preview.stderr[-2000:]}"
    applied = _run_setup_multi_agent(clone, fake_home, "--host", "opencode", "--apply")
    assert applied.returncode == 0, f"setup apply failed: {applied.stderr[-2000:]}"
    assert "CREATED opencode" in applied.stdout

    # Verification commands from the install-guide "Vault selection" section:
    # doctor reports selection/readiness, lint proves the adopted vault reads clean.
    doctor = _run_cli(clone, "doctor", "--vault", str(existing_vault), timeout=120)
    assert doctor.returncode == 0, f"doctor failed: {doctor.stderr[-2000:]}"
    assert json.loads(doctor.stdout)["ok"] is True
    linted = _run_cli(clone, "lint", "--vault", str(existing_vault), timeout=120)
    assert linted.returncode == 0, f"lint failed: {linted.stderr[-2000:]}"

    removed = _uninstall_reported_links(fake_home, applied.stdout, clone)

    check_preexisting_unchanged(existing_vault, vault_before)
    check_home_identical(fake_home, home_before)
    assert (existing_skill / "SKILL.md").is_file(), "existing skill must survive"
    added = list_added_files(existing_vault, set(vault_before))
    check_upstream_paths(existing_vault)

    elapsed = time.monotonic() - started
    print(
        "W9SUMMARY "
        + json.dumps(
            {
                "vault_before": len(vault_before),
                "home_before": len(home_before),
                "added_count": len(added),
                "added": added,
                "removed_links": len(removed),
                "wsl_steps": wsl_steps,
                "verify_commands": ["doctor --vault", "lint --vault"],
                "elapsed_seconds": round(elapsed, 1),
            },
            ensure_ascii=False,
        )
    )
