# -*- coding: utf-8 -*-
"""插件：豆包工作。

【核心】豆包云端只返回**百分比**，没有绝对 token 字段。所以只需一条固定换算：

    TOKENS_PER_PCT = 500_000        # 1% = 50 万 token

    总量 = timeline 百分比 × 50 万

其余一切都是为论证这个系数服务的，推导与证据链见 docs/DOUBAO.md
（可一键复现：python tools/calibrate_doubao.py）。

【数据来源】只有一条：timeline API 百分比（Cookie 认证，拉全部记录累加）。
拿不到就**直接抛错**，绝不静默降级 —— 理由见下。

【为什么不做降级】本地任何途径都拿不到与 timeline 同一额度池的数字：
- Local Storage 的 usedThisPeriod/monthlyLimit 是**另一个额度池**
  （plan:premium / billingMode:metered，周期约 4 个月），实测 2.83% vs
  timeline 499.20%，差 176 倍。乘系数得 141.5 万，而正确量级 2.50 亿。
  它曾作为降级分支存在，导致 cookie 失效时用量**静默暴跌 176 倍**；
  2026-09-22 连同分支一起移除，并把降级改为报错。
- trajectory.jsonl 只有 5 个顶层字段（role/content/tool_call_id/tool_calls/
  image_link_list），**没有任何 usage 字段**；按文本估算与真实用量无稳定关系
  （同一个 1% 随任务长度浮动 7.7 倍）。
- IndexedDB / .alaudalog 里的 inputTokens 零散，且无法证明同额度池。
给一个会被误读成"真实用量"的数，比明确失败更糟。

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
import os, json, time, urllib.request

KEY = 'doubao'
NAME = '豆包工作'
ESTIMATE = True
WATCH_PATHS = ['%USERPROFILE%\\AppData\\Local\\DoubaoWork\\User Data\\Default']

# 固定系数：1% = 50 万 token（仅对「账户总量」成立，边界见模块 docstring）
TOKENS_PER_PCT = 500_000

_CONFIG_PATH = os.path.expanduser("~/.doubao-usage/config.json")

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


def _extract_cookie():
    if not os.path.isfile(_CONFIG_PATH):
        return None
    try:
        with open(_CONFIG_PATH, 'r') as f:
            config = json.load(f)
        cookie = config.get("doubao_cookie", "")
        return cookie if cookie else None
    except Exception:
        return None


def _cookie_hint():
    """报错文案：cookie 缺失/失效时告诉用户下一步怎么做。"""
    if not os.path.isfile(_CONFIG_PATH):
        return (f"未配置 cookie：请把浏览器里 doubao.com 的 Cookie 写入 {_CONFIG_PATH}\n"
                f'    格式：{{"doubao_cookie": "你的cookie字符串"}}')
    return (f"cookie 已配置（{_CONFIG_PATH}）但拿不到 timeline 数据，"
            f"多半已过期：请重新登录 doubao.com 复制 Cookie 覆盖该文件")


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
    """只认 timeline API。拿不到就抛错，不降级、不估算。"""
    cookie = _extract_cookie()
    if not cookie:
        raise RuntimeError(f'豆包用量取不到：{_cookie_hint()}')

    api_pct, api_count, api_daily = _fetch_timeline(cookie)
    if not api_pct or api_pct <= 0:
        raise RuntimeError(f'豆包用量取不到：{_cookie_hint()}')

    source = f'timeline-api ({api_count}条, {api_pct:.1f}%)'
    daily_map = {}   # day -> tokens，供 daily 表使用
    sessions = []
    today_str = time.strftime('%Y-%m-%d')
    for day, pct in sorted((api_daily or {}).items()):
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

    if not sessions:
        # 有百分比但没有任何一天 ≥1000 token ⇒ 数据异常，宁可报错也不写脏数据
        raise RuntimeError(f'豆包 timeline 返回 {api_pct:.2f}%（{api_count} 条）'
                           f'但按日拆不出任何用量，数据异常，已跳过')

    return _wrap_result(sessions, daily_map, source)
