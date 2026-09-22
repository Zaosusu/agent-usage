# -*- coding: utf-8 -*-
"""插件：豆包工作。

【核心】豆包云端只返回**百分比**，没有绝对 token 字段。所以只需一条固定换算：

    TOKENS_PER_PCT = 500_000        # 1% = 50 万 token

    总量 = timeline 百分比 × 50 万

其余一切都是为论证这个系数服务的，推导与证据链见 docs/DOUBAO.md
（可一键复现：python tools/calibrate_doubao.py）。

数据来源（优先级从高到低）：
1. timeline API 百分比（最精确）：Cookie 认证，拉全部记录累加
2. trajectory 文本估算（兜底，按日期分布）：扫 .sessions 目录，按天拆分

【两条边界，读数前必读】
- **只对账户总量成立**：1% 不是固定 token 数，按任务长度浮动 7.7 倍
  （短对话 21.1 万 / 中等 46.7 万 / 长 agent 161.9 万）。拿 50 万 推算单个任务
  或某一天，会低估长任务 3~8 倍。
- **不能与其他 Agent 横向比**：豆包记的是折后计价量，WorkBuddy 等记原始传输量。

【系数怎么来的（三行版）】
- 消息级对齐：timeline 每条 = 一条 user 消息，本地重建按同一批消息累加。
  双向匹配 正向 83.3% / 反向 94.9% ⇒ 对齐成立。
- 定义式：Σtok(全量) 246,085,702 ÷ Σpct(全量) 499.200% = 49.3 万/1% ⇒ 取整 **50 万**。
- 独立交叉验证：256K 窗口下 max 单次上下文 242,683 + system 2,671 只剩 16,790 余量
  ⇒ 系数上界 58.9 万（能否定 120 万/230 万，定不了真值）。50 万 落在区间内。

【钉死精确值】拿到「一个 7 天窗口 = 多少 token」的绝对数即可一击锁死：
    python tools/calibrate_doubao.py --anchor 1.5e8 --apply
"""
import os, re, json, time, urllib.request
from engine.common import collect_strings, estimate_tokens

KEY = 'doubao'
NAME = '豆包工作'
ESTIMATE = True
WATCH_PATHS = ['%USERPROFILE%\\AppData\\Local\\DoubaoWork\\User Data\\Default']

# 固定系数：1% = 50 万 token（仅对「账户总量」成立，边界见模块 docstring）
TOKENS_PER_PCT = 500_000

_DEFAULT_ROOT = os.path.expanduser(WATCH_PATHS[0].replace('%USERPROFILE%', os.path.expanduser('~')))


_TIMELINE_QS = ("version_code=20800&language=zh&device_platform=web"
                "&aid=497858&real_aid=497858&pkg_type=release_version"
                "&device_id=7667391337516697151&pc_version=3.37.5"
                "&doubao_pc_version=3.37.5&web_id=7505737990292227620"
                "&tea_uuid=7505737990292227620&region=CN&sys_region=CN"
                "&samantha_web=1&web_platform=browser&use-olympus-account=1")
_TIMELINE_URL = f"https://www.doubao.com/alice/commerce/usage/timeline/?{_TIMELINE_QS}"


def _timeline_headers(cookie_str):
    return {
        "Cookie": cookie_str,
        "Referer": "https://www.doubao.com/chat",
        "User-Agent": "Mozilla/5.0",
        "agw-js-conv": "str",
        "accept": "application/json",
        "content-type": "application/json",
    }


def _fetch_timeline(cookie_str):
    """拉 timeline API，返回 (总百分比, 记录数, {日期: 当日百分比})"""
    if not cookie_str:
        return None, None, {}
    headers = _timeline_headers(cookie_str)
    all_pct = 0.0
    count = 0
    daily = {}
    cursor = None
    try:
        for _ in range(200):
            body = json.dumps({"cursor": cursor} if cursor else {}).encode()
            req = urllib.request.Request(_TIMELINE_URL, data=body,
                                         headers=headers, method="POST")
            r = json.loads(urllib.request.urlopen(req, timeout=15).read())
            d = r.get("data", {})
            entries = d.get("entries", [])
            if not entries:
                break
            for e in entries:
                u = e.get("usage", {})
                pct_str = u.get("quota_source", {}).get("display_text", "0%")
                if "<" in pct_str:
                    pct = 0.005
                else:
                    try:
                        pct = float(pct_str.replace("%", ""))
                    except ValueError:
                        pct = 0
                all_pct += pct
                count += 1
                # 提取日期
                ts = u.get("occurred_at_ms", 0) / 1000 if u.get("occurred_at_ms") else 0
                if ts > 0:
                    day = time.strftime('%Y-%m-%d', time.localtime(ts))
                    daily[day] = daily.get(day, 0) + pct
            cursor = d.get("next_cursor")
            if not d.get("has_more") or not cursor:
                break
    except Exception:
        return None, None, {}
    return all_pct, count, daily


def _fetch_timeline_entries(cookie_str):
    """拉 timeline 逐条 [(display_name, pct)]。

    仅供 tools/calibrate_doubao.py 的“算法 F”使用：把每条用量按用户消息文本
    与本地 trajectory 的 turn 一一对上，做 Σtok/Σ% 聚合反推。
    与 _fetch_timeline 共用同一套请求参数，避免逻辑漂移。
    """
    if not cookie_str:
        return []
    headers = _timeline_headers(cookie_str)
    out = []
    cursor = None
    try:
        for _ in range(200):
            body = json.dumps({"cursor": cursor} if cursor else {}).encode()
            req = urllib.request.Request(_TIMELINE_URL, data=body,
                                         headers=headers, method="POST")
            d = json.loads(urllib.request.urlopen(req, timeout=15).read()).get("data", {})
            entries = d.get("entries", [])
            if not entries:
                break
            for e in entries:
                u = e.get("usage", {})
                pct_str = u.get("quota_source", {}).get("display_text", "0%")
                if "<" in pct_str:
                    pct = 0.005
                else:
                    try:
                        pct = float(pct_str.replace("%", ""))
                    except ValueError:
                        pct = 0.0
                out.append((u.get("display_name") or "", pct))
            cursor = d.get("next_cursor")
            if not d.get("has_more") or not cursor:
                break
    except Exception:
        return out
    return out


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


def _wrap_result(sessions, daily_map, source):
    """按引擎新协议返回 {sessions, daily, daily_files}，让豆包进入按天曲线。

    之前只返回 sessions 列表（旧协议），引擎拿不到 daily，导致豆包的每日用量
    只进 sessions（总量 KPI）而按天曲线始终为空——今天/按天视图看不到豆包。
    注意：daily 主键是 (agent, day, source_file)，故 source_file 必须按天唯一，
    否则同一天多行互相覆盖（历史踩过的坑）。
    """
    daily_rows = [{'agent': KEY, 'day': d, 'source_file': f'doubao-{d}',
                   'tokens': int(t), 'est': 1}
                  for d, t in daily_map.items() if t >= 1000]
    # daily_files 带上旧的共享 source，用于清理历史遗留行
    daily_files = [f'doubao-{d}' for d in daily_map] + [source]
    return {'sessions': sessions, 'daily': daily_rows, 'daily_files': daily_files}


def scan(full, need, mark):
    cookie = _extract_cookie()
    api_pct, api_count, api_daily = _fetch_timeline(cookie)
    idx_in, idx_out = _scan_indexeddb()
    daily_traj = _scan_trajectory_daily()

    if api_pct and api_pct > 0 and api_daily:
        total_est = int(api_pct * TOKENS_PER_PCT)
        source = f'timeline-api ({api_count}条, {api_pct:.1f}%)'
        # 用 API 返回的按日期分组的用量
        daily_pct = api_daily
    else:
        # cookie 缺失/失效 ⇒ 直接用 trajectory 文本估算兜底。
        #
        # ⚠️ 不要在这里插入 Local Storage 的 usedThisPeriod/monthlyLimit：
        #    那是**另一个额度池**（plan:premium / billingMode:metered，周期约 4 个月，
        #    单位疑似按次或点数），实测 2.83% vs timeline 499.20% —— 差 176 倍。
        #    乘 TOKENS_PER_PCT 会得出 141.5 万（正确量级 2.50 亿），低估 176 倍。
        #    2026-09-22 移除该分支（曾是 `elif quota:`，会导致降级时用量暴跌）。
        total_est = sum(daily_traj.values())
        source = 'trajectory-estimate'
        daily_pct = None

    if total_est == 0 and idx_in + idx_out == 0:
        return []

    daily_map = {}   # day -> tokens，供 daily 表使用

    # 优先用 API 的按日期数据
    if daily_pct and total_est > 0:
        sessions = []
        today_str = time.strftime('%Y-%m-%d')
        for day, pct in sorted(daily_pct.items()):
            if day > today_str:
                continue
            day_tokens = int(pct * TOKENS_PER_PCT)
            if day_tokens < 1000:
                continue
            daily_map[day] = day_tokens
            ts_ms = int(time.mktime(time.strptime(day, '%Y-%m-%d')) * 1000)
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
        return _wrap_result(sessions, daily_map, source)

    # 兜底：按 trajectory 日期分布拆分
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
                daily_map[day] = day_tokens
                ts_ms = int(time.mktime(time.strptime(day, '%Y-%m-%d')) * 1000)
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
            return _wrap_result(sessions, daily_map, source)

    return _wrap_result([{
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
    }], daily_map, source)
