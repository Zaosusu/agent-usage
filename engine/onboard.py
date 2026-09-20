# -*- coding: utf-8 -*-
"""engine/onboard.py — 机器可调用的自助接入。

任何 agent 都可以通过 POST /api/agents 注册新 agent：
  1. 给 name（可选 data_path）：自动探测常见数据目录，识别 jsonl/sqlite；
  2. 能识别 -> 自动在扩展 plugins/ 生成插件、热加载、开始监控；
  3. 识别不了 -> 返回结构化的 need_config，告诉调用方去哪找、要给什么字段。
"""
import os
import re
import glob
import json
import sqlite3

from . import common


def derive_key(name):
    k = re.sub(r'[^a-z0-9]+', '', (name or '').lower())
    if not k:
        # 纯中文等无 ASCII 的名字：用名字 hash 做短 key，避免空串遍历整个目录
        import hashlib
        h = hashlib.md5((name or '').encode('utf-8')).hexdigest()[:8]
        return 'ag_' + h
    return k


def _candidate_roots(name, key):
    home = os.path.expanduser('~')
    nl = re.sub(r'[^a-z0-9]+', '', (name or '').lower())
    cands = [
        os.path.join(home, '.' + key),
    ]
    if nl:
        cands.append(os.path.join(home, '.' + nl))
    if name:
        cands.append(os.path.join(home, 'AppData', 'Roaming', name))
        cands.append(os.path.join(home, 'AppData', 'Local', name))
    cands.append(os.path.join(home, '.config', key))
    out = []
    for c in cands:
        if c and os.path.isdir(c) and c not in out:
            out.append(c)
    return out


_TOKEN_HINTS = ['input_tokens', 'total_tokens', 'prompt_tokens',
                'output_tokens', 'completion_tokens', '"usage"', 'token_usage']


def probe(name, key=None):
    """按名字自动探测可能的数据文件。返回 (key, roots, [文件路径按相关性排序])。"""
    key = key or derive_key(name)
    roots = _candidate_roots(name, key)
    found = []
    for root in roots:
        for pat in ('**/*.jsonl', '**/*.json', '**/*.sqlite', '**/*.db',
                    '**/*.sqlite3'):
            try:
                for f in glob.glob(os.path.join(root, pat), recursive=True):
                    if os.path.isfile(f):
                        found.append(f)
            except OSError:
                continue

    def score(f):
        s = 0
        lf = f.lower()
        for kw, w in (('usage', 5), ('session', 3), ('history', 3),
                      ('conversation', 2), ('thread', 2), ('chat', 2)):
            if kw in lf:
                s += w
        try:
            s += os.path.getmtime(f) / 1e12
        except OSError:
            pass
        return s

    found = sorted(set(found), key=score, reverse=True)
    return key, roots, found[:20]


def detect_jsonl(path, max_lines=400):
    """peek 一个 jsonl：返回 True=含 token 用量字段。"""
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            for i, line in enumerate(f):
                if i >= max_lines:
                    break
                if any(h in line for h in _TOKEN_HINTS):
                    return True
    except OSError:
        return False
    return False


def detect_sqlite(path):
    """猜测 sqlite 里的用量表：返回 (table, token_col, time_col) 或 None。"""
    try:
        con = common.ro_connect(path)
        if con is None:
            return None
        tables = [r[0] for r in con.execute(
            "select name from sqlite_master where type='table'").fetchall()]
        for t in tables:
            try:
                cols = [c[1].lower() for c in con.execute(
                    'PRAGMA table_info(%s)' % t).fetchall()]
            except sqlite3.Error:
                continue
            tok = next((c for c in cols if 'token' in c), None)
            if tok:
                tim = next((c for c in cols
                            if 'time' in c or 'date' in c or 'created' in c), None)
                con.close()
                return (t, tok, tim)
        con.close()
    except sqlite3.Error:
        pass
    return None


def _plugin_dir():
    """用户扩展插件目录（exe 旁 plugins/ 或项目 plugins/）。"""
    from . import core
    dirs = core.plugin_dirs()
    return dirs[0] if dirs else None


def write_jsonl_plugin(key, name, root, glob_pat, estimate):
    """为 jsonl 数据源生成一个插件文件。"""
    pdir = _plugin_dir()
    if not pdir:
        raise RuntimeError('找不到插件目录')
    os.makedirs(pdir, exist_ok=True)
    path = os.path.join(pdir, key + '.py')
    fn = 'parse_estimate_file' if estimate else 'parse_claude_like_file'
    code = (
        '# -*- coding: utf-8 -*-\n'
        '"""自动生成插件：%s（由 onboarding API 创建）"""\n'
        'import os\n'
        'from engine.common import scan_jsonl_dir, %s\n\n'
        'KEY = %r\n'
        'NAME = %r\n'
        'ESTIMATE = %s\n'
        'WATCH_PATHS = [%r]\n'
        '_ROOT = %r\n'
        '_GLOB = %r\n\n'
        'def scan(full, need, mark):\n'
        '    return scan_jsonl_dir(_ROOT, _GLOB,\n'
        '        lambda p: %s(KEY, p, _ROOT), full, need, mark)\n'
        % (name, fn, key, name, 'True' if estimate else 'False',
           root, root, glob_pat, fn)
    )
    with open(path, 'w', encoding='utf-8') as f:
        f.write(code)
    return path


def write_sqlite_plugin(key, name, db_path, table, tok_col, time_col):
    """为 sqlite 数据源生成一个插件文件。"""
    pdir = _plugin_dir()
    if not pdir:
        raise RuntimeError('找不到插件目录')
    os.makedirs(pdir, exist_ok=True)
    path = os.path.join(pdir, key + '.py')
    code = (
        '# -*- coding: utf-8 -*-\n'
        '"""自动生成插件：%s（由 onboarding API 创建）"""\n'
        'import os\n'
        'from engine.common import ro_connect\n\n'
        'KEY = %r\n'
        'NAME = %r\n'
        'ESTIMATE = False\n'
        'WATCH_PATHS = [%r]\n'
        '_DB = %r\n'
        '_TABLE = %r\n'
        '_TOK = %r\n'
        '_TIME = %r\n\n'
        'def scan(full, need, mark):\n'
        '    if not os.path.exists(_DB):\n'
        '        return []\n'
        '    st = os.stat(_DB)\n'
        "    fp = f'{st.st_mtime_ns}:{st.st_size}'\n"
        '    if not need(full, _DB, fp):\n'
        '        return []\n'
        '    mark(_DB, fp)\n'
        '    con = ro_connect(_DB)\n'
        '    if con is None:\n'
        '        return []\n'
        '    out = []\n'
        "    try:\n"
        "        rows = con.execute(\n"
        "            'select rowid, %s from %s' % (_TOK, _TABLE)).fetchall()\n"
        "    except Exception:\n"
        "        con.close(); return []\n"
        "    for rid, tok in rows:\n"
        "        out.append({\n"
        "            'agent': KEY, 'session_id': '%s_' + str(rid),\n"
        "            'title': '会话 %s ' + str(rid), 'cwd': '', 'model': '',\n"
        "            'provider': '', 'created_at': 0, 'last_activity_at': 0,\n"
        "            'input_tokens': tok or 0, 'output_tokens': 0,\n"
        "            'cache_read_tokens': 0, 'cache_write_tokens': 0,\n"
        "            'total_tokens': tok or 0, 'cost': None, 'est': 0,\n"
        "            'source_file': _DB,\n"
        "        })\n"
        "    con.close()\n"
        "    return out\n"
        % (name, key, name, db_path, db_path, table, tok_col, time_col or '')
    )
    with open(path, 'w', encoding='utf-8') as f:
        f.write(code)
    return path


def register(name, data_path=None):
    """主入口：注册一个新 agent。返回结构化结果。"""
    key = derive_key(name)

    # 1) 调用方直接给了数据路径
    if data_path:
        dp = os.path.expanduser(data_path)
        if not os.path.exists(dp):
            return {'status': 'error', 'agent_key': key,
                    'message': 'data_path 不存在: %s' % dp}
        ext = os.path.splitext(dp)[1].lower()
        if ext in ('.jsonl', '.json'):
            has_usage = detect_jsonl(dp)
            root = os.path.dirname(dp) if os.path.isfile(dp) else dp
            glob_pat = os.path.basename(dp) if os.path.isfile(dp) else '**/*.jsonl'
            path = write_jsonl_plugin(key, name, root, glob_pat,
                                     estimate=not has_usage)
            return {'status': 'ok', 'agent_key': key, 'plugin_file': path,
                    'data_source': dp, 'estimate': not has_usage,
                    'message': '已接入 %s（jsonl，%s）' % (
                        name, '精确计数' if has_usage else '估算模式')}
        if ext in ('.sqlite', '.db', '.sqlite3'):
            guess = detect_sqlite(dp)
            if guess:
                table, tok, tim = guess
                path = write_sqlite_plugin(key, name, dp, table, tok, tim)
                return {'status': 'ok', 'agent_key': key, 'plugin_file': path,
                        'data_source': dp, 'table': table, 'token_col': tok,
                        'message': '已接入 %s（sqlite，表=%s）' % (name, table)}
            return {'status': 'need_config', 'agent_key': key,
                    'message': 'sqlite 里没找到 token 列',
                    'what_i_need': {
                        'table': '含 token 计数的表名',
                        'token_col': 'token 数量列名',
                        'time_col': '时间列名（可选）'}}
        return {'status': 'need_config', 'agent_key': key,
                'message': '不认识的文件类型 %s' % ext,
                'what_i_need': {'format': 'jsonl 或 sqlite/db'}}

    # 2) 只给了名字：自动探测
    key, roots, files = probe(name, key)
    if not roots:
        roots = _candidate_roots(name, key)
    # 在候选文件里找最好的 jsonl
    for f in files:
        if os.path.splitext(f)[1].lower() in ('.jsonl', '.json'):
            has_usage = detect_jsonl(f)
            root = os.path.dirname(f)
            glob_pat = os.path.basename(f)
            path = write_jsonl_plugin(key, name, root, glob_pat,
                                      estimate=not has_usage)
            return {'status': 'ok', 'agent_key': key, 'plugin_file': path,
                    'data_source': f, 'estimate': not has_usage,
                    'message': '自动发现并接入 %s' % name}
    for f in files:
        if os.path.splitext(f)[1].lower() in ('.sqlite', '.db', '.sqlite3'):
            guess = detect_sqlite(f)
            if guess:
                table, tok, tim = guess
                path = write_sqlite_plugin(key, name, f, table, tok, tim)
                return {'status': 'ok', 'agent_key': key, 'plugin_file': path,
                        'data_source': f, 'table': table, 'token_col': tok,
                        'message': '自动发现并接入 %s（sqlite）' % name}

    # 3) 找不到：返回结构化引导
    return {
        'status': 'need_config',
        'agent_key': key,
        'message': '未自动发现 %s 的用量数据' % name,
        'searched_roots': roots,
        'what_i_need': {
            'data_path': '用量文件或目录的绝对路径（通常是 *.jsonl / *.sqlite / *.db）',
            'format': 'jsonl | sqlite（提供 data_path 后可自动识别）',
        },
        'how_to': ('找到该 agent 存放会话/用量记录的文件后，再次 POST /api/agents '
                   '，body 为 {"name": "%s", "data_path": "<绝对路径>"} 即可；'
                   '若数据在非标准位置，先帮我定位包含 token 用量记录的文件。' % name),
    }


BUILTIN_KEYS = {
    'claude', 'codebuddy', 'codex', 'doubao', 'kimi',
    'qwenworkcn', 'workbuddy', 'zcode',
}


def remove(key):
    """删除一个用户注册的插件文件（内置插件受保护）。"""
    if key in BUILTIN_KEYS:
        return False, '这是内置插件，不允许删除'
    from . import core
    pdir = _plugin_dir()
    if not pdir:
        return False, '找不到插件目录'
    path = os.path.join(pdir, key + '.py')
    if not os.path.exists(path):
        return False, '未找到插件: %s' % key
    os.remove(path)
    return True, '已移除 %s' % key
