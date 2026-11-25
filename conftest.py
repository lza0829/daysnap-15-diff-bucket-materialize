# -*- coding: utf-8 -*-
"""仓库级 pytest 引导：让评测用例在任意调用目录下都能 ``import app...``。

只做路径引导，不设置 数据目录/夹具目录 —— 库隔离与夹具位置由评测方自行决定。
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_BACKEND = _ROOT / "backend"
for p in (str(_BACKEND), str(_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

# 避免裸跑 pytest 时扫到前端依赖目录。
collect_ignore = ["frontend/node_modules"]
