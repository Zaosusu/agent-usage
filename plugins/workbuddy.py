"""WorkBuddy 用量解析（新版：从 projects jsonl 文件估算）。

新版 WorkBuddy 不再往 session_usage 表写 credit_json，
改为从 projects/*/*.jsonl 文件里统计消息文本，按字数估算 token。
"""
from __future__ import annotations
import json, os, re, glob, time

KEY = 'workbuddy'
ROOT = os.path.expanduser('~/.workbuddy/projects')
WATCH_PATHS = [ROOT]

# 估算系数：中文 1 字 ≈ 1.5 token，英文 1 词 ≈ 0.75 token
def _estimate_tokens(text):
    if not text:
        return 0
    chinese = len(re.findall(r'[\u4e00-\u9fff]', text))
    english = len(re.findall(r'[a-zA-Z]+', text))
    return int(chinese * 1.5 + english * 0.75)

def _parse_session_file(path):
    """解析一个 WorkBuddy jsonl 会话文件，返回按天的估算 token 数和标题"""
    daily = {}
    title = os.path.basename(path).replace('.jsonl', '')
    with open(path, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            try:
                d = json.loads(line)
            except:
                continue
            t = d.get('type')
            ts = d.get('timestamp')
            if not ts or not isinstance(ts, (int, float)):
                continue
            day = time.strftime('%Y-%m-%d', time.localtime(ts / 1000))
            # 找 AI 生成的标题
            if t == 'ai-title':
                title = d.get('aiTitle', title)
            # 统计 message 类型的文本
            elif t == 'message':
                role = d.get('role')
                if role not in ('user', 'assistant'):
                    continue
                content = d.get('content', [])
                text = ''
                if isinstance(content, list):
                    for c in content:
                        if isinstance(c, dict):
                            if 'text' in c:
                                text += c['text']
                elif isinstance(content, str):
                    text = content
                tokens = _estimate_tokens(text)
                if day not in daily:
                    daily[day] = 0
                daily[day] += tokens
            # 统计 reasoning 类型的文本（assistant 推理）
            elif t == 'reasoning':
                content = d.get('rawContent', [])
                text = ''
                if isinstance(content, list):
                    for c in content:
                        if isinstance(c, dict) and 'text' in c:
                            text += c['text']
                tokens = _estimate_tokens(text)
                if day not in daily:
                    daily[day] = 0
                daily[day] += tokens
    return daily, title

def scan(full, need, mark):
    out = []
    daily_rows = []
    daily_files = []
    if not os.path.isdir(ROOT):
        return {'sessions': out, 'daily': daily_rows, 'daily_files': daily_files}
    files = glob.glob(os.path.join(ROOT, '*', '*.jsonl'))
    for p in files:
        try:
            st = os.stat(p)
        except OSError:
            continue
        fp = f'{st.st_mtime_ns}:{st.st_size}'
        if not need(full, p, fp):
            continue
        daily, title = _parse_session_file(p)
        # 生成按天的 sessions
        for day, tokens in daily.items():
            if tokens < 100:
                continue
            ts_ms = int(time.mktime(time.strptime(day, '%Y-%m-%d')) * 1000)
            short_id = os.path.basename(p).replace('.jsonl', '')[:8]
            out.append({
                'agent': KEY, 'session_id': f'workbuddy-{day}-{short_id}',
                'title': f'{title[:30]} {day}',
                'cwd': '', 'model': 'workbuddy', 'provider': 'workbuddy',
                'created_at': ts_ms, 'last_activity_at': ts_ms,
                'input_tokens': 0, 'output_tokens': 0,
                'cache_read_tokens': 0, 'cache_write_tokens': 0,
                'total_tokens': tokens, 'cost': None, 'est': 1,
                'source_file': p,
            })
            daily_rows.append({
                'agent': KEY, 'day': day, 'source_file': p,
                'tokens': tokens, 'est': 1,
            })
        daily_files.append(p)
        mark(p, fp)
    return {'sessions': out, 'daily': daily_rows, 'daily_files': daily_files}
