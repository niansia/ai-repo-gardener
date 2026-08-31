![AI Repo Gardener — 找出 AI 忘記刪除的檔案](docs/hero.svg)

<p align="center">
  <a href="README.md">English</a> ·
  <strong>繁體中文</strong> ·
  <a href="README.zh-CN.md">简体中文</a> ·
  <a href="README.ja.md">日本語</a>
</p>

<p align="center">
  <a href="https://github.com/niansia/ai-repo-gardener/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/niansia/ai-repo-gardener/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://pypi.org/project/repo-gardener/"><img alt="PyPI" src="https://img.shields.io/pypi/v/repo-gardener?include_prereleases"></a>
  <a href="https://pypi.org/project/repo-gardener/"><img alt="Python 3.11–3.14" src="https://img.shields.io/pypi/pyversions/repo-gardener"></a>
  <a href="https://github.com/niansia/ai-repo-gardener/releases/tag/v0.1.0-alpha.11"><img alt="GitHub prerelease" src="https://img.shields.io/github/v/release/niansia/ai-repo-gardener?include_prereleases&label=release"></a>
</p>

AI Repo Gardener 是專為 AI 修改過的 Python 儲存庫打造的確定性垃圾回收器與
Agent Skill。它能找出已被取代的檔案、遺忘的 helper、重複實作、殘留依賴、
資料夾結構壓力，以及偏離此儲存庫自身 Python 風格的程式碼；全程不呼叫模型、
不上傳原始碼，也不會把薄弱猜測變成刪除操作。

> **版本狀態：** `0.1.0a11` 是 **v0.1** 系列的第 11 個 alpha。
> 穩定版 `0.1.0` 尚未發布。Repo GC 是目前 alpha 的核心功能；架構與
> house-style 分析仍為實驗性、僅供檢視的功能。

## 30 秒開始使用

需要 Python 3.11 以上版本。

```bash
python -m pip install "repo-gardener==0.1.0a11"
repo-gardener diff .
repo-gardener fix . --dry-run
```

也可以不安裝，直接執行一次：

```bash
uvx --from "repo-gardener==0.1.0a11" repo-gardener diff .
```

`diff` 與 `fix` 都預設使用 `--base HEAD`，因此檢視時看到的 Git 證據會自然延續到
dry-run。若要稽核其他 commit 範圍，請在兩個指令中使用相同的明確 Git ref。

![AI Repo Gardener 的實際 diff 與安全 dry-run](docs/demo.gif)

## 三套證據系統

| 系統 | 回答的問題 | 狀態 | 從這裡開始 |
| --- | --- | --- | --- |
| **Repo GC** | 最新一輪 AI 修改取代、遺棄、重複或留下了什麼？ | Alpha 核心 | `repo-gardener diff .` |
| **Architecture Gardener** | 某個資料夾是否承擔太多無關程式碼？合理的搬移方案是什麼？ | 實驗性、僅供檢視 | `repo-gardener structure . --confidence all` |
| **House-style Gardener** | 哪些 Python 檔案偏離了此儲存庫自己的基準風格？ | 實驗性、僅供檢視 | `repo-gardener style . --baseline HEAD~20 --confidence all` |

Repo GC 包含 `stale-file`、`orphan-file`、`orphan-helper`、
`duplicate-implementation` 與 `dependency-leftover`。Structure 會產生有評分但不會
自動執行的 migration proposal。Style 會比較 typing、路徑、comprehension、輸出、
複雜度、wrapper、防禦式判斷、命名等 Python 訊號，基準可來自儲存庫同儕或
AI 介入前的 commit／日期。Style finding 只代表風格漂移，不能證明檔案由 AI 撰寫。

## 作為 Agent Skill 使用

PyPI wheel 已包含完整可攜式 Skill。先安裝 CLI，再取得內附 Skill 的實際路徑：

```bash
python -m pip install "repo-gardener==0.1.0a11"
repo-gardener skill-path
```

請只選擇你實際使用的 agent 目標。專案層級的安裝位置使用儲存庫內相同的目錄名稱。

| Agent | 個人 Skill 位置 | 專案 Skill 位置 | 呼叫方式 |
| --- | --- | --- | --- |
| [OpenAI Codex](https://developers.openai.com/codex/skills/) | `~/.agents/skills/repo-gardener` | `.agents/skills/repo-gardener` | `$repo-gardener` 或 `/skills` |
| [Claude Code](https://code.claude.com/docs/en/skills) | `~/.claude/skills/repo-gardener` | `.claude/skills/repo-gardener` | `/repo-gardener` |
| [Cursor](https://cursor.com/docs/skills) | `~/.cursor/skills/repo-gardener` | `.cursor/skills/repo-gardener` | `/repo-gardener` |

### macOS / Linux

```bash
SKILL_SOURCE="$(repo-gardener skill-path)"

# OpenAI Codex
mkdir -p "$HOME/.agents/skills/repo-gardener"
cp -R "$SKILL_SOURCE"/. "$HOME/.agents/skills/repo-gardener/"

# 使用 Claude Code 時改用這個位置
mkdir -p "$HOME/.claude/skills/repo-gardener"
cp -R "$SKILL_SOURCE"/. "$HOME/.claude/skills/repo-gardener/"

# 使用 Cursor 時改用這個位置
mkdir -p "$HOME/.cursor/skills/repo-gardener"
cp -R "$SKILL_SOURCE"/. "$HOME/.cursor/skills/repo-gardener/"
```

### Windows PowerShell

```powershell
$SkillSource = repo-gardener skill-path

# 選擇一個目標：
$Target = "$HOME\.agents\skills\repo-gardener" # OpenAI Codex
# $Target = "$HOME\.claude\skills\repo-gardener" # Claude Code
# $Target = "$HOME\.cursor\skills\repo-gardener" # Cursor

New-Item -ItemType Directory -Force $Target | Out-Null
Get-ChildItem -Force $SkillSource | Copy-Item -Destination $Target -Recurse -Force
```

請勿在同一個 agent 會掃描的多個位置安裝重複副本。儲存庫裡的
`skills/repo-gardener/` 也能獨立使用，並遵循開放的
[Agent Skills 規格](https://agentskills.io/specification)。

## 先檢視，再套用完全相同的計畫

唯讀指令不會修改儲存庫。刪除檔案需要一份確切的 JSON 計畫、第二次相符的分析，
以及驗證指令：

```bash
repo-gardener fix . --dry-run --format json > reviewed-plan.json

# 檢查 reviewed-plan.json 後，套用這份完全相同的計畫：
repo-gardener fix . --apply \
  --plan reviewed-plan.json \
  --validate "python -m pytest" \
  --validation-timeout 300
```

在 PowerShell 中，請把 apply 指令寫成同一行，或將每個 `\` 換成 PowerShell 的
反引號。Apply 會先在隔離副本中驗證刪除結果，再接觸原始儲存庫，之後重新核對
plan identity 與檔案 hash。驗證失敗時原始儲存庫不會被修改；成功操作則保留可供
`repo-gardener fix . --restore` 使用的復原快照。

計畫會固定 base ref 與 SHA、HEAD SHA、有效設定、operation set、候選／替代檔案
hash 及 evidence-file hash。儲存庫只要發生變更就會產生不同計畫，套用會被拒絕。

## 指令

| 指令 | 用途 | 會修改檔案？ |
| --- | --- | --- |
| `scan .` | 執行支援的 Repo GC 規則 | 否 |
| `stale .` | 聚焦檔案、symbol、重複實作與依賴層級 GC | 否 |
| `diff . [--base <ref>]` | 稽核 committed、staged、worktree 與 untracked 的迭代變更 | 否 |
| `fix . --dry-run` | 預覽符合條件的高信心刪除候選 | 否 |
| `fix . --dry-run --format json` | 建立供檢視的 plan contract | 否 |
| `fix . --apply --plan <json> --validate <cmd>` | 驗證並套用完全相同的已檢視計畫 | **是** |
| `fix . --restore` | 復原最近一次刪除操作 | **是** |
| `structure . --confidence all` | 明確執行架構分析 | 否 |
| `style . --baseline <ref-or-date> --confidence all` | 明確執行相對基準的 style 分析 | 否 |
| `scan . --experimental` | 在完整掃描中加入 structure 與 style | 否 |
| `skill-path` | 顯示 wheel 內附的可攜式 Skill 路徑 | 否 |

所有報告指令都支援供 agent 與 CI 使用的穩定 JSON。`--fail-on high`、
`--fail-on medium` 與 `--fail-on any` 達到門檻時回傳 exit code `1`；工具或設定錯誤
回傳 `2`。

## 證據，不是感覺

以下數字描述的是已發布、可重現的 gate，不是對所有儲存庫的準確率宣稱。

| 已發布 gate | 目前結果 |
| --- | --- |
| Source suite | **182 個測試** |
| 破壞性安全對抗案例 | **0 / 59 個 eligible-deletion false positive** |
| Curated labeled corpus | **10 TP、0 FP、0 FN、10 TN**；此 corpus 內 precision 與 recall 均為 100% |
| Release wheel | 同一份 wheel 通過 **12 種 OS/Python 組合**：Ubuntu、Windows、macOS × Python 3.11–3.14 |
| 固定版真實儲存庫 | requests、Flask、pandas、Django、FastAPI、pytest、Pydantic；**0 個 automatic-deletion candidate** |
| Hosted style benchmark | alpha.10 的 159.11s → alpha.11 的 97.51s；finding 完全相同，**快 38.7%** |

完整 fixture、commit、測試機器資料與限制：

- [對抗式安全 gate](benchmarks/safety-benchmark.md)
- [有標籤的 precision／recall corpus](benchmarks/labeled-corpus.md)
- [固定版真實儲存庫 smoke 與效能測試](benchmarks/real-world-smoke.md)

## 安全界線

AI Repo Gardener 採取刻意保守的策略：

- Parse error、不透明／動態載入、無法解析的 packaging metadata、templated deployment
  command 都會在整個儲存庫停用自動刪除。
- Framework root、packaging entry point、公開／package API、plugin、runtime string、
  generated code、migration 與 partial replacement 會受保護或只能人工檢視。
- 預設只有儲存庫根目錄的檔案能通過自動刪除 risk gate。`app/`、`src/`、package
  與 namespace package 內的檔案，除非 owner 明確調整兩項安全設定，否則都只能檢視。
- Architecture 與 style finding 絕不會搬移或刪除檔案。
- 工具沒有 runtime 第三方依賴、不呼叫模型或網路，也不會傳送原始碼或 telemetry。

已人工確認為應用程式內部 package 時，可明確設定這兩項 override：

```toml
[safety]
allow_delete_src = true
allow_delete_package_modules = true
```

將 [`repo-gardener.toml.example`](repo-gardener.toml.example) 複製成
`repo-gardener.toml`，可設定 entrypoint、受保護路徑、排除項目、驗證與門檻。建議明確
傳入 `--validate`；儲存庫提供的驗證指令預設會被忽略，只有加上
`--trust-repo-config` 才會執行。

已套用的操作會把可復原狀態寫進 `.repo-gardener/`。請將它加入目標儲存庫的
`.gitignore`：

```gitignore
.repo-gardener/
```

完整 mutation policy 與 JSON contract 請參考
[安全政策](skills/repo-gardener/references/safety-policy.md)及
[finding schema](skills/repo-gardener/references/finding-schema.md)。安全問題請依
[`SECURITY.md`](SECURITY.md) 回報。

## 開發與貢獻

```bash
git clone https://github.com/niansia/ai-repo-gardener.git
cd ai-repo-gardener
python -m pip install -e ".[dev]"
python -m pytest
ruff check .
ruff format --check .
python skills/repo-gardener/scripts/run_repo_gardener.py scan . --confidence all
```

歡迎貢獻。每一個 false-positive 修正或新 evidence rule 都必須附上可重複使用的
fixture。提交 pull request 前請先閱讀 [`CONTRIBUTING.md`](CONTRIBUTING.md)。

## 授權

[MIT](LICENSE)
