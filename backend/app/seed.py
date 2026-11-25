from datetime import datetime, timezone

from app.db import 连接

建表 = """
CREATE TABLE IF NOT EXISTS 台参 (
  键 TEXT PRIMARY KEY,
  值 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS 账户 (
  账户键 TEXT PRIMARY KEY,
  户名 TEXT NOT NULL,
  币种 TEXT NOT NULL DEFAULT 'CNY',
  开户营业日 TEXT NOT NULL,
  状态 TEXT NOT NULL DEFAULT '正常'
);
CREATE TABLE IF NOT EXISTS 变动流水 (
  流水号 TEXT PRIMARY KEY,
  账户键 TEXT NOT NULL,
  营业日 TEXT NOT NULL,
  发生时 TEXT NOT NULL,
  变动额 INTEGER NOT NULL,
  摘要 TEXT NOT NULL DEFAULT '',
  批次编号 TEXT NOT NULL DEFAULT '',
  命运 TEXT NOT NULL DEFAULT '已入账'
);
CREATE TABLE IF NOT EXISTS 抽取批次 (
  编号 TEXT PRIMARY KEY,
  营业日 TEXT NOT NULL,
  时刻 TEXT NOT NULL,
  报告 TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS 周期快照 (
  快照键 TEXT PRIMARY KEY,
  账户键 TEXT NOT NULL,
  营业日 TEXT NOT NULL,
  余额 INTEGER NOT NULL,
  抽取批次编号 TEXT NOT NULL,
  来源 TEXT NOT NULL DEFAULT '日终抽取',
  状态 TEXT NOT NULL DEFAULT '有效',
  UNIQUE(账户键, 营业日)
);
CREATE TABLE IF NOT EXISTS 回放批次 (
  编号 TEXT PRIMARY KEY,
  营业日 TEXT NOT NULL,
  时刻 TEXT NOT NULL,
  报告 TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS 差额行 (
  编号 INTEGER PRIMARY KEY AUTOINCREMENT,
  回放批次编号 TEXT NOT NULL,
  账户键 TEXT NOT NULL,
  营业日 TEXT NOT NULL,
  快照余额 INTEGER,
  回放余额 INTEGER,
  差额 INTEGER NOT NULL,
  原因 TEXT NOT NULL,
  时刻 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS 断档记录 (
  编号 INTEGER PRIMARY KEY AUTOINCREMENT,
  账户键 TEXT NOT NULL,
  缺营业日 TEXT NOT NULL,
  检出批次编号 TEXT NOT NULL,
  处置 TEXT NOT NULL DEFAULT '待处理',
  时刻 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS 跳变记录 (
  编号 INTEGER PRIMARY KEY AUTOINCREMENT,
  账户键 TEXT NOT NULL,
  营业日 TEXT NOT NULL,
  前日余额 INTEGER,
  当日余额 INTEGER,
  流水净额 INTEGER,
  偏离 INTEGER NOT NULL,
  检出批次编号 TEXT NOT NULL,
  时刻 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS 旁置簿 (
  编号 INTEGER PRIMARY KEY AUTOINCREMENT,
  原因 TEXT NOT NULL,
  账户键 TEXT NOT NULL DEFAULT '',
  营业日 TEXT NOT NULL DEFAULT '',
  载荷 TEXT NOT NULL,
  时刻 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS 装载批次 (
  编号 TEXT PRIMARY KEY,
  时刻 TEXT NOT NULL,
  报告 TEXT NOT NULL DEFAULT '{}'
);
-- ===== 差额分桶（方案甲：差额桶表为权威，批号+原因→账户键集合落表）=====
CREATE TABLE IF NOT EXISTS 差额桶 (
  桶键 TEXT PRIMARY KEY,
  回放批次编号 TEXT NOT NULL,
  营业日 TEXT NOT NULL DEFAULT '',
  原因 TEXT NOT NULL,
  账户键集合 TEXT NOT NULL DEFAULT '[]',
  账户数 INTEGER NOT NULL DEFAULT 0,
  状态 TEXT NOT NULL DEFAULT '有效',
  版本 INTEGER NOT NULL DEFAULT 1,
  时刻 TEXT NOT NULL DEFAULT '',
  更新时刻 TEXT,
  UNIQUE(回放批次编号, 原因)
);
CREATE TABLE IF NOT EXISTS 差额桶账户 (
  编号 INTEGER PRIMARY KEY AUTOINCREMENT,
  桶键 TEXT NOT NULL,
  回放批次编号 TEXT NOT NULL,
  原因 TEXT NOT NULL,
  账户键 TEXT NOT NULL,
  UNIQUE(回放批次编号, 原因, 账户键)
);
CREATE INDEX IF NOT EXISTS idx_桶账户_批 ON 差额桶账户(回放批次编号, 原因);
CREATE TABLE IF NOT EXISTS 局部重核任务 (
  任务编号 TEXT PRIMARY KEY,
  回放批次编号 TEXT NOT NULL,
  桶键 TEXT NOT NULL DEFAULT '',
  原因 TEXT NOT NULL DEFAULT '',
  营业日 TEXT NOT NULL DEFAULT '',
  状态 TEXT NOT NULL DEFAULT '进行中',
  局部是否允许 INTEGER NOT NULL DEFAULT 1,
  原账户键集合 TEXT NOT NULL DEFAULT '[]',
  受影响键 TEXT NOT NULL DEFAULT '[]',
  新账户键集合 TEXT NOT NULL DEFAULT '[]',
  变更键 TEXT NOT NULL DEFAULT '[]',
  结果 TEXT NOT NULL DEFAULT '{}',
  错误 TEXT NOT NULL DEFAULT '',
  时刻 TEXT NOT NULL,
  完成时刻 TEXT
);
CREATE INDEX IF NOT EXISTS idx_重核任务_日状态 ON 局部重核任务(营业日, 状态);
CREATE TABLE IF NOT EXISTS 操作锁 (
  锁键 TEXT PRIMARY KEY,
  操作 TEXT NOT NULL,
  持有者 TEXT NOT NULL,
  营业日 TEXT NOT NULL DEFAULT '',
  时刻 TEXT NOT NULL
);
-- 表名别名（只读视图）：兼容以模块名「差额分桶」/英文名直查权威桶集合的用例
CREATE VIEW IF NOT EXISTS 差额分桶 AS SELECT * FROM 差额桶;
CREATE VIEW IF NOT EXISTS diff_buckets AS SELECT * FROM 差额桶;
"""

# 旧库增量列（CREATE TABLE IF NOT EXISTS 不会给已存在的差额行补列）
_增量列 = {
    "差额行": [
        ("状态", "TEXT NOT NULL DEFAULT '有效'"),
        ("重核时刻", "TEXT"),
        ("重核任务", "TEXT"),
    ],
}


def _迁移(conn) -> None:
    for 表, 列们 in _增量列.items():
        已有 = {r["name"] for r in conn.execute(f"PRAGMA table_info({表})").fetchall()}
        for 列, 定义 in 列们:
            if 列 not in 已有:
                conn.execute(f"ALTER TABLE {表} ADD COLUMN {列} {定义}")

空桩模块 = [
    "开户首日无前日",
    "同日重复抽取幂等",
    "流水迟到补抽",
    "销户终镜",
    "负余额闸",
    "币种隔离",
    "半截抽取回滚",
    "快照封印写闸",
    "回放权威择定",
    "断档补齐",
    "断档旁置",
    "跳变阈值",
    "账户分片抽取",
    "抽取时钟漂移",
    "流水序缺口",
    "并户继承镜",
    "子户汇总镜",
    "假期跳日日历",
    "差额分桶",
    "三日连环守恒",
    "抽取重试预算",
    "跨日切点策略",
]


def 此刻() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def 初始化库() -> None:
    conn = 连接()
    conn.executescript(建表)
    _迁移(conn)
    if conn.execute("SELECT COUNT(*) AS c FROM 台参").fetchone()["c"] == 0:
        for k, v in {
            "站名": "日镜账",
            "骨架": "日终余额抽取→周期快照落行→变动流水回放核对→断档跳变检出→快照与回放差额清单",
            "跳变阈值分": "0",
            "断档处置": "记断档并继续",
            "缺前日策略": "以零为开端",
            "重复抽取": "盖写同日快照",
        }.items():
            conn.execute("INSERT INTO 台参(键, 值) VALUES (?, ?)", (k, v))
    if conn.execute("SELECT COUNT(*) AS c FROM 账户").fetchone()["c"] == 0:
        for 键, 名, 日 in [
            ("户甲", "甲收单户", "2026-11-01"),
            ("户乙", "乙结算户", "2026-11-01"),
            ("户丙", "丙备用户", "2026-11-05"),
        ]:
            conn.execute(
                "INSERT INTO 账户(账户键, 户名, 币种, 开户营业日, 状态) VALUES (?,?,?,?,?)",
                (键, 名, "CNY", 日, "正常"),
            )
    conn.commit()
    conn.close()
