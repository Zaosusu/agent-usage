# -*- coding: utf-8 -*-
"""engine/common.py — 插件公共工具（各插件 import 用）。"""
import os
import json
import glob
import sqlite3
import datetime


def expand(path):
    """展开 %USERPROFILE% / %USERNAME% / ~ 等占位符。"""
    if not path:
        return path
    p = path.replace('%USERPROFILE%', os.path.expanduser('~'))
    p = p.replace('%USERNAME%', os.environ.get('USERNAME', ''))
    return os.path.expanduser(p)


def glob_files(root, pattern):
    """递归 glob，返回排序后的绝对路径列表。"""
    if not os.path.isdir(root):
        return []
    return sorted(glob.glob(os.path.join(root, pattern), recursive=True))


def ro_connect(path):
    """只读打开 sqlite。"""
    try:
        return sqlite3.connect('file:' + path.replace('\\', '/') + '?mode=ro', uri=True)
    except sqlite3.Error:
        return None


def iso_to_ms(ts):
    """ISO8601 -> ms epoch；失败返回 None。"""
    if not ts:
        return None
    try:
        s = str(ts).replace('Z', '+00:00')
        dt = datetime.datetime.fromisoformat(s)
        return int(dt.timestamp() * 1000)
    except Exception:
        return None


def ts_to_ms(v):
    """兼容 ISO 字符串 / 秒 / 毫秒三种时间戳。"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        if v > 1e14:
            return int(v / 1e6)
        if v > 1e12:
            return int(v)
        return int(v * 1000)
    return iso_to_ms(v)


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


def first_text(obj, maxlen=60):
    """递归找第一个较长的字符串作为标题。"""
    if isinstance(obj, dict):
        for v in obj.values():
            t = first_text(v, maxlen)
            if t:
                return t
    elif isinstance(obj, list):
        for v in obj:
            t = first_text(v, maxlen)
            if t:
                return t
    elif isinstance(obj, str):
        s = obj.strip()
        if len(s) >= 2:
            return s[:maxlen]
    return ''


def walk_json_for_usage(obj, acc):
    """递归收集含 input_tokens/output_tokens 的 usage 字典。"""
    if isinstance(obj, dict):
        if 'input_tokens' in obj and 'output_tokens' in obj:
            acc.append(obj)
        for v in obj.values():
            walk_json_for_usage(v, acc)
    elif isinstance(obj, list):
        for v in obj:
            walk_json_for_usage(v, acc)


def extract_usage(u):
    """从 usage 字典提取 (input, output, cache_read, cache_write)，兼容多种字段命名。"""
    if not isinstance(u, dict):
        return (0, 0, 0, 0)
    inp = u.get('input_tokens') or u.get('prompt_tokens') or 0
    out_t = u.get('output_tokens') or u.get('completion_tokens') or 0
    cache_r = u.get('cache_read_input_tokens') or u.get('cache_read_tokens') or u.get('cached_tokens') or 0
    cache_w = u.get('cache_creation_input_tokens') or u.get('cache_creation_tokens') or 0
    return (inp, out_t, cache_r, cache_w)


def collect_strings(obj, out, cap=200000):
    """递归收集所有字符串（估算用）。"""
    if len(out) >= 5000:
        return
    if isinstance(obj, dict):
        for v in obj.values():
            collect_strings(v, out, cap)
    elif isinstance(obj, list):
        for v in obj:
            collect_strings(v, out, cap)
    elif isinstance(obj, str):
        if obj.strip():
            out.append(obj[:cap])


def scan_jsonl_dir(root, pattern, parse_fn, full, need, mark):
    """通用 JSONL 目录扫描：glob 匹配 -> 指纹增量 -> parse_fn(path) -> 会话或 None。"""
    files = glob_files(root, pattern)
    out = []
    for p in files:
        try:
            st = os.stat(p)
        except OSError:
            continue
        fp = f'{st.st_mtime_ns}:{st.st_size}'
        if not need(full, p, fp):
            continue
        sess = parse_fn(p)
        if sess is not None:
            out.append(sess)
        mark(p, fp)
    return out


def parse_claude_like_file(agent, path, root=None):
    """Claude CLI / CodeBuddy 类 JSONL 会话解析。root 用于生成相对 session_id。"""
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
            ts = ts_to_ms(obj.get('timestamp'))
            if ts:
                if first_ts is None:
                    first_ts = ts
                last_ts = ts
            msg = obj.get('message')
            msg = msg if isinstance(msg, dict) else {}
            role = msg.get('role') or obj.get('role')
            if role == 'user' and first_user and not title:
                t = first_text(msg.get('content') or obj.get('content'), 60)
                if t:
                    title = t
                    first_user = False
            u = msg.get('usage')
            if not isinstance(u, dict):
                found = []
                walk_json_for_usage(obj, found)
                if found:
                    u = found[0]
            if isinstance(u, dict):
                i, o, cr, cw = extract_usage(u)
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
                t = first_text(obj.get('message') or obj.get('content'), 60)
                if t:
                    title = t
                    first_user = False
    total = inp + out_t + cache_r + cache_w
    if total == 0 and first_ts is None:
        return None
    return {
        'agent': agent, 'session_id': sid, 'title': title or '未命名会话',
        'cwd': cwd, 'model': model, 'provider': '',
        'created_at': first_ts or 0, 'last_activity_at': last_ts or 0,
        'input_tokens': inp, 'output_tokens': out_t,
        'cache_read_tokens': cache_r, 'cache_write_tokens': cache_w,
        'total_tokens': total, 'cost': None, 'est': 0, 'source_file': path,
    }


def parse_estimate_file(agent, path, root):
    """估算型会话解析（豆包/千问等无 usage 字段的数据源）。"""
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
            ts = iso_to_ms(obj.get('timestamp'))
            if ts:
                if first_ts is None:
                    first_ts = ts
                last_ts = ts
            if not title:
                title = first_text(obj, 60)
            texts = []
            collect_strings(obj, texts, 200000)
            tokens += estimate_tokens('\n'.join(texts))
    if tokens == 0 and first_ts is None:
        return None
    rel = os.path.relpath(path, root)
    sid = rel.replace(os.sep, '/')
    return {
        'agent': agent, 'session_id': sid, 'title': title or '会话 ' + sid[:20],
        'cwd': '', 'model': '', 'provider': '',
        'created_at': first_ts or int(st.st_mtime * 1000),
        'last_activity_at': last_ts or int(st.st_mtime * 1000),
        'input_tokens': 0, 'output_tokens': 0,
        'cache_read_tokens': 0, 'cache_write_tokens': 0,
        'total_tokens': tokens, 'cost': None, 'est': 1, 'source_file': path,
    }

