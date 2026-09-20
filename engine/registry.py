# -*- coding: utf-8 -*-
"""engine/registry.py — 插件发现与加载。

插件约定（plugins/ 下任意 .py 文件，排除 _ 开头和 __init__）：
  文件内必须定义：
    KEY         = 'myagent'              # 稳定唯一标识（小写字母/数字/下划线）
    NAME        = '我的 Agent'            # 显示名
    ESTIMATE    = False                  # True=估算模式（无真实计数）
    WATCH_PATHS = ['%USERPROFILE%\\.myagent\\data.jsonl']   # 监视路径（支持 %USERPROFILE%、目录或 glob 模式）
    def scan(full, need, mark) -> list[dict]:
        ... 返回会话列表，schema 见 engine/core.py 顶部注释

  核心引擎会：
    1. 动态 import 每个插件文件（importlib，从文件路径加载，无需安装）；
    2. 展开 WATCH_PATHS 中的 %USERPROFILE%；
    3. 用 need()/mark() 做文件级增量指纹，跳过未变化的文件；
    4. 汇总所有插件的会话做聚合与烘焙。
"""
import os
import sys
import importlib.util


def _load_plugin(path):
    mod_name = 'plug_' + os.path.splitext(os.path.basename(path))[0]
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as e:
        print(f'  [registry] 插件加载失败 {os.path.basename(path)}: {e}')
        return None
    return mod


def discover(plugin_dirs):
    """从多个目录发现插件，返回已校验的插件元信息列表。

    每个元素：{
      'key', 'name', 'estimate', 'watch', 'scan', 'module', 'file'
    }
    """
    plugins = []
    seen = {}
    for d in plugin_dirs:
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if not fn.endswith('.py') or fn.startswith('_'):
                continue
            path = os.path.join(d, fn)
            mod = _load_plugin(path)
            if mod is None:
                continue
            key = getattr(mod, 'KEY', None)
            if not key:
                print(f'  [registry] {fn} 缺少 KEY，跳过')
                continue
            scan = getattr(mod, 'scan', None)
            watch = getattr(mod, 'WATCH_PATHS', None)
            if not callable(scan) or not watch:
                print(f'  [registry] {fn} 缺少 scan() 或 WATCH_PATHS，跳过')
                continue
            info = {
                'key': str(key),
                'name': getattr(mod, 'NAME', str(key)),
                'estimate': bool(getattr(mod, 'ESTIMATE', False)),
                'watch': list(watch),
                'scan': scan,
                'module': mod,
                'file': path,
            }
            if info['key'] in seen:
                # 靠后的目录优先（用户扩展目录覆盖内置）
                seen[info['key']] = info
            else:
                seen[info['key']] = info
    plugins = list(seen.values())
    plugins.sort(key=lambda p: p['key'])
    return plugins
