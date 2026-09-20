# -*- coding: utf-8 -*-
"""插件：Codex CLI。
数据源：
  1. ~/.codex/sessions/**/rollout-*.jsonl（精确 token，含 cwd）
  2. ~/.codex/config.toml [projects] 段（工作区列表，用于项目归属）
"""
import os, json, glob, re
from engine.common import iso_to_ms

KEY = 'codex'
NAME = 'Codex'
ESTIMATE = False
WATCH_PATHS = [
    '%USERPROFILE%\\.codex\\sessions',
    '%USERPROFILE%\\.codex\\config.toml',
]

_SESS_ROOT = os.path.expanduser('~/.codex/sessions')
_CONFIG = os.path.expanduser('~/.codex/config.toml')


def _parse_config_projects():
    """从 config.toml 解析 [projects] 段的工作目录列表。"""
    projects = []
    if not os.path.isfile(_CONFIG):
        return projects
    try:
        with open(_CONFIG, 'r', encoding='utf-8') as f:
            content = f.read()
        # 匹配 [projects."path"] 和 [projects.'path']
        for m in re.finditer(r'\[projects\.[\'"](.+?)[\'"]\]', content):
            path = m.group(1).replace('\\\\', '\\')
            if os.path.isdir(path):
                projects.append(path)
    except:
        pass
    return projects


def _parse_rollout(fp):
    sid = os.path.basename(fp).replace('.jsonl', '')
    cwd = ''
    model = 'gpt-5'
    last_total = None
    last_ts = 0
    first_ts = 0

    try:
        with open(fp, 'r', encoding='utf-8', errors='replace') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except:
                    continue

                typ = obj.get('type', '')
                payload = obj.get('payload', {})
                ts = obj.get('timestamp', '')
                ms = iso_to_ms(ts) if ts else 0

                if typ == 'session_meta':
                    cwd = payload.get('cwd', '')
                    m = payload.get('model', '')
                    if m:
                        model = m
                    if ms and (not first_ts or ms < first_ts):
                        first_ts = ms

                info = payload.get('info', {})
                ttu = info.get('total_token_usage', {})
                if ttu and (ttu.get('input_tokens') or ttu.get('output_tokens')):
                    last_total = ttu
                    if ms > last_ts:
                        last_ts = ms
    except:
        pass

    if not last_total:
        return None

    it = last_total.get('input_tokens', 0)
    ot = last_total.get('output_tokens', 0)
    cached = last_total.get('cached_input_tokens', 0)
    cw = last_total.get('cache_write_input_tokens', 0)
    new_in = max(0, it - cached)

    return {
        'agent': KEY,
        'session_id': sid,
        'title': f'Codex {os.path.basename(fp)[:40]}',
        'cwd': cwd,
        'model': model,
        'provider': 'openai',
        'created_at': first_ts,
        'last_activity_at': last_ts,
        'input_tokens': new_in,
        'output_tokens': ot,
        'cache_read_tokens': cached,
        'cache_write_tokens': cw,
        'total_tokens': new_in + ot + cached + cw,
        'cost': None,
        'est': 0,
        'source_file': fp,
    }


def scan(full, need, mark):
    results = []

    # 1. 扫描 rollout 文件
    files = glob.glob(os.path.join(_SESS_ROOT, '**', '*.jsonl'), recursive=True)
    arch = os.path.join(os.path.dirname(_SESS_ROOT), 'archived_sessions')
    if os.path.isdir(arch):
        files += glob.glob(os.path.join(arch, '*.jsonl'))

    for fp in files:
        r = _parse_rollout(fp)
        if r and r['total_tokens'] > 0:
            results.append(r)

    # 2. 解析 config.toml 工作区列表（用于项目归属，不直接产生 session）
    projects = _parse_config_projects()
    # 把项目路径注入到每个 session 的 cwd 推断（如果 session 没 cwd，用项目路径匹配）
    if projects and results:
        for r in results:
            if not r['cwd']:
                # 从 source_file 路径推断 session_id 对应的项目
                # rollout 文件名里有 session id，无法直接映射到项目
                pass

    return results
