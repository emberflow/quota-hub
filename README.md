# 额度看板（quota-hub）

本机网页，只读聚合 Cursor Pro、ChatGPT Plus / Codex、SuperGrok / grokcli、Google AI Pro / Antigravity 的**剩余量**和**还剩多久**。另有 GitHub 仓库目录树、即将作废提示、每日用量、按模型拆分。

不上传密钥。不改 v2ray / 系统代理。不写各家 `auth.json`（Grok 过期时只在内存里试 refresh，失败则提示你打开 grok CLI）。

## 启动

在资源管理器双击 [`run.cmd`](run.cmd)，或：

```bat
cd /d G:\Projects\quota-hub
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8787
```

浏览器打开 http://127.0.0.1:8787

需要本机已登录：Cursor、`codex login`、grok CLI、Antigravity（或 `npx -y antigravity-usage login`）、`gh auth login`。

## 数据从哪来

| 产品 | 剩余量 | 刷新时间 |
|---|---|---|
| Cursor | 本机 `state.vscdb` token → cursor.com 账单接口 | 账单周期结束 |
| Codex | `quota-axi`（读 `~/.codex`） | 5h / 周窗口 |
| SuperGrok | grok.com `GetGrokCreditsConfig` | 周池 |
| Antigravity | `antigravity-usage`（IDE 开着或已 login） | 各模型 resetTime |
| GitHub | `gh repo list` + Trees API | 不是额度 |

每日用量：看板每次刷新把剩余 % 写入 `data/snapshots.db`，按日求差值。Codex / Grok 本地 session 的 token 只作参考。

## 终端快查（和网页分开）

```bat
npx -y quota-axi --tui --provider cursor,codex,grok
npx -y antigravity-usage
npx -y gh-axi repo list
```

OpenUsage 对 Cursor/Codex 更细，但其 xAI 格是 **API Key 账单**，不是 SuperGrok 周池。

## 官方兜底

- Cursor: https://cursor.com/dashboard/spending
- Codex: ChatGPT / Codex Usage，或 CLI `/status`
- Grok: Settings → Usage
- Antigravity: `/usage`
