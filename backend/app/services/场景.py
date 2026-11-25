"""场景夹具装载：把「双原因 / 局部重核 / 回滚 / 并发」等富声明式 JSON 落成可核证状态。

普通流水夹具仍走 services.装载；只有含下列任一节的 JSON 才按场景处理：
账户 / 快照 / 删快照 / 差额行 / 差额桶 / 局部重核任务 / 进行中重核 / 操作锁 /
抽取 / 回放(请求)。本模块主要服务于随仓夹具与一键自证，隐藏测试通常自行造数。
"""
from __future__ import annotations

import json
from typing import Any

from app.db import 连接
from app.services import 装载, 回放, 抽取
from app.services.时间 import 此刻

_场景键 = {
    "场景",
    "账户",
    "快照",
    "删快照",
    "差额行",
    "差额桶",
    "桶",
    "局部重核任务",
    "进行中重核",
    "操作锁",
    "抽取",
    "回放",
    "回放请求",
    "回放失败请求",
}


def 是场景(data: Any) -> bool:
    return isinstance(data, dict) and any(k in data for k in _场景键)


def _清批(conn, 批: str) -> None:
    """重放固定批号夹具前，清掉该批派生物，保证可重复装载且不双写。"""
    for sql, a in [
        ("DELETE FROM 局部重核任务 WHERE 回放批次编号=?", (批,)),
        ("DELETE FROM 差额桶账户 WHERE 回放批次编号=?", (批,)),
        ("DELETE FROM 差额桶 WHERE 回放批次编号=?", (批,)),
        ("DELETE FROM 差额行 WHERE 回放批次编号=?", (批,)),
        ("DELETE FROM 跳变记录 WHERE 检出批次编号=?", (批,)),
        ("DELETE FROM 回放批次 WHERE 编号=?", (批,)),
    ]:
        conn.execute(sql, a)


def _指令(data: dict, *键: str):
    for k in 键:
        if k in data:
            v = data[k]
            return v if isinstance(v, dict) else ({} if v is True else None), k
    return None, ""


def 装载场景(data: dict[str, Any]) -> dict[str, Any]:
    概要: dict[str, Any] = {"完成": True, "场景": data.get("场景", "")}
    日 = str(data.get("营业日") or "2026-11-22")

    # 1) 账户（upsert）
    账户 = data.get("账户") or []
    if 账户:
        conn = 连接()
        try:
            for a in 账户:
                键 = str(a.get("账户键") or "").strip()
                if not 键:
                    continue
                conn.execute(
                    "INSERT INTO 账户(账户键, 户名, 币种, 开户营业日, 状态) VALUES (?,?,?,?,?) "
                    "ON CONFLICT(账户键) DO UPDATE SET 户名=excluded.户名, 币种=excluded.币种, "
                    "开户营业日=excluded.开户营业日, 状态=excluded.状态",
                    (
                        键,
                        a.get("户名", 键),
                        a.get("币种", "CNY"),
                        a.get("开户营业日", "2026-11-01"),
                        a.get("状态", "正常"),
                    ),
                )
            conn.commit()
        finally:
            conn.close()
        概要["账户写入"] = len(账户)

    # 2) 流水（复用既有校验/旁置逻辑）
    流水 = data.get("流水") or data.get("事件") or []
    if 流水:
        概要["流水"] = 装载.装载流水(流水, str(data.get("批次编号") or ""))

    # 3) 快照 / 删快照 / 直接差额行 / 桶 / 任务 / 锁
    conn = 连接()
    try:
        for s in data.get("删快照") or []:
            conn.execute(
                "DELETE FROM 周期快照 WHERE 账户键=? AND 营业日=?",
                (s.get("账户键"), s.get("营业日", 日)),
            )

        for s in data.get("快照") or []:
            k = str(s.get("账户键"))
            d = str(s.get("营业日", 日))
            conn.execute(
                "DELETE FROM 周期快照 WHERE 账户键=? AND 营业日=?", (k, d)
            )
            conn.execute(
                "INSERT INTO 周期快照(快照键, 账户键, 营业日, 余额, 抽取批次编号, 来源, 状态) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    s.get("快照键") or f"{k}|{d}",
                    k,
                    d,
                    int(s.get("余额", 0)),
                    s.get("抽取批次编号", "场景夹具"),
                    s.get("来源", "场景夹具"),
                    s.get("状态", "有效"),
                ),
            )

        for r in data.get("差额行") or []:
            conn.execute(
                "INSERT INTO 差额行(回放批次编号, 账户键, 营业日, 快照余额, 回放余额, 差额, 原因, 时刻, 状态) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    r.get("回放批次编号", ""),
                    r.get("账户键", ""),
                    r.get("营业日", 日),
                    r.get("快照余额"),
                    r.get("回放余额"),
                    int(r.get("差额", 0)),
                    r.get("原因", "快照与回放不一致"),
                    r.get("时刻", 此刻()),
                    r.get("状态", "有效"),
                ),
            )

        # 固定批号重放前清派生物（若随后有回放指令）
        rp, _ = _指令(data, "回放", "回放请求", "回放失败请求")
        回放批 = ""
        if rp is not None:
            回放批 = str(rp.get("批次编号") or data.get("回放批次编号") or "")
            if 回放批:
                _清批(conn, 回放批)
        conn.commit()
    finally:
        conn.close()

    # 4) 直接声明的桶（需要模块函数，延迟导入避免循环）
    from app.modules.差额分桶 import _写桶集合

    for b in data.get("差额桶") or data.get("桶") or []:
        conn = 连接()
        try:
            _写桶集合(
                conn,
                b["回放批次编号"],
                b.get("营业日", 日),
                b["原因"],
                b.get("账户键集合") or b.get("keys") or [],
                状态=b.get("状态", "有效"),
            )
            conn.commit()
        finally:
            conn.close()

    # 5) 抽取指令
    ex, _ = _指令(data, "抽取")
    if ex is not None:
        try:
            概要["抽取"] = 抽取.日终抽取(str(ex.get("营业日", 日)), str(ex.get("批次编号", "")))
        except ValueError as e:
            概要["抽取错误"] = str(e)

    # 6) 回放指令（可能是回滚夹具：捕获错误并点算回滚后计数）
    if rp is not None:
        try:
            概要["回放"] = 回放.回放核对(
                str(rp.get("营业日", 日)),
                str(rp.get("批次编号", "")),
                故障注入=str(rp.get("故障注入", "")),
                强制失败=bool(rp.get("强制失败", False)),
            )
            回放批 = 概要["回放"].get("批次编号", 回放批)
        except ValueError as e:
            概要["回放错误"] = str(e)
            回放批 = str(rp.get("批次编号", "") or 回放批)
        if 回放批:
            conn = 连接()
            try:
                概要["回滚后计数"] = {
                    "回放批次": conn.execute(
                        "SELECT COUNT(*) c FROM 回放批次 WHERE 编号=?", (回放批,)
                    ).fetchone()["c"],
                    "差额行": conn.execute(
                        "SELECT COUNT(*) c FROM 差额行 WHERE 回放批次编号=?", (回放批,)
                    ).fetchone()["c"],
                    "差额桶": conn.execute(
                        "SELECT COUNT(*) c FROM 差额桶 WHERE 回放批次编号=?", (回放批,)
                    ).fetchone()["c"],
                }
            finally:
                conn.close()

    # 7) 播种「进行中重核」（解析桶键 + 任务行 + 桶级操作锁）
    for seed in data.get("进行中重核") or []:
        批 = str(seed.get("回放批次编号", ""))
        因 = str(seed.get("原因", ""))
        任务号 = str(seed.get("任务编号") or f"任务-占用-{len(概要)}")
        conn = 连接()
        try:
            h = None
            if seed.get("桶键"):
                h = conn.execute("SELECT * FROM 差额桶 WHERE 桶键=?", (seed["桶键"],)).fetchone()
            elif 批 and 因:
                h = conn.execute(
                    "SELECT * FROM 差额桶 WHERE 回放批次编号=? AND 原因=?", (批, 因)
                ).fetchone()
            桶键 = h["桶键"] if h else str(seed.get("桶键", ""))
            营业日 = h["营业日"] if h else 日
            keys = json.loads(h["账户键集合"]) if h else (seed.get("账户键集合") or [])
            conn.execute(
                "INSERT OR REPLACE INTO 局部重核任务(任务编号, 回放批次编号, 桶键, 原因, 营业日, "
                "状态, 局部是否允许, 原账户键集合, 受影响键, 时刻) VALUES (?,?,?,?,?, '进行中',1,?,?,?)",
                (任务号, 批, 桶键, 因, 营业日, json.dumps(keys, ensure_ascii=False),
                 json.dumps(keys, ensure_ascii=False), 此刻()),
            )
            conn.execute(
                "INSERT OR IGNORE INTO 操作锁(锁键, 操作, 持有者, 营业日, 时刻) VALUES (?,?,?,?,?)",
                (f"桶:{桶键}", "局部重核", 任务号, 营业日, 此刻()),
            )
            conn.commit()
        finally:
            conn.close()

    # 8) 原始任务/操作锁
    for t in data.get("局部重核任务") or []:
        conn = 连接()
        try:
            for k in ("原账户键集合", "受影响键", "新账户键集合", "变更键", "结果"):
                if k in t and not isinstance(t[k], str):
                    t[k] = json.dumps(t[k], ensure_ascii=False)
            conn.execute(
                "INSERT OR REPLACE INTO 局部重核任务(任务编号, 回放批次编号, 桶键, 原因, 营业日, "
                "状态, 局部是否允许, 原账户键集合, 受影响键, 错误, 时刻) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    t.get("任务编号") or f"任务-{此刻()}-{len(t)}",
                    t.get("回放批次编号", ""),
                    t.get("桶键", ""),
                    t.get("原因", ""),
                    t.get("营业日", 日),
                    t.get("状态", "进行中"),
                    1 if t.get("局部是否允许", True) else 0,
                    t.get("原账户键集合", "[]"),
                    t.get("受影响键", "[]"),
                    t.get("错误", ""),
                    t.get("时刻", 此刻()),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    for lk in data.get("操作锁") or []:
        conn = 连接()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO 操作锁(锁键, 操作, 持有者, 营业日, 时刻) VALUES (?,?,?,?,?)",
                (
                    lk["锁键"],
                    lk.get("操作", "局部重核"),
                    lk.get("持有者", "夹具播种"),
                    lk.get("营业日", 日),
                    lk.get("时刻", 此刻()),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    # 9) 核证回显
    核证批 = data.get("核证")
    if 核证批:
        from app.modules.差额分桶 import 分桶核证

        批 = 核证批 if isinstance(核证批, str) else data.get("回放批次编号", "")
        c = 分桶核证(批)
        概要["核证"] = {
            "valid": c["valid"],
            "桶数": c["桶数"],
            "局部是否允许": c["局部是否允许"],
            "桶集合": [
                {"原因": b["原因"], "账户数": b["账户数"], "账户键集合": b["账户键集合"], "valid": b["valid"]}
                for b in c["桶集合"]
            ],
        }
    return 概要
