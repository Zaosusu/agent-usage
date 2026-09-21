"""WorkBuddy 用量解析（新版兼容：老会话读 session_usage，新会话从 jsonl 估算）。

老版本 WorkBuddy 把累计花费存在 session_usage.credit_json；
新版本不再写这个字段，所以从 projects/*/*.jsonl 文件里统计消息文本估算 token。
"""
from __future__ import annotations
import json, os, re, glob, time, sqlite3
from engine.common import ro_connect

KEY = 'workbuddy'
NAME = 'WorkBuddy'
DBP = os.path.expanduser('~/.workbuddy/workbuddy.db')
PROJECTS_ROOT = os.path.expanduser('~/.workbuddy/projects')
WATCH_PATHS = [DBP, PROJECTS_ROOT]
PRICE_PER_M = 2.0  # 元 / 1M tokens

# 估算系数：中文 1 字 ≈ 1.5 token，英文 1 词 ≈ 0.75 token
def _estimate_tokens(text):
    if not text:
        return 0
    chinese = len(re.findall(r'[\u4e00-\u9fff]', text))
    english = len(re.findall(r'[a-zA-Z]+', text))
    return int(chinese * 1.5 + english * 0.75)

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
    return int(money / PRICE_PER_M * 1_000_000) if PRICE_PER_M > 0 else 0

def _parse_jsonl_file(path):
    """解析一个 WorkBuddy jsonl 会话文件，返回按天的估算 token 数和标题"""
    daily = {}
    title = os.path.basename(path).replace('.jsonl', '')
    with open(path, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            try:
                d = json.loads(line)
            except:
                continue
            t = d.get('type')
            ts = d.get('timestamp')
            if not ts or not isinstance(ts, (int, float)):
                continue
            day = time.strftime('%Y-%m-%d', time.localtime(ts / 1000))
            if t == 'ai-title':
                title = d.get('aiTitle', title)
            elif t == 'message':
                role = d.get('role')
                if role not in ('user', 'assistant'):
                    continue
                content = d.get('content', [])
                text = ''
                if isinstance(content, list):
                    for c in content:
                        if isinstance(c, dict) and 'text' in c:
                            text += c['text']
                elif isinstance(content, str):
                    text = content
                tokens = _estimate_tokens(text)
                if day not in daily:
                    daily[day] = 0
                daily[day] += tokens
            elif t == 'reasoning':
                content = d.get('rawContent', [])
                text = ''
                if isinstance(content, list):
                    for c in content:
                        if isinstance(c, dict) and 'text' in c:
                            text += c['text']
                tokens = _estimate_tokens(text)
                if day not in daily:
                    daily[day] = 0
                daily[day] += tokens
    return daily, title

def scan(full, need, mark):
    out = []
    daily_rows = []
    daily_files = []
    used_session_ids = set()

    # 1. 先读 session_usage 表的老数据（有 credit_json 的真实花费）
    if os.path.exists(DBP):
        con = ro_connect(DBP)
        if con:
            try:
                rows = con.execute(
                    'select su.session_id, su.credit_json, s.title, s.model, s.created_at, s.last_activity_at, s.cwd '
                    'from session_usage su left join sessions s on su.session_id = s.id'
                ).fetchall()
            except Exception:
                rows = []
            con.close()
            for r in rows:
                sid, credit, title, model, created, last, cwd = r
                money = _money_of(credit)
                if money <= 0:
                    continue
                est_tok = _tokens_of(money)
                cost = round(money, 4)
                ts = last or created
                day = time.strftime('%Y-%m-%d', time.localtime(ts / 1000)) if ts else time.strftime('%Y-%m-%d')
                ts_ms = int(ts) if ts else int(time.time() * 1000)
                out.append({
                    'agent': KEY, 'session_id': sid,
                    'title': (title or '未命名会话')[:30],
                    'cwd': cwd or '', 'model': model or '', 'provider': 'workbuddy',
                    'created_at': created or 0, 'last_activity_at': last or 0,
                    'input_tokens': est_tok, 'output_tokens': 0,
                    'cache_read_tokens': 0, 'cache_write_tokens': 0,
                    'total_tokens': est_tok, 'cost': cost, 'est': 0,
                    'source_file': DBP,
                })
                daily_rows.append({
                    # source_file 必须按会话唯一：daily 表主键是 (agent, day, source_file)，
                    # 若同一数据源的多个会话共用 source_file，同一天会被互相覆盖。
                    'agent': KEY, 'day': day, 'source_file': f'{DBP}#{sid}',
                    'tokens': est_tok, 'est': 0,
                })
                used_session_ids.add(sid)
            # 注册旧的整表 source_file，让引擎清掉历史遗留的旧格式 daily 行
            # （旧版本曾用 source_file=DBP 导致同日多会话互相覆盖，这里做一次性迁移清理）。
            # 新写入的行是 DBP#sid 格式，不会被这次删除命中。
            daily_files.append(DBP)
            mark(DBP, f'{os.stat(DBP).st_mtime_ns}:{os.stat(DBP).st_size}')

    # 2. 再读 projects 下的 jsonl 文件，统计没有 credit_json 的会话
    if os.path.isdir(PROJECTS_ROOT):
        files = glob.glob(os.path.join(PROJECTS_ROOT, '*', '*.jsonl'))
        for p in files:
            # 从文件名提取 session id（文件名就是 session id）
            sid = os.path.basename(p).replace('.jsonl', '')
            if sid in used_session_ids:
                continue  # 已经从 db 里读过了，不用再算
            try:
                st = os.stat(p)
            except OSError:
                continue
            fp = f'{st.st_mtime_ns}:{st.st_size}'
            if not need(full, p, fp):
                continue
            daily, title = _parse_jsonl_file(p)
            for day, tokens in daily.items():
                if tokens < 100:
                    continue
                ts_ms = int(time.mktime(time.strptime(day, '%Y-%m-%d')) * 1000)
                short_id = sid[:8]
                out.append({
                    'agent': KEY, 'session_id': f'workbuddy-{day}-{short_id}',
                    'title': f'{title[:30]} {day}',
                    'cwd': '', 'model': 'workbuddy', 'provider': 'workbuddy',
                    'created_at': ts_ms, 'last_activity_at': ts_ms,
                    'input_tokens': 0, 'output_tokens': 0,
                    'cache_read_tokens': 0, 'cache_write_tokens': 0,
                    'total_tokens': tokens, 'cost': None, 'est': 1,
                    'source_file': p,
                })
                daily_rows.append({
                    'agent': KEY, 'day': day, 'source_file': p,
                    'tokens': tokens, 'est': 1,
                })
            daily_files.append(p)
            mark(p, fp)

    return {'sessions': out, 'daily': daily_rows, 'daily_files': daily_files}
