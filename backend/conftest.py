# -*- coding: utf-8 -*-
"""pytest 引导：从 backend/ 目录或其上级调用时都能 ``import app...``。

只做路径引导；数据目录/夹具目录与库隔离由评测方通过环境变量控制。
"""
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))
