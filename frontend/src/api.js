async function 请求(path, opts = {}) {
  const r = await fetch(`/api${path}`, {
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    ...opts,
  });
  const text = await r.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (!r.ok) {
    const msg =
      (data && (data.错误 || data.detail || data.message)) ||
      text ||
      r.statusText;
    const err = new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
    err.status = r.status;
    err.data = data; // 保留完整 valid/错误码/受影响键/核证 信封
    throw err;
  }
  return data;
}

/** 拼 query string，自动跳过空值；值为数组时按多键展开。 */
function qs(参数 = {}) {
  const sp = new URLSearchParams();
  Object.entries(参数).forEach(([k, v]) => {
    if (v === undefined || v === null || v === "") return;
    sp.append(k, v);
  });
  const s = sp.toString();
  return s ? `?${s}` : "";
}

export const api = {
  健康: () => 请求("/健康"),
  总览: () => 请求("/总览"),
  台参: () => 请求("/台参"),
  账户: () => 请求("/账户"),
  流水: (q = "") => 请求(`/流水${q}`),
  快照: (q = "") => 请求(`/快照${q}`),
  差额: (q = "") => 请求(`/差额${q}`),
  /** 以参数对象按原因/批次/状态/营业日做服务端过滤（非前端分组）。 */
  差额查询: (参数 = {}) => 请求(`/差额${qs(参数)}`),
  断档: () => 请求("/断档"),
  跳变: () => 请求("/跳变"),
  旁置: () => 请求("/旁置"),
  夹具: () => 请求("/夹具"),
  装载批次: () => 请求("/装载批次"),
  抽取批次: () => 请求("/抽取批次"),
  回放批次: () => 请求("/回放批次"),
  装载夹具: (名) =>
    请求("/装载夹具", { method: "POST", body: JSON.stringify({ 名 }) }),
  抽取: (营业日, 额外 = {}) =>
    请求("/抽取", {
      method: "POST",
      body: JSON.stringify({ 营业日, ...额外 }),
    }),
  回放: (营业日, 额外 = {}) =>
    请求("/回放", {
      method: "POST",
      body: JSON.stringify({ 营业日, ...额外 }),
    }),
  重置: () => 请求("/重置业务库", { method: "POST", body: "{}" }),

  // —— 差额分桶（方案甲：差额桶表权威）· 核证 / 局部重核 / 任务 / 锁 ——
  差额桶: (参数 = {}) => 请求(`/差额桶${qs(参数)}`),
  分桶核证: (体 = {}) =>
    请求("/分桶核证", { method: "POST", body: JSON.stringify(体) }),
  /** 400 时 throw 的 err.data 即 valid:false 信封（含 错误码/受影响键/核证）。 */
  局部重核: (体 = {}) =>
    请求("/局部重核", { method: "POST", body: JSON.stringify(体) }),
  重核任务: (参数 = {}) => 请求(`/局部重核任务${qs(参数)}`),
  操作锁: (营业日 = "") => 请求(`/操作锁${qs({ 营业日 })}`),
};
