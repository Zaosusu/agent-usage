# -*- coding: utf-8 -*-
"""插件：豆包工作。
数据来源（三层校准，优先级从高到低）：
1. timeline API 百分比（最精确）：Cookie 认证，拉全部记录累加
2. Local Storage 订阅百分比（本地）：从 leveldb 读 usedThisPeriod/monthlyLimit
3. trajectory 文本估算（兜底，按日期分布）：扫 .sessions 目录，按天拆分

校准系数：TOKENS_PER_PCT = 500_000（1% ≈ 50 万 token），来自「消息级对齐」直接测量。
推导全过程见 README「系数校准：1% 等于多少 token」一节，可一键复现：
    python tools/calibrate_doubao.py

【为什么是「暂定」而非「精确」】
- 豆包的 timeline API 只返回百分比（quota_source.display_text，如 "0.15%"），
  配额接口只返回 used_percent（占 7 天额度），**本地与 API 均无任何「绝对 token 数」字段**。
  所以无法像 WorkBuddy 那样做 1:1 硬锚点反推，只能用「本地重建 ÷ 同批 timeline 百分比」夹逼。
- 唯一可靠的测法是**消息级对齐**：timeline 每条 = 一条 user 消息，本地重建按同一批消息
  累加 token。分子分母来自**同一批消息**，对齐成立性可用双向匹配率验证。

  ⚠️ **但注意：「同批消息」≠「同口径消耗」**（2026-09-22 更正，此处曾写错）：
  对齐只保证消息集合一致，不保证消耗量一致。凡本地看不到的消耗
  （tool schema 每次重发、推理 token、非 agent 用量）都**只进分母不进分子**
  ⇒ 算出的系数**系统性偏低**。
  ⇒ 上界由窗口约束给出：模型窗口 = **256K**（doubao-seed-2-1-turbo / -pro 均为 256K；
  本地配置出现的模型只有这两个系列，无 1M 窗口模型）。实测 max 单次上下文 242,683，
  加 system prompt 后距 256K 只剩 **16,790 tok** —— 这是全部「看不见的消耗」每次的总预算
  ⇒ **系数上界 58.9 万**（它能否定 120 万 / 230 万，但定不了真值）。
  真值取**定义式** Σtok(全量) / Σpct(全量)。详见 README「最大的适用边界」一节。

【测量结果（2026-09-21 复核；47 会话 / 1504 turn / 2856 次模型调用）】
- 双向匹配验证：timeline 1590 条中 **90%** 能对上本地 agent 消息（占 Σpct 82.8%）；
  反向本地 1470 条消息中 **95.2%** 能在 timeline 找到 ⇒ 匹配是真的，不是巧合。
- agent 内容密度（直接测得）：Σtok 239,859,254 ÷ Σpct 413.165% = **58.1 万/1%**
- 账户下限（假设非 agent 内容零消耗）：Σtok 240,694,651 ÷ Σpct 499.200% = **48.2 万/1%**
- 未匹配的约 17% pct 多为普通对话模式（比 agent 循环轻），真值落在 48~58 万之间
  ⇒ **取整采用 50 万**。

【已作废的系数与原因（改之前务必先读）】
- 500 万：commit 90c788c 把 50 万拍成 500 万（diff 一行、无依据），虚高 10 倍。
- 230 万：commit 463818f 拿 IndexedDB 里 65 条陪伴会话（conv_mori）记录当「硬锚点」。
  **该推导不成立**：① 窗口从 ±0 放宽到 ±60min，系数从 256 万漂到 6 万（40 倍），
  说明分子分母根本不是同一批事件；② 该库全量字节里 `quota_source_code` 出现 **0 次**，
  「同额度池」无法证明；③ 那 65 条时间集中在 01:46~01:59，是 memory 压缩批量落盘，
  不是真实调用时刻。
- 47.8 万：只算消息体，漏掉上下文重发。
- 113.6 万：在 tool 消息处**多加一次「上下文重放」**，同一份 input 算两遍，高估约 2 倍。
  正确模型：一次 assistant 消息 = 一次模型调用，消耗 = 累积上下文 + 本条输出。
- 120 万：继承 113.6 万的错误区间（113.6~139.0 万），一并作废。

【如何进一步钉死】
拿到「一个 7 天窗口 = 多少 token」的绝对数即可一击锁死：
    python tools/calibrate_doubao.py --anchor 1.5e8 --apply
数字来源：豆包用量页「占 7 天额度」旁的绝对已用/总额，或订阅计划的单窗口 token 配额。
"""
import os, re, json, time, urllib.request
from engine.common import collect_strings, estimate_tokens

KEY = 'doubao'
NAME = '豆包工作'
ESTIMATE = True
WATCH_PATHS = ['%USERPROFILE%\\AppData\\Local\\DoubaoWork\\User Data\\Default']

# 系数：1% = 50 万 token —— **仅对「账户总量」成立**（2026-09-22 定稿）。
#
# 【定义式】系数 = Σtok(全量本地重建) / Σpct(timeline 全时段)
#             = 246,085,702 / 499.200% = 49.3 万/1%  ⇒ 取整 50 万
#   这个口径保证「pct × 系数 = 该账户全时段的原始 token 总量」是对的。
#
# 【⚠️ 最大的适用边界：1% ≠ 固定 token 数】分型实测（见 calibrate 脚本 ①·补）：
#     短对话   (<3 次调用)   227.2%  →  0.478 亿  ⇒   21.1 万/1%
#     中等     (3~10 次)      72.5%  →  0.338 亿  ⇒   46.7 万/1%
#     长 agent (≥10 次)       99.9%  →  1.617 亿  ⇒  161.9 万/1%
#   同为 1%，长任务对应的原始 token 是短对话的 **7.7 倍**。原因：agent 长循环里绝大部分
#   input 是**重复的累积上下文**，云端按**缓存折扣**计费 ⇒ 同样 1% 覆盖多得多的原始 token。
#   所以**不能拿 50 万去推算单个任务/单天的 token 量**（会低估长任务 3~8 倍）。
#   用户 2026-09-22 的直觉（「一天用 50% 额度怎么可能才 2500 万」）正是命中了这一点：
#   若那天以长 agent 任务为主，70% ≈ **1.1 亿**原始 token，而不是 2500 万。
#
# 【测量要点：2026-09-22 修掉的两个真 bug（会显著影响结果）】
#   1) **连续 user 消息算一个工作单元**：用户会连发短消息、模型只回一次，而 timeline 给
#      每条都记了 pct。旧实现把整轮消耗全算给最后一条 ⇒ 前面的 key 有 pct 无 token
#      ⇒ 系数被系统性拽低 2~4 倍。现在单元内均摊，Σ(单元各 key) = 单元总消耗。
#   2) user 消息本身也是 input，必须计入累积上下文（旧实现漏了）。
#
# 【验证】匹配率：正向 1325/1590 = 83.3%（占 Σpct 80.0%）、反向 1296/1366 = 94.9%。
#   但双对数相关系数 **r 仅 0.185** ⇒ % 与原始 token **非严格正比**（正是缓存折扣所致）。
#   所以「匹配率高」只说明事件能对上，**不等于可以乘系数推单个任务的量**。
#   窗口检验：256K 窗口下系数上界 58.9 万（max 单次上下文 242,683 + system 2,671 = 245,354，
#   距窗口仅剩 16,790）；故 120 万 / 230 万 物理不可能（单次均值会到窗口 80% / 153%）。
#   ⚠️ 上界依赖「2856 次调用 = 账户全部消耗」这一前提；若有未落盘的调用，上界相应放宽。
#
# 已作废：500 万（90c788c 十倍误改）、230 万（463818f 窗口漂 40 倍 + 编造同额度池）、
#         47.8 万（漏算上下文）、113.6 万（重复计算）、120 万（继承 113.6 万错误区间）。
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
    quota = _scan_quota()
    idx_in, idx_out = _scan_indexeddb()
    daily_traj = _scan_trajectory_daily()

    if api_pct and api_pct > 0 and api_daily:
        total_est = int(api_pct * TOKENS_PER_PCT)
        source = f'timeline-api ({api_count}条, {api_pct:.1f}%)'
        # 用 API 返回的按日期分组的用量
        daily_pct = api_daily
    elif quota:
        pct, used, limit = quota
        total_est = int(pct * 100 * TOKENS_PER_PCT)
        source = 'local-quota'
        daily_pct = None
    else:
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
