# -*- coding: utf-8 -*-
"""
adapters.py — 各 agent 的 token 用量读取器（只读）。

每个 adapter 实现 scan(full: bool) -> list[dict]：
  {
    "agent": str,            # 稳定键，如 "codex"
    "session_id": str,       # 会话唯一 id
    "title": str,            # 会话标题（可为空）
    "cwd": str,
    "model": str,
    "provider": str,
    "created_at": int,       # ms epoch
    "last_activity_at": int, # ms epoch
    "input_tokens": int,
    "output_tokens": int,
    "cache_read_tokens": int,
    "cache_write_tokens": int,
    "total_tokens": int,
    "cost": float | None,    # 有费用数据才填
    "est": int,              # 1=估算
    "source_file": str,      # 供增量扫描用（指纹存 meta）
  }

增量扫描：monitor.py 会把每个 source_file 的 (mtime_ns, size) 指纹存进 meta 表。
adapter 内部用 scan_state 回调判断文件是否需要重新解析：
  need(full, path) -> bool；解析成功后调用 mark(path)。
sqlite 类数据源（codex/workbuddy/zcode）整体重查即可，开销小，不做增量。
"""
import os
import json
import glob
import sqlite3
import datetime
import re

HOME = os.path.expanduser('~')
_CODECX_DB = os.path.join(HOME, '.codex', 'state_5.sqlite')
_WORKBUDDY_DBS = [
    os.path.join(HOME, '.workbuddy', 'workbuddy.db'),
    os.path.join(HOME, '.workbuddy-ai', 'workbuddy.db'),
]
_ZCODE_DB = os.path.join(HOME, '.zcode', 'cli', 'db', 'db.sqlite')
_CLAUDE_PROJ = os.path.join(HOME, '.claude', 'projects')
_CODEBUDDY_PROJ = os.path.join(HOME, '.codebuddy', 'projects')
_KIMI_SESSIONS = os.path.join(HOME, '.kimi', 'sessions')
_QWEN_PROJ = os.path.join(HOME, '.qwenworkcn', 'projects')
_DOUBAO_SESSIONS = os.path.join(
    HOME, 'AppData', 'Local', 'DoubaoWork', 'User Data', 'Default',
    '.doubaowork', 'agent_mode', 'workspace', '.sessions')

AGENT_NAMES = {
    'codex': 'Codex',
    'claude': 'Claude CLI',
    'workbuddy': 'WorkBuddy',
    'codebuddy': 'CodeBuddy',
    'kimi': 'Kimi',
    'zcode': 'ZCode',
    'qwenworkcn': '千问工作',
    'doubao': '豆包工作',
}


def _iso_to_ms(ts):
    """ISO8601 -> ms epoch；失败返回 None。"""
    if not ts:
        return None
    try:
        s = str(ts).replace('Z', '+00:00')
        dt = datetime.datetime.fromisoformat(s)
        return int(dt.timestamp() * 1000)
    except Exception:
        return None


def estimate_tokens(text):
    """估算 token：CJK 字符按 1 token/字，其余按 4 字符/token。"""
    if not text:
        return 0
    cjk = 0
    other = 0
    for ch in text:
        cp = ord(ch)
        if 0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF:
            cjk += 1
        elif not ch.isspace():
            other += 1
    return cjk + (other + 3) // 4


def _first_text(obj, maxlen=60):
    """递归找第一个较长的字符串作为标题。"""
    if isinstance(obj, dict):
        for v in obj.values():
            t = _first_text(v, maxlen)
            if t:
                return t
    elif isinstance(obj, list):
        for v in obj:
            t = _first_text(v, maxlen)
            if t:
                return t
    elif isinstance(obj, str):
        s = obj.strip()
        if len(s) >= 2:
            return s[:maxlen]
    return ''


def _walk_json_for_usage(obj, acc):
    """递归收集含 input_tokens/output_tokens 的 usage 字典。"""
    if isinstance(obj, dict):
        if 'input_tokens' in obj and 'output_tokens' in obj:
            acc.append(obj)
        for v in obj.values():
            _walk_json_for_usage(v, acc)
    elif isinstance(obj, list):
        for v in obj:
            _walk_json_for_usage(v, acc)


def _sum_usage(u, keys):
    total = 0
    for k in keys:
        v = u.get(k)
        if isinstance(v, (int, float)):
            total += v
    return total


# ---------------------------------------------------------------- codex
def scan_codex(full, need, mark):
    if not os.path.exists(_CODECX_DB):
        return []
    out = []
    con = _ro_connect(_CODECX_DB)
    if con is None:
        return []
    try:
        rows = con.execute(
            'select id, rollout_path, title, cwd, model, model_provider, '
            'tokens_used, created_at_ms, updated_at_ms, created_at, updated_at '
            'from threads'
        ).fetchall()
    except sqlite3.Error:
        con.close()
        return []
    con.close()
    for r in rows:
        (sid, path, title, cwd, model, provider, tokens,
         cms, ums, cs, us) = r
        created = cms if cms else (cs * 1000 if cs else None)
        updated = ums if ums else (us * 1000 if us else None)
        cwd = (cwd or '').replace('\\\\?\\', '').replace('\\?\\', '')
        out.append({
            'agent': 'codex',
            'session_id': sid,
            'title': (title or '').strip() or '未命名会话',
            'cwd': cwd,
            'model': model or '',
            'provider': provider or '',
            'created_at': created or updated or 0,
            'last_activity_at': updated or created or 0,
            'input_tokens': tokens or 0,
            'output_tokens': 0,
            'cache_read_tokens': 0,
            'cache_write_tokens': 0,
            'total_tokens': tokens or 0,
            'cost': None,
            'est': 0,
            'source_file': _CODECX_DB,
        })
    return out


# ---------------------------------------------------------------- claude / codebuddy (同构)
def _scan_claude_like(agent, proj_root, full, need, mark):
    if not os.path.isdir(proj_root):
        return []
    out = []
    files = glob.glob(os.path.join(proj_root, '**', '*.jsonl'), recursive=True)
    for p in files:
        try:
            st = os.stat(p)
        except OSError:
            continue
        fp = f'{st.st_mtime_ns}:{st.st_size}'
        if not need(full, p, fp):
            continue
        sess = _parse_claude_like_file(agent, p)
        if sess is not None:
            out.append(sess)
        mark(p, fp)
    return out


def _ts_to_ms(v):
    """兼容 ISO 字符串 / 秒 / 毫秒三种时间戳。"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        if v > 1e14:      # 纳秒（少见）
            return int(v / 1e6)
        if v > 1e12:      # 毫秒
            return int(v)
        return int(v * 1000)  # 秒
    return _iso_to_ms(v)


def _extract_usage(u):
    """从 usage 字典提取 (input, output, cache_read, cache_write)，兼容多种字段命名。"""
    if not isinstance(u, dict):
        return (0, 0, 0, 0)
    inp = u.get('input_tokens') or u.get('prompt_tokens') or 0
    out_t = u.get('output_tokens') or u.get('completion_tokens') or 0
    cache_r = u.get('cache_read_input_tokens') or u.get('cache_read_tokens') or u.get('cached_tokens') or 0
    cache_w = u.get('cache_creation_input_tokens') or u.get('cache_creation_tokens') or 0
    return (inp, out_t, cache_r, cache_w)


def _parse_claude_like_file(agent, path):
    sid = os.path.splitext(os.path.basename(path))[0]
    first_ts = last_ts = None
    title = ''
    cwd = ''
    model = ''
    inp = out_t = cache_r = cache_w = 0
    first_user = True
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
            ts = _ts_to_ms(obj.get('timestamp'))
            if ts:
                if first_ts is None:
                    first_ts = ts
                last_ts = ts
            msg = obj.get('message')
            msg = msg if isinstance(msg, dict) else {}
            role = msg.get('role') or obj.get('role')
            if role == 'user' and first_user and not title:
                t = _first_text(msg.get('content') or obj.get('content'), 60)
                if t:
                    title = t
                    first_user = False
            u = msg.get('usage')
            if not isinstance(u, dict):
                # 兜底：在整条记录里找 usage 字典（providerData.usage 等）
                found = []
                _walk_json_for_usage(obj, found)
                if found:
                    u = found[0]
            if isinstance(u, dict):
                i, o, cr, cw = _extract_usage(u)
                inp += i
                out_t += o
                cache_r += cr
                cache_w += cw
            if not model:
                pd = obj.get('providerData')
                if isinstance(pd, dict):
                    model = pd.get('model') or pd.get('requestModelName') or ''
                if not model:
                    model = msg.get('model') or ''
            if obj.get('cwd') and not cwd:
                cwd = obj['cwd']
            if obj.get('type') == 'user' and first_user and not title:
                t = _first_text(obj.get('message') or obj.get('content'), 60)
                if t:
                    title = t
                    first_user = False
    total = inp + out_t + cache_r + cache_w
    if total == 0 and first_ts is None:
        return None  # 空文件 / 无消息
    return {
        'agent': agent,
        'session_id': sid,
        'title': title or '未命名会话',
        'cwd': cwd,
        'model': model,
        'provider': '',
        'created_at': first_ts or 0,
        'last_activity_at': last_ts or 0,
        'input_tokens': inp,
        'output_tokens': out_t,
        'cache_read_tokens': cache_r,
        'cache_write_tokens': cache_w,
        'total_tokens': total,
        'cost': None,
        'est': 0,
        'source_file': path,
    }


# ---------------------------------------------------------------- workbuddy
def scan_workbuddy(full, need, mark):
    out = []
    for dbp in _WORKBUDDY_DBS:
        if not os.path.exists(dbp):
            continue
        con = _ro_connect(dbp)
        if con is None:
            continue
        try:
            rows = con.execute(
                'select su.session_id, su.used, su.size, su.credit_json, '
                's.title, s.model, s.created_at, s.last_activity_at, s.cwd '
                'from session_usage su left join sessions s on su.session_id = s.id'
            ).fetchall()
        except sqlite3.Error as e:
            con.close()
            continue
        con.close()
        for r in rows:
            (sid, used, size, credit, title, model, created, last, cwd) = r
            cost = None
            if credit:
                try:
                    cd = json.loads(credit)
                    if isinstance(cd, dict):
                        cost = round(sum(float(v) for v in cd.values() if isinstance(v, (int, float))), 4)
                except Exception:
                    cost = None
            out.append({
                'agent': 'workbuddy',
                'session_id': sid,
                'title': (title or '').strip() or '未命名会话',
                'cwd': cwd or '',
                'model': model or '',
                'provider': '',
                'created_at': created or 0,
                'last_activity_at': last or 0,
                'input_tokens': used or 0,
                'output_tokens': 0,
                'cache_read_tokens': 0,
                'cache_write_tokens': 0,
                'total_tokens': used or 0,
                'cost': cost,
                'est': 0,
                'source_file': dbp,
            })
    return out


# ---------------------------------------------------------------- kimi
def scan_kimi(full, need, mark):
    if not os.path.isdir(_KIMI_SESSIONS):
        return []
    out = []
    wires = glob.glob(os.path.join(_KIMI_SESSIONS, '*', '*', 'wire.jsonl'))
    wires += glob.glob(os.path.join(_KIMI_SESSIONS, '*', 'wire.jsonl'))
    for p in wires:
        try:
            st = os.stat(p)
        except OSError:
            continue
        fp = f'{st.st_mtime_ns}:{st.st_size}'
        if not need(full, p, fp):
            continue
        sess = _parse_kimi_wire(p)
        if sess is not None:
            out.append(sess)
        mark(p, fp)
    return out


def _parse_kimi_wire(path):
    parts = os.path.relpath(path, _KIMI_SESSIONS).split(os.sep)
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
        'agent': 'kimi',
        'session_id': sid,
        'title': 'Kimi 会话 ' + sid.split(os.sep)[0][:8],
        'cwd': '',
        'model': '',
        'provider': '',
        'created_at': first_ts or 0,
        'last_activity_at': last_ts or 0,
        'input_tokens': inp,
        'output_tokens': out_t,
        'cache_read_tokens': cache_r,
        'cache_write_tokens': cache_w,
        'total_tokens': total,
        'cost': None,
        'est': 0,
        'source_file': path,
    }


# ---------------------------------------------------------------- zcode（结构存在但可能为空）
def scan_zcode(full, need, mark):
    if not os.path.exists(_ZCODE_DB):
        return []
    con = _ro_connect(_ZCODE_DB)
    if con is None:
        return []
    try:
        n = con.execute('select count(*) from model_usage').fetchone()[0]
    except sqlite3.Error:
        n = 0
    con.close()
    if n == 0:
        return []
    # 目前表内无数据；若未来有数据，在此解析（列结构见探查结果）
    return []


# ---------------------------------------------------------------- 估算型：doubao / qwenworkcn
def _scan_estimate(agent, root, glob_pat, full, need, mark, path_key):
    if not os.path.isdir(root):
        return []
    out = []
    files = glob.glob(os.path.join(root, glob_pat), recursive=True)
    for p in files:
        try:
            st = os.stat(p)
        except OSError:
            continue
        fp = f'{st.st_mtime_ns}:{st.st_size}'
        if not need(full, p, fp):
            continue
        sess = _parse_estimate_file(agent, p, path_key)
        if sess is not None:
            out.append(sess)
        mark(p, fp)
    return out


def _parse_estimate_file(agent, path, path_key):
    tokens = 0
    first_ts = last_ts = None
    title = ''
    try:
        st = os.stat(path)
    except OSError:
        return None
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
                tokens += estimate_tokens(line)
                continue
            ts = _iso_to_ms(obj.get('timestamp'))
            if ts:
                if first_ts is None:
                    first_ts = ts
                last_ts = ts
            if not title:
                title = _first_text(obj, 60)
            texts = []
            _collect_strings(obj, texts, 200000)
            tokens += estimate_tokens('\n'.join(texts))
    if tokens == 0 and first_ts is None:
        return None
    rel = os.path.relpath(path, path_key)
    sid = rel.replace(os.sep, '/')
    return {
        'agent': agent,
        'session_id': sid,
        'title': title or '会话 ' + sid[:20],
        'cwd': '',
        'model': '',
        'provider': '',
        'created_at': first_ts or int(st.st_mtime * 1000),
        'last_activity_at': last_ts or int(st.st_mtime * 1000),
        'input_tokens': 0,
        'output_tokens': 0,
        'cache_read_tokens': 0,
        'cache_write_tokens': 0,
        'total_tokens': tokens,
        'cost': None,
        'est': 1,
        'source_file': path,
    }


def _collect_strings(obj, out, cap):
    if len(out) >= 5000:
        return
    if isinstance(obj, dict):
        for v in obj.values():
            _collect_strings(v, out, cap)
    elif isinstance(obj, list):
        for v in obj:
            _collect_strings(v, out, cap)
    elif isinstance(obj, str):
        if obj.strip():
            out.append(obj[:cap])


def scan_doubao(full, need, mark):
    return _scan_estimate('doubao', _DOUBAO_SESSIONS, '*/agents/*/system/trajectory.jsonl',
                          full, need, mark, _DOUBAO_SESSIONS)


def scan_qwenworkcn(full, need, mark):
    return _scan_estimate('qwenworkcn', _QWEN_PROJ, '**/*.jsonl',
                          full, need, mark, _QWEN_PROJ)


# ---------------------------------------------------------------- 工具
def _ro_connect(path):
    try:
        return sqlite3.connect('file:' + path.replace('\\', '/') + '?mode=ro', uri=True)
    except sqlite3.Error:
        return None


ADAPTERS = [
    ('codex', scan_codex),
    ('claude', lambda f, n, m: _scan_claude_like('claude', _CLAUDE_PROJ, f, n, m)),
    ('workbuddy', scan_workbuddy),
    ('codebuddy', lambda f, n, m: _scan_claude_like('codebuddy', _CODEBUDDY_PROJ, f, n, m)),
    ('kimi', scan_kimi),
    ('zcode', scan_zcode),
    ('qwenworkcn', scan_qwenworkcn),
    ('doubao', scan_doubao),
]
