---
name: agent-usage
description: 监控你电脑上所有 AI Agent（Codex、Claude Code、Kimi、WorkBuddy、豆包工作等）的 token 用量。当用户问"我用了多少 token"、"我的额度怎么消耗的"、"哪个 AI 用最多"、"我的 AI 用量统计"时使用。
---

# Agent Usage Monitor

本地多 AI Agent Token 用量统一监控工具。插件化架构，任何 AI 编程工具都能接入。

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

输出结构化 JSON：

```json
{
  "ok": true,
  "summary": {
    "total_tokens": 17572947860,
    "real_tokens": 17571456163,
    "est_tokens": 1491697,
    "agent_count": 7,
    "agents": [
      {
        "key": "codex",
        "name": "codex",
        "total_tokens": 8742652774,
        "sessions": 34,
        "estimate": 0
      }
    ]
  }
}
```

直接解析 JSON 回答用户即可。

### 3. 启动 Web 看板（可选）

```bash
python monitor.py serve
```

启动 http://127.0.0.1:8765/，有可视化图表。

## 已支持的 Agent

| Agent | key | 精确度 |
|---|---|---|
| Codex / Claude | `codex` / `claude` | 精确（CC Switch 代理日志） |
| Kimi Code | `kimi` | 精确（本地 wire 协议） |
| WorkBuddy | `workbuddy` | 估算（credit 折算） |
| CodeBuddy | `codebuddy` | 估算（文本长度） |
| 豆包工作 | `doubao` | 四层校准（API → Local Storage → IndexedDB → trajectory） |
| 千问工作 | `qwenworkcn` | 估算（文本长度） |
| ZCode | `zcode` | 精确（本地 SQLite） |

标"估算"的 Agent 本地没有精确 token 用量，按文本长度或费用折算，仅供参考。

## 注意事项

- 所有数据本地存储，不上传
- 标 `estimate=1` 的是估算值，不是精确 token 数
- 豆包工作插件支持可选 cookie 配置（`~/.doubao-usage/config.json`），配置了就拿精确 API 数据，没配置就本地估算兜底
- 新增 Agent 只需在 `plugins/` 丢一个 Python 文件
