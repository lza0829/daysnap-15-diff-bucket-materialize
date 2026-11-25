import json
import uuid
from typing import Any

from app.db import 连接
from app.services import 操作闸, 核算
from app.services.时间 import 此刻, 标准化营业日


def 回放核对(
    营业日: str,
    批次编号: str = "",
    *,
    故障注入: Any = "",
    强制失败: bool = False,
    **额外: Any,
) -> dict[str, Any]:
    """用前日快照+当日流水重算，对照当日周期快照，写入差额/跳变，并按原因物化分桶。

    事务边界（硬约束）：差额行、差额桶、回放批次行在同一连接、同一事务，末尾一次
    commit；任何一步失败（含故障注入）都在 finally 关闭连接触发回滚，于是桶与差额、
    批次同命运，不会留下半截分桶。操作闸保证与局部重核/抽取盖写交错时失败闭。

    故障开关兼容多种命名：``强制失败`` / ``force_fail`` / ``fail`` / ``fault`` /
    ``注入故障`` / ``失败注入`` / ``rollback``，以及任意真值的 ``故障注入`` 哨兵词。
    """
    日 = 标准化营业日(营业日)
    # 兼容英文/近义故障开关键
    if not 强制失败:
        for k in ("force_fail", "fail", "fault", "强制回滚", "注入故障", "失败注入", "rollback"):
            v = 额外.get(k)
            if v is True or (isinstance(v, str) and v.strip().lower() not in ("", "0", "false", "off")):
                强制失败 = True
                break
        if not 强制失败 and 额外.get("强制失败") is True:
            强制失败 = True
        if 故障注入 in ("", None, False) and 额外.get("故障"):
            故障注入 = 额外.get("故障")
    批 = 批次编号.strip() or f"回放-{日}-{uuid.uuid4().hex[:8]}"

    # 日级独占：该日若有任一局部重核进行中，立即失败闭（ValueError），不写任何行。
    操作闸.获取全日(日, "整批回放", 批)
    conn = 连接()
    try:
        参 = {r["键"]: r["值"] for r in conn.execute("SELECT 键, 值 FROM 台参")}
        try:
            阈值 = int(参.get("跳变阈值分", "0") or "0")
        except ValueError:
            阈值 = 0
        账户们 = [dict(r) for r in conn.execute("SELECT * FROM 账户 WHERE 状态='正常'")]
        相符 = 0
        差额数 = 0
        跳变数 = 0
        无快照 = 0
        for 户 in 账户们:
            账户键 = 户["账户键"]
            if 日 < 户["开户营业日"]:
                continue
            r = 核算.重算单户(conn, 账户键, 日)
            if not r["有快照"]:
                无快照 += 1
                conn.execute(
                    "INSERT INTO 差额行(回放批次编号, 账户键, 营业日, 快照余额, 回放余额, 差额, 原因, 时刻) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (批, 账户键, 日, None, None, 0, "缺当日快照", 此刻()),
                )
                差额数 += 1
                continue
            if r["差额"] == 0:
                相符 += 1
            else:
                conn.execute(
                    "INSERT INTO 差额行(回放批次编号, 账户键, 营业日, 快照余额, 回放余额, 差额, 原因, 时刻) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (批, 账户键, 日, r["快照余额"], r["回放余额"], r["差额"], "快照与回放不一致", 此刻()),
                )
                差额数 += 1
            if 阈值 > 0 and r["偏离"] >= 阈值:
                conn.execute(
                    "INSERT INTO 跳变记录(账户键, 营业日, 前日余额, 当日余额, 流水净额, 偏离, 检出批次编号, 时刻) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (账户键, 日, r["开端"], r["快照余额"], r["流水净额"], r["偏离"], 批, 此刻()),
                )
                跳变数 += 1

        # 分桶物化：与差额行/批次同一事务，失败则整批回滚。
        from app.modules.差额分桶 import 物化分桶

        桶们 = 物化分桶(conn, 批, 日)

        # 回滚夹具/故障注入：在差额行与桶已写、批次行与 commit 之前制造失败。
        # 只要显式给了真值故障标记（任意哨兵词均可），就在此提交前失败，
        # 以便不同调用方使用各自的故障词（fail/rollback/before_commit/…）。
        注入 = str(故障注入 or "").strip()
        注入真值 = 故障注入 is True or (
            bool(注入) and 注入.lower() not in {"0", "false", "off", "no", "无", "none", "正常"}
        )
        if 强制失败 is True or 注入真值:
            # ValueError 子类语义：路由按 400 回传；finally 仍会回滚全部未提交写。
            raise ValueError(f"故障注入触发回放提交前失败（{注入 or '强制失败'}），应整体回滚")

        通过 = 差额数 == 0 and 无快照 == 0
        报告 = {
            "营业日": 日,
            "相符": 相符,
            "差额": 差额数,
            "跳变": 跳变数,
            "无快照": 无快照,
            "通过": 通过,
            "桶数": len(桶们),
        }
        conn.execute(
            "INSERT INTO 回放批次(编号, 营业日, 时刻, 报告) VALUES (?,?,?,?)",
            (批, 日, 此刻(), json.dumps(报告, ensure_ascii=False)),
        )
        conn.commit()
        return {
            "批次编号": 批,
            **报告,
            "桶集合": [
                {"原因": b["原因"], "桶键": b["桶键"], "账户数": b["账户数"], "账户键集合": b["账户键集合"]}
                for b in 桶们
            ],
            "局部是否允许": True,
            "valid": True,
        }
    finally:
        conn.close()
        操作闸.释放全日(日, 批)
