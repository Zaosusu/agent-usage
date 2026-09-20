# -*- coding: utf-8 -*-
"""插件：WorkBuddy。

本地数据真实情况：
  - session_usage.used 是"当前上下文快照"（ capped 到 size 窗口），不是累计消耗；
  - session_usage.credit_json 是真实花费（钱），这是本地唯一可信的用量信号。
因此：
  - cost = credit_json 求和（真实人民币）；
  - total_tokens = cost 按均价折算（估算，本地不存累计 token）；
  - 按天用量 = 按 last_activity 把当天花费归集后折算。
均价默认 ¥2 / 1M tokens（Kimi/DeepSeek/混元/GLM 等国产模型混合），可自行调整 PRICE_PER_M。
"""
import os
import json
import time
import collections
from engine.common import ro_connect

KEY = 'workbuddy'
NAME = 'WorkBuddy'
ESTIMATE = False         # credit 花费是真实值，token 按均价折算但花费本身精确
WATCH_PATHS = [
    '%USERPROFILE%\\.workbuddy\\workbuddy.db',
    '%USERPROFILE%\\.workbuddy-ai\\workbuddy.db',
]
PRICE_PER_M = 2.0        # 元 / 1M tokens（混合国产模型均价假设）


def _money_of(credit):
    if not credit:
        return 0.0
    try:
        cd = json.loads(credit)
        if isinstance(cd, dict):
            return sum(float(v) for v in cd.values() if isinstance(v, (int, float)))
    except Exception:
        pass
    return 0.0


def _tokens_of(money):
    # 钱 -> token 估算
    return int(money / PRICE_PER_M * 1_000_000) if PRICE_PER_M > 0 else 0


def _scan_one(dbp, by_day):
    if not os.path.exists(dbp):
        return []
    con = ro_connect(dbp)
    if con is None:
        return []
    try:
        rows = con.execute(
            'select su.session_id, su.used, su.credit_json, '
            's.title, s.model, s.created_at, s.last_activity_at, s.cwd '
            'from session_usage su left join sessions s on su.session_id = s.id'
        ).fetchall()
    except Exception:
        con.close()
        return []
    con.close()
    out = []
    for r in rows:
        (sid, used, credit, title, model, created, last, cwd) = r
        money = _money_of(credit)
        cost = round(money, 4) if money else None
        est_tok = _tokens_of(money)
        ts = last or created
        if ts:
            day = time.strftime('%Y-%m-%d', time.localtime(ts / 1000))
            by_day[day] += est_tok
        out.append({
            'agent': KEY, 'session_id': sid,
            'title': (title or '').strip() or '未命名会话',
            'cwd': cwd or '', 'model': model or '', 'provider': '',
            'created_at': created or 0, 'last_activity_at': last or 0,
            'input_tokens': est_tok, 'output_tokens': 0,
            'cache_read_tokens': 0, 'cache_write_tokens': 0,
            'total_tokens': est_tok, 'cost': cost, 'est': 0,
            'source_file': dbp,
        })
    return out


def scan(full, need, mark):
    home = os.path.expanduser('~')
    # 用 db 指纹决定是否重扫
    dbps = []
    for w in WATCH_PATHS:
        dbp = w.replace('%USERPROFILE%', home)
        if os.path.exists(dbp):
            dbps.append(dbp)
    if not dbps:
        return {'sessions': [], 'daily': [], 'daily_files': []}

    # 任一 db 变化才重扫
    changed = False
    daily_files = []
    for dbp in dbps:
        st = os.stat(dbp)
        fp = f'{st.st_mtime_ns}:{st.st_size}'
        if need(full, dbp, fp):
            mark(dbp, fp)
            changed = True
            daily_files.append(dbp)

    if not changed:
        return {'sessions': [], 'daily': [], 'daily_files': []}

    by_day = collections.Counter()
    sessions = []
    for dbp in dbps:
        sessions += _scan_one(dbp, by_day)

    daily_rows = [{'day': d, 'tokens': t, 'est': 0, 'source_file': dbps[0]}
                  for d, t in by_day.items()]
    return {'sessions': sessions, 'daily': daily_rows, 'daily_files': daily_files}
