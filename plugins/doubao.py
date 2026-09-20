# -*- coding: utf-8 -*-
"""插件：豆包工作。
数据来源（三层校准，优先级从高到低）：
1. timeline API 百分比（最精确）：Cookie 认证，拉全部记录累加
2. Local Storage 订阅百分比（本地）：从 leveldb 读 usedThisPeriod/monthlyLimit
3. IndexedDB 精确 token（当前会话）：扫 inputTokensI/outputTokensI 字段
4. trajectory 文本估算（兜底）：扫 .sessions 目录文本量

校准系数：1% ≈ 50 万 token（通过 timeline + 本地 trajectory 交叉校准）
"""
import os, re, json, urllib.request
from engine.common import collect_strings, estimate_tokens

KEY = 'doubao'
NAME = '豆包工作'
ESTIMATE = True
WATCH_PATHS = ['%USERPROFILE%\\AppData\\Local\\DoubaoWork\\User Data\\Default']

# 校准系数：1% ≈ 50 万 token
# 校准依据：12 天 1171 条 = 419% ≈ 2 亿 token
TOKENS_PER_PCT = 500_000

_DEFAULT_ROOT = os.path.expanduser(WATCH_PATHS[0].replace('%USERPROFILE%', os.path.expanduser('~')))


# ========== 数据源 1：timeline API（最精确） ==========

def _fetch_timeline(cookie_str):
    """调 timeline API 拉全部用量记录。
    返回 (total_pct, record_count) 或 (None, None)。
    失败自动降级，不抛异常。
    """
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
        for _ in range(100):  # 最多拉 100 页
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
                    all_pct += 0.005  # <0.01% 取中位数
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
    """从配置文件读取用户手动提供的 cookie（可选）。
    如果用户知道怎么从 DevTools 复制 cookie，就存到配置文件里，拿到精确的 API 数据。
    不知道就不用管，自动降级到本地估算。

    配置文件路径: ~/.doubao-usage/config.json
    格式: {"doubao_cookie": "sessionid=xxx; passport_csrf_token=xxx; ..."}
    """
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


# ========== 数据源 2：Local Storage 订阅百分比 ==========

def _scan_quota():
    """从 Local Storage leveldb 读订阅用量。
    返回 (pct, used, limit) 或 None。
    """
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


# ========== 数据源 3：IndexedDB 精确 token ==========

def _scan_indexeddb():
    """扫 IndexedDB 里的 inputTokens/outputTokens 字段。
    返回 (total_in, total_out)，只覆盖当前会话。
    """
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


# ========== 数据源 4：trajectory 文本估算（兜底） ==========

def _scan_trajectory():
    """扫所有 session 的 trajectory.jsonl，估算文本 token 量。
    这是最兜底的方案，数据最不精确。
    """
    sess_root = os.path.join(_DEFAULT_ROOT, '.doubaowork', 'agent_mode',
                             'workspace', '.sessions')
    total_tokens = 0
    if not os.path.isdir(sess_root):
        return 0
    for root, dirs, files in os.walk(sess_root):
        for f in files:
            if f != 'trajectory.jsonl':
                continue
            fp = os.path.join(root, f)
            try:
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
                    total_tokens += estimate_tokens('\n'.join(texts))
            except Exception:
                pass
    return total_tokens


# ========== 主入口 ==========

def scan(full, need, mark):
    # 1. 先试 API（最精确）
    cookie = _extract_cookie()
    api_pct, api_count = _fetch_timeline(cookie)

    # 2. 再试 Local Storage 百分比
    quota = _scan_quota()

    # 3. IndexedDB 精确 token（参考值）
    idx_in, idx_out = _scan_indexeddb()

    # 4. trajectory 文本估算（兜底）
    traj_tokens = _scan_trajectory()

    # 选择最优数据源
    if api_pct is not None and api_pct > 0:
        # API 数据最精确
        est_tokens = int(api_pct * TOKENS_PER_PCT)
        title = f'豆包工作（API {api_count} 条 = {api_pct:.1f}%）'
        source = 'timeline-api'
    elif quota:
        # 本地百分比
        pct, used, limit = quota
        est_tokens = int(pct * 100 * TOKENS_PER_PCT)
        title = f'豆包工作（本地 {used}/{limit} 次 = {pct*100:.1f}%）'
        source = 'local-quota'
    else:
        # 纯文本估算
        est_tokens = traj_tokens
        title = '豆包工作（本地文本估算）'
        source = 'trajectory-estimate'

    # IndexedDB 精确值作为参考，但总量用百分比校准值
    total_tokens = max(est_tokens, idx_in + idx_out)

    if total_tokens == 0:
        return []

    return [{
        'agent': KEY,
        'session_id': 'doubao-local',
        'title': title,
        'cwd': '',
        'model': 'doubao-seed',
        'provider': 'bytedance',
        'created_at': int(time.time()*1000),
        'last_activity_at': int(time.time()*1000),
        'input_tokens': idx_in,
        'output_tokens': idx_out,
        'cache_read_tokens': 0,
        'cache_write_tokens': 0,
        'total_tokens': total_tokens,
        'cost': None,
        'est': 1,
        'source_file': source,
    }]
