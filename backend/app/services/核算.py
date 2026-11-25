"""单户回放核算：整批回放与单桶局部重核共用，保证两种路径算法完全一致。

金额单位：分。语义与原 services/回放.py 逐行对齐：
前日有效快照为开端（无前日镜则 0），加当日已入账流水净额，与当日有效快照比差额。
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any


def 前日(营业日: str) -> str:
    y, m, d = map(int, 营业日.split("-"))
    return (date(y, m, d) - timedelta(days=1)).isoformat()


def 重算单户(conn, 账户键: str, 日: str) -> dict[str, Any]:
    """返回一户在某营业日的回放核算结果（只读，不写库）。"""
    快照 = conn.execute(
        "SELECT 余额 FROM 周期快照 WHERE 账户键=? AND 营业日=? AND 状态='有效'",
        (账户键, 日),
    ).fetchone()

    if 快照 is None:
        return {
            "账户键": 账户键,
            "营业日": 日,
            "有快照": False,
            "快照余额": None,
            "回放余额": None,
            "开端": 0,
            "流水净额": 0,
            "差额": 0,
            "偏离": 0,
            "相符": False,
            "原因": "缺当日快照",
        }

    前 = 前日(日)
    前日快照 = conn.execute(
        "SELECT 余额 FROM 周期快照 WHERE 账户键=? AND 营业日=? AND 状态='有效'",
        (账户键, 前),
    ).fetchone()
    开端 = 0 if 前日快照 is None else int(前日快照["余额"])
    净额 = int(
        conn.execute(
            "SELECT COALESCE(SUM(变动额),0) AS s FROM 变动流水 "
            "WHERE 账户键=? AND 营业日=? AND 命运='已入账'",
            (账户键, 日),
        ).fetchone()["s"]
    )
    回放余额 = 开端 + 净额
    快照余额 = int(快照["余额"])
    差额 = 快照余额 - 回放余额
    return {
        "账户键": 账户键,
        "营业日": 日,
        "有快照": True,
        "快照余额": 快照余额,
        "回放余额": 回放余额,
        "开端": 开端,
        "流水净额": 净额,
        "差额": 差额,
        "偏离": abs(快照余额 - 回放余额),
        "相符": 差额 == 0,
        "原因": None if 差额 == 0 else "快照与回放不一致",
    }
