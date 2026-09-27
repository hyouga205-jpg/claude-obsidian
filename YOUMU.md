# branch `youmu` — upstream との差分と版上げの手順

この branch は upstream(AgriciDaniel/claude-obsidian)の tag に、Youmu(利用者の Obsidian vault
`multi-llm/claude-obsidian`)で使うための変更を足したもの。**upstream との差分はこの branch にだけ置く。**
変更の理由と実測は vault の `05-DAILY/handoffs/2026-09-13-handoff-claude-obsidian-upstream-v2-adoption.md`。

## 差分(この branch にだけある変更)

| file | 何をするか | なぜ |
|---|---|---|
| `scripts/claude-obsidian.py`(4行)・`claude_obsidian/wsl_route.py`・`tests/test_wsl_route.py` | vault に書くサブコマンドを、ネイティブ Windows では WSL の `python3` で実行する | upstream はネイティブ Windows での vault 書き込みを `UNSUPPORTED_PLATFORM` で拒否する |
| `scripts/retrieve.py` `bm25-index.py` `contextual-prefix.py` `rerank.py`(各末尾の9行)・`claude_obsidian/youmu_bridge.py`・`tests/test_youmu_bridge.py` | upstream の実装はそのまま残し、`--vault`(または `YOUMU_VAULT`)が Youmu の vault(`.vault-meta/` と `scripts/<name>.py` がある)を指すときだけ、その vault の同名スクリプトへ引き渡す | 検索は Youmu の実装を使う(利用者の裁定)。upstream の検索スクリプトは Youmu の索引を読めない。**全部を置き換えると upstream の検索と test が壊れる**(2026-09-27、Linux で7件。要件 F24) |
| `tests/test_wsl_route.py`・`tests/test_youmu_bridge.py` の末尾 | file を直接走らせたときに pytest で自分を実行する | upstream の `make test` は test file を直接走らせる。pytest の形の test は、この入口が無いと何も実行せずに通過する |
| `tests/test_youmu_install_roundtrip.py` | 一時 dir の中で clone → `init`・`adopt`・`setup-multi-agent.sh --host opencode` → `doctor`・`lint` → per-skill link の削除を行い、既存の vault と HOME が1 byte も変わらないことを比べる | Youmu の要件 F25・F27・F28(配布の検査)。WSL が使えない環境では skip せず失敗する。file を直接走らせたときも pytest で自分を実行する |
| `YOUMU.md` | この文書 | — |

vault 側の入口は `10-SYSTEM/scripts/co.sh`(製品ツリーを `<vault の親>/claude-obsidian-product` に置く前提)。

## 版上げの手順

`X.Y.Z` は取り込む tag。

1. 取り込む
   ```
   git -C claude-obsidian-product fetch origin --tags
   git -C claude-obsidian-product switch youmu
   git -C claude-obsidian-product merge vX.Y.Z
   ```
2. 衝突を解く。衝突しうるのは上の表の既存 file だけ。
   - 入口4本(`scripts/retrieve.py` ほか): **upstream 側を採り、末尾の `if __name__ == "__main__":` の中に引き渡しの数行を戻す。**
     upstream が引数を変えていたら手順 4-f で確かめる。
   - `scripts/claude-obsidian.py`: upstream 側を採り、`from claude_obsidian.cli import main` の直前に振り分けの4行を戻す。
3. upstream が検索スクリプトを**新しく足していないか**を見る(`git diff vOLD vX.Y.Z --stat -- scripts/`)。
   足していて、それが Youmu の索引を読むなら、入口に置き換えるか判断する。
4. 受け入れ(**全部**。1つでも落ちたら使わない)
   - a. `python -m pytest -q tests/test_youmu_bridge.py tests/test_wsl_route.py` が全件緑。
     `test_every_cli_subcommand_is_classified_as_routed_or_native` が落ちたら、upstream がサブコマンドを足している。
     vault に書くなら `ROUTED_GROUPS`、書かないなら `NATIVE_GROUPS` へ足す(`claude_obsidian/wsl_route.py`)。
   - b. **upstream の test を upstream の想定環境で、upstream と同じ走らせ方で比べる**(要件 F24)。
     vault の `10-SYSTEM/scripts/run_upstream_tests_wsl.sh` が、WSL の home へこの branch と素の tag を clone し、
     `make test` と同じ中身を1 file ずつ走らせて失敗の集合を出す。**この branch だけで落ちる行が今回の変更で壊したもの。**
     Windows の pytest で比べない — 2026-09-13〜15 は pytest 用の skip 一覧が効いて、Linux で落ちる7件が隠れていた。
     2026-09-27 時点で、両方で揺れるのは `tests/test_transaction.py`(同じ大きさの内容変更の検出。時刻の粒度)だけ。
   - d. `bash <vault>/10-SYSTEM/scripts/co.sh doctor` が ok、`co.sh contracts --verify --capability wiki-retrieve` が verified。
   - e. 全体の `co.sh contracts --verify` で degraded が増えていないか。v2.2.0 時点の degraded は wiki / wiki-cli / wiki-lint の3件で、
     **3件とも同じテストを WSL で走らせると通る**(ネイティブで落ちる原因は symlink の作成権限と、試験用 vault への書き込み拒否)。
     増えたら、まず同じテストを WSL で走らせて、機能の故障かテストの作りかを分ける。
   - f. スキルが検索スクリプトに渡す引数を、Youmu のスクリプトが受け付けるか。
     `grep -n "scripts/" skills/wiki-query/SKILL.md skills/wiki-retrieve/SKILL.md` で呼び方を見る。
     受け付けない引数は `claude_obsidian/youmu_bridge.py` の `_DROPPED_OPTIONS` で落とす(落としたことは stderr に出る)。
   - g. 書き込みを端から端まで: 作業用 vault で `init` → `transaction inspect` → `transaction apply` → `checkpoint` が通り、
     commit の中身が LF。
5. 使い始める。問題が出たら `git -C claude-obsidian-product switch --detach vOLD` ではなく、
   **前の版で youmu branch を指していた commit** へ戻す(差分ごと戻すため)。
