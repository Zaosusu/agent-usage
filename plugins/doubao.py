# -*- coding: utf-8 -*-
"""插件：豆包工作。
数据来源（三层校准，优先级从高到低）：
1. timeline API 百分比（最精确）：Cookie 认证，拉全部记录累加
2. Local Storage 订阅百分比（本地）：从 leveldb 读 usedThisPeriod/monthlyLimit
3. trajectory 文本估算（兜底，按日期分布）：扫 .sessions 目录，按天拆分

校准系数：1% ≈ 50 万 token（通过 timeline + 本地 trajectory 交叉校准）
"""
import os, re, json, time, urllib.request
from engine.common import collect_strings, estimate_tokens

KEY = 'doubao'
NAME = '豆包工作'
ESTIMATE = True
WATCH_PATHS = ['%USERPROFILE%\\AppData\\Local\\DoubaoWork\\User Data\\Default']

TOKENS_PER_PCT = 500_000

_DEFAULT_ROOT = os.path.expanduser(WATCH_PATHS[0].replace('%USERPROFILE%', os.path.expanduser('~')))


def _fetch_timeline(cookie_str):
    if not cookie_str:
        return None, None
    qs = ("version_code=20800&language=zh&device_platform=web"
          "&aid=497858&real_aid=497858&pkg_type=release_version"
          "&device_id=7667391337516697151&pc_version=3.37.5"
          "&doubao_pc_version=3.37.5&web_id=7505737990292227620"
          "&tea_uuid=7505737990292227620&region=CN&sys_region=CN"
          "&samantha_web=1&web_platform=browser&use-olympus-account=1")
    headers = {
        "Cookie": cookie_str,
        "Referer": "https://www.doubao.com/chat",
        "User-Agent": "Mozilla/5.0",
        "agw-js-conv": "str",
        "accept": "application/json",
        "content-type": "application/json",
    }
    all_pct = 0.0
    count = 0
    cursor = None
    try:
        for _ in range(100):
            body = json.dumps({"cursor": cursor} if cursor else {}).encode()
            url = f"https://www.doubao.com/alice/commerce/usage/timeline/?{qs}"
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            r = json.loads(urllib.request.urlopen(req, timeout=10).read())
            d = r.get("data", {})
            entries = d.get("entries", [])
            if not entries:
                break
            for e in entries:
                u = e.get("usage", {})
                pct_str = u.get("quota_source", {}).get("display_text", "0%")
                if "<" in pct_str:
                    all_pct += 0.005
                else:
                    try:
                        all_pct += float(pct_str.replace("%", ""))
                    except ValueError:
                        pass
                count += 1
            cursor = d.get("next_cursor")
            if not d.get("has_more") or not cursor:
                break
    except Exception:
        return None, None
    return all_pct, count


def _extract_cookie():
    config_path = os.path.expanduser("~/.doubao-usage/config.json")
    if not os.path.isfile(config_path):
        return None
    try:
        with open(config_path, 'r') as f:
            config = json.load(f)
        cookie = config.get("doubao_cookie", "")
        return cookie if cookie else None
    except Exception:
        return None


def _scan_quota():
    ls_dir = os.path.join(_DEFAULT_ROOT, 'Local Storage', 'leveldb')
    if not os.path.isdir(ls_dir):
        return None
    text = ''
    for fn in os.listdir(ls_dir):
        if fn.endswith('.ldb') or fn.endswith('.log'):
            try:
                with open(os.path.join(ls_dir, fn), 'rb') as f:
                    text += f.read().decode('utf-8', 'ignore')
            except Exception:
                pass
    m_used = re.search(r'"usedThisPeriod":(\d+)', text)
    m_limit = re.search(r'"monthlyLimit":(\d+)', text)
    used = int(m_used.group(1)) if m_used else 0
    limit = int(m_limit.group(1)) if m_limit else 0
    if limit > 0 and used > 0:
        return (used / limit, used, limit)
    return None


def _scan_indexeddb():
    idb_dir = os.path.join(_DEFAULT_ROOT, 'IndexedDB')
    total_in = 0
    total_out = 0
    if not os.path.isdir(idb_dir):
        return 0, 0
    for sub in os.listdir(idb_dir):
        ldb_dir = os.path.join(idb_dir, sub)
        if not os.path.isdir(ldb_dir):
            continue
        for fn in os.listdir(ldb_dir):
            if not (fn.endswith('.ldb') or fn.endswith('.log')):
                continue
            fp = os.path.join(ldb_dir, fn)
            try:
                with open(fp, 'rb') as f:
                    data = f.read()
            except Exception:
                continue
            for kw, arr in [(b'inputTokensI', 'in'), (b'outputTokensI', 'out')]:
                idx = 0
                while True:
                    idx = data.find(kw, idx)
                    if idx < 0:
                        break
                    pos = idx + len(kw)
                    val = 0
                    shift = 0
                    while pos < len(data):
                        b = data[pos]
                        pos += 1
                        val |= (b & 0x7f) << shift
                        if not (b & 0x80):
                            break
                        shift += 7
                    if 0 < val < 500_000_000:
                        if arr == 'in':
                            total_in += val
                        else:
                            total_out += val
                    idx = pos
    return total_in, total_out


def _scan_trajectory_daily():
    """扫 trajectory.jsonl，按日期分组返回 {date_str: token_estimate}。"""
    sess_root = os.path.join(_DEFAULT_ROOT, '.doubaowork', 'agent_mode',
                             'workspace', '.sessions')
    daily = {}
    if not os.path.isdir(sess_root):
        return daily
    for root, dirs, files in os.walk(sess_root):
        for f in files:
            if f != 'trajectory.jsonl':
                continue
            fp = os.path.join(root, f)
            try:
                mtime = os.path.getmtime(fp)
                day_str = time.strftime('%Y-%m-%d', time.localtime(mtime))
                with open(fp, 'r', encoding='utf-8', errors='replace') as fh:
                    texts = []
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            obj = json.loads(line)
                            collect_strings(obj, texts, 50000)
                        except Exception:
                            texts.append(line)
                    tok = estimate_tokens('\n'.join(texts))
                if tok > 0:
                    daily[day_str] = daily.get(day_str, 0) + tok
            except Exception:
                pass
    return daily


def scan(full, need, mark):
    cookie = _extract_cookie()
    api_pct, api_count = _fetch_timeline(cookie)
    quota = _scan_quota()
    idx_in, idx_out = _scan_indexeddb()
    daily_traj = _scan_trajectory_daily()

    if api_pct is not None and api_pct > 0:
        total_est = int(api_pct * TOKENS_PER_PCT)
        source = 'timeline-api'
    elif quota:
        pct, used, limit = quota
        total_est = int(pct * 100 * TOKENS_PER_PCT)
        source = 'local-quota'
    else:
        total_est = sum(daily_traj.values())
        source = 'trajectory-estimate'

    if total_est == 0 and idx_in + idx_out == 0:
        return []

    # 按 trajectory 日期分布拆分到每天
    if daily_traj and total_est > 0:
        traj_total = sum(daily_traj.values())
        if traj_total > 0:
            sessions = []
            today_str = time.strftime('%Y-%m-%d')
            for day, traj_tok in sorted(daily_traj.items()):
                if day > today_str:
                    continue
                day_tokens = int(total_est * traj_tok / traj_total)
                if day_tokens < 1000:
                    continue
                ts_ms = int(time.mktime(time.strptime(day, '%Y-%m-%d')) * 1000)
                # 今天的记录用当前时间，这样排在最前面
                if day == today_str:
                    ts_ms = int(time.time() * 1000)
                sessions.append({
                    'agent': KEY,
                    'session_id': f'doubao-{day}',
                    'title': f'豆包工作 {day}',
                    'cwd': '',
                    'model': 'doubao-seed',
                    'provider': 'bytedance',
                    'created_at': ts_ms,
                    'last_activity_at': ts_ms,
                    'input_tokens': 0,
                    'output_tokens': 0,
                    'cache_read_tokens': 0,
                    'cache_write_tokens': 0,
                    'total_tokens': day_tokens,
                    'cost': None,
                    'est': 1,
                    'source_file': source,
                })
            return sessions

    return [{
        'agent': KEY,
        'session_id': 'doubao-local',
        'title': f'豆包工作（{source}）',
        'cwd': '',
        'model': 'doubao-seed',
        'provider': 'bytedance',
        'created_at': int(time.time()*1000),
        'last_activity_at': int(time.time()*1000),
        'input_tokens': idx_in,
        'output_tokens': idx_out,
        'cache_read_tokens': 0,
        'cache_write_tokens': 0,
        'total_tokens': max(total_est, idx_in + idx_out),
        'cost': None,
        'est': 1,
        'source_file': source,
    }]
