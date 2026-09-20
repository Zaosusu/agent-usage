# -*- coding: utf-8 -*-
"""插件：豆包工作。
数据来源（本地可得部分）：
1. IndexedDB (http_127.0.0.1_5188) 的 inputTokens/outputTokens（仅当前会话，精确）
2. trajectory.jsonl 文本估算（兜底）
3. Local Storage 订阅配额（请求次数，非 token）

注意：豆包是云端应用，历史 token 用量全在服务端。
本地 IndexedDB 只缓存当前活跃会话（~25请求/65条记录），
历史用量需从 doubao.com → 订阅与额度管理 查看。
"""
import os, glob, json, struct
from engine.common import estimate_tokens, iso_to_ms, collect_strings

KEY = 'doubao'
NAME = '豆包工作'
ESTIMATE = True
WATCH_PATHS = [
    '%USERPROFILE%\\AppData\\Local\\DoubaoWork\\User Data\\Default'
]

_DEFAULT_ROOT = os.path.expanduser(WATCH_PATHS[0].replace('%USERPROFILE%', os.path.expanduser('~')))


def _parse_varint(data, pos):
    val = 0; shift = 0
    while pos < len(data):
        b = data[pos]; pos += 1
        val |= (b & 0x7f) << shift
        if not (b & 0x80): return val, pos
        shift += 7
    return val, pos


def _extract_tokens_from_bytes(data):
    ins, outs = [], []
    for kw, arr in [(b'inputTokensI', ins), (b'outputTokensI', outs)]:
        idx = 0
        while True:
            idx = data.find(kw, idx)
            if idx < 0: break
            pos = idx + len(kw)
            val, _ = _parse_varint(data, pos)
            if 0 < val < 500_000_000:
                arr.append(val)
            idx = pos
    return ins, outs


def _scan_indexeddb():
    """从 IndexedDB leveldb 提取真实 token 记录。"""
    idb_dir = os.path.join(_DEFAULT_ROOT, 'IndexedDB')
    total_in = 0; total_out = 0; count = 0
    if not os.path.isdir(idb_dir):
        return 0, 0, 0
    for sub in os.listdir(idb_dir):
        ldb_dir = os.path.join(idb_dir, sub)
        if not os.path.isdir(ldb_dir): continue
        for fn in os.listdir(ldb_dir):
            if not (fn.endswith('.ldb') or fn.endswith('.log')): continue
            fp = os.path.join(ldb_dir, fn)
            try:
                with open(fp, 'rb') as f:
                    data = f.read()
            except: continue
            ins, outs = _extract_tokens_from_bytes(data)
            total_in += sum(ins)
            total_out += sum(outs)
            count += len(ins)
    return total_in, total_out, count


def _scan_trajectory():
    """从 trajectory.jsonl 估算 token。"""
    sess_root = os.path.join(_DEFAULT_ROOT, '.doubaowork', 'agent_mode',
                             'workspace', '.sessions')
    total_tokens = 0
    if not os.path.isdir(sess_root):
        return 0
    for root, dirs, files in os.walk(sess_root):
        for f in files:
            if f != 'trajectory.jsonl': continue
            fp = os.path.join(root, f)
            try:
                with open(fp, 'r', encoding='utf-8', errors='replace') as fh:
                    texts = []
                    for line in fh:
                        line = line.strip()
                        if not line: continue
                        try:
                            obj = json.loads(line)
                            collect_strings(obj, texts, 50000)
                        except:
                            texts.append(line)
                    total_tokens += estimate_tokens('\n'.join(texts))
            except: pass
    return total_tokens


def _scan_quota():
    """从 Local Storage 提取订阅配额。"""
    ls_dir = os.path.join(_DEFAULT_ROOT, 'Local Storage', 'leveldb')
    info = {}
    if not os.path.isdir(ls_dir):
        return info
    for fn in os.listdir(ls_dir):
        if not (fn.endswith('.ldb') or fn.endswith('.log')): continue
        fp = os.path.join(ls_dir, fn)
        try:
            with open(fp, 'rb') as f:
                text = f.read().decode('utf-8', 'ignore')
        except: continue
        for kw in ['monthlyLimit', 'usedThisPeriod', 'usedThisMonth',
                   'plan', 'billingMode']:
            idx = text.find(kw)
            if idx >= 0:
                snippet = text[idx:idx+200]
                info[kw] = snippet[:100]
    return info


def _parse_quota_num(quota, key):
    import re
    m = re.search(rf'{key}["\s:]+(\d+)', quota.get(key, ''))
    return int(m.group(1)) if m else 0


def scan(full, need, mark):
    idx_in, idx_out, idx_count = _scan_indexeddb()
    traj_tokens = _scan_trajectory()
    quota = _scan_quota()

    total_tokens = idx_in + idx_out + traj_tokens

    if total_tokens == 0:
        return []

    # 构造单条会话记录
    period = ''
    if 'usedThisPeriod' in quota and 'monthlyLimit' in quota:
        import re
        m1 = re.search(r'usedThisPeriod[:"\s]+(\d+)', quota.get('usedThisPeriod', ''))
        m2 = re.search(r'monthlyLimit[:"\s]+(\d+)', quota.get('monthlyLimit', ''))
        if m1 and m2:
            period = f'（订阅用量 {m1.group(1)}/{m2.group(1)} 次）'

    return [{
        'agent': KEY, 'session_id': 'doubao-local',
        'title': f'豆包工作（本地估算{period}）',
        'cwd': '', 'model': 'doubao-seed', 'provider': 'bytedance',
        'created_at': 0,
        'last_activity_at': 0,
        'input_tokens': idx_in,
        'output_tokens': idx_out,
        'cache_read_tokens': 0, 'cache_write_tokens': 0,
        'total_tokens': total_tokens,
        'cost': None, 'est': 1,
        'source_file': 'IndexedDB+trajectory',
    }]
