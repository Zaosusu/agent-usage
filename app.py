# -*- coding: utf-8 -*-
"""
app.py — Agent Token Monitor 统一入口（开发与打包共用）。

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
    ap = argparse.ArgumentParser(description='Agent Token Monitor')
    ap.add_argument('--port', type=int, default=8765)
    ap.add_argument('--no-open', action='store_true', help='启动后不自动打开浏览器')
    ap.add_argument('--interval', type=float, default=5.0, help='实时监控轮询间隔（秒，最小 1）')
    ap.add_argument('--full', action='store_true', help='启动时全量重扫')
    args = ap.parse_args()

    print('=' * 56)
    print('  Agent Token Monitor')
    print('=' * 56)
    print('数据目录:', core.DATA_DIR)
    print('插件目录:', core.plugin_dirs())

    # 首次/强制扫描
    if not os.path.exists(core.JSON_PATH) or args.full:
        print('扫描中（首次或 --full）……')
        t0 = time.time()
        data, stats = core.scan(full=True)
        print('  完成: %s, %.1fs' % (data['generated_at_str'], time.time() - t0))
    else:
        print('已有数据，启动后由实时监控负责增量更新（--full 可强制全量）')

    srv = serve.create_server(args.port)
    url = f'http://127.0.0.1:{args.port}/'

    watcher = Watcher(interval=args.interval, on_update=serve.publish,
                      scan_lock=serve.scan_lock)
    watcher.start()

    print('本地服务:', url)
    print('实时监控: 每 %ss 检测数据源变化，变化自动推送（可改 --interval）' % args.interval)
    print('按 Ctrl+C 停止。')

    if not args.no_open:
        import webbrowser
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print('\n已停止。')


if __name__ == '__main__':
    main()
