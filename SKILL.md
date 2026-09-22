---
name: agent-usage-skill
description: 监控你电脑上所有 AI Agent（Codex、Claude Code、Kimi、WorkBuddy、豆包工作等）的 token 用量。当用户问"我用了多少 token"、"我的额度怎么消耗的"、"哪个 AI 用最多"、"我的 AI 用量统计"时使用。
---

# Agent Usage Skill

本地多 AI Agent Token 用量统一监控 Skill。插件化架构，任何 AI 编程工具都能接入。

## 什么时候用

用户问以下问题时触发：
- "我用了多少 token / AI 用量 / token 消耗"
- "我的额度怎么用的 / 哪个 Agent 用最多"
- "帮我统计一下 AI 编程工具的用量"
- "看看我这个月花了多少 token"

## 怎么用

### 1. 先扫描（第一次或定期跑）

```bash
python monitor.py scan
```

输出各 Agent 扫描状态和总用量。

### 2. 拿精简摘要（最常用）

```bash
python monitor.py summary
```

输出结构化 JSON（下为真实样例，已省略部分 agent）：

```json
{
  "ok": true,
  "summary": {
    "total_tokens": 18213307134,
    "real_tokens": 17963630443,
    "est_tokens": 249676691,
    "agent_count": 7,
    "agents": [
      { "key": "codex",     "name": "codex",     "total_tokens": 8567256657, "sessions": 33, "estimate": 0 },
      { "key": "kimi",      "name": "Kimi",      "total_tokens": 5152300033, "sessions": 138, "estimate": 0 },
      { "key": "workbuddy", "name": "WorkBuddy", "total_tokens": 3823534605, "sessions": 156, "estimate": 0 },
      { "key": "claude",    "name": "claude",    "total_tokens": 403153985,  "sessions": 2,   "estimate": 0 },
      { "key": "doubao",    "name": "豆包工作",   "total_tokens": 249599994,  "sessions": 13,  "estimate": 1 },
      { "key": "codebuddy", "name": "CodeBuddy", "total_tokens": 17385163,   "sessions": 6,   "estimate": 0 }
    ]
  }
}
```

直接解析 JSON 回答用户即可。`estimate=1` 的条目（豆包、千问）是换算值，
其余是本地真实计数。

### 3. 启动 Web 看板（可选）

```bash
python monitor.py serve
```

启动 http://127.0.0.1:8765/，有可视化图表。

⚠️ **铁律：启动时绝不要接 `| head`**（如 `python serve.py | head -5`）——
管道读满就关闭 ⇒ 服务被 SIGPIPE 干掉或卡死。症状极具迷惑性：
**端口仍在 LISTENING，但 curl 返回 HTTP 000**，看着像活着实际完全不响应。
正确姿势：后台启动 + 输出重定向到日志文件，然后**必须探活**：

```bash
curl -s -o /dev/null -w "HTTP %{http_code}" --max-time 8 "http://127.0.0.1:8765/"
```

必须拿到 `HTTP 200`；不是 200 就 `taskkill` 重来。详细排障见
`doubao-token-calibration` skill 第四节。

## 已支持的 Agent

| Agent | key | 精确度 |
|---|---|---|
| Codex | `codex` | 精确（CC Switch 代理日志，含缓存命中） |
| Claude Code | `claude` | 精确（CC Switch 代理日志，含缓存命中） |
| Kimi | `kimi` | 精确（本地 wire 协议） |
| WorkBuddy | `workbuddy` | 精确（jsonl 真实 `usage`，去重后 est=0） |
| CodeBuddy | `codebuddy` | 精确（jsonl `message.usage`） |
| ZCode | `zcode` | 精确（本地 SQLite；当前表 0 行，尚未实际使用） |
| 豆包工作 | `doubao` | **估算**（云端只给百分比 × 50 万系数） |
| 千问工作 | `qwenworkcn` | 估算（文本长度） |

> **`codex` / `claude` 的来源**：两者的请求都经过本机 CC Switch 代理，
> 由 `plugins/ccswitch.py` 从 `~/.cc-switch/cc-switch.db` 的 `proxy_request_logs`
> 表读取后按 `app_type` 映射上报（`APP_TO_AGENT = {'codex':'codex','claude':'claude'}`）。
> 注意插件文件名是 `ccswitch.py`、文件内 `KEY='ccswitch'`，但**上报 key 是 `codex`/`claude`**。

标"估算"的 Agent 本地没有精确 token 用量，按文本长度或费用折算，仅供参考。
**豆包**是唯一需要系数校准的（云端只给百分比）；其系数依据、验证过程与适用边界
见 [`docs/DOUBAO.md`](docs/DOUBAO.md)，操作与排障见独立 skill
`doubao-token-calibration`（已固化，勿重推）。

## 注意事项

- 所有数据本地存储，不上传
- 标 `estimate=1` 的是估算值，不是精确 token 数
- ⚠️ **跨 agent 的 token 数不可横向比较**：WorkBuddy / Codex / Kimi 记的是**原始传输量**
  （每轮把整段上下文重发一遍的累计，input 占 99%+、output 不足 1%），
  豆包 / 千问记的是**折后计价量换算**（缓存命中的重复上下文近乎免费）。
  同一段工作实际在两边能差 5~8 倍。回答「哪个 AI 用最多」时，
  只能表述为**同一口径内的排序**，必须一并提示量纲差异，否则会得出误导结论。
  实测样本见 `docs/DOUBAO.md`「三个必须知道的适用边界 · 边界二」。
- 豆包工作插件支持可选 cookie 配置（`~/.doubao-usage/config.json`，键 `doubao_cookie`）：
  配了就拉 timeline API 拿**真实百分比**，没配就本地估算兜底。
  但**注意**：即使配了 cookie，API 也只返回**百分比**（如 `0.15%`），
  绝对 token 仍需乘系数换算 —— 见 [`docs/DOUBAO.md`](docs/DOUBAO.md)。
- 新增 Agent 只需在 `plugins/` 丢一个 Python 文件（照 `_template.py` 改，
  记得设 `KEY` / `NAME` / `ESTIMATE` / `WATCH_PATHS`）
