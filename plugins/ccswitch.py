# -*- coding: utf-8 -*-
"""插件：CC Switch（权威用量源）。

CC Switch 是本机的 API 代理，所有 Codex / Claude Code 的请求都经过它，
proxy_request_logs 表记录了每一次请求的精确 token 数（含缓存命中），
这比逆向本地 rollout 文件准确得多。

数据源：~/.cc-switch/cc-switch.db
  - proxy_request_logs: created_at(秒), app_type(codex/claude), model,
    input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens,
    total_cost_usd, session_id
"""
import os
import time
import collections
from engine.common import ro_connect

KEY = 'ccswitch'
NAME = 'CC Switch'
ESTIMATE = False
WATCH_PATHS = ['%USERPROFILE%\\.cc-switch\\cc-switch.db']

# app_type -> 上报用的 agent key（与内置 codex/claude 对齐）
APP_TO_AGENT = {
    'codex': 'codex',
    'claude': 'claude',
}


def _db_path():
    return os.path.expanduser(WATCH_PATHS[0].replace('%USERPROFILE%', os.path.expanduser('~')))


def scan(full, need, mark):
    dbp = _db_path()
    if not os.path.exists(dbp):
        return {'sessions': [], 'daily': [], 'daily_files': []}

    st = os.stat(dbp)
    fp = f'{st.st_mtime_ns}:{st.st_size}'
    if not need(full, dbp, fp):
        return {'sessions': [], 'daily': [], 'daily_files': []}
    mark(dbp, fp)

    con = ro_connect(dbp)
    if con is None:
        return {'sessions': [], 'daily': [], 'daily_files': []}
    try:
        rows = con.execute(
            'select created_at, app_type, model, input_tokens, output_tokens, '
            'cache_read_tokens, cache_creation_tokens, total_cost_usd, session_id '
            'from proxy_request_logs'
        ).fetchall()
    except Exception:
        con.close()
        return {'sessions': [], 'daily': [], 'daily_files': []}
    con.close()

    # 按 (agent, day) 聚合
    by_day = collections.defaultdict(lambda: collections.Counter())
    # 按 session 聚合
    sess = {}
    for r in rows:
        (ts, app, model, inp, out, cr, cc, cost, sid) = r
        agent = APP_TO_AGENT.get(app)
        if not agent or not ts:
            continue
        tok = (inp or 0) + (out or 0) + (cr or 0) + (cc or 0)
        day = time.strftime('%Y-%m-%d', time.localtime(ts))
        by_day[(agent, day)]['tokens'] += tok
        by_day[(agent, day)]['cost'] += float(cost or 0)

        key = (agent, sid or '')
        if key not in sess:
            sess[key] = {
                'agent': agent, 'session_id': sid or '',
                'title': (model or '').strip() or '未命名会话',
                'cwd': '', 'model': model or '', 'provider': 'ccswitch',
                'created_at': ts * 1000, 'last_activity_at': ts * 1000,
                'input_tokens': 0, 'output_tokens': 0,
                'cache_read_tokens': 0, 'cache_write_tokens': 0,
                'total_tokens': 0, 'cost': 0.0, 'est': 0,
                'source_file': dbp,
            }
        s = sess[key]
        s['input_tokens'] += inp or 0
        s['output_tokens'] += out or 0
        s['cache_read_tokens'] += cr or 0
        s['cache_write_tokens'] += cc or 0
        s['total_tokens'] += tok
        s['cost'] += float(cost or 0)
        s['last_activity_at'] = max(s['last_activity_at'], ts * 1000)

    daily_rows = []
    for (agent, day), c in by_day.items():
        daily_rows.append({
            'day': day, 'tokens': c['tokens'], 'est': 0,
            'source_file': dbp, 'agent': agent,
        })

    sessions = []
    for s in sess.values():
        s['cost'] = round(s['cost'], 4)
        if s['total_tokens'] > 0:
            sessions.append(s)

    return {
        'sessions': sessions,
        'daily': daily_rows,
        'daily_files': [dbp],
    }
