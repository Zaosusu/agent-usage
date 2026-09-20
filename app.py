# -*- coding: utf-8 -*-
"""
app.py 鈥?Agent Token Monitor 缁熶竴鍏ュ彛锛堝紑鍙戜笌鎵撳寘鍏辩敤锛夈€?
鍙屽嚮 / 杩愯鏈▼搴忓悗锛?  1. 鑻ユ棤鏁版嵁鍒欏厛鍏ㄩ噺鎵弿涓€娆★紱
  2. 鍚姩鏈湴鏈嶅姟锛堥粯璁?http://127.0.0.1:8765/锛夛紱
  3. 鍚姩瀹炴椂鐩戞帶绾跨▼锛堟娴嬪悇 agent 鏁版嵁婧愬彉鍖栵紝鑷姩澧為噺鎵弿骞堕€氳繃 SSE 鎺ㄩ€侊級锛?  4. 鑷姩鎵撳紑榛樿娴忚鍣ㄣ€?
鐢ㄦ硶锛?  python app.py [--port 8765] [--no-open] [--interval 5]
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
    ap.add_argument('--no-open', action='store_true', help='鍚姩鍚庝笉鑷姩鎵撳紑娴忚鍣?)
    ap.add_argument('--interval', type=float, default=5.0, help='瀹炴椂鐩戞帶杞闂撮殧锛堢锛屾渶灏?1锛?)
    ap.add_argument('--full', action='store_true', help='鍚姩鏃跺叏閲忛噸鎵?)
    args = ap.parse_args()

    print('=' * 56)
    print('  Agent Token Monitor')
    print('=' * 56)
    print('鏁版嵁鐩綍:', core.DATA_DIR)
    print('鎻掍欢鐩綍:', core.plugin_dirs())

    # 棣栨/寮哄埗鎵弿
    if not os.path.exists(core.JSON_PATH) or args.full:
        print('鎵弿涓紙棣栨鎴?--full锛夆€︹€?)
        t0 = time.time()
        data, stats = core.scan(full=True)
        print('  瀹屾垚: %s, %.1fs' % (data['generated_at_str'], time.time() - t0))
    else:
        print('宸叉湁鏁版嵁锛屽惎鍔ㄥ悗鐢卞疄鏃剁洃鎺ц礋璐ｅ閲忔洿鏂帮紙--full 鍙己鍒跺叏閲忥級')

    srv = serve.create_server(args.port)
    url = f'http://127.0.0.1:{args.port}/'

    watcher = Watcher(interval=args.interval, on_update=serve.publish,
                      scan_lock=serve.scan_lock)
    watcher.start()

    print('鏈湴鏈嶅姟:', url)
    print('瀹炴椂鐩戞帶: 姣?%ss 妫€娴嬫暟鎹簮鍙樺寲锛屽彉鍖栬嚜鍔ㄦ帹閫侊紙鍙敼 --interval锛? % args.interval)
    print('鎸?Ctrl+C 鍋滄銆?)

    if not args.no_open:
        import webbrowser
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print('\n宸插仠姝€?)


if __name__ == '__main__':
    main()




