# -*- coding: utf-8 -*-
"""engine/core.py — 扫描调度、聚合、烘焙（v2 插件化版）。

会话 schema（插件 scan 返回）：
  {
    "agent": str, "session_id": str, "title": str, "cwd": str,
    "model": str, "provider": str,
    "created_at": int(ms), "last_activity_at": int(ms),
    "input_tokens": int, "output_tokens": int,
    "cache_read_tokens": int, "cache_write_tokens": int,
    "total_tokens": int, "cost": float|None, "est": int(0/1),
    "source_file": str,
  }
"""
import os
import sys
import json
import time
import sqlite3

from . import registry
from .common import expand

if getattr(sys, 'frozen', False):
    BASE = os.path.dirname(sys.executable)
else:
    BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE, 'data')
DB_PATH = os.path.join(DATA_DIR, 'usage.db')
JSON_PATH = os.path.join(DATA_DIR, 'usage.json')

# 插件目录：exe 旁 plugins/（用户可扩展）优先，其次内置（打包时 _MEIPASS/plugins_internal）
def plugin_dirs():
    dirs = []
    if getattr(sys, 'frozen', False):
        ext = os.path.join(os.path.dirname(sys.executable), 'plugins')
        if os.path.isdir(ext):
            dirs.append(ext)
        meipass = getattr(sys, '_MEIPASS', '')
        if meipass:
            builtin = os.path.join(meipass, 'plugins_internal')
            if os.path.isdir(builtin):
                dirs.append(builtin)
    else:
        dirs.append(os.path.join(BASE, 'plugins'))
    return dirs


_plugin_cache = None


def get_plugins(refresh=False):
    global _plugin_cache
    if _plugin_cache is None or refresh:
        _plugin_cache = registry.discover(plugin_dirs())
    return _plugin_cache


def web_dir():
    """web 静态资源目录（打包后指向 _MEIPASS/web）。"""
    if getattr(sys, 'frozen', False):
        meipass = getattr(sys, '_MEIPASS', '')
        if meipass:
            return os.path.join(meipass, 'web')
    return os.path.join(BASE, 'web')


def _conn():
    os.makedirs(DATA_DIR, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.executescript('''
        CREATE TABLE IF NOT EXISTS sessions(
            agent TEXT NOT NULL,
            session_id TEXT NOT NULL,
            title TEXT,
            cwd TEXT,
            model TEXT,
            provider TEXT,
            created_at INTEGER DEFAULT 0,
            last_activity_at INTEGER DEFAULT 0,
            input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            cache_read_tokens INTEGER DEFAULT 0,
            cache_write_tokens INTEGER DEFAULT 0,
            total_tokens INTEGER DEFAULT 0,
            cost REAL,
            est INTEGER DEFAULT 0,
            source_file TEXT,
            scan_ts INTEGER,
            PRIMARY KEY(agent, session_id)
        );
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS daily(
            agent TEXT NOT NULL,
            day TEXT NOT NULL,
            source_file TEXT,
            tokens INTEGER DEFAULT 0,
            est INTEGER DEFAULT 0,
            PRIMARY KEY(agent, day, source_file)
        );
    ''')
    return con


def scan(full=False, only=None):
    """增量/全量扫描。返回 (data, stats)。"""
    plugins = get_plugins()
    con = _conn()
    now = int(time.time() * 1000)

    def need(f, path, fp):
        if f:
            return True
        row = con.execute('select value from meta where key=?', ('fp:' + path,)).fetchone()
        return row is None or row[0] != fp

    def mark(path, fp):
        con.execute(
            'insert into meta(key, value) values(?, ?) '
            'on conflict(key) do update set value=excluded.value',
            ('fp:' + path, fp))

    stats = {}
    for plug in plugins:
        key = plug['key']
        if only and key not in only:
            continue
        t0 = time.time()
        try:
            result = plug['scan'](full, need, mark) or []
        except Exception as e:
            stats[key] = {'status': 'error', 'error': str(e)}
            continue
        # 新协议：scan 可返回 dict {sessions, daily, daily_files}；旧协议返回 list
        if isinstance(result, dict):
            rows = result.get('sessions') or []
            daily_rows = result.get('daily') or []
            daily_files = result.get('daily_files') or []
        else:
            rows = result
            daily_rows = []
            daily_files = []
        upserted = 0
        for r in rows:
            con.execute('''
                insert into sessions(agent, session_id, title, cwd, model, provider,
                    created_at, last_activity_at, input_tokens, output_tokens,
                    cache_read_tokens, cache_write_tokens, total_tokens, cost, est,
                    source_file, scan_ts)
                values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                on conflict(agent, session_id) do update set
                    title=excluded.title, cwd=excluded.cwd, model=excluded.model,
                    provider=excluded.provider, created_at=excluded.created_at,
                    last_activity_at=excluded.last_activity_at,
                    input_tokens=excluded.input_tokens, output_tokens=excluded.output_tokens,
                    cache_read_tokens=excluded.cache_read_tokens,
                    cache_write_tokens=excluded.cache_write_tokens,
                    total_tokens=excluded.total_tokens, cost=excluded.cost,
                    est=excluded.est, source_file=excluded.source_file,
                    scan_ts=excluded.scan_ts
            ''', (r['agent'], r['session_id'], r['title'], r['cwd'], r['model'],
                  r['provider'], r['created_at'], r['last_activity_at'],
                  r['input_tokens'], r['output_tokens'], r['cache_read_tokens'],
                  r['cache_write_tokens'], r['total_tokens'], r['cost'], r['est'],
                  r['source_file'], now))
            upserted += 1
        # 按天真实聚合（插件返回的 daily）：先删该数据源旧行，再插入
        # 一个插件可能报多个 agent（如 ccswitch 同时报 codex+claude），按 source_file 删
        for df in daily_files:
            con.execute('delete from daily where source_file=?', (df,))
        for d in daily_rows:
            d_agent = d.get('agent', key)
            con.execute(
                'insert or replace into daily(agent, day, source_file, tokens, est) values(?,?,?,?,?)',
                (d_agent, d.get('day'), d.get('source_file'), int(d.get('tokens') or 0),
                 int(d.get('est') or 0)))
        con.commit()
        stats[key] = {'status': 'ok', 'upserted': upserted,
                      'seconds': round(time.time() - t0, 2)}

    con.execute('insert or replace into meta(key, value) values(?,?)',
                ('last_scan_ms', str(now)))
    con.commit()

    # 收集本次扫描实际出现的 agent（含多 agent 插件的子 agent），用于清理残留
    active_agents = set()
    for plug in plugins:
        active_agents.add(plug['key'])
    for (ag,) in con.execute('select distinct agent from sessions').fetchall():
        active_agents.add(ag)

    # 清理已移除插件的残留会话（防止数据里出现已不存在的 agent）
    if not only:
        if active_agents:
            ph = ','.join('?' * len(active_agents))
            al = list(active_agents)
            con.execute('delete from sessions where agent not in (%s)' % ph, al)
            con.execute('delete from daily where agent not in (%s)' % ph, al)
            con.commit()

    data = build_json(con, now, plugins)
    con.close()

    with open(JSON_PATH, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)
    bake_html(data)
    return data, stats


def build_json(con, now, plugins=None):
    plugins = plugins or get_plugins()
    names = {p['key']: p['name'] for p in plugins}
    est_flags = {p['key']: p['estimate'] for p in plugins}
    order = [p['key'] for p in plugins]

    agents = {}
    days = {}
    models = {}
    sessions = []
    # 按天真实聚合表（有该表数据的 agent，优先用真实每轮时间戳，不再用会话最后活跃日兜底）
    daily_rows = con.execute(
        'select agent, day, sum(tokens) from daily group by agent, day').fetchall()
    daily_agents = set()
    for (ag, day, tok) in daily_rows:
        daily_agents.add(ag)
        dd = days.setdefault(day, {})
        dd[ag] = dd.get(ag, 0) + int(tok or 0)
    rows = con.execute('''
        select agent, session_id, title, cwd, model, provider,
               created_at, last_activity_at, input_tokens, output_tokens,
               cache_read_tokens, cache_write_tokens, total_tokens, cost, est
        from sessions
    ''').fetchall()
    for r in rows:
        (agent, sid, title, cwd, model, provider, created, last,
         inp, out_t, cache_r, cache_w, total, cost, est) = r
        a = agents.setdefault(agent, {
            'key': agent, 'name': names.get(agent, agent),
            'count': 0, 'total_tokens': 0, 'est_tokens': 0, 'real_tokens': 0,
            'cost': 0.0, 'est': 0,
        })
        a['count'] += 1
        a['total_tokens'] += total
        if est:
            a['est_tokens'] += total
            a['est'] = 1
        else:
            a['real_tokens'] += total
        if cost:
            a['cost'] += cost
        # 无按天真实数据的 agent，才回退用会话最后活跃日兜底
        if last and agent not in daily_agents:
            d = time.strftime('%Y-%m-%d', time.localtime(last / 1000))
            dd = days.setdefault(d, {})
            dd[agent] = dd.get(agent, 0) + total
        mkey = (agent, model or '(未知)')
        mm = models.setdefault(mkey, {'agent': agent, 'model': model or '(未知)', 'tokens': 0, 'count': 0})
        mm['tokens'] += total
        mm['count'] += 1
        sessions.append({
            'agent': agent, 'agent_name': names.get(agent, agent),
            'session_id': sid, 'title': title, 'cwd': cwd, 'model': model,
            'created_at': created, 'last_activity_at': last,
            'input_tokens': inp, 'output_tokens': out_t,
            'cache_read_tokens': cache_r, 'cache_write_tokens': cache_w,
            'total_tokens': total, 'cost': cost, 'est': est,
        })

    all_dates = sorted(days.keys())
    # 列顺序：插件 key 在前，再补上 daily 里出现的子 agent（如 ccswitch->codex/claude）
    daily_agents = set()
    for d in all_dates:
        daily_agents.update(days[d].keys())
    col_order = list(order)
    for a in sorted(daily_agents):
        if a not in col_order:
            col_order.append(a)
    day_series = []
    for d in all_dates:
        row = {'date': d}
        for k in col_order:
            row[k] = days[d].get(k, 0)
        day_series.append(row)

    sessions.sort(key=lambda s: s['total_tokens'], reverse=True)
    total_real = sum(a['real_tokens'] for a in agents.values())
    total_est = sum(a['est_tokens'] for a in agents.values())
    coverage = []
    for p in plugins:
        k = p['key']
        if k not in agents:
            coverage.append({'agent': k, 'name': p['name'],
                             'status': 'empty', 'note': '未发现本地用量数据'})
    for a in agents.values():
        if a['est']:
            coverage.append({'agent': a['key'], 'name': a['name'], 'status': 'estimate',
                             'note': '本地无真实计数，按会话文本估算'})

    return {
        'generated_at': now,
        'generated_at_str': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(now / 1000)),
        'agents': sorted(agents.values(), key=lambda x: -x['total_tokens']),
        'days': day_series,
        'models': sorted(models.values(), key=lambda x: -x['tokens'])[:30],
        'sessions': sessions[:500],
        'session_count': len(sessions),
        'totals': {
            'total_tokens': total_real + total_est,
            'real_tokens': total_real,
            'est_tokens': total_est,
        },
        'coverage': coverage,
    }


def bake_html(data):
    """把数据烘焙进 web/dashboard.html 的占位符。"""
    html_path = os.path.join(web_dir(), 'dashboard.html')
    if not os.path.exists(html_path):
        return
    with open(html_path, 'r', encoding='utf-8') as f:
        html = f.read()
    start = '/*__AGENT_DATA_START__*/'
    end = '/*__AGENT_DATA_END__*/'
    if start not in html or end not in html:
        return
    import re
    payload = json.dumps(data, ensure_ascii=False)
    payload = payload.replace('</', '<\\/')
    payload = payload.replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')
    # lambda 替换避免 re.sub 把 payload 里的反斜杠转义当转义序列
    html = re.sub(re.escape(start) + r'.*?' + re.escape(end),
                  lambda m: start + payload + end, html, flags=re.S)
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(html)
