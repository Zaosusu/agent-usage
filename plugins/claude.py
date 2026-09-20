# -*- coding: utf-8 -*-
"""插件：Claude Code（独立直连，不依赖 CC Switch）。
数据源：~/.claude/projects/**/*.jsonl
每行消息含 usage: input_tokens / output_tokens / cache_read / cache_creation。
"""
import os, json, glob
from engine.common import iso_to_ms

KEY = 'claude'
NAME = 'Claude'
ESTIMATE = False
WATCH_PATHS = ['%USERPROFILE%\\.claude\\projects']

_ROOT = os.path.expanduser(WATCH_PATHS[0].replace('%USERPROFILE%', os.path.expanduser('~')))


def _parse_session(fp):
    """解析一个 Claude session jsonl。"""
    sid = os.path.basename(fp).replace('.jsonl', '')
    cwd = ''
    model = 'claude'
    total_in = 0
    total_out = 0
    total_cache_read = 0
    total_cache_write = 0
    last_ts = 0
    first_ts = 0
    msg_count = 0

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

                # cwd 和 model 可能在消息元数据里
                if not cwd and 'cwd' in obj:
                    cwd = obj['cwd']
                if not model and 'model' in obj:
                    model = obj['model']
                if not cwd:
                    # 从路径推断
                    cwd = os.path.basename(os.path.dirname(fp)).replace('-', '\\').replace('C--', 'C:\\')

                # 时间戳
                ts = obj.get('timestamp', '') or obj.get('ts', '')
                ms = 0
                if ts:
                    ms = iso_to_ms(ts) if isinstance(ts, str) else int(ts)
                if ms:
                    if not first_ts or ms < first_ts:
                        first_ts = ms
                    if ms > last_ts:
                        last_ts = ms

                # 找 usage
                usage = obj.get('usage', {})
                if not usage:
                    # 也可能在 message 里
                    msg = obj.get('message', {})
                    if isinstance(msg, dict):
                        usage = msg.get('usage', {})

                if usage and isinstance(usage, dict):
                    it = usage.get('input_tokens', 0)
                    ot = usage.get('output_tokens', 0)
                    cr = usage.get('cache_read_input_tokens', 0)
                    cw = usage.get('cache_creation_input_tokens', 0)
                    if it or ot:
                        total_in += it
                        total_out += ot
                        total_cache_read += cr
                        total_cache_write += cw
                        msg_count += 1
    except:
        pass

    return {
        'agent': KEY,
        'session_id': sid,
        'title': f'Claude {os.path.basename(fp)[:40]}',
        'cwd': cwd,
        'model': model,
        'provider': 'anthropic',
        'created_at': first_ts,
        'last_activity_at': last_ts,
        'input_tokens': total_in,
        'output_tokens': total_out,
        'cache_read_tokens': total_cache_read,
        'cache_write_tokens': total_cache_write,
        'total_tokens': total_in + total_out + total_cache_read + total_cache_write,
        'cost': None,
        'est': 0,
        'source_file': fp,
    }


def scan(full, need, mark):
    if not os.path.isdir(_ROOT):
        return []

    files = glob.glob(os.path.join(_ROOT, '**', '*.jsonl'), recursive=True)
    results = []
    for fp in files:
        r = _parse_session(fp)
        if r['total_tokens'] > 0:
            results.append(r)
    return results
