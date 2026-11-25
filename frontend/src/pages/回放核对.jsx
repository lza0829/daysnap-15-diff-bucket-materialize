import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api.js";

export default function 回放核对() {
  const [日, set日] = useState("2026-11-22");
  const [批, set批] = useState([]);
  const [讯, set讯] = useState("");
  const [错信封, set错信封] = useState(null);
  const [上一果, set上一果] = useState(null);
  const [忙, set忙] = useState("");

  const 刷新 = () => api.回放批次().then(set批);

  useEffect(() => {
    刷新().catch((e) => set讯(String(e.message || e)));
  }, []);

  const 跑回放 = async (额外 = {}, 标签 = "回放") => {
    set忙(标签);
    set讯("");
    set错信封(null);
    set上一果(null);
    try {
      const r = await api.回放(日, 额外);
      set上一果(r);
      set讯(
        `批次 ${r.批次编号} 通过=${r.通过} 相符=${r.相符} 差额=${r.差额} 无快照=${r.无快照} 桶数=${r.桶数 ?? (r.桶集合 || []).length}`
      );
      await 刷新();
    } catch (e) {
      set讯(String(e.message || e));
      set错信封(e.data || null);
      await 刷新().catch(() => {});
    } finally {
      set忙("");
    }
  };

  return (
    <div>
      <h1 className="题">回放核对</h1>
      <p className="述">
        用前日快照加当日流水重算，对照周期快照写出差额行，并在同一事务内按原因物化「差额桶」。
        回放失败则差额行与桶整体回滚；该日有局部重核在途时，整批回放失败闭、不双写。
      </p>
      <div className="行">
        <input value={日} onChange={(e) => set日(e.target.value)} />
        <button className="水" disabled={!!忙} onClick={() => 跑回放({}, "回放")}>
          {忙 === "回放" ? "回放中…" : "执行回放"}
        </button>
        <button className="次" disabled={!!忙} onClick={() => 跑回放({ 故障注入: "提交前失败" }, "回滚演练")}>
          {忙 === "回滚演练" ? "注入中…" : "故障注入：提交前失败（验回滚）"}
        </button>
        <button className="次" onClick={() => 刷新().catch((e) => set讯(String(e.message || e)))}>
          刷新批次
        </button>
        <Link className="次 链接钮" to="/差额清单">
          打开差额分桶核证
        </Link>
      </div>

      {讯 ? <p className="讯">{讯}</p> : null}
      {错信封 ? (
        <div className="条 err">
          <b>回放被拒绝 / 已回滚（失败闭）</b>
          {错信封.错误码 ? <span className="mini徽章 败字">{错信封.错误码}</span> : null}
          <div className="条内">{错信封.错误 || 错信封.detail || 讯}</div>
          <div className="条内 淡字">
            未产生新批次/差额/桶；可到 <Link to="/差额清单">差额清单</Link> 核对。
          </div>
        </div>
      ) : null}

      {上一果 ? (
        <div className="条 ok">
          <div>
            <b>本次分桶（{上一果.桶数 ?? (上一果.桶集合 || []).length}）：</b>
            {(上一果.桶集合 || []).length ? (
              <span className="chips">
                {上一果.桶集合.map((b) => (
                  <Link
                    key={b.桶键 || b.原因}
                    className="chip 链接chip"
                    to={`/差额清单?批=${encodeURIComponent(上一果.批次编号)}`}
                  >
                    {b.原因} · {b.账户数}户
                  </Link>
                ))}
              </span>
            ) : (
              <span className="淡字">无差额桶（回放相符）</span>
            )}
          </div>
          <div className="条内">
            <Link
              className="次 链接钮"
              to={`/差额清单?批=${encodeURIComponent(上一果.批次编号)}`}
            >
              查看本批「{上一果.批次编号}」的桶核证 / 局部重核 →
            </Link>
          </div>
        </div>
      ) : null}

      <div className="段">
        <h2 className="段题">回放批次</h2>
        {!批.length ? (
          <div className="空态">尚无回放</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>编号</th>
                <th>营业日</th>
                <th>时刻</th>
                <th>桶数</th>
                <th>报告</th>
                <th>差额页</th>
              </tr>
            </thead>
            <tbody>
              {批.map((r) => {
                const 报 = typeof r.报告 === "object" && r.报告 ? r.报告 : {};
                return (
                  <tr key={r.编号}>
                    <td>{r.编号}</td>
                    <td>{r.营业日}</td>
                    <td>{r.时刻}</td>
                    <td className="数">{报.桶数 ?? "—"}</td>
                    <td>{typeof r.报告 === "object" ? JSON.stringify(r.报告) : r.报告}</td>
                    <td>
                      <Link to={`/差额清单?批=${encodeURIComponent(r.编号)}`}>分桶核证</Link>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
