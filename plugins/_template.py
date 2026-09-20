# -*- coding: utf-8 -*-
"""
插件模板 — 接入新 Agent 从这里复制。

把本文件复制为 plugins/<你的agent>.py（例如 myagent.py），
改下面几处声明和 scan() 即可被引擎自动发现，无需改任何核心代码。

--- 三个必填声明 ---
  KEY         : 稳定唯一标识（小写字母/数字/下划线），显示在数据里的 agent 键
  NAME        : 界面显示名（中文随意）
  WATCH_PATHS : 引擎监视的路径列表，用于"实时监控"检测变化。
                支持三种形态：
                  - 单个文件：'%USERPROFILE%\\.myagent\\data.sqlite'
                  - 目录（内部递归 glob）：'%USERPROFILE%\\.myagent\\data'
                路径用 %USERPROFILE% 占位，引擎会展开成当前用户的 Home，换机器也能用。
  ESTIMATE    : True 表示该数据源本地没有真实 token 计数、按文本估算（面板会标"估算"）

--- scan() 返回统一会话列表 ---
  每个元素是 dict：
  {
    'agent': KEY, 'session_id': '<唯一ID>', 'title': '<会话标题>',
    'cwd': '', 'model': '<模型名，可为空>', 'provider': '',
    'created_at': <ms时间戳或0>, 'last_activity_at': <ms时间戳或0>,
    'input_tokens': <int>, 'output_tokens': <int>,
    'cache_read_tokens': <int>, 'cache_write_tokens': <int>,
    'total_tokens': <int>, 'cost': <float或None>, 'est': <0或1>,
    'source_file': '<来源文件绝对路径>',
  }

--- 增量扫描（重要，必须做） ---
  引擎传入两个回调：
    need(full, path, fingerprint) -> bool   # True=需要重新解析该文件
    mark(path, fingerprint)                 # 解析成功后记录指纹
  指纹 = f'{mtime_ns}:{size}'，文件没变就跳过，扫描秒级完成。
  sqlite 数据源整体重查即可（开销小），JSONL 类务必走 need/mark 增量。

--- 引擎提供的工具（engine/common.py）---
  ro_connect(path)                        # 只读打开 sqlite
  scan_jsonl_dir(root, pattern, parse_fn, full, need, mark)  # 通用 JSONL 增量扫描
  parse_claude_like_file(agent, path)     # Claude/CodeBuddy 风格 usage 解析
  parse_estimate_file(agent, path, root)  # 估算型解析
  estimate_tokens(text)                   # 估算 token
  iso_to_ms / ts_to_ms                    # 时间戳换算

  如果引擎 tools 不够用，直接在这里写你自己的解析逻辑（纯 Python + 标准库）。
"""
import os
from engine.common import scan_jsonl_dir, parse_claude_like_file, estimate_tokens

KEY = 'myagent'
NAME = '我的新 Agent'
ESTIMATE = False                      # 本地无真实计数则改 True
WATCH_PATHS = [
    '%USERPROFILE%\\.myagent\\sessions',          # 目录型：JSONL 会话
    '%USERPROFILE%\\.myagent\\usage.sqlite',      # 文件型：sqlite 用量
]


# ---------- 形态 1：JSONL 会话目录（精确 usage 字段） ----------
def scan(full, need, mark):
    root = os.path.expanduser(WATCH_PATHS[0].replace('%USERPROFILE%', os.path.expanduser('~')))
    # parse_claude_like_file 适用于 "message.usage 含 input/output/cache" 的 JSONL；
    # 字段不同就自己写 parse 函数（参考下面注释掉的 _parse_myagent）。
    return scan_jsonl_dir(root, '**/*.jsonl',
                          lambda p: parse_claude_like_file(KEY, p), full, need, mark)


# ---------- 形态 2：自定义解析（字段不同时取消注释改写） ----------
# def _parse_myagent(path):
#     import json
#     inp = out_t = 0
#     first_ts = last_ts = None
#     with open(path, 'r', encoding='utf-8', errors='replace') as f:
#         for line in f:
#             line = line.strip()
#             if not line:
#                 continue
#             try:
#                 obj = json.loads(line)
#             except Exception:
#                 continue
#             usage = (obj.get('message') or {}).get('usage') or {}
#             inp += usage.get('input_tokens', 0)
#             out_t += usage.get('output_tokens', 0)
#     if inp + out_t == 0:
#         return None
#     return {
#         'agent': KEY,
#         'session_id': os.path.splitext(os.path.basename(path))[0],
#         'title': '会话 ' + os.path.basename(path)[:16],
#         'cwd': '', 'model': '', 'provider': '',
#         'created_at': 0, 'last_activity_at': 0,
#         'input_tokens': inp, 'output_tokens': out_t,
#         'cache_read_tokens': 0, 'cache_write_tokens': 0,
#         'total_tokens': inp + out_t, 'cost': None, 'est': 0,
#         'source_file': path,
#     }

# ---------- 形态 3：估算模式（无 usage 字段，ESTIMATE=True） ----------
# def scan(full, need, mark):
#     root = os.path.expanduser(WATCH_PATHS[0].replace('%USERPROFILE%', os.path.expanduser('~')))
#     return scan_jsonl_dir(root, '**/*.jsonl',
#                           lambda p: parse_estimate_file(KEY, p, root), full, need, mark)
