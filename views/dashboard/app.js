/* AstrBot 把 bridge-sdk.js 注入在 app.js **之后**（实测 56 行是本文件、57 行才是 SDK），
   所以脚本刚执行时 window.AstrBotPluginView 还不存在——只抓一次就会永远抓不到，
   面板会一直「读取失败」。这里每次调用都重新找，并给启动加一个等待桥接的重试。 */
let bridge = null;
function resolveBridge() {
  if (bridge) return bridge;
  const candidates = [];
  try { candidates.push(window.AstrBotPluginView, window.AstrBotPluginPage); } catch (err) {}
  try { candidates.push(window.parent.AstrBotPluginView, window.parent.AstrBotPluginPage); } catch (err) {}
  try { candidates.push(window.top.AstrBotPluginView, window.top.AstrBotPluginPage); } catch (err) {}
  bridge = candidates.find((item) => item && typeof item.apiGet === "function") || null;
  return bridge;
}
function waitForBridge(fn, tries) {
  const left = (typeof tries === "number") ? tries : 50;   // 默认最多等 6 秒
  if (resolveBridge()) { fn(); return; }
  if (left > 0) setTimeout(() => waitForBridge(fn, left - 1), 120);
}
const $ = (id) => document.getElementById(id);

function unwrap(res) {
  if (res == null) return null;
  let v = res;
  if (v && typeof v === "object" && "code" in v && "data" in v) v = v.data;
  if (v && typeof v === "object" && "data" in v && v.code === undefined) v = v.data;
  return v;
}
async function get(p) {
  const b = resolveBridge();
  try { return b ? unwrap(await b.apiGet(p)) : null; } catch (e) { return null; }
}
async function post(p, body) {
  const b = resolveBridge();
  try { return b ? unwrap(await b.apiPost(p, body || {})) : null; } catch (e) { return null; }
}
const money = (v) => "¥" + Number(v || 0).toFixed(2);

async function load() {
  const d = await get("status");
  if (!d) { $("status").textContent = "读取失败"; return; }
  if (d.link) amoLinkRender(d.link);
  const h = d.health || {};
  $("statePill").textContent = h.sick ? "生病中" : "健康";
  $("status").textContent = h.status || "健康";
  $("note").textContent = h.note || "—";
  $("treated").textContent = h.sick ? (h.treated ? "已就医" : "还没去") : "—";
  $("used").textContent = money(d.used_insurance);

  const q = d.quote || {};
  $("quoteCard").hidden = false;
  if (h.sick && q.ok) {
    $("autoHint").textContent = "她自己会去看病（钱不够会先扛着）；你催她、带她去并兜底时，才走手动就医。";
    $("total").textContent = money(q.total);
    $("reimbursed").textContent = money(q.reimbursed);
    $("selfpay").textContent = money(q.self_pay);
  } else {
    $("total").textContent = "—";
    $("reimbursed").textContent = "—";
    $("selfpay").textContent = "—";
    $("autoHint").textContent = "现在没病，不用治。";
  }
  loadHistory();
}

async function loadDrugPrices() {
  const d = await get("medicines");
  const items = (d && d.items) || [];
  const list = $("drugPrices");
  if (!items.length) { list.innerHTML = '<li class="empty">没有药价</li>'; return; }
  $("drugHint").textContent = items.length + " 种";
  list.innerHTML = items.slice(0, 24).map((it) =>
    '<li><span class="p">' + (it.name || it.key) + '</span>' +
    '<span class="s">' + money(it.price) + '</span></li>'
  ).join("");
}

async function loadHistory() {
  const d = await get("history");
  const items = (d && d.items) || [];
  const list = $("history");
  if (!items.length) { list.innerHTML = '<li class="empty">还没有记录</li>'; return; }
  list.innerHTML = items.slice(0, 20).map((it) => {
    const t = new Date((it.ts || 0) * 1000);
    const hh = String(t.getHours()).padStart(2, "0") + ":" + String(t.getMinutes()).padStart(2, "0");
    return '<li><span class="t">' + hh + '</span><span class="p">' + (it.name || "") +
      '</span><span class="s ' + (it.paid ? "ok" : "bad") + '">' +
      (it.paid ? money(it.self_pay) : "未付") + "</span></li>";
  }).join("");
}

waitForBridge(load);
waitForBridge(loadDrugPrices);
setInterval(load, 30000);
setInterval(loadDrugPrices, 300000);

/* ---- 连接状态：我这个挂载插件自己有没有连上终端/记忆库 ---- */
function amoLinkDot(ok) { return '<span style="color:' + (ok ? "#2e9e5b" : "#d83b3b") + '">\u25cf</span>'; }
function amoLinkRender(d) {
  const box = document.getElementById("linkbar");
  if (!box) return;
  box.onclick = null;
  if (!d) {
    // 失败要能自己说明白、能点重试，别只留一句「读不到」
    box.innerHTML = '连接状态：读取失败（health 接口没响应，<span style="text-decoration:underline;cursor:pointer">点这里重试</span>）';
    box.onclick = () => amoLinkLoad();
    return;
  }
  const hb = (typeof d.last_heartbeat === "number" && d.last_heartbeat >= 0)
    ? ("心跳 " + d.last_heartbeat + " 秒前") : "从未连上过";
  box.innerHTML = [
    amoLinkDot(d.terminal_connected) + " 终端" + (d.terminal_connected ? " v" + (d.terminal_version || "?") : " 未连接"),
    amoLinkDot(d.data_connected) + " 记忆库" + (d.data_connected ? " v" + (d.data_version || "?") : " 未连接"),
    "bot_id " + (d.bot_id || "\u2014"),
    hb
  ].join(' <span style="opacity:.35">\uff5c</span> ');
  box.title = d.error || "";
}

async function amoLinkFetch() {
  try { return await get("health"); } catch (e) { return null; }
}
async function amoLinkLoad() {
  // 面板脚本比 AstrBot 的桥接先跑，头几次拿不到数据是正常的：
  // 等桥接就绪再重试，别一上来就把「读不到」画给人看。
  const box = document.getElementById("linkbar");
  if (box && /检查中|读取失败/.test(box.textContent || "")) box.textContent = "连接状态：读取中…";
  for (let i = 0; i < 20; i += 1) {
    const d = await amoLinkFetch();
    if (d) { amoLinkRender(d); return; }
    await new Promise((resolve) => setTimeout(resolve, 400));
  }
  amoLinkRender(null);
}

amoLinkLoad();
setInterval(amoLinkLoad, 30000);

/* 生理期设置：双击「医疗」标题打开（数值由终端算，这里只转发） */
function fillPeriod(info) {
  const data = info || {};
  const cycle = $("periodCycle");
  const start = $("periodStart");
  if (cycle && data.cycle_days) cycle.value = String(data.cycle_days);
  if (start && data.last_start) start.value = String(data.last_start);
  const hint = $("periodHint");
  if (hint && data.phase) {
    hint.textContent = "当前：" + data.phase + (data.day ? " · 第" + data.day + "天" : "") +
      (data.next_start ? " · 下次 " + data.next_start : "");
  }
}

async function loadPeriod() {
  const info = await get("period");
  fillPeriod(info);
}

function bindPeriodDialog() {
  const title = $("brandTitle");
  const modal = $("periodModal");
  if (!title || !modal) return;
  title.addEventListener("dblclick", () => {
    modal.hidden = false;
    loadPeriod();
  });
  const close = $("periodClose");
  if (close) close.addEventListener("click", () => { modal.hidden = true; });
  modal.addEventListener("click", (event) => {
    if (event.target === modal) modal.hidden = true;
  });
  const save = $("periodSave");
  if (save) save.addEventListener("click", async () => {
    const body = { cycle_days: Number($("periodCycle").value || 0) };
    const start = $("periodStart").value;
    if (start) body.start = start;
    const info = await post("period", body);
    if (info) {
      fillPeriod(info);
      $("periodHint").textContent = "已保存：周期 " + (info.cycle_days || "?") + " 天";
    } else {
      $("periodHint").textContent = "保存失败：终端没连上";
    }
  });
}
waitForBridge(bindPeriodDialog);
waitForBridge(loadPeriod);
