"""Youmu の検索層への中継(youmu branch)の検査。

なぜ要るか(2026-09-13 ユーザー裁定「検索系は案2」): upstream v2.2.0 の検索スクリプトは Youmu の索引を読めない
(版番号と本文ハッシュの流儀が違う)。スキル wiki-query / wiki-retrieve は `$PRODUCT_ROOT/scripts/*.py --vault V ...`
を呼ぶ。この4本は upstream の実装を残したまま、`--vault`(または YOUMU_VAULT)が Youmu の vault を
指すときだけ、その vault の同名スクリプトへ引き渡す(2026-09-27。全部を置き換えると upstream の test が
Linux で7件落ちた。要件 F24)。検証コマンドは upstream のまま。
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
# scripts/ の *.py は全部「起動スクリプト」として検査される(tests/test_installed_tree_boundary.py)。
# 中継の本体は起動スクリプトではないので、製品パッケージの中に置く。
BRIDGE_PATH = ROOT / "claude_obsidian" / "youmu_bridge.py"
_spec = importlib.util.spec_from_file_location("_youmu_bridge", BRIDGE_PATH)
bridge = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bridge)

NAMES = ["retrieve", "bm25-index", "contextual-prefix", "rerank"]

STUB = """import json, os, sys
from pathlib import Path
Path(sys.argv[0]).with_suffix(".argv.json").write_text(json.dumps(sys.argv[1:]), encoding="utf-8")
print(json.dumps({"stub": Path(sys.argv[0]).stem, "argv": sys.argv[1:], "candidates": [{"page_path": "wiki/x.md"}]}))
raise SystemExit(int(os.environ.get("STUB_EXIT", "0")))
"""


def make_vault(base: Path, *, health_exit: int = 0) -> Path:
    vault = base / "claude-obsidian"
    (vault / ".vault-meta").mkdir(parents=True)
    (vault / "scripts").mkdir()
    for name in NAMES:
        (vault / "scripts" / f"{name}.py").write_bytes(STUB.encode("utf-8"))
    health = vault / "10-SYSTEM" / "scripts"
    health.mkdir(parents=True)
    (health / "recall_health.py").write_bytes(f"raise SystemExit({health_exit})\n".encode("utf-8"))
    return vault


# --- 引数の分解 ---------------------------------------------------------------

@pytest.mark.parametrize(
    "argv, vault, rest",
    [
        (["--vault", "V", "q", "--top", "5"], "V", ["q", "--top", "5"]),
        (["--vault=V", "build"], "V", ["build"]),
        (["q", "--vault", "V", "--no-rerank"], "V", ["q", "--no-rerank"]),
        (["stats"], None, ["stats"]),
    ],
)
def test_vault_option_is_removed_before_handing_over(argv, vault, rest):
    got_vault, got_rest, dropped = bridge.split_args(argv, name="retrieve")
    assert (got_vault, got_rest) == (vault, rest)
    assert dropped == []


def test_rerank_drops_the_model_option_it_does_not_understand():
    v, rest, dropped = bridge.split_args(["--vault", "V", "q", "--model", "nomic-embed-text", "--peek"], name="rerank")
    assert (v, rest) == ("V", ["q", "--peek"])
    assert dropped == ["--model", "nomic-embed-text"]
    v, rest, dropped = bridge.split_args(["q", "--model=nomic-embed-text"], name="rerank")
    assert rest == ["q"] and dropped == ["--model=nomic-embed-text"]


def test_other_scripts_keep_a_model_option_untouched():
    _, rest, dropped = bridge.split_args(["q", "--model", "x"], name="retrieve")
    assert rest == ["q", "--model", "x"] and dropped == []


# --- vault の決め方 -------------------------------------------------------------

def test_explicit_vault_is_used(tmp_path):
    vault = make_vault(tmp_path)
    assert bridge.resolve_vault(str(vault), product_root=tmp_path / "prod", environ={}) == vault.resolve()


def test_environment_overrides_the_default(tmp_path):
    vault = make_vault(tmp_path / "elsewhere")
    got = bridge.resolve_vault(None, product_root=tmp_path / "prod", environ={"YOUMU_VAULT": str(vault)})
    assert got == vault.resolve()


def test_default_is_the_sibling_named_claude_obsidian(tmp_path):
    vault = make_vault(tmp_path)
    (tmp_path / "claude-obsidian-product").mkdir()
    got = bridge.resolve_vault(None, product_root=tmp_path / "claude-obsidian-product", environ={})
    assert got == vault.resolve()


def test_a_directory_that_is_not_a_youmu_vault_is_refused(tmp_path):
    (tmp_path / "plain").mkdir()
    with pytest.raises(bridge.BridgeError) as info:
        bridge.resolve_vault(str(tmp_path / "plain"), product_root=tmp_path / "prod", environ={})
    assert info.value.code == "YOUMU_VAULT_INVALID"


def test_the_product_tree_itself_is_refused_so_the_bridge_cannot_call_itself(tmp_path):
    prod = make_vault(tmp_path)  # 形は vault でも、製品ツリーそのものなら拒否する
    with pytest.raises(bridge.BridgeError) as info:
        bridge.resolve_vault(str(prod), product_root=prod, environ={})
    assert info.value.code == "YOUMU_VAULT_INVALID"


# --- 実行 ---------------------------------------------------------------------------

def test_run_hands_over_to_the_vault_script_and_passes_the_exit_code(tmp_path):
    vault = make_vault(tmp_path)
    calls = []

    def runner(cmd, cwd=None):
        calls.append((cmd, cwd))
        return subprocess.CompletedProcess(cmd, 7)

    code = bridge.run("bm25-index", ["--vault", str(vault), "query", "q", "--top", "10"],
                      product_root=tmp_path / "prod", environ={}, runner=runner, python="PY")
    assert code == 7
    assert calls == [(["PY", str(vault.resolve() / "scripts" / "bm25-index.py"), "query", "q", "--top", "10"], str(vault.resolve()))]


def test_run_reports_a_refused_vault_without_running_anything(tmp_path):
    def runner(cmd, cwd=None):  # pragma: no cover - must not run
        raise AssertionError("must not run")

    err = io.StringIO()
    code = bridge.run("retrieve", ["--vault", str(tmp_path / "nope"), "q"],
                      product_root=tmp_path / "prod", environ={}, runner=runner, stderr=err)
    assert code == 2
    assert "YOUMU_VAULT_INVALID" in err.getvalue()


def test_run_says_when_it_dropped_an_option(tmp_path):
    vault = make_vault(tmp_path)
    err = io.StringIO()
    bridge.run("rerank", ["--vault", str(vault), "q", "--model", "m"], product_root=tmp_path / "prod",
               environ={}, runner=lambda cmd, cwd=None: subprocess.CompletedProcess(cmd, 0), python="PY", stderr=err)
    assert "--model" in err.getvalue()


def _must_not_run(cmd, cwd=None):  # pragma: no cover - must not run
    raise AssertionError("must not run")


def test_help_without_a_vault_explains_the_bridge_instead_of_failing(tmp_path):
    # 2026-09-13 実測: 製品ツリーだけを置いた環境(tests/test_installed_tree_boundary.py)で
    # `--help` が exit 2 になっていた。使い方を聞くだけで vault を要求しない。
    out, err = io.StringIO(), io.StringIO()
    code = bridge.run("retrieve", ["--help"], product_root=tmp_path / "prod", environ={},
                      runner=_must_not_run, stdout=out, stderr=err)
    assert code == 0, err.getvalue()
    assert "Youmu" in out.getvalue() and "YOUMU_VAULT" in out.getvalue()


def test_help_with_a_vault_shows_the_youmu_script_help(tmp_path):
    vault = make_vault(tmp_path)
    calls = []
    code = bridge.run("retrieve", ["-h"], product_root=tmp_path / "prod", environ={"YOUMU_VAULT": str(vault)},
                      runner=lambda cmd, cwd=None: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0),
                      python="PY", stdout=io.StringIO())
    assert code == 0
    assert calls == [["PY", str(vault.resolve() / "scripts" / "retrieve.py"), "-h"]]


@pytest.mark.parametrize("script", [f"{name}.py" for name in NAMES])
def test_help_entry_points_exit_zero_without_a_vault(tmp_path, script):
    # Youmu の vault を名指ししない呼び出しは upstream の実装が答える。
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / script), "--help"],
        capture_output=True, text=True, timeout=120, cwd=str(tmp_path),
        env={k: v for k, v in os.environ.items() if k != "YOUMU_VAULT"},
    )
    assert completed.returncode == 0, completed.stderr


# --- 4本の起動スクリプト: Youmu の vault のときだけ引き渡す ------------------------

@pytest.mark.parametrize("name", NAMES)
def test_each_entry_point_hands_over_when_the_vault_is_youmu(tmp_path, name):
    vault = make_vault(tmp_path)
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / f"{name}.py"), "--vault", str(vault), "arg1", "--flag"],
        capture_output=True, text=True, timeout=120, env={**os.environ, "STUB_EXIT": "5"},
    )
    assert completed.returncode == 5, completed.stderr
    recorded = json.loads((vault / "scripts" / f"{name}.argv.json").read_text(encoding="utf-8"))
    assert recorded == ["arg1", "--flag"]


@pytest.mark.parametrize("name", NAMES)
def test_each_entry_point_runs_upstream_for_any_other_vault(tmp_path, name):
    # F24(2026-09-27 実測): 全部を置き換えると upstream の検索と test が壊れる。
    # .vault-meta の無い vault は Youmu ではないので、同名の script があっても引き渡さない。
    vault = make_vault(tmp_path)
    for child in (vault / ".vault-meta",):
        child.rmdir()
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / f"{name}.py"), "--vault", str(vault), "--help"],
        capture_output=True, text=True, timeout=120, env={**os.environ, "STUB_EXIT": "5"},
    )
    assert completed.returncode == 0, completed.stderr
    assert not (vault / "scripts" / f"{name}.argv.json").exists()


def test_handover_declines_without_a_named_vault(tmp_path):
    assert bridge.handover("retrieve", ["q"], product_root=tmp_path / "prod", environ={},
                           runner=_must_not_run) is None


def test_handover_declines_a_vault_without_the_script(tmp_path):
    vault = make_vault(tmp_path)
    (vault / "scripts" / "retrieve.py").unlink()
    assert bridge.handover("retrieve", ["--vault", str(vault), "q"], product_root=tmp_path / "prod",
                           environ={}, runner=_must_not_run) is None


def test_handover_declines_a_vault_without_vault_meta(tmp_path):
    vault = make_vault(tmp_path)
    (vault / ".vault-meta").rmdir()
    assert bridge.handover("retrieve", ["--vault", str(vault), "q"], product_root=tmp_path / "prod",
                           environ={}, runner=_must_not_run) is None


def test_handover_declines_the_product_tree_itself(tmp_path):
    vault = make_vault(tmp_path)
    assert bridge.handover("retrieve", ["q"], product_root=vault,
                           environ={"YOUMU_VAULT": str(vault)}, runner=_must_not_run) is None


def test_handover_uses_the_environment_vault_and_passes_the_exit_code(tmp_path):
    vault = make_vault(tmp_path)
    calls = []
    code = bridge.handover(
        "retrieve", ["q"], product_root=tmp_path / "prod", environ={"YOUMU_VAULT": str(vault)},
        runner=lambda cmd, cwd=None: calls.append((cmd, cwd)) or subprocess.CompletedProcess(cmd, 7),
        python="PY",
    )
    assert code == 7
    assert calls == [(["PY", str(vault.resolve() / "scripts" / "retrieve.py"), "q"], str(vault.resolve()))]


def test_an_explicit_other_vault_wins_over_the_environment(tmp_path):
    youmu = make_vault(tmp_path)
    other = tmp_path / "plain vault"
    other.mkdir()
    assert bridge.handover("retrieve", ["--vault", str(other), "q"], product_root=tmp_path / "prod",
                           environ={"YOUMU_VAULT": str(youmu)}, runner=_must_not_run) is None


# --- 検証コマンドは upstream のまま ------------------------------------------------

def test_capability_verification_is_upstreams():
    # 以前は wiki-retrieve の検証を Youmu 用に差し替えていた。upstream の実装が残るので戻す。
    caps = (ROOT / "config" / "capabilities.json").read_text(encoding="utf-8")
    assert "youmu_retrieve_verify" not in caps
    assert not (ROOT / "scripts" / "youmu_retrieve_verify.py").exists()


if __name__ == "__main__":
    # upstream の `make test` は test file を直接走らせる。pytest の形の test を
    # 何も実行せずに通過させないため、ここで pytest に渡す(pytest が無ければ落ちる)。
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
