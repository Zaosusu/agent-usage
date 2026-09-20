# -*- coding: utf-8 -*-
"""插件：ZCode（精确计数，结构存在）。数据源：~/.zcode/cli/db/db.sqlite 的 model_usage/turn_usage 表。
当前表内为 0 行（CLI 无实际使用），返回空列表即可；未来有数据时在此扩展解析。"""
import os
from engine.common import ro_connect

KEY = 'zcode'
NAME = 'ZCode'
ESTIMATE = False
WATCH_PATHS = ['%USERPROFILE%\\.zcode\\cli\\db\\db.sqlite']


def scan(full, need, mark):
    dbp = WATCH_PATHS[0].replace('%USERPROFILE%', os.path.expanduser('~'))
    if not os.path.exists(dbp):
        return []
    con = ro_connect(dbp)
    if con is None:
        return []
    try:
        n = con.execute('select count(*) from model_usage').fetchone()[0]
    except Exception:
        n = 0
    con.close()
    if n == 0:
        return []
    # TODO: 表内有数据后在此解析（列结构见 README 中的探查记录）
    return []
