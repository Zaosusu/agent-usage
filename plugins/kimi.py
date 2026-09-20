# -*- coding: utf-8 -*-
"""插件：Kimi / KimiCode（精确计数）。数据源：~/.kimi/sessions/<sid>/<tid>/wire.jsonl，
每行 message.payload.token_usage 含 input_other/output/input_cache_read/input_cache_creation。"""
import os
import json
from engine.common import scan_jsonl_dir

KEY = 'kimi'
NAME = 'Kimi'
ESTIMATE = False
WATCH_PATHS = ['%USERPROFILE%\\.kimi\\sessions']

_ROOT = os.path.expanduser(WATCH_PATHS[0].replace('%USERPROFILE%', os.path.expanduser('~')))


def _parse(path):
    parts = os.path.relpath(path, _ROOT).split(os.sep)
    sid = '/'.join(parts[:-1])
    inp = out_t = cache_r = cache_w = 0
    first_ts = last_ts = None
    try:
        fh = open(path, 'r', encoding='utf-8', errors='replace')
    except OSError:
        return None
    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            ts = obj.get('timestamp')
            if isinstance(ts, (int, float)):
                ts_ms = int(ts * 1000)
                if first_ts is None:
                    first_ts = ts_ms
                last_ts = ts_ms
            msg = obj.get('message')
            if not isinstance(msg, dict):
                continue
            if msg.get('type') != 'StatusUpdate':
                continue
            tu = (msg.get('payload') or {}).get('token_usage')
            if not isinstance(tu, dict):
                continue
            inp += tu.get('input_other', 0) or 0
            out_t += tu.get('output', 0) or 0
            cache_r += tu.get('input_cache_read', 0) or 0
            cache_w += tu.get('input_cache_creation', 0) or 0
    total = inp + out_t + cache_r + cache_w
    if total == 0 and first_ts is None:
        return None
    return {
        'agent': KEY, 'session_id': sid,
        'title': 'Kimi 会话 ' + sid.split(os.sep)[0][:8],
        'cwd': '', 'model': 'kimi-k2.7-code', 'provider': 'moonshot',
        'created_at': first_ts or 0, 'last_activity_at': last_ts or 0,
        'input_tokens': inp, 'output_tokens': out_t,
        'cache_read_tokens': cache_r, 'cache_write_tokens': cache_w,
        'total_tokens': total, 'cost': None, 'est': 0,
        'source_file': path,
    }


def scan(full, need, mark):
    out = []
    for pat in ('*/*/wire.jsonl', '*/wire.jsonl'):
        out += scan_jsonl_dir(_ROOT, pat, _parse, full, need, mark)
    return out
