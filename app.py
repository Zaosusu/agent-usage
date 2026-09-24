# -*- coding: utf-8 -*-
"""
app.py — 算力资源管理（算力资源管理局）统一入口（开发与打包共用）。
双击 / 运行本程序后：
  1. 若无数据则先全量扫描一次；
  2. 启动本地服务（默认 http://127.0.0.1:8765/）；
  3. 启动实时监控线程（检测各 agent 数据源变化，自动增量扫描并通过 SSE 推送）；
  4. 自动打开默认浏览器。
用法：
  python app.py [--port 8765] [--no-open] [--interval 5]
"""
import os
import sys
import time
import threading
import argparse

from engine import core
from engine.watcher import Watcher
import serve


def main():
    ap = argparse.ArgumentParser(description='算力资源管理 · 算力 HR · Token 劳务派遣')
    ap.add_argument('--port', type=int, default=8765)
    ap.add_argument('--no-open', action='store_true')
    ap.add_argument('--interval', type=float, default=5.0)
    ap.add_argument('--full', action='store_true')
    args = ap.parse_args()

    print('=' * 56)
    print('  算力资源管理局')
    print('  算力 HR · Token 劳务派遣')
    print('=' * 56)
    print('数据目录:', core.DATA_DIR)
    print('插件目录:', core.plugin_dirs())

    if not os.path.exists(core.JSON_PATH) or args.full:
        print('扫描中（首次 / --full）...')
        t0 = time.time()
        data, stats = core.scan(full=True)
        print('  完成: %s, %.1fs' % (data['generated_at_str'], time.time() - t0))
    else:
        print('已有数据，启动后由实时监控负责增量更新')

    srv = serve.create_server(args.port)
    url = f'http://127.0.0.1:{args.port}/'

    watcher = Watcher(interval=args.interval, on_update=serve.publish,
                      scan_lock=serve.scan_lock)
    watcher.start()

    print('本地服务:', url)
    print('实时监控: 每 %ss 检测数据源变化，变化自动推送' % args.interval)
    print('按 Ctrl+C 停止')

    if not args.no_open:
        import webbrowser
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print('\n已停止')


if __name__ == '__main__':
    main()
