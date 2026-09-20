# agent-usage

> 本地多 AI Agent Token 用量统一监控看板

监控你电脑上所有 AI Agent 的 token 用量，统一看板展示。**插件化架构**——装了新 Agent，丢一个插件文件就能接入；**实时监控**——数据源一变，看板自动刷新。

## 功能

- 总用量 KPI、每日趋势堆叠柱（含区间平均日用量）、各 Agent 对比、模型 TOP15、会话明细
- 区间平均日用量：选近14天/30天/90天/全部，自动算对应区间日均
- 实时推送（SSE），数据源变化即自动刷新
- 单文件 exe，无需安装 Python
- 本地运行，数据不上传

## 快速开始

1. 下载 `dist/agent-usage.exe`
2. 双击运行，自动打开 http://127.0.0.1:8765/
3. 看数

命令行参数：

```
agent-usage.exe [--port 8765] [--no-open] [--interval 5] [--full]
```

## 内置 Agent 支持

| Agent | 精确度 | 数据源 | 说明 |
| --- | --- | --- | --- |
| Codex / Claude | 精确 | CC Switch `proxy_request_logs` | 代理层记录每次请求的 input/output/cache tokens |
| Kimi Code | 精确 | `~/.kimi/sessions/**/wire.jsonl` | 本地 wire 协议含 token_usage |
| WorkBuddy | 估算 | `~/.workbuddy/workbuddy.db` | credit 花费按 ¥2/百万 tokens 折算 |
| CodeBuddy | 估算 | `~/.codebuddy/projects/**/*.jsonl` | 文本长度估算 |
| 豆包工作 | 估算 | IndexedDB + 会话轨迹 | 云端应用，本地只缓存当前会话，历史在服务端 |
| 千问工作 | 估算 | `~/.qwenworkcn/projects/**/*.jsonl` | jsonl 无 usage 字段，文本长度估算 |
| ZCode | 精确 | `~/.zcode/cli/db/db.sqlite` | 本地 SQLite 用量记录 |

> 标"估算"的 Agent 本地没有精确 token 用量，按文本长度或费用折算，仅供参考。

## 接入新 Agent

### 方式一：插件文件

在 `plugins/` 新建 `<name>.py`：

```python
KEY = 'myagent'
NAME = '我的 Agent'
ESTIMATE = False
WATCH_PATHS = ['%USERPROFILE%\\.myagent\\data']

def scan(full, need, mark):
    return [...]
```

重启即可。

### 方式二：API 自助接入

```bash
curl -X POST http://127.0.0.1:8765/api/agents \
  -H "Content-Type: application/json" \
  -d '{"name": "MyAgent", "data_path": "C:/Users/you/.myagent/sessions/a.jsonl"}'
```

找不到数据时会返回结构化提示，告诉你去哪找。

## 架构

```
app.py          入口
serve.py        HTTP + SSE 服务
engine/
  core.py       扫描调度、聚合
  registry.py   插件发现/加载
  watcher.py    实时监控
  common.py     公共工具
plugins/        各 Agent 适配器
web/            ECharts 看板
```

## 开发

```bash
python app.py          # 源码运行
python monitor.py scan # 命令行扫描
build.bat              # 打包 exe
```

Python 3.8+，无第三方依赖。

## License

MIT
