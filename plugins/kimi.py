# -*- coding: utf-8 -*-
"""插件：Kimi / KimiCode（精确计数）。数据源：~/.kimi/sessions/<sid>/<tid>/wire.jsonl，
每行 message.payload.token_usage 含 input_other/output/input_cache_read/input_cache_creation。
按每条消息的真实时间戳按天拆分用量。"""
import os
import json
import time
from engine.common import glob_files


KEY = 'kimi'
NAME = 'Kimi'
ESTIMATE = False
WATCH_PATHS = ['%USERPROFILE%\\.kimi\\sessions']

_ROOT = os.path.expanduser(WATCH_PATHS[0].replace('%USERPROFILE%', os.path.expanduser('~')))


def _parse(path):
    """解析单个 wire.jsonl，按天拆分 token 用量。
    返回 {date_str: {'input': inp, 'output': out, 'cache_r': cr, 'cache_w': cw}}
    """
    daily = {}
    try:
        fh = open(path, 'r', encoding='utf-8', errors='replace')
    except OSError:
        return {}
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
            if not isinstance(ts, (int, float)):
                continue
            day = time.strftime('%Y-%m-%d', time.localtime(ts))
            msg = obj.get('message')
            if not isinstance(msg, dict):
                continue
            if msg.get('type') != 'StatusUpdate':
                continue
            tu = (msg.get('payload') or {}).get('token_usage')
            if not isinstance(tu, dict):
                continue
            inp = tu.get('input_other', 0) or 0
            out_t = tu.get('output', 0) or 0
            cache_r = tu.get('input_cache_read', 0) or 0
            cache_w = tu.get('input_cache_creation', 0) or 0
            if day not in daily:
                daily[day] = {'input': 0, 'output': 0, 'cache_r': 0, 'cache_w': 0}
            daily[day]['input'] += inp
            daily[day]['output'] += out_t
            daily[day]['cache_r'] += cache_r
            daily[day]['cache_w'] += cache_w
    return daily


def scan(full, need, mark):
    daily_rows = []
    daily_files = []
    for pat in ('*/*/wire.jsonl', '*/wire.jsonl'):
        files = glob_files(_ROOT, pat)
        for p in files:
            try:
                st = os.stat(p)
            except OSError:
                continue
            fp = f'{st.st_mtime_ns}:{st.st_size}'
            if not need(full, p, fp):
                continue
            daily = _parse(p)
            for day, tokens in daily.items():
                total = tokens['input'] + tokens['output'] + tokens['cache_r'] + tokens['cache_w']
                if total < 1000:
                    continue
                daily_rows.append({
                    'agent': KEY, 'day': day, 'source_file': p,
                    'tokens': total, 'est': 0,
                })
            daily_files.append(p)
            mark(p, fp)
    # 生成按天的汇总 sessions（每天只生成一个，用于总用量统计）
    daily_total = {}
    for d in daily_rows:
        day = d['day']
        if day not in daily_total:
            daily_total[day] = 0
        daily_total[day] += d['tokens']
    out = []
    for day, total in daily_total.items():
        ts_ms = int(time.mktime(time.strptime(day, '%Y-%m-%d')) * 1000)
        out.append({
            'agent': KEY, 'session_id': f'kimi-{day}',
            'title': f'Kimi {day}',
            'cwd': '', 'model': 'kimi', 'provider': 'moonshot',
            'created_at': ts_ms, 'last_activity_at': ts_ms,
            'input_tokens': 0, 'output_tokens': 0,
            'cache_read_tokens': 0, 'cache_write_tokens': 0,
            'total_tokens': total, 'cost': None, 'est': 0,
            'source_file': _ROOT,
        })
    return {'sessions': out, 'daily': daily_rows, 'daily_files': daily_files}
