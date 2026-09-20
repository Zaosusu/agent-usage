# agent-usage-skill

> 本地多 AI Agent Token 用量统一监控 Skill

监控你电脑上所有 AI Agent 的 token 用量，统一看板展示。**插件化架构**——装了新 Agent，丢一个插件文件就能接入；**实时监控**——数据源一变，看板自动刷新；**Skill 接口**——写了 SKILL.md，任何 AI 都能自动发现并调用。

## 功能

- 总用量 KPI、每日趋势堆叠柱（含区间平均日用量）、各 Agent 对比、模型 TOP15、会话明细
- 区间平均日用量：选近14天/30天/90天/全部，自动算对应区间日均
- 实时推送（SSE），数据源变化即自动刷新
- 单文件 exe，无需安装 Python
- 本地运行，数据不上传
- **Agent API**：结构化 JSON 返回，AI 直接调用，不用解析复杂数据

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
| 豆包工作 | 四层校准 | timeline API → Local Storage → IndexedDB → trajectory | 云端应用，四层数据源降级 |
| 千问工作 | 估算 | `~/.qwenworkcn/projects/**/*.jsonl` | jsonl 无 usage 字段，文本长度估算 |
| ZCode | 精确 | `~/.zcode/cli/db/db.sqlite` | 本地 SQLite 用量记录 |

> 标"估算"的 Agent 本地没有精确 token 用量，按文本长度或费用折算，仅供参考。

## 豆包工作插件详细说明

### 四层数据源降级架构

| 层级 | 数据源 | 精确度 | 触发条件 | 降级策略 |
|---|---|---|---|---|
| 1 | timeline API | 最精确 | 配置了 cookie | 失败→降级到本地 |
| 2 | Local Storage 百分比 | 精确 | 找到 usedThisPeriod 字段 | 失败→降级到文本 |
| 3 | IndexedDB 精确 token | 精确（当前会话） | 扫到 inputTokensI/outputTokensI | 作为参考值 |
| 4 | trajectory 文本估算 | 最粗 | 兜底方案 | 任何情况都能跑 |

### 校准系数

- **1% ≈ 50 万 token**
- 校准依据：12 天 1171 条 = 419% ≈ 2 亿 token（API + 本地交叉验证）

### 可选 cookie 配置

如果用户知道怎么从 DevTools 复制 cookie，就存到配置文件里，拿到精确的 API 数据。不知道也没关系，自动降级到本地估算。

配置文件路径：`~/.doubao-usage/config.json`
```json
{"doubao_cookie": "sessionid=xxx; passport_csrf_token=xxx; ..."}
```

## Agent API（给 AI 调用）

所有接口返回结构化 JSON，AI 直接调用，不用解析复杂数据。

### 接口列表

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 健康检查 |
| GET | `/api/plugins` | 已接入的插件列表 |
| GET | `/api/data` | 完整聚合数据 |
| GET | `/api/refresh` | 触发一次增量扫描 |
| GET | `/api/stream` | SSE 实时推送 |
| **GET** | **`/api/agent/summary`** | **精简用量摘要（推荐）** |
| **GET** | **`/api/agent/agents/<key>/usage`** | **单个 agent 详细用量** |
| **GET** | **`/api/agent/config`** | **查看本地配置** |
| POST | `/api/agents` | 添加新 agent |
| DELETE | `/api/agents/<key>` | 删除 agent |

### 示例

```bash
# 拿摘要（AI 最常用）
curl http://127.0.0.1:8765/api/agent/summary

# 拿单个 agent 详情
curl http://127.0.0.1:8765/api/agent/agents/doubao/usage

# 检查豆包 cookie 是否已配置
curl http://127.0.0.1:8765/api/agent/config

# 触发扫描
curl http://127.0.0.1:8765/api/refresh
```

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
serve.py        HTTP + SSE 服务 + Agent API
monitor.py      命令行扫描
adapters.py     旧版适配器
engine/
  core.py       扫描调度、聚合
  registry.py   插件发现/加载
  watcher.py    实时监控
  onboard.py    新 agent 自助接入
  common.py     公共工具
plugins/        各 Agent 适配器
web/            ECharts 看板
```

## 兜底方案

任何一个数据源失败，都自动降级到下一层，不会崩：

| 场景 | 兜底行为 |
|---|---|
| API 超时/401 | 降级到 Local Storage 百分比 |
| Local Storage 没数据 | 降级到 trajectory 文本估算 |
| IndexedDB 被锁 | 跳过，不影响其他数据源 |
| 某个插件崩了 | 其他插件正常扫描 |
| 服务挂了 | 数据还在 JSON 文件里，重启自动加载 |

## 开发

```bash
python app.py          # 源码运行
python monitor.py scan # 命令行扫描
build.bat              # 打包 exe
```

Python 3.8+，无第三方依赖。

## License

MIT
