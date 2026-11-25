import React, { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api.js";

const 因甲 = "快照与回放不一致";
const 因乙 = "缺当日快照";

function 键组({ keys }) {
  const ks = Array.isArray(keys) ? keys : [];
  if (!ks.length) return <span className="淡字">∅ 空集</span>;
  return (
    <span className="chips">
      {ks.map((k) => (
        <span className="chip" key={k}>{k}</span>
      ))}
    </span>
  );
}

function 态徽章({ 状态 }) {
  const s = String(状态 || "");
  const 败 = ["拒绝", "失败", "已消解"];
  const 通 = ["完成", "有效"];
  const cls = 败.includes(s) ? "败" : 通.includes(s) ? "通" : "警";
  return <span className={`徽章 ${cls}`}>{s || "—"}</span>;
}

export default function 差额清单() {
  const [searchParams, setSearchParams] = useSearchParams();
  const 初始批 = searchParams.get("批") || "";

  const [批次列表, set批次列表] = useState([]);
  const [批, set批] = useState(初始批);
  const [原因, set原因] = useState("");
  const [行, set行] = useState([]);
  const [核证, set核证] = useState(null);
  const [任务, set任务] = useState([]);
  const [讯, set讯] = useState("");
  const [动作, set动作] = useState(null);
  const [忙因, set忙因] = useState("");
  const [忙夹具, set忙夹具] = useState("");

  const 日 = useMemo(
    () => 批次列表.find((b) => b.编号 === 批)?.营业日 || 核证?.营业日 || "",
    [批次列表, 批, 核证]
  );

  const 选批 = useCallback(
    (b) => {
      set批(b);
      set原因("");
      set动作(null);
      setSearchParams(b ? { 批: b } : {}, { replace: true });
    },
    [setSearchParams]
  );

  // 初次载入批次下拉，并在未指定 ?批= 时选最新一批
  useEffect(() => {
    let alive = true;
    api
      .回放批次()
      .then((list) => {
        if (!alive) return;
        set批次列表(list || []);
        if (!初始批 && list && list.length) 选批(list[0].编号);
      })
      .catch((e) => alive && set讯(String(e.message || e)));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const 刷新全部 = useCallback(
    async (override批) => {
      const b = override批 ?? 批;
      if (!b) {
        set核证(null);
        set任务([]);
        return;
      }
      set讯("");
      try {
        const [c, t] = await Promise.all([
          api.分桶核证({ 回放批次编号: b }),
          api.重核任务({ 回放批次编号: b }),
        ]);
        set核证(c);
        set任务(Array.isArray(t) ? t : []);
      } catch (e) {
        set讯(String(e.message || e));
      }
    },
    [批]
  );

  useEffect(() => {
    刷新全部();
  }, [刷新全部]);

  // 原因过滤走真实 GET /api/差额?原因=（服务端过滤，非仅前端分组）
  useEffect(() => {
    let alive = true;
    if (!批) {
      set行([]);
      return undefined;
    }
    api
      .差额查询({ 回放批次编号: 批, 原因 })
      .then((rows) => alive && set行(Array.isArray(rows) ? rows : []))
      .catch((e) => alive && set讯(String(e.message || e)));
    return () => {
      alive = false;
    };
  }, [批, 原因, 动作]);

  const 桶列表 = 核证?.桶集合 || [];
  const 原因选项 = useMemo(
    () => Array.from(new Set(桶列表.map((b) => b.原因).filter(Boolean))),
    [桶列表]
  );

  const 重核 = async (b) => {
    set忙因(b.原因);
    set动作(null);
    try {
      const r = await api.局部重核({ 回放批次编号: 批, 原因: b.原因 });
      set动作({ ok: true, ...r });
    } catch (e) {
      set动作({ ok: false, 文: String(e.message || e), 信封: e.data || null });
    } finally {
      set忙因("");
      await 刷新全部();
    }
  };

  const 装载验收夹具 = async (file, 目标批) => {
    set忙夹具(file);
    set动作(null);
    set讯("");
    try {
      const res = await api.装载夹具(file);
      set动作({ ok: true, 夹具: file, 装载概要: res });
      选批(目标批);
      await 刷新全部(目标批);
    } catch (e) {
      set动作({ ok: false, 文: String(e.message || e), 信封: e.data || null });
    } finally {
      set忙夹具("");
    }
  };

  const 动 = 动作;
  const 信封 = 动 && !动.ok ? 动.信封 : null;
  const 展示受影响键 = 动
    ? 动.ok
      ? 动.受影响键 || []
      : 信封?.受影响键 || []
    : [];

  return (
    <div>
      <h1 className="题">差额清单 · 原因分桶核证</h1>
      <p className="述">
        差额按原因物化到「差额桶」表，与回放批同事务成败（方案甲：桶表权威，允许单桶局部重核且只动该桶账户）。
        核证比对桶头 JSON、规范化成员表与差额行三方；半截/篡改桶会被判 invalid，局部重核失败闭。
      </p>

      <div className="行">
        <label className="淡字">回放批</label>
        <input
          list="批选项"
          value={批}
          placeholder="输入或选择回放批次编号"
          style={{ minWidth: 220 }}
          onChange={(e) => 选批(e.target.value)}
        />
        <datalist id="批选项">
          {批次列表.map((b) => (
            <option key={b.编号} value={b.编号}>
              {b.营业日}
            </option>
          ))}
        </datalist>
        <button className="次" onClick={() => 刷新全部().catch(() => {})}>
          刷新核证
        </button>
        <Link className="次 链接钮" to="/回放核对">
          去回放核对
        </Link>
        {日 ? (
          <Link className="次 链接钮" to={`/周期快照?日=${encodeURIComponent(日)}`}>
            看 {日} 快照
          </Link>
        ) : null}
      </div>

      <div className="行 夹具行">
        <span className="淡字">验收夹具：</span>
        <button
          className="次"
          disabled={!!忙夹具}
          onClick={() => 装载验收夹具("双原因夹具.json", "批-双原因")}
        >
          双原因
        </button>
        <button
          className="次"
          disabled={!!忙夹具}
          onClick={() => 装载验收夹具("局部重核夹具.json", "批-局部")}
        >
          局部重核（乙占用）
        </button>
        <button
          className="次"
          disabled={!!忙夹具}
          onClick={() => 装载验收夹具("回滚夹具.json", "批-回滚")}
        >
          回滚
        </button>
        <button
          className="次"
          disabled={!!忙夹具}
          onClick={() => 装载验收夹具("并发夹具.json", "批-并发")}
        >
          并发占用
        </button>
        {忙夹具 ? <span className="淡字">装载 {忙夹具} …</span> : null}
      </div>

      {讯 ? <p className="讯">{讯}</p> : null}

      {动 ? (
        <div className={`条 ${动.ok ? "ok" : "err"}`}>
          {动.夹具 ? (
            <div>
              <b>已装载夹具：{动.夹具}</b>
              {动.装载概要?.回滚后计数 ? (
                <div className="条内">
                  故障回放后点算（应全 0）：
                  {Object.entries(动.装载概要.回滚后计数).map(([k, v]) => (
                    <span key={k} className="mini徽章">
                      {k}={v}
                    </span>
                  ))}
                  {动.装载概要.回放错误 ? (
                    <div className="淡字">已捕获预期失败：{动.装载概要.回放错误}</div>
                  ) : null}
                </div>
              ) : null}
            </div>
          ) : null}

          {!动.夹具 && 动.ok ? (
            <div>
              <b>局部重核完成</b>
              <span className="mini徽章">任务 {动.任务编号}</span>
              <span className="mini徽章">状态 {动.状态}</span>
              <span className="mini徽章">
                局部是否允许 {String(动.局部是否允许)}
              </span>
              <div className="条内">
                <span className="淡字">受影响键（恰为该桶账户集合）：</span>
                <键组 keys={动.受影响键} />
              </div>
              <div className="条内">
                <span className="淡字">重核后桶集合：</span>
                <键组 keys={动.新账户键集合} />
              </div>
              <div className="条内">
                <span className="淡字">变更键：</span>
                <键组 keys={动.变更键} />
              </div>
            </div>
          ) : null}

          {!动.ok ? (
            <div>
              <b>局部重核被拒绝（失败闭）</b>
              {信封?.错误码 ? (
                <span className="mini徽章 败字">{信封.错误码}</span>
              ) : null}
              <div className="条内">{动.文}</div>
              {展示受影响键.length ? (
                <div className="条内">
                  <span className="淡字">受影响键：</span>
                  <键组 keys={展示受影响键} />
                </div>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}

      {!批 ? (
        <div className="空态">
          尚未选择回放批。先到 <Link to="/回放核对">回放核对</Link> 执行回放，或点上方验收夹具。
        </div>
      ) : (
        <>
          {/* 核证总览 */}
          <div className="段">
            <h2 className="段题">分桶核证</h2>
            <div className="核证头">
              {核证 ? (
                <>
                  <span className={`徽章 ${核证.valid ? "通" : "败"}`}>
                    valid={String(!!核证.valid)}
                  </span>
                  <span className="徽章 水徽">
                    架构：{核证.架构 || "差额桶表权威"}
                  </span>
                  <span className="徽章 通">
                    局部是否允许（架构甲）={String(!!核证.局部是否允许)}
                  </span>
                  <span className="淡字">
                    桶数 {核证.桶数 ?? 桶列表.length}
                    {日 ? ` · 营业日 ${日}` : ""}
                  </span>
                  {核证.错误 ? <span className="败字">{核证.错误}</span> : null}
                </>
              ) : (
                <span className="淡字">核证中…</span>
              )}
            </div>

            {!桶列表.length ? (
              <div className="空态">
                该批暂无差额桶（回放无差额，或尚未物化分桶）。可执行回放或装载双原因夹具。
              </div>
            ) : (
              <table className="桶表">
                <thead>
                  <tr>
                    <th>原因</th>
                    <th>账户键集合</th>
                    <th>户数</th>
                    <th>核证 valid</th>
                    <th>该桶局部重核</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {桶列表.map((b) => (
                    <tr key={b.桶键 || b.原因}>
                      <td>
                        <b>{b.原因}</b>
                        {b.状态 && b.状态 !== "有效" ? (
                          <div>
                            <span className={`徽章 ${b.valid ? "" : "败"}`}>{b.状态}</span>
                          </div>
                        ) : null}
                        {b.占用中 ? (
                          <div>
                            <span className="徽章 警">局部重核进行中{b.持有者 ? ` · ${b.持有者}` : ""}</span>
                          </div>
                        ) : null}
                        {Array.isArray(b.缺失) && b.缺失.length ? (
                          <div className="败字 小字">{b.缺失.join("；")}</div>
                        ) : null}
                      </td>
                      <td>
                        <键组 keys={b.账户键集合} />
                      </td>
                      <td className="数">{b.账户数 ?? b.账户键集合?.length ?? 0}</td>
                      <td>
                        <span className={`徽章 ${b.valid ? "通" : "败"}`}>
                          {String(!!b.valid)}
                        </span>
                      </td>
                      <td>
                        <span className={`徽章 ${b.局部是否允许 ? "通" : "败"}`}>
                          {String(!!b.局部是否允许)}
                        </span>
                      </td>
                      <td>
                        <button
                          className="水 小钮"
                          disabled={忙因 === b.原因}
                          onClick={() => 重核(b)}
                          title={
                            b.valid
                              ? "只重算该原因桶内全部账户"
                              : "核证未通过，后端将失败闭拒绝"
                          }
                        >
                          {忙因 === b.原因 ? "重核中…" : "局部重核"}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          {/* 原因过滤后的差额行（服务端过滤） */}
          <div className="段">
            <h2 className="段题">差额行（原因过滤）</h2>
            <div className="行">
              <select value={原因} onChange={(e) => set原因(e.target.value)}>
                <option value="">全部原因</option>
                {原因选项.map((x) => (
                  <option key={x} value={x}>
                    {x}
                  </option>
                ))}
              </select>
              <span className="淡字">{行.length} 行 · 过滤条件直接下推 /api/差额</span>
            </div>
            {!行.length ? (
              <div className="空态">当前过滤下无差额行</div>
            ) : (
              <table>
                <thead>
                  <tr>
                    <th>账户键</th>
                    <th>营业日</th>
                    <th>快照余额</th>
                    <th>回放余额</th>
                    <th>差额</th>
                    <th>原因</th>
                    <th>状态</th>
                    <th>重核任务</th>
                  </tr>
                </thead>
                <tbody>
                  {行.map((r) => (
                    <tr key={r.编号}>
                      <td>{r.账户键}</td>
                      <td>{r.营业日}</td>
                      <td>{r.快照余额 ?? "—"}</td>
                      <td>{r.回放余额 ?? "—"}</td>
                      <td className={Number(r.差额) !== 0 ? "败字" : ""}>{r.差额}</td>
                      <td>{r.原因}</td>
                      <td>
                        <态徽章 状态={r.状态 || "有效"} />
                      </td>
                      <td className="小字">{r.重核任务 || "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          {/* 局部重核任务留痕 */}
          <div className="段">
            <h2 className="段题">局部重核任务</h2>
            {!任务.length ? (
              <div className="空态">本批尚无局部重核任务</div>
            ) : (
              <table>
                <thead>
                  <tr>
                    <th>任务编号</th>
                    <th>原因</th>
                    <th>状态</th>
                    <th>受影响键</th>
                    <th>错误</th>
                    <th>时刻</th>
                  </tr>
                </thead>
                <tbody>
                  {任务.map((t) => (
                    <tr key={t.任务编号}>
                      <td className="小字">{t.任务编号}</td>
                      <td>{t.原因}</td>
                      <td>
                        <态徽章 状态={t.状态} />
                      </td>
                      <td>
                        <键组 keys={t.受影响键 || t.原账户键集合} />
                      </td>
                      <td className="败字 小字">{t.错误 || ""}</td>
                      <td className="小字">{t.时刻}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </>
      )}
    </div>
  );
}
