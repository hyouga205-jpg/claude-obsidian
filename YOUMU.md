# branch `youmu` — upstream との差分と版上げの手順

この branch は upstream(AgriciDaniel/claude-obsidian)の tag に、Youmu(利用者の Obsidian vault
`multi-llm/claude-obsidian`)で使うための変更を足したもの。**upstream との差分はこの branch にだけ置く。**
変更の理由と実測は vault の `05-DAILY/handoffs/2026-09-13-handoff-claude-obsidian-upstream-v2-adoption.md`。

## 差分(この branch にだけある変更)

| file | 何をするか | なぜ |
|---|---|---|
| `scripts/claude-obsidian.py`(4行)・`claude_obsidian/wsl_route.py`・`tests/test_wsl_route.py` | vault に書くサブコマンドを、ネイティブ Windows では WSL の `python3` で実行する | upstream はネイティブ Windows での vault 書き込みを `UNSUPPORTED_PLATFORM` で拒否する |
| `scripts/retrieve.py` `bm25-index.py` `contextual-prefix.py` `rerank.py`・`claude_obsidian/youmu_bridge.py`・`tests/test_youmu_bridge.py` | 4本とも、Youmu の vault にある同名スクリプトへ引き渡すだけの入口 | 検索は Youmu の実装を使う(利用者の裁定)。upstream の検索スクリプトは Youmu の索引を読めない |
| `scripts/youmu_retrieve_verify.py`・`config/capabilities.json`(wiki-retrieve の `verification_command` だけ) | wiki-retrieve の検証を、Youmu の BM25 検索が1件以上返ることに置き換える | wiki-query はこの検証が通らないと検索を使わない |
| `tests/conftest.py` | 置き換えた実装を試す upstream のテストを、file 名か test id の完全一致で skip | 通るはずのない失敗が本物の回帰を隠すため |
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
   - 入口4本(`scripts/retrieve.py` ほか): **youmu 側を採る。**upstream が引数を変えていたら手順 4-f で確かめる。
   - `scripts/claude-obsidian.py`: upstream 側を採り、`from claude_obsidian.cli import main` の直前に振り分けの4行を戻す。
   - `config/capabilities.json`: upstream 側を採り、wiki-retrieve の `verification_command` だけ戻す。
3. upstream が検索スクリプトを**新しく足していないか**を見る(`git diff vOLD vX.Y.Z --stat -- scripts/`)。
   足していて、それが Youmu の索引を読むなら、入口に置き換えるか判断する。
4. 受け入れ(**全部**。1つでも落ちたら使わない)
   - a. `python -m pytest -q tests/test_youmu_bridge.py tests/test_wsl_route.py` が全件緑。
     `test_every_cli_subcommand_is_classified_as_routed_or_native` が落ちたら、upstream がサブコマンドを足している。
     vault に書くなら `ROUTED_GROUPS`、書かないなら `NATIVE_GROUPS` へ足す(`claude_obsidian/wsl_route.py`)。
   - b. `tests/conftest.py` の一覧が古いと収集の段階で止まる。test id の改名なら追従する。**置き換えていない実装のテストは skip に足さない。**
   - c. **失敗を基準と比べる。**素の tag を別に checkout し、同じテスト群を Windows で走らせて、
     `FAILED` / `SUBFAILED` の一覧がこの branch と一致することを確かめる。一致しない行が今回の変更で壊したもの。
     ```
     git -C claude-obsidian-product worktree add --detach <一時ディレクトリ> vX.Y.Z
     python -m pytest -q -p no:cacheprovider -rf --tb=no <テスト群>   # 両方の木で
     git -C claude-obsidian-product worktree remove --force <一時ディレクトリ>
     ```
     2026-09-13(v2.2.0)時点で、両方に共通する失敗は `tests/test_vault_root_separation.py` の4件と
     `tests/test_installed_tree_boundary.py` の11件(どちらも Windows の `bash` が WSL を指すことと、試験用 vault への書き込み拒否が原因)。
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
