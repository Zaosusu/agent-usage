# -*- coding: utf-8 -*-
"""豆包 token 系数校准器（一键复用，不要再手工推）。

背景：豆包 timeline API 只返回「占 7 天额度」的百分比（如 "0.15%"），
**本地与 API 都没有任何“绝对 token 数”字段**，无法做 1:1 硬锚点反推。
所以系数只能靠「本地会话轨迹重建」这类间接算法去夹逼。

本脚本把 2026-09-21 那次人工推导固化为可重复执行的程序：
跑 7 个互相独立的算法，输出有效区间与推荐整数系数。

用法：
    python tools/calibrate_doubao.py                 # 只算，不改代码
    python tools/calibrate_doubao.py --apply         # 算出后直接改写 plugins/doubao.py
    python tools/calibrate_doubao.py --anchor 1.5e8  # 有硬锚点：一个 7 天窗口的绝对 token 数
    python tools/calibrate_doubao.py --root "D:/xxx/DoubaoWork/User Data/Default"

设计约束：
- 全部算法只统计 agent 模式（.doubaowork/agent_mode/workspace/.sessions），
  不含普通对话/图像/其他模型，因此**每一个结果都是下界**。
- 若拿到「一个 7 天窗口 = 多少 token」的绝对数，用 --anchor 一击钉死，其余算法作废。
"""
import os
import re
import sys
import json
import time
import glob
import urllib.request
import argparse
import statistics
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine.common import estimate_tokens          # noqa: E402
from plugins.doubao import _extract_cookie, _fetch_timeline  # noqa: E402


def default_root():
    """豆包工作数据根目录（通用路径，不含个人信息）。"""
    return os.path.expandvars(
        r'%USERPROFILE%\AppData\Local\DoubaoWork\User Data\Default'
    )


def sessions_dir(root):
    return os.path.join(root, '.doubaowork', 'agent_mode', 'workspace', '.sessions')


# ---------------------------------------------------------------- 本地重建

def _msg_tokens(o):
    return estimate_tokens(json.dumps(o, ensure_ascii=False))


def _user_text(o):
    c = o.get('content')
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        parts = []
        for b in c:
            if isinstance(b, dict):
                parts.append(str(b.get('text') or b.get('content') or ''))
            else:
                parts.append(str(b))
        return '\n'.join(parts)
    return str(c or '')


def _norm(s):
    return re.sub(r'\s+', '', s or '')


def rebuild_sessions(root, verbose=True):
    """逐会话重建 agent 循环，返回统计量与逐 turn 明细。

    模型（关键，别再简化）：
      - **一次 assistant 消息 = 一次模型调用**：消耗 = 该次请求的 input + output
        input = 此刻累积上下文（历史消息已含 tool result）+ system prompt + tool schema
        output = 本条 assistant 内容
      - ⚠️ **不要在 tool 消息处再额外加一次“上下文重放”**：工具结果已经进了 ctx，
        下一次 assistant 调用的 input（ctx_before）里本就包含它。额外加一次等于
        把同一份 input 算两遍，会系统性高估约 2 倍（2026-09-21 踩过两次：
        第一次“漏算”→改出 113.6 万；第二次“重复算”→同样偏高）。
      - 因此本函数只产出一个模型调用成本 `call_cost`，system / tool schema 由
        run_algorithms 作为独立分量叠加并做敏感性扫描。
    """
    sdir = sessions_dir(root)
    files = glob.glob(os.path.join(sdir, '**', 'trajectory.jsonl'), recursive=True)
    if not files:
        raise SystemExit('[!] 未找到 trajectory.jsonl，请检查 --root：' + sdir)

    stats = dict(sessions=0, turns=0, assistant=0, model_calls=0, tool_calls=0)
    call_cost = 0          # 模型调用成本：Σ(input=累积上下文 + output=本条)
    turn_items = []        # (day, user_text, turn_token)

    for fp in files:
        try:
            lines = open(fp, encoding='utf-8', errors='replace').read().splitlines()
        except Exception:
            continue
        stats['sessions'] += 1
        ctx = 0
        cur_day = time.strftime('%Y-%m-%d', time.localtime(os.path.getmtime(fp)))
        cur_text = ''
        cur_tok = 0
        for ln in lines:
            ln = ln.strip()
            if not ln:
                continue
            try:
                o = json.loads(ln)
            except Exception:
                continue
            role = o.get('role')
            t = _msg_tokens(o)
            if role == 'user':
                if cur_text:
                    turn_items.append((cur_day, cur_text, cur_tok))
                cur_text = _user_text(o)
                cur_tok = 0
                stats['turns'] += 1
            elif role == 'assistant':
                stats['assistant'] += 1
                stats['model_calls'] += 1
                call_cost += ctx + t
                cur_tok += ctx + t
                ctx += t
            elif role == 'tool':
                # 只进上下文，不另计消耗（它的成本体现在下一次调用的 input 里）
                stats['tool_calls'] += 1
                ctx += t
            else:
                ctx += t
        if cur_text:
            turn_items.append((cur_day, cur_text, cur_tok))

    stats['call_cost'] = call_cost
    stats['turn_items'] = turn_items
    if verbose:
        print(f'[本地] 会话 {stats["sessions"]} / turn {stats["turns"]} / '
              f'assistant {stats["assistant"]} / 工具调用 {stats["tool_calls"]} / '
              f'模型调用 {stats["model_calls"]}')
    return stats


def system_prompt_avg(root):
    """system prompt（assignment.md）平均 token —— 每次模型调用都要重发。"""
    files = glob.glob(os.path.join(sessions_dir(root), '**', 'system', 'assignment.md'),
                      recursive=True)
    if not files:
        return 0, 0
    vals = [estimate_tokens(open(f, encoding='utf-8', errors='replace').read()) for f in files]
    return sum(vals) / len(vals), len(vals)


# ---------------------------------------------------------------- 算法

def run_algorithms(stats, sys_avg, api_pct, api_count, timeline_entries):
    """返回 [(算法名, 1%对应token, 说明)]，全部为 agent 模式下界。"""
    turns = max(stats['turns'], 1)
    calls = max(stats['model_calls'], 1)
    out = []

    base = stats['call_cost']
    out.append(('1 裸模型调用（仅消息体）', base / api_pct, base))

    C = base + sys_avg * calls
    out.append((f'2  +system prompt({sys_avg:,.0f}tok×{calls})', C / api_pct, C))

    for k in (5_000, 10_000, 20_000):
        D = C + k * calls
        out.append((f'3  +tool schema {k//1000}K', D / api_pct, D))

    E = (C / turns) / (api_pct / max(api_count, 1))
    out.append((f'4  单turn均值 {C/turns:,.0f} ÷ 单条均值 {api_pct/max(api_count,1):.3f}%',
                E, C))

    # F：timeline 逐条 ↔ turn 重建匹配（聚合口径 Σtok/Σ%，分布右偏不能看中位数）
    if timeline_entries:
        idx = {}
        for day, txt, tok in stats['turn_items']:
            idx.setdefault(_norm(txt)[:40], []).append(tok)
        sum_tok = 0.0
        sum_pct = 0.0
        hit = 0
        for title, pct in timeline_entries:
            key = _norm(title)[:40]
            if key and key in idx:
                sum_tok += max(idx[key])
                sum_pct += pct
                hit += 1
        if sum_pct > 0 and hit >= max(10, len(timeline_entries) * 0.1):
            out.append((f'F  逐条匹配 {hit}/{len(timeline_entries)}'
                        f'({hit/len(timeline_entries)*100:.0f}%) Σtok/Σ%',
                        sum_tok / sum_pct, sum_tok))
    return out


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description='豆包 token 系数校准器')
    ap.add_argument('--root', default=default_root(), help='豆包工作 Default 目录')
    ap.add_argument('--apply', action='store_true', help='把结果写回 plugins/doubao.py')
    ap.add_argument('--anchor', type=float, default=0,
                    help='硬锚点：一个 7 天窗口(100%%)的绝对 token 数，如 1.5e8')
    ap.add_argument('--round-to', type=int, default=100_000, help='取整粒度，默认 10 万')
    ap.add_argument('--uplift', type=float, default=1.0,
                    help='未建模用量放大系数。重建只覆盖 agent 模式，'
                         '普通对话/图像/premium 倍率/思维链不在内，默认 1.0=纯下界')
    args = ap.parse_args()

    print('=' * 72)
    print('豆包 token 系数校准器')
    print('=' * 72)

    # 1) timeline 百分比（唯一权威分母）
    cookie = _extract_cookie()
    api_pct, api_count, api_daily = _fetch_timeline(cookie)
    if not api_pct:
        raise SystemExit('[!] 拿不到 timeline 百分比：cookie 缺失或失效 '
                         '(~/.doubao-usage/config.json)')
    print(f'[API] timeline 总百分比 = {api_pct:.2f}%  ({api_count} 条, {len(api_daily)} 天)')
    print(f'[本地] root = {args.root}')

    # 2) 硬锚点优先
    if args.anchor > 0:
        exact = args.anchor / 100.0
        print()
        print(f'[硬锚点] 一个 7 天窗口 = {args.anchor:,.0f} token')
        print(f'  ⇒ 1% = {exact:,.0f} token（精确，其余算法作废）')
        if args.apply:
            _apply(exact, reason=f'硬锚点：7天窗口 {args.anchor:,.0f} token')
        return

    # 3) 硬锚点（最高优先级）：IndexedDB 真实 token ↔ timeline 同窗口百分比
    anchor = algo_hard_anchor(args.root, cookie)
    if anchor:
        coef, desc = anchor
        print()
        print('★ 硬锚点（直接测量，优先于一切重建法）：')
        print(f'   {desc}')
        print(f'   ⇒ 1% = {coef:,.0f} token（{coef/1e4:.0f} 万）')
        print(f'   ⇒ 全时段 {api_pct:.2f}% = {api_pct*coef/1e8:.2f} 亿')
        cur = _current_coef()
        if cur:
            print(f'   当前 {cur/1e4:.0f} 万 vs 锚点 {coef/1e4:.0f} 万 '
                  f'(差 {coef/cur:.2f} 倍)')
        if args.apply:
            _apply(int(round(coef / args.round_to) * args.round_to),
                   reason=f'IndexedDB 硬锚点：{desc}')
            return
        print('   （加 --apply 可写入）')
        print()

    # 4) 多算法夹逼（无锚点时的兜底）
    stats = rebuild_sessions(args.root)
    sys_avg, n_sys = system_prompt_avg(args.root)
    print(f'[本地] system prompt 均值 {sys_avg:,.0f} tok（{n_sys} 份）')
    print()

    # timeline 逐条（用于算法 F）
    entries = _timeline_entries(cookie)
    algos = run_algorithms(stats, sys_avg, api_pct, api_count, entries)

    print('%-46s %14s %14s' % ('算法', '总量(token)', '推算 1%'))
    print('-' * 78)
    valid = []
    for name, per_pct, total in algos:
        mark = 'x' if '已作废' in name else ' '
        print('%-46s %14s %14s' % (name, f'{total/1e8:.2f}亿', f'{per_pct/1e4:.1f}万'))
        if mark == ' ':
            valid.append((name, per_pct))
    print('-' * 78)

    vals = sorted(v for _, v in valid)
    lo, hi = vals[0], vals[-1]
    med = statistics.median(vals)
    up = med * args.uplift

    def _r(x):
        return int(round(x / args.round_to) * args.round_to)

    print()
    print(f'下界区间 : {lo/1e4:.1f} ~ {hi/1e4:.1f} 万/1%   中位 {med/1e4:.1f} 万  (uplift={args.uplift})')
    print(f'推荐整数 : {_r(up)/1e4:.0f} 万/1%  (取整粒度 {args.round_to//10000} 万)')
    print()
    print('⚠ 以上**全部是下界**：重建只覆盖 agent 模式（47 个会话目录），')
    print('  普通对话 / 图像 / 其他模型 / premium 倍率 / 思维链 token 都不在内。')
    print('  真实系数 ≥ 推荐值。想留未建模余量用 --uplift 1.5~2.0。')
    print('  要钉死：--anchor <一个7天窗口的绝对token数>。')

    cur = _current_coef()
    if cur:
        print()
        print(f'当前代码 TOKENS_PER_PCT = {cur:,}（{cur/1e4:.0f} 万/1%），'
              f'= 本次下界中位 × {cur/med:.2f}')
        print(f'  换算：timeline {api_pct:.2f}% → {api_pct*cur/1e8:.2f} 亿')

    if args.apply:
        reason = (f'{len(valid)} 算法下界区间 {lo/1e4:.1f}~{hi/1e4:.1f} 万，'
                  f'中位 {med/1e4:.1f} 万 × uplift {args.uplift}（agent 模式重建）')
        _apply(_r(up), reason=reason)


def scan_indexeddb(root):
    """扫 IndexedDB，返回 [(ts_ms, input_tok, output_tok)]。

    这里存的是**真实逐次 API 调用的 input/output token**（随上下文单调递增），
    是豆包本地唯一能拿到的真实计费量。时间戳是 ISO 8601 字符串，不是 varint。
    注：记录可能来自陪伴/角色扮演类会话（如 conv_mori），但只要 quota_source_code
    与 timeline 相同（doubao_personal_vip_quota），就属同一额度池，可直接做锚点。
    """
    recs = []
    idb = os.path.join(root, 'IndexedDB')
    if not os.path.isdir(idb):
        return recs
    for sub in os.listdir(idb):
        d = os.path.join(idb, sub)
        if not os.path.isdir(d):
            continue
        for fn in os.listdir(d):
            if not (fn.endswith('.ldb') or fn.endswith('.log')):
                continue
            try:
                data = open(os.path.join(d, fn), 'rb').read()
            except Exception:
                continue
            idx = 0
            while True:
                idx = data.find(b'inputTokens', idx)
                if idx < 0:
                    break
                p = idx + len(b'inputTokens')
                if p < len(data) and data[p:p + 1] == b'I':
                    p += 1
                in_tok, p2 = _varint(data, p)
                m = re.search(rb'outputTokensI?', data[p2:p2 + 300])
                out_tok = 0
                if m:
                    out_tok, _ = _varint(data, p2 + m.end())
                recs.append((_iso_near(data, idx), in_tok, out_tok))
                idx = p2
    return [r for r in recs if r[0] > 0]


def _varint(data, pos):
    val = 0
    shift = 0
    while pos < len(data):
        b = data[pos]
        pos += 1
        val |= (b & 0x7F) << shift
        if not (b & 0x80):
            break
        shift += 7
    return val, pos


def _iso_near(data, idx, span=4000):
    """在 idx 附近找最近的 ISO 时间串，返回 epoch ms。"""
    win = data[max(0, idx - span): idx + span].decode('latin1')
    best = None
    for m in re.finditer(r'(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})', win):
        y, mo, d, h, mi, s = (int(x) for x in m.groups())
        ts = datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc).timestamp() * 1000
        if best is None or abs(m.start() - span) < best[0]:
            best = (abs(m.start() - span), ts)
    return best[1] if best else 0


def timeline_with_ts(cookie):
    """拉 timeline 逐条 [(ts_ms, pct, name, quota_code)]。"""
    from plugins.doubao import _TIMELINE_URL, _timeline_headers
    out = []
    cursor = None
    try:
        for _ in range(200):
            body = json.dumps({"cursor": cursor} if cursor else {}).encode()
            req = urllib.request.Request(_TIMELINE_URL, data=body,
                                         headers=_timeline_headers(cookie), method="POST")
            d = json.loads(urllib.request.urlopen(req, timeout=15).read()).get("data", {})
            es = d.get("entries", [])
            if not es:
                break
            for e in es:
                u = e.get("usage", {})
                ps = u.get("quota_source", {}).get("display_text", "0%")
                p = 0.005 if "<" in ps else float(ps.replace("%", "") or 0)
                out.append((u.get("occurred_at_ms", 0), p,
                            u.get("display_name") or "",
                            (u.get("quota_source") or {}).get("quota_source_code", "")))
            cursor = d.get("next_cursor")
            if not d.get("has_more") or not cursor:
                break
    except Exception:
        return out
    return [x for x in out if x[0] > 0]


def algo_hard_anchor(root, cookie):
    """算法 G（硬锚点）：真实 token ↔ 同时间窗 timeline 百分比。

    这是唯一能直接测量的算法，优先级高于所有重建法。
    返回 (系数, 描述) 或 None。
    """
    recs = scan_indexeddb(root)
    if not recs:
        return None
    tl = timeline_with_ts(cookie)
    if not tl:
        return None
    ts = sorted(r[0] for r in recs)
    total = sum(r[1] + r[2] for r in recs)
    lo, hi = ts[0] - 5 * 60_000, ts[-1] + 5 * 60_000
    win = [x for x in tl if lo <= x[0] <= hi]
    win_pct = sum(x[1] for x in win)
    if win_pct <= 0:
        return None
    coef = total / win_pct
    t0 = datetime.fromtimestamp(ts[0] / 1000)
    t1 = datetime.fromtimestamp(ts[-1] / 1000)
    desc = (f'{len(recs)} 次真实调用 Σ={total:,} tok '
            f'({t0:%m-%d %H:%M}~{t1:%H:%M}) ↔ timeline '
            f'{len(win)} 条 Σ={win_pct:.3f}% [同额度池]')
    return coef, desc


def _current_coef():
    m = re.search(r'^TOKENS_PER_PCT = ([\d_]+)',
                  open(os.path.join(ROOT, 'plugins', 'doubao.py'), encoding='utf-8').read(),
                  re.M)
    return int(m.group(1).replace('_', '')) if m else None


def _timeline_entries(cookie):
    """拉 timeline 逐条 (title, pct)，供算法 F 匹配。失败返回 []。"""
    if not cookie:
        return []
    from plugins.doubao import _fetch_timeline_entries
    return _fetch_timeline_entries(cookie)


def _apply(value, reason):
    path = os.path.join(ROOT, 'plugins', 'doubao.py')
    txt = open(path, encoding='utf-8').read()
    # 注意：必须匹配 [\d_]+，正则写 \d+ 只会吃掉 "1_200_000" 里的 "1"，
    # 替换后变成 "2300000_200_000"（已踩过）。
    pretty = f'{int(value):_}'
    new = re.sub(r'^TOKENS_PER_PCT = [\d_]+', f'TOKENS_PER_PCT = {pretty}',
                 txt, count=1, flags=re.M)
    if new == txt:
        print(f'[apply] 未匹配到 TOKENS_PER_PCT 赋值行，请手工改 {path}')
        return
    open(path, 'w', encoding='utf-8').write(new)
    print(f'[apply] 已写入 plugins/doubao.py : TOKENS_PER_PCT = {int(value)}')
    print(f'        依据：{reason}')
    print('[!] 记得重启 serve（不重启 watcher 会用旧系数写回），并跑：')
    print('    python monitor.py scan --agents doubao --full')


if __name__ == '__main__':
    main()
