# -*- coding: utf-8 -*-
"""插件：Codex CLI（独立直连）。
用法取每个会话最后一个 total_token_usage（累计值），不是逐 turn 累加。
"""
import os, json, glob
from engine.common import iso_to_ms

KEY = 'codex'
NAME = 'Codex'
ESTIMATE = False
WATCH_PATHS = ['%USERPROFILE%\\.codex\\sessions']

_ROOT = os.path.expanduser(WATCH_PATHS[0].replace('%USERPROFILE%', os.path.expanduser('~')))


def _parse_rollout(fp):
    sid = os.path.basename(fp).replace('.jsonl', '')
    cwd = ''
    model = 'gpt-5'
    # 取最后一个 total_token_usage
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
    if not os.path.isdir(_ROOT):
        return []
    files = glob.glob(os.path.join(_ROOT, '**', '*.jsonl'), recursive=True)
    arch = os.path.join(os.path.dirname(_ROOT), 'archived_sessions')
    if os.path.isdir(arch):
        files += glob.glob(os.path.join(arch, '*.jsonl'))
    results = []
    for fp in files:
        r = _parse_rollout(fp)
        if r and r['total_tokens'] > 0:
            results.append(r)
    return results
