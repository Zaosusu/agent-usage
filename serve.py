# -*- coding: utf-8 -*-
"""
serve.py — Agent Token Monitor 本地服务（v2：HTTP + SSE 实时推送 + 插件 API）。

路由：
  /             -> web/dashboard.html（内嵌最新数据）
  /api/data     -> 最新聚合数据
  /api/refresh  -> 触发一次增量扫描（后台执行，完成经 SSE 推送）
  /api/health   -> 健康检查
  /api/plugins  -> 已接入的插件列表
  /api/stream   -> SSE 实时推送（EventSource）
"""
import os
import re
import sys
import json
import time
import threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

from engine import core

BASE = core.BASE
scan_lock = threading.Lock()
_cond = threading.Condition()
_state = {'version': 0, 'data': None}


def publish(data, stats=None, source=None):
    with _cond:
        _state['version'] += 1
        _state['data'] = data
        _cond.notify_all()


def load_data():
    if _state['data'] is not None:
        return _state['data']
    if os.path.exists(core.JSON_PATH):
        with open(core.JSON_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    return None


def run_scan(full=False):
    """执行一次扫描并广播。返回 (data, stats_or_error)。"""
    if not scan_lock.acquire(blocking=False):
        return None, '扫描正在进行中'
    try:
        data, stats = core.scan(full=full)
        publish(data)
        return data, stats
    except Exception as e:
        return None, str(e)
    finally:
        scan_lock.release()


class Handler(BaseHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)

    def log_message(self, fmt, *args):
        sys.stderr.write('[%s] %s\n' % (time.strftime('%H:%M:%S'), fmt % args))

    def _send_bytes(self, body, ctype, status=200):
        self.send_response(status)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:
            pass

    def _send_json(self, obj, status=200):
        self._send_bytes(json.dumps(obj, ensure_ascii=False).encode('utf-8'),
                         'application/json; charset=utf-8', status)

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ('/', '/index.html'):
            html_path = os.path.join(core.web_dir(), 'dashboard.html')
            if os.path.exists(html_path):
                with open(html_path, 'rb') as f:
                    self._send_bytes(f.read(), 'text/html; charset=utf-8')
            else:
                self._send_json({'ok': False, 'error': 'dashboard.html 不存在'}, 404)
            return
        if path == '/api/data':
            data = load_data()
            if data is not None:
                self._send_json({'ok': True, 'data': data})
            else:
                self._send_json({'ok': False, 'error': '尚未扫描'}, 404)
            return
        if path == '/api/refresh':
            # 后台扫描，结果经 SSE 推送；立即返回，避免前端卡死
            threading.Thread(target=self._async_refresh, daemon=True).start()
            self._send_json({'ok': True, 'scan_started': True})
            return
        if path == '/api/health':
            self._send_json({'ok': True, 'time': time.time()})
            return
        if path == '/api/plugins':
            plugs = []
            for p in core.get_plugins(refresh=False):
                plugs.append({
                    'key': p['key'], 'name': p['name'], 'estimate': p['estimate'],
                    'watch': p['watch'], 'file': os.path.basename(p['file']),
                })
            self._send_json({'ok': True, 'plugins': plugs})
            return

        # ===== Agent 友好接口（给 AI 调用，返回精简结构化数据） =====
        if path == '/api/agent/summary':
            self._handle_agent_summary()
            return
        m = re.match(r'^/api/agent/agents/([A-Za-z0-9_]+)/usage$', path)
        if m:
            self._handle_agent_agent_usage(m.group(1))
            return
        if path == '/api/agent/config':
            self._handle_agent_config_get()
            return

        if path == '/api/stream':
            self._sse_loop()
            return
        # 动态路由：/api/agents/<key>
        m = re.match(r'^/api/agents/([A-Za-z0-9_]+)$', path)
        if m:
            if self.command == 'DELETE':
                self._handle_remove_agent(m.group(1))
            else:
                self._send_json({'ok': False, 'error': 'method not allowed'}, 405)
            return
        self._send_json({'ok': False, 'error': 'not found'}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == '/api/agents':
            self._handle_add_agent()
            return
        self._send_json({'ok': False, 'error': 'not found'}, 404)

    def do_DELETE(self):
        path = urlparse(self.path).path
        m = re.match(r'^/api/agents/([A-Za-z0-9_]+)$', path)
        if m:
            from engine import onboard
            ok, msg = onboard.remove(m.group(1))
            if ok:
                try:
                    core.get_plugins(refresh=True)
                except Exception:
                    pass
            self._send_json({'ok': ok, 'message': msg}, 200 if ok else 400)
            return
        self._send_json({'ok': False, 'error': 'not found'}, 404)

    def _read_json_body(self):
        try:
            n = int(self.headers.get('Content-Length', 0))
        except (TypeError, ValueError):
            n = 0
        raw = self.rfile.read(n) if n else b'{}'
        try:
            return json.loads(raw.decode('utf-8') or '{}')
        except Exception:
            return None

    def _handle_add_agent(self):
        from engine import onboard
        body = self._read_json_body()
        if body is None:
            self._send_json({'ok': False, 'error': '请求体不是合法 JSON'}, 400)
            return
        name = (body.get('name') or '').strip()
        if not name:
            self._send_json({'ok': False, 'error': '缺少 name 字段'}, 400)
            return
        data_path = body.get('data_path')
        try:
            result = onboard.register(name, data_path=data_path)
        except Exception as e:
            self._send_json({'ok': False, 'error': str(e)}, 500)
            return
        # 热加载插件并后台扫描新 agent
        if result.get('status') == 'ok':
            key = result.get('agent_key')
            try:
                core.get_plugins(refresh=True)
                def _bg():
                    try:
                        data, _ = core.scan(full=True, only=[key])
                        publish(data)
                    except Exception as e:
                        print('  [onboard] 后台扫描失败:', e)
                threading.Thread(target=_bg, daemon=True).start()
            except Exception as e:
                result['reload_warning'] = str(e)
        self._send_json({'ok': True, 'result': result},
                        200 if result.get('status') in ('ok', 'need_config') else 400)

    def _handle_remove_agent(self, key):
        from engine import onboard
        ok, msg = onboard.remove(key)
        if ok:
            try:
                core.get_plugins(refresh=True)
            except Exception:
                pass
        self._send_json({'ok': ok, 'message': msg}, 200 if ok else 400)

    # ===== Agent 友好接口 handler =====

    def _handle_agent_summary(self):
        """返回精简的用量摘要，AI 不用解析复杂 JSON。"""
        data = load_data()
        if data is None:
            self._send_json({'ok': False, 'error': '尚未扫描'}, 404)
            return
        agents = data.get('agents', [])
        summary = {
            'total_tokens': data.get('totals', {}).get('total_tokens', 0),
            'real_tokens': data.get('totals', {}).get('real_tokens', 0),
            'est_tokens': data.get('totals', {}).get('est_tokens', 0),
            'agent_count': len(agents),
            'agents': [
                {
                    'key': a.get('agent'),
                    'name': a.get('title', a.get('agent')),
                    'total_tokens': a.get('total_tokens', 0),
                    'sessions': a.get('session_count', 0),
                    'estimate': a.get('est', 0),
                }
                for a in agents
            ],
        }
        self._send_json({'ok': True, 'summary': summary})

    def _handle_agent_agent_usage(self, key):
        """返回单个 agent 的详细用量。"""
        data = load_data()
        if data is None:
            self._send_json({'ok': False, 'error': '尚未扫描'}, 404)
            return
        agents = data.get('agents', [])
        found = [a for a in agents if a.get('agent') == key]
        if not found:
            self._send_json({'ok': False, 'error': f'agent {key} 未找到'}, 404)
            return
        self._send_json({'ok': True, 'agent': found[0]})

    def _handle_agent_config_get(self):
        """查看本地配置（比如 doubao cookie 是否已配置）。"""
        import os as _os
        config_path = _os.path.expanduser("~/.doubao-usage/config.json")
        if not _os.path.isfile(config_path):
            self._send_json({'ok': True, 'config_exists': False, 'path': config_path})
            return
        try:
            with open(config_path, 'r') as f:
                config = json.load(f)
            # 不返回敏感值，只返回 key 列表
            keys = list(config.keys())
            self._send_json({
                'ok': True,
                'config_exists': True,
                'path': config_path,
                'keys': keys,
            })
        except Exception as e:
            self._send_json({'ok': False, 'error': str(e)}, 500)

    def _async_refresh(self):
        try:
            run_scan(full=False)
        except Exception as e:
            print('  [serve] 后台扫描异常:', e)

    def _sse_loop(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Connection', 'keep-alive')
        self.end_headers()
        last = 0
        try:
            while True:
                with _cond:
                    if _state['version'] > last:
                        last = _state['version']
                        payload = _state['data']
                    else:
                        payload = None
                if payload is not None:
                    body = 'event: update\ndata: ' + json.dumps(
                        {'ok': True, 'data': payload}, ensure_ascii=False) + '\n\n'
                    self.wfile.write(body.encode('utf-8'))
                else:
                    self.wfile.write(b': ping\n\n')
                self.wfile.flush()
                time.sleep(1)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        except Exception:
            pass


def create_server(port=8765):
    # 必须用多线程：SSE 是长连接，单线程 HTTPServer 会被一条 SSE 连接阻塞全部请求
    return ThreadingHTTPServer(('127.0.0.1', port), Handler)


def main():
    import argparse
    from engine.watcher import Watcher

    ap = argparse.ArgumentParser(description='Agent Token Monitor 本地服务')
    ap.add_argument('--port', type=int, default=8765)
    ap.add_argument('--open', action='store_true', help='启动后自动打开浏览器')
    ap.add_argument('--interval', type=float, default=5.0, help='实时监控轮询间隔（秒）')
    args = ap.parse_args()

    if not os.path.exists(core.JSON_PATH):
        print('未找到 usage.json，先执行一次扫描……')
        core.scan(full=True)

    srv = create_server(args.port)
    url = f'http://127.0.0.1:{args.port}/'

    watcher = Watcher(interval=args.interval, on_update=publish, scan_lock=scan_lock)
    watcher.start()

    print('Agent Token Monitor 已启动:', url)
    print('实时监控：每 %ss 检测数据源变化，变化自动推送。按 Ctrl+C 停止。' % args.interval)
    if args.open:
        import webbrowser
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print('\n已停止。')


if __name__ == '__main__':
    main()
