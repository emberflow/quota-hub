# 额度看板（quota-hub）

本机网页，只读聚合 Cursor Pro、ChatGPT Plus / Codex、SuperGrok / grokcli、Google AI Pro / Antigravity 的**剩余量**和**还剩多久**。另有 GitHub 仓库目录树、即将作废提示、每日用量、按模型拆分。

不上传密钥。不改 v2ray / 系统代理。不写各家 `auth.json`（Grok 过期时只在内存里试 refresh，失败则提示你打开 grok CLI）。

## 启动

运行一次 [`run.cmd`](run.cmd) 完成依赖安装，然后使用桌面上的 **额度看板** 快捷方式。快捷方式使用无控制台的 `pythonw.exe`，浏览器会打开 http://127.0.0.1:8788。

已在运行时再点一次快捷方式，只会打开浏览器，不会再起一份服务。需要停止后台服务时，在页面右上角点击“退出看板”。

启动失败或上游接口异常时，查看 `data\quota-hub.log`；正常访问不会打开 CMD 窗口。

若快捷方式丢了，在项目目录执行：

```bat
cd /d D:\Projects\quota-hub
.venv\Scripts\python.exe scripts\make_shortcut.py
```

```bat
cd /d D:\Projects\quota-hub
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe launch.py
```

浏览器打开 http://127.0.0.1:8788

需要本机已登录：Cursor、`codex login`、grok CLI / Antigravity（`agy`）、`gh auth login`。

## 数据从哪来

| 产品 | 剩余量 | 刷新时间 |
|---|---|---|
| Cursor | 本机 `state.vscdb` token → cursor.com 账单接口 | 账单周期结束 |
| Codex | `quota-axi`（读 `~/.codex`） | 5h / 周窗口 |
| SuperGrok | grok.com `GetGrokCreditsConfig` | 周池 |
| Antigravity | 本机 `agy` 凭据 → Cloud Code `retrieveUserQuotaSummary` | 周池 + 5 小时窗口 |
| GitHub | `gh repo list` + Trees API | 不是额度 |

每日用量默认最近 14 天柱状图，可在 Cursor / ChatGPT Plus / SuperGrok / Antigravity 之间切换。日期显示为 `8.14`。Cursor 用官方用量事件的当日花费；Codex 用本机会话 token。

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
