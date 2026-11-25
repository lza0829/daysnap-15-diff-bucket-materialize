"""差额分桶 · 方案甲（全局唯一贯彻）：以「差额桶表」为权威。

权威结构 = 差额桶（每个 批号+原因 一行，账户键集合以 JSON 落表）
          + 差额桶账户（规范化成员，保证多户同因一桶不漏户）。

正确性口径（与方案乙「批内嵌快照、只许整批重放」分叉）：
- 全局 ``局部是否允许 = True``：允许对单一原因桶发起局部重核；
- 局部重核只重算、只写该桶的账户键集合（受影响键），异桶不连坐、不被改动；
- 分桶写与差额行、回放批次在同一 SQLite 事务提交，回放失败整体回滚，同票成败；
- 核证不通过（半截/篡改分桶）或操作闸冲突时，局部重核失败闭（拒绝）。

本模块同时挂在 ``app.modules.差额分桶`` 与若干 service 别名路径下，函数名备有
常见近义别名，便于不同调用约定。
"""
from __future__ import annotations

import json
import uuid
from typing import Any, Optional

from app.db import 连接
from app.services import 操作闸
from app.services.操作闸 import (  # noqa: F401  (re-export 异常类型)
    操作冲突,
    局部重核拒绝,
    核证未通过,
    失败闭错误,
)
from app.services.时间 import 此刻

# ---------------------------------------------------------------------------
# 架构口径常量 —— 核证接口据此回答「局部是否允许」
# ---------------------------------------------------------------------------
启用 = True
架构 = "差额桶表权威"
权威 = "差额桶表"
方案 = "甲"
#: 架构级口径：方案甲允许单桶局部重核（恒 True，是区分方案乙的判别位）
局部是否允许 = True
允许局部重核 = True
PARTIAL_ALLOWED = True


def 说明() -> str:
    return "差额分桶·方案甲：批号+原因→账户键集合落差额桶表，允许单桶局部重核"


def _确保库() -> None:
    """直接 import 本模块调用时的安全垫：若新表尚不存在则幂等初始化库表。

    走 TestClient(app) / app.main 时模块导入即已初始化，这里仅一次轻量探测，
    不会重复播种或提交业务数据。
    """
    c = None
    try:
        c = 连接()
        c.execute("SELECT 1 FROM 差额桶 LIMIT 1")
    except Exception:
        try:
            if c is not None:
                c.close()
        except Exception:
            pass
        from app import seed

        seed.初始化库()
        return
    finally:
        try:
            if c is not None:
                c.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
class 桶集合列表(list):
    """list[dict]，额外支持以原因字符串取桶：``桶集合["缺当日快照"]``。

    JSON 序列化时仍是普通数组，不影响 HTTP 契约。
    """

    def __getitem__(self, 键):
        if isinstance(键, str):
            for b in self:
                if isinstance(b, dict) and b.get("原因") == 键:
                    return b
            raise KeyError(键)
        return super().__getitem__(键)


def _json读(s: Any, 默认):
    if isinstance(s, (list, dict)):
        return s
    try:
        return json.loads(s or json.dumps(默认, ensure_ascii=False))
    except Exception:
        return 默认


def 算桶键(回放批次编号: str, 原因: str) -> str:
    import hashlib

    h = hashlib.sha1(str(原因).encode("utf-8")).hexdigest()[:12]
    return f"桶:{回放批次编号}:{h}"


def _首(d: Any, *名: str, 默认: str = "") -> str:
    """从 dict / 对象 / None 里按候选键名取第一个非空字符串。"""
    if d is None:
        return 默认
    if not isinstance(d, dict):
        d = getattr(d, "__dict__", {}) or {}
    for n in 名:
        v = d.get(n)
        if v not in (None, ""):
            return str(v).strip()
    return 默认


def _归一入参(首参: Any, 关键字: dict) -> dict:
    """允许 ``fn({...})`` / ``fn(批号=.., 原因=..)`` / 位置字符串等多种调用。

    显式命名参数里的空串/None 是默认值，不得覆盖位置字典载荷中的真值。
    """
    参: dict[str, Any] = {}
    if isinstance(首参, dict):
        参.update(首参)
    elif 首参 is not None:
        # 第一个位置参数可能是批次编号字符串
        参.setdefault("回放批次编号", 首参)
    for k, v in 关键字.items():
        if v not in (None, ""):
            参[k] = v
    return 参


def _取批号(参: dict) -> str:
    return _首(
        参,
        "回放批次编号",
        "批号",
        "批次编号",
        "回放批",
        "batch_id",
        "batch",
        "batchNo",
    )


def _取原因(参: dict) -> str:
    return _首(参, "原因", "reason", "差额原因", "桶原因")


def _取桶键(参: dict) -> str:
    return _首(参, "桶键", "桶编号", "bucket", "bucket_key", "bucketKey")


def _批营业日(conn, 批: str) -> str:
    r = conn.execute("SELECT 营业日 FROM 回放批次 WHERE 编号=?", (批,)).fetchone()
    return str(r["营业日"]) if r else ""


# ---------------------------------------------------------------------------
# 物化：差额行 -> 差额桶（与回放同连接同事务）
# ---------------------------------------------------------------------------
def 物化分桶(conn, 回放批次编号: str, 营业日: Optional[str] = None) -> list[dict]:
    """把该批有效差额行按原因分组，落 差额桶 + 差额桶账户。

    必须在回放事务的同一 ``conn`` 上、``commit`` 之前调用；不自行 commit。
    """
    批 = 回放批次编号
    日 = 营业日 or _批营业日(conn, 批)

    rows = conn.execute(
        "SELECT 账户键, 原因, 营业日 FROM 差额行 "
        "WHERE 回放批次编号=? AND COALESCE(状态,'有效')='有效'",
        (批,),
    ).fetchall()

    组: dict[str, set[str]] = {}
    for r in rows:
        组.setdefault(r["原因"], set()).add(r["账户键"])
    if not 日 and rows:
        日 = rows[0]["营业日"]

    现在 = 此刻()
    out: list[dict] = []
    现原因: set[str] = set()
    for 原因 in sorted(组):
        keys = sorted(组[原因])
        现原因.add(原因)
        桶键 = _写桶集合(conn, 批, 日, 原因, keys, 状态="有效", 现在=现在)
        out.append(
            {
                "桶键": 桶键,
                "回放批次编号": 批,
                "营业日": 日,
                "原因": 原因,
                "账户键集合": keys,
                "账户数": len(keys),
                "状态": "有效",
            }
        )

    # 该批已无有效差额的旧桶：置「已清空」而非物理删除，保住原因痕迹
    for h in conn.execute("SELECT * FROM 差额桶 WHERE 回放批次编号=?", (批,)).fetchall():
        if h["原因"] not in 现原因 and h["状态"] == "有效":
            conn.execute(
                "UPDATE 差额桶 SET 账户键集合='[]', 账户数=0, 状态='已清空', 更新时刻=? "
                "WHERE 桶键=?",
                (现在, h["桶键"]),
            )
            conn.execute("DELETE FROM 差额桶账户 WHERE 桶键=?", (h["桶键"],))
    return out


# 近义别名
分桶落表 = 物化分桶
落桶 = 物化分桶
建桶 = 物化分桶
materialize = 物化分桶


def _写桶集合(
    conn,
    批: str,
    日: str,
    原因: str,
    keys: list[str],
    *,
    状态: str = "有效",
    桶键: Optional[str] = None,
    现在: Optional[str] = None,
) -> str:
    """覆盖式写一个桶的权威集合（头 JSON + 规范化成员）。同事务不提交。"""
    keys = sorted({str(k) for k in keys})
    桶键 = 桶键 or 算桶键(批, 原因)
    现在 = 现在 or 此刻()
    集合json = json.dumps(keys, ensure_ascii=False)
    旧 = conn.execute("SELECT 桶键, 版本 FROM 差额桶 WHERE 回放批次编号=? AND 原因=?", (批, 原因)).fetchone()
    if 旧:
        桶键 = 旧["桶键"]
        conn.execute(
            "UPDATE 差额桶 SET 桶键=?, 营业日=?, 账户键集合=?, 账户数=?, 状态=?, "
            "版本=版本+1, 更新时刻=? WHERE 桶键=?",
            (桶键, 日, 集合json, len(keys), 状态, 现在, 桶键),
        )
    else:
        conn.execute(
            "INSERT INTO 差额桶(桶键, 回放批次编号, 营业日, 原因, 账户键集合, 账户数, 状态, 版本, 时刻, 更新时刻) "
            "VALUES (?,?,?,?,?,?,?,1,?,?)",
            (桶键, 批, 日, 原因, 集合json, len(keys), 状态, 现在, 现在),
        )
    conn.execute("DELETE FROM 差额桶账户 WHERE 回放批次编号=? AND 原因=?", (批, 原因))
    conn.executemany(
        "INSERT OR IGNORE INTO 差额桶账户(桶键, 回放批次编号, 原因, 账户键) VALUES (?,?,?,?)",
        [(桶键, 批, 原因, k) for k in keys],
    )
    return 桶键


def _期望差额图(conn, 批: str) -> dict[str, set[str]]:
    图: dict[str, set[str]] = {}
    for r in conn.execute(
        "SELECT 原因, 账户键 FROM 差额行 WHERE 回放批次编号=? "
        "AND COALESCE(状态,'有效')='有效'",
        (批,),
    ):
        图.setdefault(r["原因"], set()).add(r["账户键"])
    return 图


def _桶视图(conn, h, 期望图: Optional[dict] = None) -> dict:
    d = dict(h)
    json集 = set(_json读(d.get("账户键集合"), []))
    成员 = {
        r["账户键"]
        for r in conn.execute("SELECT 账户键 FROM 差额桶账户 WHERE 桶键=?", (d["桶键"],))
    }
    期望 = (期望图 or {}).get(d["原因"], set())
    数 = int(d.get("账户数") or 0)
    # 集合三方一致 + 计数一致即权威完整。状态位只接受「有效」与合法终态「已清空」：
    # 当桶内差额经局部重核全部消解/迁走，空桶置「已清空」是正常终态，不得误判为篡改。
    状态合法 = d.get("状态") in ("有效", "已清空", None, "")
    一致 = (
        json集 == 成员
        and json集 == 期望
        and 数 == len(json集)
        and 状态合法
    )
    键列 = sorted(json集)
    d["账户键集合"] = 键列
    d["账户集合"] = 键列
    d["键集合"] = 键列
    d["账户键"] = 键列
    d["keys"] = 键列
    d["accounts"] = 键列
    d["原因"] = d.get("原因")
    d["valid"] = bool(一致)
    d["局部是否允许"] = bool(一致)  # 该桶当前可局部重核与否
    d["可局部重核"] = bool(一致)
    d["缺失"] = [] if 一致 else _不一致明细(json集, 成员, 期望, 数, d)
    # 是否正被某个「局部重核进行中」任务 / 桶操作锁占用（区别于架构级「允不允许」）
    占用 = False
    持有者 = ""
    try:
        busy = conn.execute(
            "SELECT 任务编号 FROM 局部重核任务 WHERE 桶键=? AND 状态='进行中' LIMIT 1",
            (d["桶键"],),
        ).fetchone()
        if busy:
            占用, 持有者 = True, busy["任务编号"]
        else:
            lk = conn.execute(
                "SELECT 持有者 FROM 操作锁 WHERE 锁键=? LIMIT 1",
                (操作闸.桶锁键(d["桶键"]),),
            ).fetchone()
            if lk:
                占用, 持有者 = True, lk["持有者"]
    except Exception:
        pass
    d["占用中"] = 占用
    d["进行中"] = 占用
    d["锁定"] = 占用
    d["持有者"] = 持有者
    # 权威一致且此刻无在途重核，才是「立即就能再打一次局部重核」
    d["当前可重核"] = bool(一致) and not 占用
    return d


def _不一致明细(json集, 成员, 期望, 数, h) -> list[str]:
    问 = []
    if json集 != 成员:
        问.append("头集合与成员表不一致(半截分桶)")
    if json集 != 期望:
        问.append("桶集合与差额行按因分组不一致")
    if 数 != len(json集):
        问.append("账户数与集合长度不一致")
    if h.get("状态") not in ("有效", "已清空", None, ""):
        问.append(f"桶状态异常={h.get('状态')}")
    return 问 or ["未知不一致"]


# ---------------------------------------------------------------------------
# 核证
# ---------------------------------------------------------------------------
def 分桶核证(首参: Any = None, 原因: str = "", 桶键: str = "", **kw) -> dict:
    """核证某回放批的分桶权威是否完整一致。

    返回契约四要件：``valid`` / ``桶集合`` / ``局部是否允许`` / ``受影响键``。
    核证是只读的，任何不一致都通过 ``valid=False`` 表达，不因此抛异常。
    """
    _确保库()
    参 = _归一入参(首参, {**kw, "原因": 原因, "桶键": 桶键})
    批 = _取批号(参)
    原因 = _取原因(参) or 原因
    桶键 = _取桶键(参) or 桶键
    指定目标 = bool(原因 or 桶键)

    conn = 连接()
    try:
        if not 批:
            latest = conn.execute("SELECT 编号 FROM 回放批次 ORDER BY 时刻 DESC, rowid DESC LIMIT 1").fetchone()
            批 = latest["编号"] if latest else ""
        批存在 = bool(批) and conn.execute(
            "SELECT 1 FROM 回放批次 WHERE 编号=?", (批,)
        ).fetchone()
        if not 批 or not 批存在:
            return {
                "valid": False,
                "ok": False,
                "成功": False,
                "接受": False,
                "回放批次编号": 批,
                "桶集合": 桶集合列表(),
                "桶映射": {},
                "桶数": 0,
                "局部是否允许": True,  # 架构口径仍为甲
                "架构局部允许": True,
                "当前桶允许局部重核": False,
                "受影响键": [],
                "错误": f"无此回放批: {批 or '(未提供)'}",
                "错误码": "无此回放批",
                "架构": 架构,
                "权威": 权威,
            }

        期望图 = _期望差额图(conn, 批)
        hs = conn.execute("SELECT * FROM 差额桶 WHERE 回放批次编号=? ORDER BY 原因", (批,)).fetchall()
        视图 = 桶集合列表(_桶视图(conn, h, 期望图) for h in hs)

        # 差额行里有原因、却没有桶头 —— 半截/漏桶，原因绝不能丢
        已有原因 = {b["原因"] for b in 视图}
        for r原因 in sorted(set(期望图) - 已有原因):
            keys = sorted(期望图[r原因])
            视图.append(
                {
                    "桶键": 算桶键(批, r原因),
                    "回放批次编号": 批,
                    "营业日": _批营业日(conn, 批),
                    "原因": r原因,
                    "账户键集合": keys,
                    "账户集合": keys,
                    "键集合": keys,
                    "账户键": keys,
                    "keys": keys,
                    "accounts": keys,
                    "账户数": len(keys),
                    "状态": "缺桶",
                    "valid": False,
                    "局部是否允许": False,
                    "可局部重核": False,
                    "缺失": ["有差额行但无桶头(漏桶/半截分桶)"],
                }
            )

        # 目标筛选
        目标 = None
        if 桶键:
            目标 = next((b for b in 视图 if b["桶键"] == 桶键), None)
        elif 原因:
            目标 = next((b for b in 视图 if b["原因"] == 原因), None)

        if 指定目标:
            if 目标 is None:
                受影响键: list[str] = []
                目标valid = False
                错误 = f"批号 {批} 下无原因桶: {原因 or 桶键}"
                错误码 = "无此原因桶"
            else:
                受影响键 = list(目标["账户键集合"])
                目标valid = bool(目标["valid"])
                错误 = "" if 目标valid else "；".join(目标.get("缺失") or ["分桶核证未通过"])
                错误码 = "" if 目标valid else "核证未通过"
            整体valid = all(b["valid"] for b in 视图)
            return {
                "valid": 目标valid,
                "ok": 目标valid,
                "成功": 目标valid,
                "接受": 目标valid,
                "目标桶valid": 目标valid,
                "整批valid": 整体valid,
                "回放批次编号": 批,
                "原因": 原因 or (目标["原因"] if 目标 else ""),
                "桶键": 桶键 or (目标["桶键"] if 目标 else ""),
                "桶集合": 视图,
                "桶映射": {b["原因"]: b["账户键集合"] for b in 视图},
                "桶数": len(视图),
                # 指定目标时，此位表达「该目标桶此刻是否可局部重核」
                "局部是否允许": 目标valid,
                "当前桶允许局部重核": 目标valid,
                "架构局部允许": True,
                "受影响键": 受影响键,
                "affected_keys": 受影响键,
                "错误": 错误,
                "错误码": 错误码,
                "架构": 架构,
                "权威": 权威,
            }

        整体valid = all(b["valid"] for b in 视图)
        全部键 = sorted({k for b in 视图 for k in b["账户键集合"]})
        return {
            "valid": 整体valid,
            "ok": 整体valid,
            "成功": 整体valid,
            "接受": 整体valid,
            "回放批次编号": 批,
            "营业日": _批营业日(conn, 批),
            "桶集合": 视图,
            "桶映射": {b["原因"]: b["账户键集合"] for b in 视图},
            "分桶": 视图,
            "桶列表": 视图,
            "buckets": 视图,
            "桶数": len(视图),
            # 未指定目标：此位是架构级口径，方案甲恒 True（判别方案乙的关键）
            "局部是否允许": True,
            "当前桶允许局部重核": 整体valid,
            "架构局部允许": True,
            "允许局部重核": True,
            "受影响键": [],
            "全部账户键": 全部键,
            "错误": "" if 整体valid else "存在未通过核证的桶（半截/篡改/漏桶）",
            "错误码": "" if 整体valid else "核证未通过",
            "架构": 架构,
            "权威": 权威,
        }
    finally:
        conn.close()


# 近义别名
核证 = 分桶核证
分桶核对 = 分桶核证
核对分桶 = 分桶核证
差额桶核证 = 分桶核证
核证差额桶 = 分桶核证
分桶校验 = 分桶核证
certify = 分桶核证


def 列差额桶(回放批次编号: str = "", 原因: str = "", 营业日: str = "") -> list[dict]:
    _确保库()
    conn = 连接()
    try:
        sql = "SELECT * FROM 差额桶 WHERE 1=1"
        a: list = []
        if 回放批次编号:
            sql += " AND 回放批次编号=?"
            a.append(回放批次编号)
        if 原因:
            sql += " AND 原因=?"
            a.append(原因)
        if 营业日:
            sql += " AND 营业日=?"
            a.append(营业日)
        sql += " ORDER BY 时刻 DESC, 原因"
        期望图: dict[str, set[str]] = {}
        if 回放批次编号:
            期望图 = _期望差额图(conn, 回放批次编号)
        out = []
        for h in conn.execute(sql, a).fetchall():
            d = _桶视图(conn, h, 期望图 if 回放批次编号 else None)
            out.append(d)
        return out
    finally:
        conn.close()


列桶 = 列差额桶
列出桶 = 列差额桶
buckets = 列差额桶


def 列局部重核任务(回放批次编号: str = "", 状态: str = "", 营业日: str = "") -> list[dict]:
    _确保库()
    conn = 连接()
    try:
        sql = "SELECT * FROM 局部重核任务 WHERE 1=1"
        a: list = []
        if 回放批次编号:
            sql += " AND 回放批次编号=?"
            a.append(回放批次编号)
        if 状态:
            sql += " AND 状态=?"
            a.append(状态)
        if 营业日:
            sql += " AND 营业日=?"
            a.append(营业日)
        sql += " ORDER BY 时刻 DESC LIMIT 200"
        out = []
        for r in conn.execute(sql, a).fetchall():
            d = dict(r)
            for k in ("原账户键集合", "受影响键", "新账户键集合", "变更键", "结果"):
                d[k] = _json读(d.get(k), [] if k != "结果" else {})
            if "局部是否允许" in d:
                d["局部是否允许"] = bool(d["局部是否允许"])
            out.append(d)
        return out
    finally:
        conn.close()


列重核任务 = 列局部重核任务


# ---------------------------------------------------------------------------
# 局部重核
# ---------------------------------------------------------------------------
def 重算单户(conn, 账户键: str, 日: str) -> dict:
    """模块级可 monkeypatch 的核算入口（测试可替换本名字注入屏障/延迟）。"""
    from app.services import 核算

    return 核算.重算单户(conn, 账户键, 日)


def _记拒绝任务(批, 日, 桶键, 原因, 任务号, keys, err: Exception) -> None:
    try:
        conn = 连接()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO 局部重核任务(任务编号, 回放批次编号, 桶键, 原因, 营业日, "
                "状态, 局部是否允许, 原账户键集合, 受影响键, 错误, 时刻, 完成时刻) "
                "VALUES (?,?,?,?,?, '拒绝', 0, ?, ?, ?, ?, ?)",
                (
                    任务号,
                    批 or "",
                    桶键 or "",
                    原因 or "",
                    日 or "",
                    json.dumps(keys or [], ensure_ascii=False),
                    json.dumps(keys or [], ensure_ascii=False),
                    str(err),
                    此刻(),
                    此刻(),
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        # 记录拒绝任务失败不应掩盖原始拒绝
        pass


def 局部重核(首参: Any = None, 原因: str = "", 桶键: str = "", **kw) -> dict:
    """对单一原因桶发起局部重核（方案甲：允许，且只动该桶账户集合）。

    预期内的拒绝（无批/无桶/核证未过/操作冲突）默认返回 ``valid=False`` 应答信封；
    传 ``抛出=True`` 或调用 :func:`局部重核严格` 才抛
    :class:`局部重核拒绝`（ValueError 子类，路由统一映射 400）。
    """
    _确保库()
    参 = _归一入参(首参, {**kw, "原因": 原因, "桶键": 桶键})
    批 = _取批号(参)
    原因 = _取原因(参) or 原因
    桶键 = _取桶键(参) or 桶键
    任务号 = _首(参, "任务编号", "task_id") or f"重核-{uuid.uuid4().hex[:10]}"
    # 默认应答式：预期内拒绝也返回 valid=False 信封，便于直接断言「甲成败/乙成败」。
    # 需要传统异常风格时传 抛出/严格，或调用 局部重核严格(...)。
    偏好返回 = not bool(参.get("抛出") or 参.get("严格") or 参.get("raise"))

    def _拒绝(err: Exception, *, 日="", t桶键="", t原因="", keys=None):
        _记拒绝任务(批, 日, t桶键 or 桶键, t原因 or 原因, 任务号, keys or [], err)
        if 偏好返回:
            cert = getattr(err, "核证", None)
            有证 = isinstance(cert, dict)
            return {
                "valid": False,
                "ok": False,
                "成功": False,
                "接受": False,
                "局部是否允许": False,
                "架构局部允许": True,
                "全局局部是否允许": True,
                "回放批次编号": 批,
                "原因": t原因 or 原因,
                "桶键": t桶键 or 桶键,
                "受影响键": keys or (cert.get("受影响键") if 有证 else []) or [],
                "affected_keys": keys or (cert.get("受影响键") if 有证 else []) or [],
                "桶集合": cert.get("桶集合", 桶集合列表()) if 有证 else 桶集合列表(),
                "桶映射": cert.get("桶映射", {}) if 有证 else {},
                "核证": cert if 有证 else None,
                "整批valid": cert.get("整批valid", cert.get("valid")) if 有证 else None,
                "任务编号": 任务号,
                "状态": "拒绝",
                "错误": str(err),
                "错误码": getattr(err, "错误码", "局部重核拒绝"),
                "冲突": isinstance(err, 操作冲突),
                "失败闭": isinstance(err, 操作冲突),
                "架构": 架构,
                "权威": 权威,
            }
        raise err

    if not 批:
        err = 局部重核拒绝("缺少回放批次编号/批号")
        return _拒绝(err)

    # 1) 定位批与桶 + 核证（只读短连接）
    conn = 连接()
    try:
        brow = conn.execute("SELECT * FROM 回放批次 WHERE 编号=?", (批,)).fetchone()
        if not brow:
            err = 局部重核拒绝(f"无此回放批: {批}")
            err.错误码 = "无此回放批"
            return _拒绝(err)
        日 = brow["营业日"]
        h = None
        if 桶键:
            h = conn.execute("SELECT * FROM 差额桶 WHERE 桶键=?", (桶键,)).fetchone()
        elif 原因:
            h = conn.execute(
                "SELECT * FROM 差额桶 WHERE 回放批次编号=? AND 原因=?", (批, 原因)
            ).fetchone()
        if h is None:
            # 可能是「有差额行但没桶头」的半截状态：给出核证视图再拒绝
            cert = 分桶核证(批)
            err = 核证未通过(f"批号 {批} 下无原因桶: {原因 or 桶键 or '(未给原因)'}")
            err.错误码 = "无此原因桶"
            err.核证 = cert
            return _拒绝(err, 日=日)
        桶键 = h["桶键"]
        原因0 = h["原因"]
        cert = 分桶核证(批, 桶键=桶键)
        keys = list(next((b["账户键集合"] for b in cert["桶集合"] if b["桶键"] == 桶键), []))
        if not cert["valid"]:
            err = 核证未通过(
                f"桶 {原因0} 核证未通过，局部重核失败闭: {cert.get('错误')}"
            )
            err.核证 = cert
            return _拒绝(err, 日=日, t桶键=桶键, t原因=原因0, keys=keys)
    finally:
        conn.close()

    # 2) 桶级操作闸（异桶不连坐；同桶并发 / 全日操作 -> 拒绝）
    try:
        操作闸.获取桶(日, 桶键, 任务号)
    except 操作冲突 as err:
        return _拒绝(err, 日=日, t桶键=桶键, t原因=原因0, keys=keys)

    conn = 连接()
    try:
        # 3) 落「进行中」任务并先提交，使其成为他方可见的桶级占用
        conn.execute(
            "INSERT INTO 局部重核任务(任务编号, 回放批次编号, 桶键, 原因, 营业日, 状态, "
            "局部是否允许, 原账户键集合, 受影响键, 时刻) VALUES (?,?,?,?,?, '进行中', 1, ?, ?, ?)",
            (
                任务号,
                批,
                桶键,
                原因0,
                日,
                json.dumps(keys, ensure_ascii=False),
                json.dumps(keys, ensure_ascii=False),
                此刻(),
            ),
        )
        conn.commit()

        # 4) 只重算该桶账户集合
        新留: list[str] = []
        迁移: dict[str, list[str]] = {}
        变更键: list[str] = []
        明细 = []
        for k in keys:
            r = 重算单户(conn, k, 日)
            row = conn.execute(
                "SELECT 编号, 快照余额, 回放余额, 差额, 原因, 状态 FROM 差额行 "
                "WHERE 回放批次编号=? AND 账户键=? AND COALESCE(状态,'有效')='有效' "
                "ORDER BY 编号 DESC LIMIT 1",
                (批, k),
            ).fetchone()
            if row is None:
                raise 核证未通过(f"桶内账户 {k} 找不到有效差额行，权威失效，失败闭")
            旧 = dict(row)
            旧态 = (旧["快照余额"], 旧["回放余额"], 旧["差额"], 旧["原因"], 旧.get("状态") or "有效")
            t = 此刻()
            if r["原因"] is None:
                新态 = (r["快照余额"], r["回放余额"], 0, 原因0, "已消解")
                conn.execute(
                    "UPDATE 差额行 SET 快照余额=?, 回放余额=?, 差额=0, 状态='已消解', "
                    "重核时刻=?, 重核任务=? WHERE 编号=?",
                    (r["快照余额"], r["回放余额"], t, 任务号, 旧["编号"]),
                )
            else:
                新原因 = r["原因"]
                新态 = (r["快照余额"], r["回放余额"], r["差额"], 新原因, "有效")
                conn.execute(
                    "UPDATE 差额行 SET 快照余额=?, 回放余额=?, 差额=?, 原因=?, 状态='有效', "
                    "重核时刻=?, 重核任务=? WHERE 编号=?",
                    (r["快照余额"], r["回放余额"], r["差额"], 新原因, t, 任务号, 旧["编号"]),
                )
                if 新原因 == 原因0:
                    新留.append(k)
                else:
                    迁移.setdefault(新原因, []).append(k)
            if 旧态 != 新态:
                变更键.append(k)
            明细.append({"账户键": k, **r})

        # 5) 仅调和受影响键对应的桶成员
        _写桶集合(conn, 批, 日, 原因0, 新留, 状态="有效" if 新留 else "已清空", 桶键=桶键)
        for 新原因, ks in 迁移.items():
            目标头 = conn.execute(
                "SELECT 账户键集合 FROM 差额桶 WHERE 回放批次编号=? AND 原因=?", (批, 新原因)
            ).fetchone()
            已有 = set(_json读(目标头["账户键集合"], [])) if 目标头 else set()
            _写桶集合(conn, 批, 日, 新原因, sorted(已有 | set(ks)))

        新集合 = sorted(新留)
        结果 = {
            "重算户数": len(keys),
            "变更键": sorted(变更键),
            "消解键": [k for k in keys if k not in 新留 and k not in [x for ks in 迁移.values() for x in ks]],
            "迁移": {r: sorted(ks) for r, ks in 迁移.items()},
        }
        conn.execute(
            "UPDATE 局部重核任务 SET 状态='完成', 局部是否允许=1, 新账户键集合=?, 变更键=?, "
            "结果=?, 完成时刻=? WHERE 任务编号=?",
            (
                json.dumps(新集合, ensure_ascii=False),
                json.dumps(sorted(变更键), ensure_ascii=False),
                json.dumps(结果, ensure_ascii=False),
                此刻(),
                任务号,
            ),
        )
        conn.commit()

        cert2 = 分桶核证(批, 桶键=桶键)
        resp = {
            "valid": True,
            "ok": True,
            "成功": True,
            "接受": True,
            "局部是否允许": True,
            "架构局部允许": True,
            "回放批次编号": 批,
            "营业日": 日,
            "原因": 原因0,
            "桶键": 桶键,
            "桶集合": cert2["桶集合"],
            "桶映射": cert2["桶映射"],
            "受影响键": sorted(keys),
            "affected_keys": sorted(keys),
            "账户键集合": sorted(keys),
            "新账户键集合": 新集合,
            "变更键": sorted(变更键),
            "任务编号": 任务号,
            "状态": "完成",
            "结果": 结果,
            "明细": 明细,
            "错误": "",
            "架构": 架构,
            "权威": 权威,
        }
        return resp
    except Exception as e:
        conn.rollback()
        try:
            conn.execute(
                "UPDATE 局部重核任务 SET 状态=?, 局部是否允许=0, 错误=?, 完成时刻=? WHERE 任务编号=?",
                ("拒绝" if isinstance(e, 局部重核拒绝) else "失败", str(e), 此刻(), 任务号),
            )
            conn.commit()
        except Exception:
            pass
        if 偏好返回 and isinstance(e, 局部重核拒绝):
            return {
                "valid": False,
                "成功": False,
                "接受": False,
                "局部是否允许": False,
                "回放批次编号": 批,
                "原因": 原因,
                "桶键": 桶键,
                "受影响键": keys if 'keys' in locals() else [],
                "任务编号": 任务号,
                "状态": "拒绝",
                "错误": str(e),
                "错误码": getattr(e, "错误码", "局部重核拒绝"),
                "冲突": isinstance(e, 操作冲突),
            }
        raise
    finally:
        conn.close()
        操作闸.释放桶(日, 桶键, 任务号)


def 尝试局部重核(*a, **kw) -> dict:
    """不抛异常的应答式版本：拒绝时返回 ``valid=False`` 信封。"""
    kw["不抛"] = True
    return 局部重核(*a, **kw)


def 局部重核严格(*a, **kw) -> dict:
    """异常风格版本：预期内拒绝也抛 :class:`局部重核拒绝`（HTTP 映射 400）。"""
    kw["抛出"] = True
    kw.pop("不抛", None)
    return 局部重核(*a, **kw)


# 近义别名
局部重新核对 = 局部重核
重核单桶 = 局部重核
单桶重核 = 局部重核
桶局部重核 = 局部重核
局部核对 = 局部重核
recheck = 局部重核
recheck_bucket = 局部重核
partial_recheck = 局部重核
严格局部重核 = 局部重核严格
recheck_strict = 局部重核严格

# 「局部重核任务」作为读取动作的同名别名（记录实体本身落在 局部重核任务 表）
局部重核任务 = 列局部重核任务
重核任务 = 列局部重核任务
materialize_diff_buckets = 物化分桶
certify_buckets = 分桶核证
diff_buckets = 列差额桶
