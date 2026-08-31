![AI Repo Gardener — AI が削除し忘れたファイルを見つける](docs/hero.svg)

<p align="center">
  <a href="README.md">English</a> ·
  <a href="README.zh-TW.md">繁體中文</a> ·
  <a href="README.zh-CN.md">简体中文</a> ·
  <strong>日本語</strong>
</p>

<p align="center">
  <a href="https://github.com/niansia/ai-repo-gardener/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/niansia/ai-repo-gardener/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://pypi.org/project/repo-gardener/"><img alt="PyPI" src="https://img.shields.io/pypi/v/repo-gardener?include_prereleases"></a>
  <a href="https://pypi.org/project/repo-gardener/"><img alt="Python 3.11–3.14" src="https://img.shields.io/pypi/pyversions/repo-gardener"></a>
  <a href="https://github.com/niansia/ai-repo-gardener/releases/tag/v0.1.0-alpha.11"><img alt="GitHub prerelease" src="https://img.shields.io/github/v/release/niansia/ai-repo-gardener?include_prereleases&label=release"></a>
</p>

AI Repo Gardener は、AI が編集した Python リポジトリ向けの決定論的な
ガベージコレクター兼 Agent Skill です。置き換え済みのファイル、忘れられた
helper、重複実装、残った依存関係、ディレクトリ構造の負荷、リポジトリ固有の
Python スタイルから外れたコードを検出します。モデルの呼び出しやソースコードの
アップロードは行わず、弱い推測を削除操作に変えることもありません。

> **リリース状況：** `0.1.0a11` は **v0.1** 系列の 11 番目の alpha です。
> 安定版 `0.1.0` はまだリリースされていません。Repo GC が alpha の中核機能で、
> architecture と house-style の分析は実験的かつレビュー専用です。

## 30 秒で始める

Python 3.11 以上が必要です。

```bash
python -m pip install "repo-gardener==0.1.0a11"
repo-gardener diff .
repo-gardener fix . --dry-run
```

インストールせずに一度だけ実行することもできます。

```bash
uvx --from "repo-gardener==0.1.0a11" repo-gardener diff .
```

`diff` と `fix` はどちらも既定値が `--base HEAD` なので、レビューで確認した Git
証拠がそのまま dry-run につながります。別の commit 範囲を監査するときは、両方の
コマンドで同じ Git ref を明示してください。

![AI Repo Gardener による実際の diff と安全な dry-run](docs/demo.gif)

## 3 つの証拠システム

| システム | 答える問い | 状態 | 最初のコマンド |
| --- | --- | --- | --- |
| **Repo GC** | 最新の AI iteration は何を置き換え、放置し、重複させ、宣言だけ残したか？ | Alpha の中核 | `repo-gardener diff .` |
| **Architecture Gardener** | 1 つのディレクトリが無関係なコードを抱えすぎていないか？妥当な移動案は何か？ | 実験的、レビュー専用 | `repo-gardener structure . --confidence all` |
| **House-style Gardener** | どの Python ファイルがこのリポジトリ自身の基準スタイルから外れているか？ | 実験的、レビュー専用 | `repo-gardener style . --baseline HEAD~20 --confidence all` |

Repo GC には `stale-file`、`orphan-file`、`orphan-helper`、
`duplicate-implementation`、`dependency-leftover` が含まれます。Structure は
スコア付きで非破壊的な migration proposal を作ります。Style は typing、path、
comprehension、出力、複雑度、wrapper、防御的な guard、命名などの Python 固有の
信号を、リポジトリ内の peer または AI 導入前の commit／日付と比較します。
Style finding はスタイルのずれを示すだけで、AI が書いた証明ではありません。

## Agent Skill として使う

PyPI wheel には完全なポータブル Skill が含まれています。まず CLI をインストールし、
同梱 Skill のパスを取得します。

```bash
python -m pip install "repo-gardener==0.1.0a11"
repo-gardener skill-path
```

使用する agent のターゲットを **1 つだけ** 選んでください。プロジェクト単位では、
同じディレクトリ名をリポジトリ内に作ります。

| Agent | 個人用 Skill の配置先 | プロジェクト用 Skill の配置先 | 呼び出し |
| --- | --- | --- | --- |
| [OpenAI Codex](https://developers.openai.com/codex/skills/) | `~/.agents/skills/repo-gardener` | `.agents/skills/repo-gardener` | `$repo-gardener` または `/skills` |
| [Claude Code](https://code.claude.com/docs/en/skills) | `~/.claude/skills/repo-gardener` | `.claude/skills/repo-gardener` | `/repo-gardener` |
| [Cursor](https://cursor.com/docs/skills) | `~/.cursor/skills/repo-gardener` | `.cursor/skills/repo-gardener` | `/repo-gardener` |

### macOS / Linux

```bash
SKILL_SOURCE="$(repo-gardener skill-path)"

# OpenAI Codex
mkdir -p "$HOME/.agents/skills/repo-gardener"
cp -R "$SKILL_SOURCE"/. "$HOME/.agents/skills/repo-gardener/"

# Claude Code を使う場合はこちら
mkdir -p "$HOME/.claude/skills/repo-gardener"
cp -R "$SKILL_SOURCE"/. "$HOME/.claude/skills/repo-gardener/"

# Cursor を使う場合はこちら
mkdir -p "$HOME/.cursor/skills/repo-gardener"
cp -R "$SKILL_SOURCE"/. "$HOME/.cursor/skills/repo-gardener/"
```

### Windows PowerShell

```powershell
$SkillSource = repo-gardener skill-path

# 配置先を 1 つ選択：
$Target = "$HOME\.agents\skills\repo-gardener" # OpenAI Codex
# $Target = "$HOME\.claude\skills\repo-gardener" # Claude Code
# $Target = "$HOME\.cursor\skills\repo-gardener" # Cursor

New-Item -ItemType Directory -Force $Target | Out-Null
Get-ChildItem -Force $SkillSource | Copy-Item -Destination $Target -Recurse -Force
```

同じ agent が読む複数の場所へ重複インストールしないでください。リポジトリ内の
`skills/repo-gardener/` も単独で利用でき、オープンな
[Agent Skills 仕様](https://agentskills.io/specification)に準拠しています。

## レビューした計画だけを、そのまま適用する

読み取り専用コマンドはリポジトリを変更しません。削除には、正確な JSON 計画、
一致する 2 回目の分析、検証コマンドが必要です。

```bash
repo-gardener fix . --dry-run --format json > reviewed-plan.json

# reviewed-plan.json を確認してから、同じ計画を適用：
repo-gardener fix . --apply \
  --plan reviewed-plan.json \
  --validate "python -m pytest" \
  --validation-timeout 300
```

PowerShell では apply コマンドを 1 行にするか、各 `\` を PowerShell のバッククォートに
置き換えてください。Apply は元のリポジトリに触れる前に隔離コピーで削除結果を検証し、
その後で plan identity とファイル hash を再確認します。検証に失敗した場合、元の
リポジトリは変更されません。成功した操作には
`repo-gardener fix . --restore` 用の復元可能な snapshot が残ります。

計画には base ref と SHA、HEAD SHA、有効な設定、operation set、候補／置換ファイルの
hash、evidence-file hash が固定されます。リポジトリが変わると計画も変わり、適用は
拒否されます。

## コマンド

| コマンド | 用途 | ファイルを変更するか |
| --- | --- | --- |
| `scan .` | 対応する Repo GC ルールを実行 | いいえ |
| `stale .` | ファイル、symbol、重複実装、依存関係レベルの GC に集中 | いいえ |
| `diff . [--base <ref>]` | committed、staged、worktree、untracked の iteration を監査 | いいえ |
| `fix . --dry-run` | 条件を満たす高信頼度の削除候補をプレビュー | いいえ |
| `fix . --dry-run --format json` | レビュー用 plan contract を作成 | いいえ |
| `fix . --apply --plan <json> --validate <cmd>` | レビュー済みの同一計画を検証して適用 | **はい** |
| `fix . --restore` | 直前の削除操作を復元 | **はい** |
| `structure . --confidence all` | architecture 分析を明示的に実行 | いいえ |
| `style . --baseline <ref-or-date> --confidence all` | baseline 相対の style 分析を明示的に実行 | いいえ |
| `scan . --experimental` | 完全 scan に structure と style を追加 | いいえ |
| `skill-path` | wheel に同梱されたポータブル Skill のパスを表示 | いいえ |

すべてのレポートコマンドは agent と CI 向けの安定した JSON に対応しています。
`--fail-on high`、`--fail-on medium`、`--fail-on any` は閾値に達すると exit code `1`、
ツールまたは設定のエラーでは `2` を返します。

## 雰囲気ではなく証拠

次の数値は、公開済みで再現可能な gate を示します。すべてのリポジトリに対する精度を
主張するものではありません。

| 公開 gate | 現在の結果 |
| --- | --- |
| Source suite | **182 tests** |
| 破壊的安全性の対抗ケース | **0 / 59 eligible-deletion false positives** |
| Curated labeled corpus | **10 TP、0 FP、0 FN、10 TN**。この corpus 内の precision／recall は 100% |
| Release wheel | 同一 wheel を **12 の OS/Python 組み合わせ**でテスト：Ubuntu、Windows、macOS × Python 3.11–3.14 |
| 固定した実在リポジトリ | requests、Flask、pandas、Django、FastAPI、pytest、Pydantic。**automatic-deletion candidate は 0** |
| Hosted style benchmark | alpha.10 の 159.11s → alpha.11 の 97.51s。finding は同一で **38.7% 高速化** |

fixture、commit、実行環境、制約の詳細：

- [対抗的な safety gate](benchmarks/safety-benchmark.md)
- [ラベル付き precision／recall corpus](benchmarks/labeled-corpus.md)
- [固定した実在リポジトリの smoke／performance 結果](benchmarks/real-world-smoke.md)

## 安全境界

AI Repo Gardener は意図的に保守的です。

- Parse error、不透明／動的 loader、解決できない packaging metadata、templated
  deployment command がある場合、リポジトリ全体で自動削除を無効化します。
- Framework root、packaging entry point、公開／package API、plugin、runtime string、
  generated code、migration、partial replacement は保護またはレビュー専用です。
- 既定では、リポジトリ直下のファイルだけが自動削除 risk gate を通過できます。
  `app/`、`src/`、package、namespace package 内は、owner が 2 つの安全設定を明示的に
  変更しない限りレビュー専用です。
- Architecture と style の finding がファイルを移動・削除することはありません。
- runtime の外部依存は 0。モデルやネットワークを呼び出さず、ソースコードや
  telemetry を送信しません。

アプリケーション内部の package だとレビューで確認できた場合のみ、次の 2 項目を
明示的に上書きできます。

```toml
[safety]
allow_delete_src = true
allow_delete_package_modules = true
```

[`repo-gardener.toml.example`](repo-gardener.toml.example) を
`repo-gardener.toml` にコピーすると、entrypoint、保護パス、除外、検証、閾値を設定
できます。明示的な `--validate` を推奨します。リポジトリが提供する検証コマンドは、
`--trust-repo-config` を付けない限り実行されません。

適用済みの操作は復元用状態を `.repo-gardener/` に保存します。対象リポジトリの
`.gitignore` に追加してください。

```gitignore
.repo-gardener/
```

完全な mutation policy と JSON contract は、
[safety policy](skills/repo-gardener/references/safety-policy.md)および
[finding schema](skills/repo-gardener/references/finding-schema.md)を参照してください。
安全上の問題は [`SECURITY.md`](SECURITY.md) に従って報告してください。

## 開発とコントリビューション

```bash
git clone https://github.com/niansia/ai-repo-gardener.git
cd ai-repo-gardener
python -m pip install -e ".[dev]"
python -m pytest
ruff check .
ruff format --check .
python skills/repo-gardener/scripts/run_repo_gardener.py scan . --confidence all
```

コントリビューションを歓迎します。false positive の修正や新しい evidence rule には、
再利用可能な fixture が必要です。pull request の前に
[`CONTRIBUTING.md`](CONTRIBUTING.md)をお読みください。

## ライセンス

[MIT](LICENSE)
