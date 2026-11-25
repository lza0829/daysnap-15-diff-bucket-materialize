"""操作闸：局部重核 × 整批回放 × 同日抽取盖写 的失败闭互斥。

两级作用域（关键）：
- 桶级：同一原因桶的局部重核互斥；不同原因桶（如同批甲乙两桶）可各自重核，
  因此「甲桶重核成功」不会被「乙桶被占/失效」连坐。
- 日级：整批回放 / 日终抽取盖写按营业日独占；只要该日存在任一「进行中」的
  局部重核任务，整批回放/抽取立即失败闭；反之全日锁存在时单桶重核也拒绝。

双保险：
- 进程内 threading 原语：同进程多线程（TestClient / 线程夹具）无竞态；
- 持久化 操作锁 表 + 局部重核任务(状态='进行中')：跨连接/跨进程、以及夹具
  直接播种「进行中」状态时同样可检出。

所有拒绝异常都继承 ValueError，便于 `pytest.raises(ValueError)` 与路由 400 约定。
"""
from __future__ import annotations

import threading
from typing import Optional

from app.db import 连接
from app.services.时间 import 此刻


class 失败闭错误(ValueError):
    """所有失败闭拒绝的基类（也是 ValueError）。"""

    错误码 = "失败闭"


class 操作冲突(失败闭错误):
    错误码 = "操作冲突"


class 局部重核拒绝(失败闭错误):
    错误码 = "局部重核拒绝"


class 核证未通过(局部重核拒绝):
    错误码 = "核证未通过"


_闸锁 = threading.RLock()
_全日持有: dict[str, str] = {}          # 营业日 -> 持有者（整批回放/抽取）
_桶持有: dict[str, str] = {}            # 桶键 -> 持有者（局部重核）
_日活桶: dict[str, set[str]] = {}       # 营业日 -> {桶键} 进行中的局部重核


def 日锁键(营业日: str) -> str:
    return f"日:{营业日}"


def 桶锁键(桶键: str) -> str:
    return f"桶:{桶键}"


def 清空进程闸() -> None:
    """重置接口/测试隔离用：清空进程内持有表（不动数据库）。"""
    with _闸锁:
        _全日持有.clear()
        _桶持有.clear()
        _日活桶.clear()


def _有进行中局部任务_db(营业日: str, 排除持有者: str = "") -> Optional[dict]:
    conn = 连接()
    try:
        row = conn.execute(
            "SELECT 任务编号, 原因, 桶键 FROM 局部重核任务 "
            "WHERE 营业日=? AND 状态='进行中' AND 任务编号<>? LIMIT 1",
            (营业日, 排除持有者),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _同桶进行中任务_db(桶键: str, 排除持有者: str = "") -> Optional[dict]:
    conn = 连接()
    try:
        row = conn.execute(
            "SELECT 任务编号, 原因, 桶键, 营业日 FROM 局部重核任务 "
            "WHERE 桶键=? AND 状态='进行中' AND 任务编号<>? LIMIT 1",
            (桶键, 排除持有者),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def 获取全日(营业日: str, 操作: str, 持有者: str) -> str:
    """整批回放 / 抽取盖写获取日级独占。任一局部重核进行中即失败闭。"""
    key = 日锁键(营业日)
    with _闸锁:
        现 = _全日持有.get(营业日)
        if 现 and 现 != 持有者:
            raise 操作冲突(f"{营业日} 已有全日操作（持有者 {现}）进行中，{操作}失败闭，禁止双写")
        if _日活桶.get(营业日):
            桶 = sorted(_日活桶[营业日])
            raise 操作冲突(
                f"{营业日} 有局部重核进行中 {桶}，{操作}须失败闭：不得与局部重核双写"
            )
        # 持久化：别的进程/夹具播种的日锁
        conn = 连接()
        try:
            row = conn.execute("SELECT 持有者, 操作 FROM 操作锁 WHERE 锁键=?", (key,)).fetchone()
            if row and row["持有者"] != 持有者:
                raise 操作冲突(
                    f"{营业日} 已有{row['操作']}（{row['持有者']}）持日锁，{操作}失败闭"
                )
            任务 = _有进行中局部任务_db(营业日, 排除持有者=持有者)
            if 任务:
                raise 操作冲突(
                    f"{营业日} 局部重核任务 {任务['任务编号']}（原因={任务['原因']}）进行中，"
                    f"{操作}失败闭，不得双写"
                )
            if not row:
                conn.execute(
                    "INSERT INTO 操作锁(锁键, 操作, 持有者, 营业日, 时刻) VALUES (?,?,?,?,?)",
                    (key, 操作, 持有者, 营业日, 此刻()),
                )
                conn.commit()
        finally:
            conn.close()
        _全日持有[营业日] = 持有者
        return key


def 释放全日(营业日: str, 持有者: str) -> None:
    key = 日锁键(营业日)
    with _闸锁:
        if _全日持有.get(营业日) == 持有者:
            del _全日持有[营业日]
    conn = 连接()
    try:
        conn.execute("DELETE FROM 操作锁 WHERE 锁键=? AND 持有者=?", (key, 持有者))
        conn.commit()
    finally:
        conn.close()


def 获取桶(营业日: str, 桶键: str, 持有者: str) -> str:
    """局部重核获取桶级锁。全日操作进行中或同桶重入 -> 拒绝；异桶不连坐。"""
    bkey = 桶锁键(桶键)
    with _闸锁:
        全 = _全日持有.get(营业日)
        if 全 and 全 != 持有者:
            raise 操作冲突(f"{营业日} 整批操作（{全}）进行中，局部重核失败闭")
        现 = _桶持有.get(桶键)
        if 现 and 现 != 持有者:
            raise 操作冲突(f"桶 {桶键} 正由 {现} 局部重核，禁止同桶并发双写")
        # 持久化检查
        conn = 连接()
        try:
            drow = conn.execute(
                "SELECT 持有者, 操作 FROM 操作锁 WHERE 锁键=?", (日锁键(营业日),)
            ).fetchone()
            if drow and drow["持有者"] != 持有者:
                raise 操作冲突(f"{营业日} 整批{drow['操作']}（{drow['持有者']}）持日锁，局部重核失败闭")
            brow = conn.execute("SELECT 持有者 FROM 操作锁 WHERE 锁键=?", (bkey,)).fetchone()
            if brow and brow["持有者"] != 持有者:
                raise 操作冲突(f"桶 {桶键} 已被 {brow['持有者']} 锁定重核，拒绝重复局部重核")
            t = _同桶进行中任务_db(桶键, 排除持有者=持有者)
            if t:
                raise 操作冲突(
                    f"桶 {桶键} 存在进行中的局部重核任务 {t['任务编号']}，拒绝再次局部重核"
                )
            if not brow:
                conn.execute(
                    "INSERT INTO 操作锁(锁键, 操作, 持有者, 营业日, 时刻) VALUES (?,?,?,?,?)",
                    (bkey, "局部重核", 持有者, 营业日, 此刻()),
                )
                conn.commit()
        finally:
            conn.close()
        _桶持有[桶键] = 持有者
        _日活桶.setdefault(营业日, set()).add(桶键)
        return bkey


def 释放桶(营业日: str, 桶键: str, 持有者: str) -> None:
    bkey = 桶锁键(桶键)
    with _闸锁:
        if _桶持有.get(桶键) == 持有者:
            del _桶持有[桶键]
        s = _日活桶.get(营业日)
        if s and 桶键 in s:
            s.discard(桶键)
            if not s:
                _日活桶.pop(营业日, None)
    conn = 连接()
    try:
        conn.execute("DELETE FROM 操作锁 WHERE 锁键=? AND 持有者=?", (bkey, 持有者))
        conn.commit()
    finally:
        conn.close()


def 锁快照(营业日: Optional[str] = None) -> dict:
    """核证/排查用：当前进程内 + 持久化锁与进行中任务。"""
    with _闸锁:
        内存 = {
            "全日": dict(_全日持有),
            "桶": dict(_桶持有),
            "日活桶": {k: sorted(v) for k, v in _日活桶.items()},
        }
    conn = 连接()
    try:
        if 营业日:
            locks = [
                dict(r)
                for r in conn.execute("SELECT * FROM 操作锁 WHERE 营业日=? ORDER BY 锁键", (营业日,))
            ]
            tasks = [
                dict(r)
                for r in conn.execute(
                    "SELECT 任务编号, 回放批次编号, 桶键, 原因, 营业日, 状态, 时刻 "
                    "FROM 局部重核任务 WHERE 营业日=? ORDER BY 时刻",
                    (营业日,),
                )
            ]
        else:
            locks = [dict(r) for r in conn.execute("SELECT * FROM 操作锁 ORDER BY 锁键")]
            tasks = [
                dict(r)
                for r in conn.execute(
                    "SELECT 任务编号, 回放批次编号, 桶键, 原因, 营业日, 状态, 时刻 "
                    "FROM 局部重核任务 ORDER BY 时刻 DESC LIMIT 100"
                )
            ]
        for t in tasks:
            for k in ("原账户键集合", "受影响键", "新账户键集合", "变更键", "结果"):
                if k in t and isinstance(t[k], str):
                    try:
                        t[k] = __import__("json").loads(t[k])
                    except Exception:
                        pass
        return {"进程内": 内存, "操作锁": locks, "局部重核任务": tasks}
    finally:
        conn.close()
