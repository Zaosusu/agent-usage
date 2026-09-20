# -*- coding: utf-8 -*-
"""
monitor.py — Agent Token Monitor 扫描入口（v2，委托 engine.core）。

用法：
  python monitor.py scan [--agents codex,kimi] [--full]

产物：
  data/usage.db      — 会话级缓存（SQLite）
  data/usage.json    — 聚合数据（仪表盘 API 用）
  web/dashboard.html — 重新烘焙内嵌数据
"""
import argparse

from engine import core


def main():
    ap = argparse.ArgumentParser(description='Agent Token Monitor')
    sub = ap.add_subparsers(dest='cmd')
    p = sub.add_parser('scan')
    p.add_argument('--full', action='store_true', help='全量重扫（忽略文件指纹）')
    p.add_argument('--agents', help='仅扫描指定 agent，逗号分隔')
    p.add_argument('--list-plugins', action='store_true', help='列出已发现的插件')
    args = ap.parse_args()
    if getattr(args, 'list_plugins', False):
        for plug in core.get_plugins():
            print(f"  {plug['key']:12s} {plug['name']:12s} est={int(plug['estimate'])} "
                  f"watch={plug['watch']} file={plug['file']}")
        return
    if args.cmd != 'scan':
        ap.print_help()
        return
    only = set(args.agents.split(',')) if args.agents else None
    data, stats = core.scan(full=args.full, only=only)
    print('scan done at', data['generated_at_str'])
    for k, v in stats.items():
        print(f"  {k:12s} status={v['status']} upserted={v.get('upserted', 0)} "
              f"sec={v.get('seconds', 0)}" + (f" error={v['error']}" if 'error' in v else ''))
    print('agents:', [(a['name'], a['count'], a['total_tokens']) for a in data['agents']])
    print('totals:', data['totals'])
    print('json:', core.JSON_PATH)


if __name__ == '__main__':
    main()
