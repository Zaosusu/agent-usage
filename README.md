# agent-usage-skill

> 本地多 AI Agent Token 用量统一监控 Skill

监控你电脑上所有 AI Agent 的 token 用量，统一看板展示。**插件化架构**——装了新 Agent，丢一个插件文件就能接入；**实时监控**——数据源一变，看板自动刷新；**Skill 接口**——写了 SKILL.md，任何 AI 都能自动发现并调用。

## 功能

- 总用量 KPI、每日趋势堆叠柱（含区间平均日用量）、各 Agent 对比、模型 TOP15、会话明细
- 区间平均日用量：今天 / 近 3 天 / 7 天 / 14 天 / 30 天 / 90 天 / 全部，自动算对应区间日均；
  默认**近 3 天**，并记住你的上次选择（localStorage）
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
| WorkBuddy | 精确 | `~/.workbuddy/projects/**/*.jsonl` | 每轮模型调用带 `usage`（prompt/completion/total_tokens），逐轮累加即真实计费量 |
| CodeBuddy | 估算 | `~/.codebuddy/projects/**/*.jsonl` | 文本长度估算 |
| 豆包工作 | 四层校准 | timeline API → IndexedDB → Local Storage → trajectory | 云端应用；系数由 IndexedDB 硬锚点实测（见下节） |
| 千问工作 | 估算 | `~/.qwenworkcn/projects/**/*.jsonl` | jsonl 无 usage 字段，文本长度估算 |
| ZCode | 精确 | `~/.zcode/cli/db/db.sqlite` | 本地 SQLite 用量记录 |

> 标"估算"的 Agent 本地没有精确 token 用量，按文本长度或费用折算，仅供参考。

### WorkBuddy 口径说明（踩坑记录）

WorkBuddy 的 jsonl 每轮调用都带真实 `usage`，但有两个坑：

1. **同一行内会出现两个 usage 字典**（原始 API 返回 + 规范化版本，字段分别是
   `prompt_tokens/completion_tokens` 与 `input_tokens/output_tokens`），
   它们描述同一次调用，**只能取一个**，否则总量恰好翻倍（实测 raw/nodedup = 2.00）。
2. `prompt_tokens` 每轮携带完整历史（实测前 60 轮 57 次单调递增），
   所以**逐轮累加 `total_tokens` 就是真实计费量**，无需换算。

不要用 `session_usage.credit_json` 折算 token：那是**费用**字段（元），
与 token 的比值随模型费率浮动（实测 0.31x ~ 12.43x），且约 65% 的会话该字段为 NULL。

## 豆包工作插件详细说明

### 四层数据源降级架构

| 层级 | 数据源 | 精确度 | 触发条件 | 降级策略 |
|---|---|---|---|---|
| 1 | timeline API | 最精确 | 配置了 cookie | 失败→降级到本地 |
| 2 | IndexedDB 精确 token | **最硬（直接测量）** | 扫到逐次调用的 input/output token | 用于**校准系数**（见下节），并作参考值 |
| 3 | Local Storage 百分比 | 精确 | 找到 usedThisPeriod 字段 | 失败→降级到文本 |
| 4 | trajectory 文本估算 | 最粗 | 兜底方案 | 任何情况都能跑 |

### 系数校准：1% 等于多少 token（怎么算出来的）

豆包是**云端应用**，本地没有权威 token 账本，而它的用量接口**只返回百分比**——
timeline API 每条只给 `quota_source.display_text`（如 `"0.15%"`），配额接口只给
`used_percent`，**没有任何绝对 token 字段**（本地与 API 都翻遍了，这是事实，不是没挖到）。

所以核心问题只有一个：**1% = 多少 token？** 用两条互相独立的路径求解。

> **现行采用值：`TOKENS_PER_PCT = 2_300_000`（1% ≈ 230 万 token）**，来自方法一的硬锚点直接测量。

#### 方法一：硬锚点直接测量（主，一锤定音）

**关键发现**：本地 IndexedDB（`http_127.0.0.1_5188`）保存了**真实逐次 API 调用的
input/output token**，且 input 随上下文单调递增（7,212 → 19,660）——
API 就是按每次调用的 `input_tokens` 累加计费的，所以**这个累加值就是真实计费量**。
（时间戳是 ISO 8601 字符串，不是 varint；曾按 varint 解析失败而误判它"不可用"。）

把它与 timeline 的同时间窗百分比对齐，并验证两者 `quota_source_code`
同为 `doubao_personal_vip_quota`（**同一额度池**，保证可比）：

```
65 次真实调用 Σ = 999,454 tok   (09-20 01:46:22 ~ 01:59:23)
timeline 同窗 3 条 Σ = 0.430%
⇒ 1% = 999,454 / 0.430 = 232.4 万 token   ⇒ 取整 230 万
```

这是**直接测量**，不是估算。

#### 方法二：agent 模式重建（兜底，只给下界）

按 agent 循环语义重建每个会话的模型调用消耗，跑多个变体做敏感性分析
（数据：47 会话 / 1504 turn / 2856 次模型调用 / 3043 次工具调用）：

| 算法 | 推算 1% |
|---|---|
| 1 裸模型调用（仅消息体） | 46.7 万 |
| 2 + system prompt(2,671 tok × 2856) | 48.2 万 |
| 3 + tool schema 5K | 51.1 万 |
| 4 单 turn 均值 160,036 ÷ 单条均值 0.313% | 51.1 万 |
| 3 + tool schema 10K | 53.9 万 |
| F timeline 逐条匹配 1431/1595（90%）Σtok/Σ% | 56.7 万 |
| 3 + tool schema 20K | 59.7 万 |

**下界区间 46.7 ~ 59.7 万，中位 ≈51 万。**

它比硬锚点低 **4.5 倍**，原因很明确：重建只能覆盖 agent 模式（`.sessions` 目录），
**陪伴/角色扮演、普通对话、图像、其他模型、premium 倍率、思维链 token 全不在内**。
所以重建法**只配做兜底与交叉验证，不能当采用值**。

#### 一键复现（两条路径都在同一个脚本里）

```bash
python tools/calibrate_doubao.py                     # 只算不改：先找硬锚点，再输出下界区间
python tools/calibrate_doubao.py --apply             # 命中硬锚点则直接写回 plugins/doubao.py
python tools/calibrate_doubao.py --anchor 1.5e8      # 有单窗口绝对配额时一击钉死
python tools/calibrate_doubao.py --root "D:/xx/DoubaoWork/User Data/Default"   # 换机器
```

#### 取值与旧值对照

| 版本 | 值 | 状态 |
|---|---|---|
| commit `90c788c` | 500 万 | ❌ 十倍误改，diff 一行、无依据 |
| 重建法 v1 | 47.8 万 | ❌ 漏算（只算消息体） |
| 重建法 v2 | 113.6 万 | ❌ **重复计算**（见下方陷阱） |
| 重建法 v3 | ≈51 万 | ⚠️ 仅兜底下界 |
| **硬锚点（现行）** | **230 万** | ✅ 直接测量 |

采用 230 万后，豆包总量由 5.99 亿 → **11.48 亿**（全站 190.07 亿）。

#### 建模陷阱（改算法前必读）

**正确模型**：一次 assistant 消息 = 一次模型调用，消耗 = `累积上下文 + 本条输出`。

1. **漏算**：只算消息体，忽略上下文重发 → 得 47.8 万（v1）。
2. **重复算（更隐蔽）**：曾在 tool 消息处额外加一次「上下文重放」，
   但**工具结果已经进了 `ctx`，下次调用的 `ctx_before` 本就包含它**
   ⇒ 同一份 input 算两遍，高估约 2 倍 → 一度得出 113.6 万（v2）。**不可再加。**

另两点：

- tool schema 本地**不落盘**（trajectory 只存 tool_calls 不存 schema），
  所以用 5K/10K/20K 做敏感性扫描，而不是假装已知。
- 算法 F 必须用**聚合口径 Σtok/Σ%**，不能看中位数——ratio 分布极度右偏
  （25%=26万 / 50%=86万 / 75%=309万 / 90%=1450万），中位数会被海量小条目拉低。

#### 如何进一步钉死

若能在豆包用量页读到「占 7 天额度」旁的**绝对已用/总额**，或订阅计划的单窗口配额：

```bash
python tools/calibrate_doubao.py --anchor <一个7天窗口的token数> --apply
```

- 用户已确认：百分比是**累加**的（首窗 + 重置 4 次 = 500%），不是单窗口封顶。

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

> **插件返回 `daily` 时的硬性约定**：`daily` 表主键是 `(agent, day, source_file)`。
> 若你的插件在 `daily` 里用**同一个 `source_file`** 上报多条同一天的数据，它们会互相覆盖，
> 导致「总用量正确、但按天曲线偏低」。凡是**一个数据源内含多个会话**（如读一张 DB 表、
> 一个目录下的多个会话）的插件，`source_file` 必须**按会话唯一**（例如 `f'{DBP}#{session_id}'`）。
> 会话天然分散在不同文件的插件（多数 jsonl 类）不受影响。

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
