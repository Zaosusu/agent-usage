# -*- coding: utf-8 -*-
"""插件：CodeBuddy（精确计数）。数据源：~/.codebuddy/projects/**/*.jsonl，
usage 位于 message.usage 或 providerData.usage，时间戳为毫秒整数。"""
from engine.common import scan_jsonl_dir, parse_claude_like_file

KEY = 'codebuddy'
NAME = 'CodeBuddy'
ESTIMATE = False
WATCH_PATHS = ['%USERPROFILE%\\.codebuddy\\projects']


def scan(full, need, mark):
    root = WATCH_PATHS[0].replace('%USERPROFILE%', __import__('os').path.expanduser('~'))
    return scan_jsonl_dir(root, '**/*.jsonl',
                          lambda p: parse_claude_like_file(KEY, p), full, need, mark)
