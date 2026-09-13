"""ネイティブ Windows から vault 書き込み系のサブコマンドを WSL へ回す経路の検査(youmu branch)。

なぜ要るか: v2.x は vault への書き込みをネイティブ Windows で UNSUPPORTED_PLATFORM として拒否する。
スキルは `python3 "$CORE" transaction apply ...` を直接呼ぶので、入口の起動スクリプトで振り分ける。
承認ハッシュは実行環境に結びつくので、確認(inspect / dry-run)と適用を同じ側で実行する必要がある。
"""
from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path

import pytest

from claude_obsidian import wsl_route

BS = chr(92)
ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "scripts" / "claude-obsidian.py"

ROUTED = [
    ["transaction", "inspect", "C:/b.json", "--vault", "C:/v"],
    ["transaction", "apply", "C:/b.json", "--vault", "C:/v", "--approved-plan-sha256", "ab" * 32],
    ["transaction", "recover", "--vault", "C:/v"],
    ["capture", "plan", "C:/src.pdf", "--vault", "C:/v"],
    ["capture", "apply", "--vault", "C:/v"],
    ["extension", "dragonscale", "--vault", "C:/v"],
    ["migrate", "--vault", "C:/v"],
    ["adopt", "C:/v"],
    ["init", "C:/new-vault"],
    ["checkpoint", "init-20260913T052156Z", "--vault", "C:/v"],
    ["mode", "set", "generic", "--vault", "C:/v"],
]
NATIVE = [
    [],
    ["--version"],
    ["doctor", "--vault", "C:/v"],
    ["lint", "--vault", "C:/v"],
    ["contracts"],
    ["package", "validate"],
    ["release", "audit"],
    ["hook", "session-start"],
    ["mode", "get", "--vault", "C:/v"],
]


def _subcommands(parser):
    import argparse

    action = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    return action.choices


def test_every_cli_subcommand_is_classified_as_routed_or_native():
    # 版を上げたときの検出器。upstream がサブコマンドを足すと、どちらの一覧にも無いのでここで落ちる。
    # 書き込み系なら ROUTED_GROUPS、読み取り系なら NATIVE_GROUPS へ足す(YOUMU.md の版上げ手順)。
    from claude_obsidian.cli import build_parser

    top = set(_subcommands(build_parser()))
    assert wsl_route.ROUTED_GROUPS.isdisjoint(wsl_route.NATIVE_GROUPS)
    assert top == wsl_route.ROUTED_GROUPS | wsl_route.NATIVE_GROUPS
    # mode だけは入れ子で分かれる(get は読み取り、set は書き込み)。
    assert set(_subcommands(_subcommands(build_parser())["mode"])) == {"get", "set"}


@pytest.mark.parametrize("argv", ROUTED, ids=lambda a: " ".join(a[:2]))
def test_write_groups_are_routed_on_native_windows(argv):
    assert wsl_route.needs_wsl(argv, os_name="nt", environ={}) is True


@pytest.mark.parametrize("argv", NATIVE, ids=lambda a: " ".join(a[:2]) or "(empty)")
def test_read_only_commands_stay_native(argv):
    assert wsl_route.needs_wsl(argv, os_name="nt", environ={}) is False


def test_nothing_is_routed_outside_windows():
    # Inside WSL the same launcher runs with os.name == "posix"; routing again would loop.
    assert wsl_route.needs_wsl(["transaction", "apply", "/mnt/c/b.json"], os_name="posix", environ={}) is False


def test_routing_can_be_disabled_explicitly():
    assert wsl_route.needs_wsl(["init", "C:/v"], os_name="nt", environ={"CLAUDE_OBSIDIAN_WSL": "0"}) is False


@pytest.mark.parametrize(
    "given, expected",
    [
        ("C:/Users/a/b.json", "/mnt/c/Users/a/b.json"),
        ("C:" + BS + "Users" + BS + "a", "/mnt/c/Users/a"),
        ("D:/x", "/mnt/d/x"),
        ("/c/Users/a", "/mnt/c/Users/a"),
    ],
)
def test_windows_absolute_paths_are_translated(given, expected):
    assert wsl_route.to_wsl_path(given) == expected
    assert wsl_route.translate_arg(given) == expected


@pytest.mark.parametrize(
    "arg",
    ["bundle.json", "generic", "init-20260913T052156Z", "/tmp/x", "--apply", "ab" * 32, "C:", "c:relative"],
)
def test_non_paths_and_relative_paths_are_left_alone(arg):
    assert wsl_route.translate_arg(arg) == arg


def test_option_with_equals_is_translated():
    assert wsl_route.translate_arg("--vault=C:/v") == "--vault=/mnt/c/v"


def test_command_runs_the_same_launcher_inside_wsl_from_the_translated_cwd():
    cmd = wsl_route.build_command(
        ["transaction", "apply", "C:/b.json", "--vault", "C:/v"],
        script="C:/p/scripts/claude-obsidian.py",
        cwd="C:/work",
    )
    assert cmd == [
        "wsl.exe", "--cd", "/mnt/c/work", "-e", "python3", "/mnt/c/p/scripts/claude-obsidian.py",
        "transaction", "apply", "/mnt/c/b.json", "--vault", "/mnt/c/v",
    ]


def test_exit_code_of_the_wsl_run_is_passed_through():
    calls = []

    def runner(cmd):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 3)

    code = wsl_route.maybe_reexec(
        ["init", "C:/v"], script="C:/p/scripts/claude-obsidian.py",
        os_name="nt", environ={}, runner=runner, cwd="C:/work",
    )
    assert code == 3
    assert calls == [wsl_route.build_command(["init", "C:/v"], script="C:/p/scripts/claude-obsidian.py", cwd="C:/work")]


def test_not_routed_returns_none_and_runs_nothing():
    def runner(cmd):  # pragma: no cover - must not be called
        raise AssertionError("runner must not run for a native command")

    assert wsl_route.maybe_reexec(
        ["doctor"], script="C:/p/scripts/claude-obsidian.py", os_name="nt", environ={}, runner=runner, cwd="C:/work",
    ) is None


def test_missing_wsl_fails_closed_with_a_named_error():
    def runner(cmd):
        raise FileNotFoundError("wsl.exe")

    err = io.StringIO()
    code = wsl_route.maybe_reexec(
        ["init", "C:/v"], script="C:/p/scripts/claude-obsidian.py",
        os_name="nt", environ={}, runner=runner, cwd="C:/work", stderr=err,
    )
    assert code != 0
    assert "WSL_UNAVAILABLE" in err.getvalue()


def test_launcher_routes_before_importing_the_cli():
    text = LAUNCHER.read_text(encoding="utf-8")
    assert "maybe_reexec" in text
    assert text.index("maybe_reexec") < text.index("from claude_obsidian.cli import main")


def test_launcher_still_runs_read_only_commands_natively():
    completed = subprocess.run(
        [sys.executable, str(LAUNCHER), "mode", "--help"], capture_output=True, text=True, timeout=60,
    )
    assert completed.returncode == 0
    assert "get" in completed.stdout and "set" in completed.stdout
