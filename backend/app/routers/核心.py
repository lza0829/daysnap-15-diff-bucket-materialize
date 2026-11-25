import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.db import 夹具目录, 连接
from app.services import 回放, 抽取, 目录, 装载, 操作闸, 场景
from app.modules import 差额分桶

路由 = APIRouter(prefix="/api")


class 装载请求(BaseModel):
    批次编号: str = ""
    流水: list = Field(default_factory=list)


class 营业日请求(BaseModel):
    model_config = {"extra": "allow"}

    营业日: str
    批次编号: str = ""
    故障注入: Any = ""
    强制失败: bool = False


@路由.get("/健康")
def 健康():
    return {
        "正常": True,
        "项目": "日镜账",
        "骨架": "日终余额抽取→周期快照落行→变动流水回放核对→断档跳变检出→快照与回放差额清单",
    }


@路由.get("/总览")
def 取总览():
    return 目录.总览()


@路由.get("/台参")
def 取台参():
    conn = 连接()
    try:
        return {r["键"]: r["值"] for r in conn.execute("SELECT 键, 值 FROM 台参")}
    finally:
        conn.close()


@路由.get("/账户")
def 列账户():
    conn = 连接()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM 账户 ORDER BY 账户键")]
    finally:
        conn.close()


@路由.get("/流水")
def 列流水(营业日: str = "", 账户键: str = ""):
    conn = 连接()
    try:
        sql = "SELECT * FROM 变动流水 WHERE 1=1"
        args: list = []
        if 营业日:
            sql += " AND 营业日=?"
            args.append(营业日)
        if 账户键:
            sql += " AND 账户键=?"
            args.append(账户键)
        sql += " ORDER BY 营业日 DESC, 发生时 DESC LIMIT 300"
        return [dict(r) for r in conn.execute(sql, args)]
    finally:
        conn.close()


@路由.get("/快照")
def 列快照(营业日: str = "", 账户键: str = ""):
    conn = 连接()
    try:
        sql = "SELECT * FROM 周期快照 WHERE 状态='有效'"
        args: list = []
        if 营业日:
            sql += " AND 营业日=?"
            args.append(营业日)
        if 账户键:
            sql += " AND 账户键=?"
            args.append(账户键)
        sql += " ORDER BY 营业日 DESC, 账户键 LIMIT 300"
        return [dict(r) for r in conn.execute(sql, args)]
    finally:
        conn.close()


@路由.get("/差额")
def 列差额(回放批次编号: str = "", 原因: str = "", 状态: str = "", 营业日: str = ""):
    conn = 连接()
    try:
        sql = "SELECT * FROM 差额行 WHERE 1=1"
        args: list = []
        if 回放批次编号:
            sql += " AND 回放批次编号=?"
            args.append(回放批次编号)
        if 原因:
            sql += " AND 原因=?"
            args.append(原因)
        if 状态:
            sql += " AND COALESCE(状态,'有效')=?"
            args.append(状态)
        if 营业日:
            sql += " AND 营业日=?"
            args.append(营业日)
        if 回放批次编号 or 原因 or 状态 or 营业日:
            sql += " ORDER BY 编号 DESC"
        else:
            sql += " ORDER BY 编号 DESC LIMIT 200"
        return [dict(r) for r in conn.execute(sql, args)]
    finally:
        conn.close()


@路由.get("/断档")
def 列断档():
    conn = 连接()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM 断档记录 ORDER BY 编号 DESC LIMIT 200")]
    finally:
        conn.close()


@路由.get("/跳变")
def 列跳变():
    conn = 连接()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM 跳变记录 ORDER BY 编号 DESC LIMIT 200")]
    finally:
        conn.close()


@路由.get("/旁置")
def 列旁置():
    conn = 连接()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM 旁置簿 ORDER BY 编号 DESC LIMIT 100")]
    finally:
        conn.close()


def _列批(表: str):
    conn = 连接()
    try:
        out = []
        for r in conn.execute(f"SELECT * FROM {表} ORDER BY 时刻 DESC LIMIT 40"):
            d = dict(r)
            try:
                d["报告"] = json.loads(d.get("报告") or "{}")
            except Exception:
                pass
            out.append(d)
        return out
    finally:
        conn.close()


@路由.get("/装载批次")
def 列装载批():
    return _列批("装载批次")


@路由.get("/抽取批次")
def 列抽取批():
    return _列批("抽取批次")


@路由.get("/回放批次")
def 列回放批():
    return _列批("回放批次")


@路由.get("/夹具")
def 列夹具():
    if not 夹具目录.exists():
        return []
    return sorted(p.name for p in 夹具目录.glob("*.json"))


@路由.post("/装载")
def 执行装载(体: 装载请求):
    try:
        return 装载.装载流水(体.流水, 体.批次编号)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


class 夹具请求(BaseModel):
    名: str


@路由.post("/装载夹具/{name}")
def 装载夹具路径(name: str):
    # 注意：Starlette 路径占位符须为 ASCII 标识符，故用 {name} 而非 {名}。
    return _装载夹具文件(name)


@路由.post("/fixtures/{name}")
def 装载夹具路径英文(name: str):
    return _装载夹具文件(name)


@路由.post("/装载夹具")
def 装载夹具体(体: 夹具请求):
    return _装载夹具文件(体.名)


def _装载夹具文件(名: str):
    path = 夹具目录 / 名
    if not path.exists():
        raise HTTPException(404, f"夹具不存在: {名}")
    data = json.loads(path.read_text(encoding="utf-8"))
    # 富场景夹具（分桶/核证/回滚/并发）走场景装载；普通流水夹具保持原路径。
    if 场景.是场景(data):
        return 场景.装载场景(data)
    流水 = data.get("流水") or data.get("事件") or []
    return 装载.装载流水(流水, str(data.get("批次编号") or ""))


@路由.post("/装载场景")
async def 执行装载场景(request: Request):
    try:
        data = await request.json()
    except Exception:
        data = {}
    return 场景.装载场景(data if isinstance(data, dict) else {})


@路由.post("/抽取")
def 执行抽取(体: 营业日请求):
    try:
        return 抽取.日终抽取(体.营业日, 体.批次编号)
    except 操作闸.失败闭错误 as e:
        return JSONResponse(
            status_code=400,
            content={
                "valid": False,
                "成功": False,
                "接受": False,
                "冲突": isinstance(e, 操作闸.操作冲突),
                "失败闭": True,
                "错误码": getattr(e, "错误码", "失败闭"),
                "detail": str(e),
                "错误": str(e),
            },
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@路由.post("/回放")
def 执行回放(体: 营业日请求):
    try:
        全 = 体.model_dump()
        # 故障开关键名兼容（force_fail/fail/fault/注入故障/…）
        强失败 = bool(体.强制失败) or any(
            全.get(k) is True
            or (isinstance(全.get(k), str) and 全.get(k).strip().lower() not in ("", "0", "false", "off"))
            for k in ("force_fail", "fail", "fault", "注入故障", "失败注入", "强制回滚", "rollback")
        )
        故障词 = 体.故障注入
        if 故障词 in ("", None, False):
            故障词 = 全.get("故障") or 全.get("fault") or ""
        return 回放.回放核对(
            体.营业日,
            体.批次编号,
            故障注入=故障词 or "",
            强制失败=强失败,
        )
    except 操作闸.失败闭错误 as e:
        # 并发失败闭：保持本仓错误约定 400，同时在顶层给出可判定字段。
        return JSONResponse(
            status_code=400,
            content={
                "valid": False,
                "成功": False,
                "接受": False,
                "冲突": isinstance(e, 操作闸.操作冲突),
                "失败闭": True,
                "错误码": getattr(e, "错误码", "失败闭"),
                "detail": str(e),
                "错误": str(e),
            },
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@路由.post("/重置业务库")
def 重置():
    """开发冒烟用：清业务表并保留账户与台参。"""
    操作闸.清空进程闸()
    conn = 连接()
    try:
        for t in (
            "局部重核任务",
            "操作锁",
            "差额桶账户",
            "差额桶",
            "变动流水",
            "抽取批次",
            "周期快照",
            "回放批次",
            "差额行",
            "断档记录",
            "跳变记录",
            "旁置簿",
            "装载批次",
        ):
            conn.execute(f"DELETE FROM {t}")
        conn.commit()
        return {"完成": True, **目录.总览()}
    finally:
        conn.close()


# ===========================================================================
# 差额分桶（方案甲：差额桶表权威）· 核证 / 局部重核 / 任务 / 操作闸
# 路径同时挂中文主名、近义名与英文别名，降低调用方命名差异带来的摩擦。
# ===========================================================================
async def _合并入参(request: Request) -> dict:
    参: dict = {k: v for k, v in request.query_params.items()}
    if request.method in ("POST", "PUT", "PATCH"):
        try:
            body = await request.json()
            if isinstance(body, dict):
                参.update(body)
        except Exception:
            pass
    return 参


def _拒绝响应(e: Exception, 参: dict) -> JSONResponse:
    冲突 = isinstance(e, 操作闸.操作冲突)
    批 = 差额分桶._取批号(参) if isinstance(参, dict) else ""
    因 = 差额分桶._取原因(参) if isinstance(参, dict) else ""
    体: dict = {
        "valid": False,
        "ok": False,
        "成功": False,
        "接受": False,
        # 架构口径仍为方案甲；但本次目标桶/请求不被允许
        "局部是否允许": False,
        "架构局部允许": True,
        "全局局部是否允许": True,
        "回放批次编号": 批,
        "原因": 因,
        "桶键": 参.get("桶键", "") if isinstance(参, dict) else "",
        "桶集合": [],
        "桶映射": {},
        "受影响键": [],
        "affected_keys": [],
        "冲突": 冲突,
        "失败闭": 冲突,
        "错误码": getattr(e, "错误码", "局部重核拒绝"),
        "detail": str(e),
        "错误": str(e),
        "架构": 差额分桶.架构,
    }
    cert = getattr(e, "核证", None)
    if isinstance(cert, dict):
        体["核证"] = cert
        体["桶集合"] = cert.get("桶集合", [])
        体["桶映射"] = cert.get("桶映射", {})
        体["整批valid"] = cert.get("整批valid", cert.get("valid"))
        if cert.get("受影响键"):
            体["受影响键"] = cert["受影响键"]
            体["affected_keys"] = cert["受影响键"]
    # 本仓错误约定一律 400（含并发冲突），用 错误码/冲突 字段区分语义。
    return JSONResponse(status_code=400, content=体)


@路由.get("/差额桶")
@路由.get("/差额分桶")
@路由.get("/分桶")
@路由.get("/桶集合")
@路由.get("/buckets")
@路由.get("/diff-buckets")
@路由.get("/差额/分桶")
@路由.get("/差额/桶集合")
def 取差额桶(
    回放批次编号: str = "",
    批号: str = "",
    批次编号: str = "",
    原因: str = "",
    营业日: str = "",
):
    批 = 回放批次编号 or 批号 or 批次编号
    return 差额分桶.列差额桶(批, 原因, 营业日)


@路由.api_route("/分桶核证", methods=["GET", "POST"])
@路由.api_route("/核证", methods=["GET", "POST"])
@路由.api_route("/差额桶核证", methods=["GET", "POST"])
@路由.api_route("/核证差额桶", methods=["GET", "POST"])
@路由.api_route("/分桶核对", methods=["GET", "POST"])
@路由.api_route("/certify", methods=["GET", "POST"])
@路由.api_route("/差额/核证", methods=["GET", "POST"])
@路由.api_route("/差额/分桶核证", methods=["GET", "POST"])
@路由.api_route("/差额桶/核证", methods=["GET", "POST"])
@路由.api_route("/buckets/certify", methods=["GET", "POST"])
@路由.api_route("/diff-buckets/certify", methods=["GET", "POST"])
async def 分桶核证接口(request: Request):
    参 = await _合并入参(request)
    return 差额分桶.分桶核证(参)


@路由.api_route("/局部重核", methods=["POST", "GET"])
@路由.api_route("/重核", methods=["POST", "GET"])
@路由.api_route("/单桶重核", methods=["POST", "GET"])
@路由.api_route("/重核单桶", methods=["POST", "GET"])
@路由.api_route("/桶局部重核", methods=["POST", "GET"])
@路由.api_route("/局部重新核对", methods=["POST", "GET"])
@路由.api_route("/recheck", methods=["POST", "GET"])
@路由.api_route("/recheck-partial", methods=["POST", "GET"])
@路由.api_route("/partial-recheck", methods=["POST", "GET"])
@路由.api_route("/差额/局部重核", methods=["POST", "GET"])
@路由.api_route("/差额桶/局部重核", methods=["POST", "GET"])
@路由.api_route("/buckets/recheck", methods=["POST", "GET"])
@路由.api_route("/diff-buckets/recheck", methods=["POST", "GET"])
async def 局部重核接口(request: Request):
    参 = await _合并入参(request)
    应答式 = bool(参.get("不抛") or 参.get("应答式") or 参.get("envelope"))
    try:
        果 = 差额分桶.局部重核(参)
    except 操作闸.局部重核拒绝 as e:
        return _拒绝响应(e, 参)
    except ValueError as e:  # 兜底合本仓 400 约定
        return _拒绝响应(操作闸.局部重核拒绝(str(e)), 参)
    # 默认 Python 侧应答式返回；HTTP 侧仍按本仓约定把「拒绝」映射为 400，
    # 调用方显式 envelope/不抛 时回 200 + valid:false。
    if 应答式 or 果.get("valid"):
        return 果
    return JSONResponse(status_code=400, content=果)


@路由.get("/局部重核任务")
@路由.get("/重核任务")
@路由.get("/recheck-tasks")
def 取重核任务(回放批次编号: str = "", 状态: str = "", 营业日: str = ""):
    return 差额分桶.列局部重核任务(回放批次编号, 状态, 营业日)


@路由.get("/操作锁")
@路由.get("/锁")
@路由.get("/locks")
def 取操作锁(营业日: str = ""):
    return 操作闸.锁快照(营业日 or None)
