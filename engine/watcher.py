# -*- coding: utf-8 -*-
"""engine/watcher.py — 实时监视线程。

每 poll_interval 秒对每个插件的 WATCH_PATHS 计算指纹：
  文件    -> (mtime_ns, size)
  目录    -> 粗指纹(dir mtime, 文件数)；每 DEEP_EVERY 轮做深指纹(+最大文件 mtime)
变化时触发一次增量扫描，并通过 on_update(data) 回调广播。
"""
import os
import time
import threading

from . import core


def _fingerprint(path, deep):
    try:
        st = os.stat(path)
    except OSError:
        return ('gone', 0)
    if os.path.isfile(path):
        return ('file', st.st_mtime_ns, st.st_size)
    n = 0
    max_m = 0
    dir_m = st.st_mtime_ns
    try:
        for root, dirs, files in os.walk(path):
            n += len(files)
            if deep:
                for f in files:
                    try:
                        m = os.stat(os.path.join(root, f)).st_mtime_ns
                        if m > max_m:
                            max_m = m
                    except OSError:
                        pass
    except OSError:
        pass
    if deep:
        return ('dir', dir_m, n, max_m)
    return ('dir', dir_m, n)


class Watcher(threading.Thread):
    def __init__(self, interval=5.0, on_update=None, scan_lock=None):
        super().__init__(daemon=True, name='agent-watcher')
        self.interval = max(1.0, float(interval))
        self.on_update = on_update
        self.scan_lock = scan_lock or threading.Lock()
        self._stop = threading.Event()
        self._last = {}          # path -> fingerprint
        self._cycle = 0
        self._plugins = None

    def stop(self):
        self._stop.set()

    def run(self):
        # 首轮先做一次全量扫描（保证数据新鲜），再进入监听循环
        self._scan_once(full=True, first=True)
        while not self._stop.is_set():
            time.sleep(self.interval)
            self._cycle += 1
            deep = (self._cycle % 6 == 0)   # 每 6 轮（约 30s）深查一次
            self._check(deep)

    def _check(self, deep):
        changed = False
        cur = {}
        plugins = core.get_plugins()
        for plug in plugins:
            for w in plug['watch']:
                path = w.replace('%USERPROFILE%', os.path.expanduser('~'))
                try:
                    fp = _fingerprint(path, deep)
                except OSError:
                    continue
                cur[path] = fp
                if self._last.get(path) != fp:
                    changed = True
        if changed:
            self._last = cur
            self._scan_once(full=False, first=False)

    def _scan_once(self, full, first):
        if not self.scan_lock.acquire(blocking=False):
            return
        try:
            if first:
                print('  首次扫描（全量）……')
            data, stats = core.scan(full=full)
            if self.on_update:
                self.on_update(data, stats, source='watch' if not first else 'startup')
        except Exception as e:
            print(f'  [watcher] 扫描失败: {e}')
        finally:
            self.scan_lock.release()
