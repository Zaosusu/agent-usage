# -*- coding: utf-8 -*-
"""
monitor.py — 算力资源管理局扫描入口（v2，委托 engine.core）。

用法：
  python monitor.py scan [--agents codex,kimi] [--full]
  python monitor.py summary          # 输出精简 JSON 摘要（给 AI 用）
  python monitor.py serve            # 启动 Web 看板 + API

产物：
  data/usage.db      — 会话级缓存（SQLite）
  data/usage.json    — 聚合数据（仪表盘 API 用）
  web/dashboard.html — 重新烘焙内嵌数据
"""
import argparse
import json
import os

from engine import core


def cmd_summary(args):
    """输出精简 JSON 摘要，AI 直接用。"""
    if not os.path.exists(core.JSON_PATH):
        print(json.dumps({'ok': False, 'error': '尚未扫描，请先跑 python monitor.py scan'}, ensure_ascii=False))
        return
    with open(core.JSON_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)
    agents = data.get('agents', [])
    summary = {
        'total_tokens': data.get('totals', {}).get('total_tokens', 0),
        'real_tokens': data.get('totals', {}).get('real_tokens', 0),
        'est_tokens': data.get('totals', {}).get('est_tokens', 0),
        'agent_count': len(agents),
        'agents': [
            {
                'key': a.get('key'),
                'name': a.get('name'),
                'total_tokens': a.get('total_tokens', 0),
                'sessions': a.get('count', 0),
                'estimate': a.get('est', 0),
            }
            for a in agents
        ],
    }
    print(json.dumps({'ok': True, 'summary': summary}, ensure_ascii=False, indent=2))


def main():
    ap = argparse.ArgumentParser(description='算力资源管理局')
    sub = ap.add_subparsers(dest='cmd')

    p = sub.add_parser('scan')
    p.add_argument('--full', action='store_true', help='全量重扫（忽略文件指纹）')
    p.add_argument('--agents', help='仅扫描指定 agent，逗号分隔')
    p.add_argument('--list-plugins', action='store_true', help='列出已发现的插件')

    sub.add_parser('summary', help='输出精简 JSON 摘要（给 AI 用）')

    sub.add_parser('serve', help='启动 Web 看板 + API 服务')

    args = ap.parse_args()
    if getattr(args, 'list_plugins', False):
        for plug in core.get_plugins():
            print(f"  {plug['key']:12s} {plug['name']:12s} est={int(plug['estimate'])} "
                  f"watch={plug['watch']} file={plug['file']}")
        return

    if args.cmd == 'summary':
        cmd_summary(args)
        return

    if args.cmd == 'serve':
        from serve import main as serve_main
        serve_main()
        return

    if args.cmd != 'scan':
        ap.print_help()
        return
    only = set(args.agents.split(',')) if args.agents else None
    import time as _time
    _t0 = _time.time()
    data, stats = core.scan(full=args.full, only=only)
    _elapsed = _time.time() - _t0
    # generated_at 是**扫描开始时**生成的，长扫描下与「现在」能差几分钟，
    # 故分别打印，避免把开始时刻误读成完成时刻（排障时踩过）。
    print(f'scan started at {data["generated_at_str"]}  (耗时 {_elapsed:.1f}s)')
    print(f'scan finished at {_time.strftime("%Y-%m-%d %H:%M:%S")}')
    for k, v in stats.items():
        print(f"  {k:12s} status={v['status']} upserted={v.get('upserted', 0)} "
              f"sec={v.get('seconds', 0)}" + (f" error={v['error']}" if 'error' in v else ''))
    print('agents:', [(a['name'], a['count'], a['total_tokens']) for a in data['agents']])
    print('totals:', data['totals'])
    print('json:', core.JSON_PATH)


if __name__ == '__main__':
    main()
