# -*- coding: utf-8 -*-
"""插件：千问工作（估算模式）。数据源：~/.qwenworkcn/projects/**/*.jsonl（无 usage 字段），
按会话文本估算 token。"""
import os
from engine.common import scan_jsonl_dir, parse_estimate_file

KEY = 'qwenworkcn'
NAME = '千问工作'
ESTIMATE = True
WATCH_PATHS = ['%USERPROFILE%\\.qwenworkcn\\projects']

_ROOT = os.path.expanduser(WATCH_PATHS[0].replace('%USERPROFILE%', os.path.expanduser('~')))


def scan(full, need, mark):
    return scan_jsonl_dir(_ROOT, '**/*.jsonl',
                          lambda p: parse_estimate_file(KEY, p, _ROOT), full, need, mark)
