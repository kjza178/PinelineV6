// Pineline V6 UI desktop — KHÔNG polling.
//   • Một kết nối SSE /api/events: snapshot (lúc nối) → job.updated / job.removed / job.touched / stage.progress.
//   • Request HTTP CHỈ phát sinh khi: người dùng bấm nút; mở chi tiết 1 job; job đang mở / hàng chờ thật sự đổi
//     (theo event, có gộp nhịp bằng setTimeout). Để yên = 0 request.
//   • Vẽ gộp theo requestAnimationFrame → 100 event/giây vẫn chỉ ~60 lần vẽ, chỉ đụng hàng đổi.
"use strict";
const STAGES = ["download", "asr", "translate", "tts", "mux"];
const STAGE_VI = { download: "Tải nguồn", asr: "Nhận dạng tiếng (ASR)", translate: "Dịch", tts: "Lồng tiếng (TTS)", mux: "Ghép / render" };
const REDO_WARN = {
  download: "Video nguồn sẽ được TẢI LẠI từ đầu (file cũ bị thay).",
  asr: "Kết quả nhận dạng tiếng cũ bị bỏ, nhận dạng lại từ đầu (tốn GPU).",
  translate: "Bản dịch hiện tại bị XOÁ và dịch lại từ đầu — câu máy dịch/soát đều mất (câu bạn đã sửa tay được giữ).",
  tts: "Toàn bộ clip giọng đọc bị xoá và đọc lại từ đầu.",
  mux: "Video kết quả được render lại từ đầu.",
};
const jobs = new Map();          // job_id → hàng tóm tắt (từ SSE)
const dirty = new Set();
const picked = new Set();        // job_id được tick
let filter = "", rafQueued = false, fullRedraw = true;
let selected = null, detail = null, queue = { queue: [], current: null, busy: false };
// Khung chi tiết có tab. Tab Bản dịch / Giọng / Kết quả chỉ nạp dữ liệu KHI MỞ (không polling).
const TABS = [["overview", "Tổng quan"], ["script", "Bản dịch"], ["voice", "Giọng đọc"], ["result", "Kết quả"]];
let tab = "overview";
let script = null;                       // {rows, wav:Set, exists} của job đang mở
let scriptFilter = "all", scriptQ = "", scriptShown = 200;
let voices = null;                       // danh sách giọng clone (nạp 1 lần)
const PAGE = 200;                        // số câu vẽ mỗi lần (bản dịch dài hàng nghìn câu)
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// ───────────── gọi API ─────────────
async function api(method, url, body) {
  const opt = { method, headers: {} };
  if (body !== undefined) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
  let r, data = null;
  try { r = await fetch(url, opt); } catch (e) { throw new Error("không gọi được backend: " + e.message); }
  try { data = await r.json(); } catch (_) { /* thân không phải JSON */ }
  if (!r.ok || (data && data.error)) {
    const d = data && (data.error || data.detail);
    throw new Error(typeof d === "string" ? d : (d ? JSON.stringify(d) : `HTTP ${r.status}`));
  }
  return data;
}

let msgTimer = null;
function say(text, kind = "ok") {
  const el = $("#msg");
  el.textContent = text; el.className = kind; el.hidden = false;
  clearTimeout(msgTimer);
  msgTimer = setTimeout(() => { el.hidden = true; }, kind === "err" ? 9000 : 3500);
}

// Chạy 1 thao tác của nút: khoá nút trong lúc chờ (chống bấm trùng), báo kết quả.
async function act(btn, okText, fn) {
  if (btn) btn.disabled = true;
  try { await fn(); if (okText) say(okText); } catch (e) { say("Lỗi: " + e.message, "err"); } finally {
    if (btn) btn.disabled = false;
    syncToolbar();
  }
}

// Gộp nhịp: nhiều lời gọi trong `ms` → chạy 1 lần cuối (không dùng vòng hẹn giờ lặp).
function debounce(fn, ms) {
  let t = null;
  return () => { clearTimeout(t); t = setTimeout(fn, ms); };
}

// ───────────── bảng job ─────────────
function schedule() {
  if (!rafQueued) { rafQueued = true; requestAnimationFrame(paint); }
}

function inQueue(id) { return queue.current === id || (queue.queue || []).includes(id); }

const ST_VI = { running: "Đang chạy", done: "Xong", failed: "Lỗi", error: "Lỗi", held: "Tạm dừng", pending: "Chờ",
  stopped: "Đã dừng", parked: "Tạm dừng", skipped: "Bỏ qua" };
const stVi = (s) => ST_VI[s] || s || "—";
function badge(s, extra = "") { return `<span class="b ${esc(s)}">${esc(stVi(s))}${esc(extra)}</span>`; }
function stepsHtml(j) {
  return `<div class="steps">${STAGES.map((s) => { const v = j.stages?.[s] || "pending";
    return `<i class="${esc(v)}" title="${esc(STAGE_VI[s])}: ${esc(stVi(v))}"></i>`; }).join("")}</div>`;
}
function rowHtml(j) {
  const q = queue.current === j.job_id ? '<span class="q">▶ đang chạy</span>' : (inQueue(j.job_id) ? '<span class="q">trong hàng chờ</span>' : "");
  const note = j.status === "held" ? `${j.held_at || ""} ${j.hold_reason || ""}`.trim() : (j.cur_text || "");
  return `<td class="ck"><input type="checkbox" class="pick"${picked.has(j.job_id) ? " checked" : ""}></td>`
    + `<td><div class="title" title="${esc(j.title)}">${esc(j.title)}</div><div class="jid">${q}${esc(j.job_id)}</div></td>`
    + `<td>${stepsHtml(j)}</td><td>${badge(j.status)}</td><td class="note" title="${esc(note)}">${esc(note)}</td>`;
}

const RANK = { running: 0, held: 1, pending: 2, failed: 3, done: 4 };
function visible() {
  const f = filter.toLowerCase();
  return [...jobs.values()]
    .filter((j) => !f || (j.title + " " + j.job_id).toLowerCase().includes(f))
    .sort((a, b) => (RANK[a.status] ?? 9) - (RANK[b.status] ?? 9) || a.job_id.localeCompare(b.job_id));
}

function paint() {
  rafQueued = false;
  const tbody = $("#rows");
  if (fullRedraw) {
    fullRedraw = false; dirty.clear();
    const list = visible();
    tbody.innerHTML = list.map((j) => `<tr data-id="${esc(j.job_id)}"${j.job_id === selected ? ' class="sel"' : ""}>${rowHtml(j)}</tr>`).join("");
    const nRun = [...jobs.values()].filter((x) => x.status === "running").length;
    $("#count").textContent = jobs.size ? `${jobs.size} job${list.length !== jobs.size ? ` · đang hiện ${list.length}` : ""}${nRun ? ` · ${nRun} đang chạy` : ""}` : "";
    $("#nav-running").textContent = nRun || ""; $("#nav-running").hidden = !nRun;
    $("#empty").hidden = jobs.size > 0;
    $("#list table").hidden = jobs.size === 0;
    syncToolbar();
    return;
  }
  for (const id of dirty) {
    const j = jobs.get(id);
    const tr = tbody.querySelector(`tr[data-id="${CSS.escape(id)}"]`);
    if (!j) { fullRedraw = true; continue; }
    if (!tr) continue;                       // đang bị ô lọc ẩn → không cần vẽ lại cả bảng
    tr.innerHTML = rowHtml(j);
    tr.classList.remove("flash"); void tr.offsetWidth; tr.classList.add("flash");
  }
  dirty.clear();
  if (fullRedraw) schedule();
}

function upsert(row) {
  const prev = jobs.get(row.job_id);
  jobs.set(row.job_id, { ...prev, ...row });
  if (!prev || prev.status !== row.status) { fullRedraw = true; refreshQueueSoon(); }   // đổi thứ tự / hàng chờ nhích
  else dirty.add(row.job_id);
  schedule();
  if (row.job_id === selected) {
    paintDetail(); refreshDetailSoon();
    // dịch / soát vừa xong (hoặc vừa bắt đầu) → bản dịch đang mở đã cũ: nạp lại 1 lần
    const tPrev = prev?.stages?.translate, tNow = row.stages?.translate;
    if (tab === "script" && script && tPrev !== tNow) loadScript();
  }
}

function syncToolbar() {
  for (const id of [...picked]) if (!jobs.has(id)) picked.delete(id);
  const n = picked.size;
  $("#sel-count").textContent = n ? `đã chọn ${n}` : "";
  $("#btn-enqueue").disabled = n === 0;
  $("#btn-dequeue").disabled = ![...picked].some(inQueue);
  $("#btn-q-stop").disabled = !(queue.busy || queue.paused);
  const vis = visible();
  $("#ck-all").checked = vis.length > 0 && vis.every((j) => picked.has(j.job_id));
}

// ───────────── hàng chờ ─────────────
function paintQueue() {
  const cur = queue.current ? (jobs.get(queue.current)?.title || queue.current) : null;
  const n = (queue.queue || []).length;
  let text = cur ? `Đang chạy «${cur}»${n ? ` · ${n} job đang chờ` : ""}` : (n ? `${n} job đang chờ` : "Trống — chọn job rồi bấm «Chạy job đã chọn»");
  // persist_ok=false: backend không ghi được hàng chờ ra đĩa (đĩa đầy / file bị khoá) — vẫn chạy, nhưng restart sẽ mất thứ tự
  if (queue.persist_ok === false) text += " · ⚠ không lưu được hàng chờ ra đĩa (kiểm tra ổ đĩa)";
  // paused: backend tự TẠM DỪNG vì nhiều job lỗi liên tiếp (lỗi chung: GPU, đĩa, cloud, cấu hình) — R7.4
  const pz = queue.paused;
  if (pz) {
    const last = (pz.fails || []).slice(-1)[0] || {};
    text = `⏸ TẠM DỪNG: ${pz.reason || "nhiều job lỗi"} · còn ${n} job chưa chạy` + (last.detail ? ` · lỗi gần nhất: ${last.detail}` : "");
  }
  $("#q-state").textContent = text;
  $("#q-state").title = text;
  $("#q-state").classList.toggle("warn", queue.persist_ok === false || !!pz);
  $("#q-dot").className = "q-dot" + (pz || queue.persist_ok === false ? " warn" : (cur ? " run" : ""));
  $("#btn-q-resume").hidden = !pz;
  fullRedraw = true; schedule();
}
async function refreshQueue() {
  try { queue = await api("GET", "/api/queue/jobs"); paintQueue(); } catch (_) { /* SSE báo mất kết nối riêng */ }
}
const refreshQueueSoon = debounce(refreshQueue, 600);

// Nhật ký hàng chờ (file, sống qua restart): gọi 1 lần khi bấm nút.
const HIST_VI = { done: "xong", failed: "lỗi", stopped: "đã dừng", held: "tạm dừng", parked: "tạm dừng",
  waiting_review: "chờ soát", sidelined: "nhường ghế" };
async function showQueueLog() {
  const d = await api("GET", "/api/v6/queue/history?limit=200");
  const rows = (d.history || []).map((h) => `<tr><td class="nowrap">${esc(h.at || "")}</td><td><span class="b ${esc(h.status === "sidelined" ? "held" : h.status)}">${esc(HIST_VI[h.status] || h.status)}</span></td>`
    + `<td title="${esc(h.job_id)}">${esc(h.title || h.job_id)}</td><td class="note">${esc(h.detail || "")}</td></tr>`).join("");
  $("#log-rows").innerHTML = rows || '<tr><td colspan="4" class="note">Chưa có mục nào.</td></tr>';
  $("#dlg-log").showModal();
}

// ───────────── chi tiết job ─────────────
async function loadDetail(id) {
  try {
    const d = await api("GET", "/api/jobs/" + encodeURIComponent(id));
    if (id !== selected) return;             // người dùng đã chọn job khác
    detail = d; paintDetail();
  } catch (e) {
    if (id === selected) { detail = null; paintDetail(e.message); }
  }
}
// Job đang mở có thay đổi (job.updated / job.touched) → nạp lại chi tiết, tối đa 1 lần / 1,5 s.
const refreshDetailSoon = debounce(() => { if (selected) loadDetail(selected); }, 1500);

function select(id) {
  selected = (id && id !== selected) ? id : null;
  detail = null;
  tab = "overview"; script = null; scriptFilter = "all"; scriptQ = ""; scriptShown = PAGE;
  fullRedraw = true; schedule();
  paintDetail();
  if (selected) loadDetail(selected);
}

function pct(st) {
  const p = Number(st?.progress ?? st?.review_progress);
  return Number.isFinite(p) && p > 0 && p < 100 ? ` ${Math.round(p)}%` : "";
}

function paintDetail(err) {
  const box = $("#detail");
  const j = selected ? jobs.get(selected) : null;
  if (!selected || !j) { box.hidden = true; box.innerHTML = ""; delete box.dataset.job; box.classList.remove("wide"); return; }
  box.hidden = false;
  if (box.dataset.job !== j.job_id || !box.querySelector("#d-top")) {
    box.dataset.job = j.job_id;
    box.innerHTML = `<div id="d-top"></div><nav class="tabs" role="tablist">${TABS.map(([k, v]) =>
      `<button class="tab" role="tab" data-act="tab" data-tab="${k}">${v}</button>`).join("")}</nav><div id="tab-body"></div>`;
  }
  box.classList.toggle("wide", tab === "script");
  box.querySelectorAll(".tab").forEach((b) => { const on = b.dataset.tab === tab; b.classList.toggle("on", on); b.setAttribute("aria-selected", on); });
  const running = j.status === "running";
  const held = j.status === "held";
  $("#d-top").innerHTML = `
    <div class="d-head"><h2 title="${esc(j.job_id)}">${esc(j.title)}</h2><button class="btn ghost sm" data-act="close" title="Đóng">✕</button></div>
    <div class="d-id">${badge(j.status)}${inQueue(j.job_id) ? '<span class="tag info">trong hàng chờ</span>' : ""}<span>${esc(j.job_id)}</span></div>
    ${running && j.cur_text ? `<div class="sub">${esc(j.cur_text)}</div>` : ""}
    ${held ? `<div class="hold"><b>Đang tạm dừng${j.held_at ? ` ở bước ${esc(j.held_at)}` : ""}</b><br>${esc(j.hold_reason || "")}</div>` : ""}
    ${err ? `<div class="err">Không đọc được chi tiết: ${esc(err)}</div>` : ""}
    <div class="d-actions">
      ${held ? '<button class="btn primary" data-act="resume" title="Gỡ tạm dừng và chạy tiếp">Chạy tiếp</button>' : ""}
      <button class="btn primary" data-act="enqueue"${running || inQueue(j.job_id) ? " disabled" : ""} title="Thêm vào hàng chờ, chạy hết các bước theo Cài đặt">Chạy</button>
      <button class="btn danger" data-act="stop"${running ? "" : " disabled"}>Dừng</button>
      <button class="btn" data-act="reveal" title="Mở thư mục job trên máy">Mở thư mục</button>
      <span class="grow"></span>
      <button class="btn danger ghost" data-act="delete"${running ? " disabled" : ""}>Xoá</button>
    </div>`;
  if (tab === "overview") paintOverview(j);
  else if (tab === "voice") paintVoice(false);
  else if (tab === "result") paintResult(j);
  else if (tab === "script") syncScriptLock(j);      // không vẽ lại danh sách (đang gõ thì mất chữ)
}

function paintOverview(j) {
  const running = j.status === "running";
  const stages = STAGES.map((s) => {
    const sum = j.stages?.[s] || "pending";
    const st = detail?.stages?.[s] || {};
    const text = st.error ? `<div class="err">${esc(st.error)}</div>` : (st.cur_text ? `<div class="sub">${esc(st.cur_text)}</div>` : "");
    return `<li><div class="st-head"><b>${esc(STAGE_VI[s])}</b>${badge(sum, pct(st))}`
      + (sum === "done"
        ? `<button class="btn ghost sm" data-act="stage" data-stage="${s}" data-force="1"${running ? " disabled" : ""} title="Bỏ kết quả cũ của bước này và làm lại từ đầu">Làm lại</button>`
        : `<button class="btn sm" data-act="stage" data-stage="${s}"${running ? " disabled" : ""} title="Chạy riêng bước này (làm tiếp phần còn dở)">Chạy bước này</button>`)
      + `</div>${text}</li>`;
  }).join("");
  $("#tab-body").innerHTML = `<ol class="stages">${stages}</ol>`;
}

// ───────────── tab Bản dịch (D2) ─────────────
const FLAG_VI = { pronoun: "xưng hô", name: "tên", empty: "trống", cjk: "còn chữ Hán" };
function fmtTime(sec) {
  const t = Math.max(0, Number(sec) || 0), m = Math.floor(t / 60), s = Math.floor(t % 60);
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}
function translating(j) { return (j?.stages?.translate) === "running"; }

async function loadScript() {
  const id = selected;
  if (!id) return;
  const body = $("#tab-body");
  if (!script) body.innerHTML = '<p class="hint">Đang tải bản dịch…</p>';
  try {
    const d = await api("GET", `/api/v6/jobs/${encodeURIComponent(id)}/script`);
    if (id !== selected || tab !== "script") return;
    script = { rows: d.rows || [], wav: new Set(d.wav || []), exists: d.exists };
    paintScript(true);
  } catch (e) {
    if (id === selected && tab === "script") body.innerHTML = `<div class="err">Không đọc được bản dịch: ${esc(e.message)}</div>`;
  }
}

function scriptMatches(r) {
  if (scriptFilter === "flag" && !(r.s_flags && r.s_flags.length)) return false;
  if (scriptFilter === "unrev" && (r.rv || r.junk)) return false;
  if (scriptFilter === "manual" && !r.manual) return false;
  if (scriptFilter === "fixed" && !(!r.manual && (r.s_act === "fixed" || r.s_act === "new"))) return false;
  if (scriptFilter === "empty" && (r.vi || "").trim()) return false;
  if (scriptQ) {
    const q = scriptQ.toLowerCase();
    if (!((r.zh || "").toLowerCase().includes(q) || (r.vi || "").toLowerCase().includes(q) || String(r.i) === q)) return false;
  }
  return true;
}

function lineHtml(r, locked, enc) {
  const tags = [];
  for (const f of r.s_flags || []) tags.push(`<span class="tag warn">${esc(FLAG_VI[f] || f)}</span>`);
  if (r.manual) tags.push('<span class="tag ok">sửa tay</span>');
  else if (!r.rv && !r.junk) tags.push('<span class="tag">chưa soát</span>');
  if (r.unreviewed_accepted) tags.push('<span class="tag">nháp chưa soát</span>');
  if (!r.manual && r.rv && r.s_act) tags.push(`<span class="tag ${r.s_act === "pass" ? "mut" : "info"}" title="${esc(ACT_TIP[r.s_act] || "")}">${esc(ACT_VI[r.s_act] || r.s_act)}</span>`);
  if (r.s_spk && r.s_spk !== "?") tags.push(`<span class="tag mut">${esc(r.s_spk)} → ${esc(r.s_to || "?")}</span>`);
  const why = (!r.manual && r.s_why && r.s_act !== "pass") ? `<div class="why small mut">Máy sửa: ${esc(r.s_why)}</div>` : "";
  const has = script.wav.has(Number(r.i));
  return `<div class="ln" data-i="${esc(r.i)}">
    <div class="ln-meta"><b>#${esc(r.i)}</b> <span class="mut">${fmtTime(r.start)}</span> ${tags.join(" ")}</div>
    <div class="zh"><span class="zh-text">${esc(r.zh || "")}</span>${locked || r.junk ? "" : ` <button class="btn xs ghost" data-act="zh-edit" data-i="${esc(r.i)}" title="Sửa câu Trung (khi ASR nghe nhầm)">✎</button>`}</div>
    <textarea class="zh-edit" data-i="${esc(r.i)}" rows="1" spellcheck="false" hidden>${esc(r.zh || "")}</textarea>
    <textarea class="vi" data-i="${esc(r.i)}" data-junk="${r.junk ? 1 : 0}" rows="2" spellcheck="false"${locked || r.junk ? " readonly" : ""}>${esc(r.vi || "")}</textarea>${why}
    <div class="ln-act"><button class="btn sm" data-act="play" data-i="${esc(r.i)}"${has ? "" : " disabled"} title="${has ? "Nghe giọng đọc câu này" : "Câu này chưa có giọng đọc"}">▶ Nghe</button>
    <button class="btn sm" data-act="clip" data-i="${esc(r.i)}" title="Đọc lại riêng câu này bằng giọng của job">Đọc lại câu</button>
    <button class="btn sm ghost" data-act="hist" data-i="${esc(r.i)}" title="Câu này đã qua những bước nào: nháp → soát → vá → hậu kỳ (cũ → mới, lý do)">Lịch sử</button>
    <span class="st" data-st="${esc(r.i)}"></span></div><div class="hist small" data-hist="${esc(r.i)}" hidden></div></div>`;
}

const ACT_VI = { pass: "giữ nháp", fixed: "máy đã sửa", new: "dịch mới" };
const ACT_TIP = { pass: "Soát xong, bản nháp đã đúng nên giữ nguyên", fixed: "Bước soát/vá đã đổi bản nháp — xem lý do bên dưới hoặc «Lịch sử»", new: "Câu chưa có nháp, máy dịch mới từ câu Trung" };
const STEP_VI = { nmt: "nháp NMT", review: "soát", "review-retry": "soát lại (lô lỗi)", repair1: "vá lượt 1", repair2: "vá lượt 2", post: "hậu kỳ", force: "dịch lại", junk: "câu rác", unreviewed_accepted: "nhận nháp chưa soát", "review-missing": "soát câu còn thiếu" };

async function toggleHistory(btn) {
  const i = btn.dataset.i, box = document.querySelector(`[data-hist="${CSS.escape(String(i))}"]`);
  if (!box) return;
  if (!box.hidden) { box.hidden = true; return; }
  box.hidden = false; box.textContent = "đang tải…";
  try {
    const d = await api("GET", `/api/v6/jobs/${encodeURIComponent(selected)}/script/${encodeURIComponent(i)}/history`);
    if (!d.history.length) { box.innerHTML = '<span class="mut">Chưa có lịch sử (job dịch bằng luồng cũ, hoặc trace đã xoay).</span>'; return; }
    box.innerHTML = d.history.map((h) => {
      const t = h.ts ? new Date(h.ts * 1000).toLocaleString("vi-VN") : "";
      const chg = ("old" in h || "new" in h) ? `<div><s class="mut">${esc(h.old || "∅")}</s> → <b>${esc(h.new || "∅")}</b></div>` : "";
      return `<div class="h-it"><span class="tag mut">${esc(STEP_VI[h.step] || h.step)}</span> <span class="mut">${esc(t)}</span>${chg}${h.why ? `<div class="mut">${esc(h.why)}</div>` : ""}</div>`;
    }).join("") + (d.n > d.history.length ? `<div class="mut">… ${d.n - d.history.length} mục cũ hơn không hiện</div>` : "");
  } catch (e) { box.innerHTML = `<span class="err">Lỗi: ${esc(e.message)}</span>`; }
}

function paintScript(rebuild) {
  const body = $("#tab-body");
  const j = jobs.get(selected);
  if (!script || !j) return;
  if (!script.exists) {
    body.innerHTML = '<p class="hint">Job này chưa có bản dịch. Chạy bước «Dịch» ở tab Tổng quan (hoặc Xếp hàng chạy).</p>';
    return;
  }
  const rows = script.rows;
  const n = { all: rows.length, flag: 0, unrev: 0, manual: 0, empty: 0, fixed: 0 };
  for (const r of rows) {
    if (!r.manual && (r.s_act === "fixed" || r.s_act === "new")) n.fixed++;
    if (r.s_flags && r.s_flags.length) n.flag++;
    if (!r.rv && !r.junk) n.unrev++;
    if (r.manual) n.manual++;
    if (!(r.vi || "").trim()) n.empty++;
  }
  if (rebuild || !body.querySelector("#sc-list")) {
    const opt = (k, label) => `<option value="${k}"${scriptFilter === k ? " selected" : ""}>${label} (${n[k]})</option>`;
    body.innerHTML = `<div class="sc-bar">
        <select id="sc-filter" aria-label="Lọc câu">${opt("all", "Tất cả")}${opt("flag", "Bị cờ")}${opt("fixed", "Máy đã sửa")}${opt("unrev", "Chưa soát")}${opt("empty", "Trống")}${opt("manual", "Đã sửa tay")}</select>
        <input id="sc-q" placeholder="Tìm trong câu Trung / Việt, hoặc số câu…" value="${esc(scriptQ)}">
        <button class="btn sm" data-act="sc-reload" title="Nạp lại bản dịch từ đĩa">Tải lại</button>
      </div>
      <div id="sc-lock" class="hold" hidden>Đang dịch / soát — tạm khoá sửa (sửa bây giờ sẽ bị ghi đè). Bản dịch tự nạp lại khi xong.</div>
      <p class="hint">Sửa câu Việt rồi bấm ra ngoài (hoặc Ctrl+Enter) để lưu. Câu đã sửa tay không bị máy soát ghi đè. Sửa xong muốn nghe lại thì bấm «Đọc lại câu».</p>
      <div id="sc-list"></div>`;
  }
  syncScriptLock(j);
  const list = rows.filter(scriptMatches);
  const shown = list.slice(0, scriptShown);
  const locked = translating(j);
  const enc = encodeURIComponent(selected);
  $("#sc-list").innerHTML = (shown.length ? shown.map((r) => lineHtml(r, locked, enc)).join("") : '<p class="hint">Không có câu nào khớp.</p>')
    + (list.length > shown.length ? `<button class="btn more" data-act="sc-more">Xem thêm (${list.length - shown.length} câu)</button>` : "");
}

function syncScriptLock(j) {
  const lock = $("#sc-lock");
  if (!lock) return;
  const on = translating(j);
  lock.hidden = !on;
  document.querySelectorAll("#sc-list textarea.vi").forEach((t) => { t.readOnly = on || t.dataset.junk === "1"; });
}

async function saveZh(ta) {
  const i = Number(ta.dataset.i), id = selected;
  const row = script?.rows.find((r) => Number(r.i) === i);
  if (!row) return;
  const zh = ta.value.trim();
  const st = document.querySelector(`[data-st="${CSS.escape(String(i))}"]`);
  ta.hidden = true;
  if (zh === (row.zh || "").trim() || !zh) return;
  if (st) { st.textContent = "đang lưu câu Trung…"; st.className = "st"; }
  try {
    const r = await api("POST", `/api/jobs/${encodeURIComponent(id)}/script-line`, { i, zh });
    row.zh = r.zh ?? zh; row.manual = 1; delete row.rv;
    const span = ta.closest(".ln")?.querySelector(".zh-text");
    if (span) span.textContent = row.zh;
    if (st) { st.textContent = "đã lưu câu Trung — câu Việt không tự dịch lại; sửa câu Việt nếu cần"; st.className = "st ok"; }
  } catch (e) {
    ta.value = row.zh || "";
    if (st) { st.textContent = "Lỗi: " + e.message; st.className = "st err"; }
  }
}

async function saveLine(ta) {
  const i = Number(ta.dataset.i), id = selected;
  const row = script?.rows.find((r) => Number(r.i) === i);
  if (!row || ta.readOnly) return;
  const vi = ta.value.trim();
  if (vi === (row.vi || "").trim()) return;
  const st = document.querySelector(`[data-st="${CSS.escape(String(i))}"]`);
  if (st) { st.textContent = "đang lưu…"; st.className = "st"; }
  try {
    const r = await api("POST", `/api/jobs/${encodeURIComponent(id)}/script-line`, { i, vi });
    row.vi = r.vi ?? vi; row.manual = 1; delete row.rv; row.s_flags = [];
    if (st) { st.textContent = r.tts_stale ? "đã lưu · giọng đọc câu này đã cũ — bấm «Đọc lại câu»" : "đã lưu"; st.className = "st ok"; }
  } catch (e) {
    ta.value = row.vi || "";
    if (st) { st.textContent = "Lỗi: " + e.message; st.className = "st err"; }
  }
}

const player = new Audio();
function playLine(i) {
  const f = `tts/seg_${String(i).padStart(4, "0")}.wav`;
  player.src = `/api/jobs/${encodeURIComponent(selected)}/file/${f}?t=${Date.now()}`;
  player.play().catch((e) => say("Không phát được: " + e.message, "err"));
}

// ───────────── tab Giọng đọc (D6, theo job) ─────────────
async function loadVoices() {
  if (voices) return voices;
  const d = await api("GET", "/api/voices");
  voices = (d.vieneu || d.omnivoice || []).map((v) => v.id);
  return voices;
}

function paintVoice(force) {
  const body = $("#tab-body");
  if (!force && body.querySelector("form.voice") && body.contains(document.activeElement)) return;   // đang chọn → giữ nguyên
  const tts = detail?.stages?.tts || {};
  const cur = { voice: tts.voice || "", speed: tts.speed || 1, engine: tts.tts_engine || tts.engine || "vieneu" };
  const vs = voices || [];
  const opts = vs.map((v) => `<option${v === cur.voice ? " selected" : ""}>${esc(v)}</option>`).join("");
  body.innerHTML = `<form class="voice">
      <label>Giọng<select name="voice">${cur.voice && !vs.includes(cur.voice) ? `<option selected>${esc(cur.voice)}</option>` : ""}${opts || '<option value="">(chưa có giọng clone)</option>'}</select></label>
      <label>Tốc độ đọc<input name="speed" type="number" min="0.5" max="2" step="0.05" value="${esc(cur.speed)}"></label>
      <label>Bộ đọc (TTS)<select name="engine">${["vieneu", "omnivoice"].map((e) => `<option${e === cur.engine ? " selected" : ""}>${e}</option>`).join("")}</select></label>
      <div class="actions"><button class="btn" data-act="voice-sample" type="button" title="Đọc một câu mẫu bằng giọng + tốc độ đang chọn (lần đầu có thể mất vài chục giây)">▶ Nghe mẫu</button>
        <button class="btn primary" data-act="voice-save" type="button">Lưu cho job này</button></div>
      <p class="hint">Áp dụng cho lần lồng tiếng tiếp theo của job này. Đã lồng tiếng rồi thì sang tab Tổng quan bấm «Làm lại» ở bước Lồng tiếng
      (hoặc «Đọc lại câu» ở tab Bản dịch cho từng câu). Giọng mặc định cho mọi job mới: Cài đặt → Giọng đọc.</p>
    </form>
    <form class="voice clone">
      <h3>Thêm giọng clone mới</h3>
      <p class="hint">File ghi âm 3–8 giây, một người nói, rõ, không nhạc nền (wav / mp3 / m4a…). Dài hơn sẽ tự cắt. Giọng dùng chung cho mọi job.</p>
      <label>File mẫu<input type="file" class="clone-file" name="clone-file" accept="audio/*,.wav,.mp3,.m4a,.flac,.ogg,.opus,.aac"></label>
      <label>Tên giọng<input name="clone-name" maxlength="60" placeholder="vd: Ngọc Huyền"></label>
      <div class="actions"><button class="btn primary" data-act="voice-clone" type="button">Tạo giọng</button></div>
    </form>`;
}

// ───────────── tab Kết quả ─────────────
function paintResult(j) {
  const enc = encodeURIComponent(j.job_id);
  const done = j.stages?.mux === "done";
  const body = $("#tab-body");
  const cur = body.querySelector("video");
  if (cur && cur.dataset.job === j.job_id && cur.dataset.done === String(done)) return;   // đang xem → không nạp lại
  body.innerHTML = (done
    ? `<video controls preload="metadata" data-job="${esc(j.job_id)}" data-done="true" src="/api/jobs/${enc}/file/output.mp4"></video>`
    : `<p class="hint">Chưa render xong — video kết quả sẽ hiện ở đây khi bước «Ghép / render» xong.</p>`)
    + `<div class="d-actions">
         <a class="btn" href="/api/jobs/${enc}/srt/vi" download>Tải phụ đề Việt (.srt)</a>
         <a class="btn" href="/api/jobs/${enc}/srt/zh" download>Tải phụ đề Trung (.srt)</a>
         <button class="btn" data-act="reveal">Mở thư mục</button>
       </div>`;
}

async function detailAction(btn) {
  const id = selected, a = btn.dataset.act;
  if (!id) return;
  const enc = encodeURIComponent(id);
  const title = jobs.get(id)?.title || id;
  if (a === "close") return select(null);
  if (a === "tab") {
    tab = btn.dataset.tab;
    paintDetail();
    if (tab === "script") { if (script) paintScript(true); else loadScript(); }
    if (tab === "voice") { try { await loadVoices(); } catch (e) { say("Không đọc được danh sách giọng: " + e.message, "err"); } if (tab === "voice") paintVoice(true); }
    return;
  }
  if (a === "sc-reload") return loadScript();
  if (a === "sc-more") { scriptShown += PAGE; return paintScript(false); }
  if (a === "play") return playLine(btn.dataset.i);
  if (a === "hist") return toggleHistory(btn);
  if (a === "zh-edit") {
    const ta = document.querySelector(`textarea.zh-edit[data-i="${CSS.escape(btn.dataset.i)}"]`);
    if (ta) { ta.hidden = false; ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length); }
    return;
  }
  if (a === "clip") {
    const i = Number(btn.dataset.i);
    return act(btn, `Đã đọc lại câu #${i}`, async () => {
      await api("POST", `/api/jobs/${enc}/tts-clip`, { i });
      script?.wav.add(i);
      const p = document.querySelector(`button[data-act="play"][data-i="${CSS.escape(String(i))}"]`);
      if (p) p.disabled = false;
      playLine(i);
    });
  }
  if (a === "voice-sample") {
    const f = btn.closest("form");
    const q = new URLSearchParams({ engine: f.engine.value || "vieneu", voice: f.voice.value || "", speed: f.speed.value || "1" });
    if (!f.voice.value) return say("Chưa có giọng để nghe", "err");
    return act(btn, "", async () => {
      btn.textContent = "Đang đọc mẫu…";
      try {
        const r = await fetch("/api/voice-sample?" + q);
        if (!r.ok) { let m = `HTTP ${r.status}`; try { m = (await r.json()).error || m; } catch (_) { /* không phải JSON */ } throw new Error(m); }
        player.src = URL.createObjectURL(await r.blob());
        await player.play();
      } finally { btn.textContent = "▶ Nghe mẫu"; }
    });
  }
  if (a === "voice-clone") {
    const f = btn.closest("form"), file = f.elements["clone-file"].files[0], name = f.elements["clone-name"].value.trim();
    if (!file) return say("Chọn file ghi âm mẫu trước", "err");
    if (!name) return say("Đặt tên cho giọng", "err");
    return act(btn, `Đã tạo giọng «${name}»`, async () => {
      btn.textContent = "Đang tạo (có thể mất 1 phút)…";
      try {
        const fd = new FormData(); fd.append("name", name); fd.append("file", file);
        const r = await fetch("/api/clone-voice", { method: "POST", body: fd });
        let d = null; try { d = await r.json(); } catch (_) { /* không phải JSON */ }
        if (!r.ok || !d || d.error) throw new Error((d && d.error) || `HTTP ${r.status}`);
        voices = null; await loadVoices(); paintVoice(true);
      } finally { btn.textContent = "Tạo giọng"; }
    });
  }
  if (a === "voice-save") {
    const f = btn.closest("form");
    const body = { voice: f.voice.value || null, speed: Number(f.speed.value) || null, engine: f.engine.value || null };
    return act(btn, "Đã lưu giọng cho job này", async () => { await api("POST", `/api/jobs/${enc}/tts-voice`, body); await loadDetail(id); });
  }
  if (a === "enqueue") return act(btn, "Đã xếp vào hàng chờ", async () => { await api("POST", "/api/queue/jobs", { job_ids: [id] }); await refreshQueue(); });
  if (a === "resume") return act(btn, "Đã gỡ tạm dừng, chạy tiếp", async () => { await api("POST", "/api/agent/resume/" + enc); await refreshQueue(); });
  if (a === "stop") return act(btn, "Đã gửi lệnh dừng", async () => { await api("POST", `/api/jobs/${enc}/stop`); await refreshQueue(); });
  if (a === "reveal") return act(btn, "", () => api("POST", `/api/jobs/${enc}/reveal`));
  if (a === "stage") {
    const stg = btn.dataset.stage, force = btn.dataset.force === "1";
    // D3: bước đã xong — chạy không kèm force thì server coi như xong rồi (TTS chỉ đọc nốt phần thiếu) → nút
    // «Làm lại» gửi force=true và phải nói rõ sẽ mất gì.
    if (force && !confirm(`Làm lại bước «${STAGE_VI[stg]}» của «${title}»?\n\n${REDO_WARN[stg]}\n\nCác bước phía sau có thể phải chạy lại theo.`)) return;
    return act(btn, force ? `Đang làm lại bước ${STAGE_VI[stg]}` : `Đã chạy bước ${STAGE_VI[stg]}`,
      () => api("POST", `/api/jobs/${enc}/run/${stg}${force ? "?force=true" : ""}`));
  }
  if (a === "delete") {
    if (!confirm(`Xoá VĨNH VIỄN job «${title}»?\nToàn bộ thư mục job (video, âm thanh, bản dịch) sẽ bị xoá, không phục hồi được.`)) return;
    return act(btn, "Đã xoá job", async () => { 
      await api("DELETE", "/api/jobs/" + enc);
      jobs.delete(id); picked.delete(id); select(null);     // bỏ khỏi bảng ngay, không chờ event job.removed
      refreshQueueSoon();
    });
  }
}

// ───────────── thêm job ─────────────
function isLocalPath(s) { return /^[a-zA-Z]:[\\/]/.test(s) || s.startsWith("\\\\") || s.startsWith("/") || s.startsWith("~"); }

async function createJob(ev) {
  ev.preventDefault();
  const src = $("#new-src").value.trim(), errEl = $("#new-err"), ok = $("#new-ok");
  if (!src) return;
  errEl.hidden = true; ok.disabled = true; ok.textContent = "Đang tạo…";
  try {
    const r = isLocalPath(src)
      ? await api("POST", "/api/jobs/local-path", { path: src, move: $("#new-move").checked })
      : await api("POST", "/api/jobs", { url: src });
    const n = r?.count ?? (r?.job_ids || []).length ?? 1;
    $("#dlg-new").close(); $("#new-src").value = "";
    say(n > 1 ? `Đã tạo ${n} job` : "Đã tạo job");
  } catch (e) {
    errEl.textContent = e.message; errEl.hidden = false;
  } finally { ok.disabled = false; ok.textContent = "Tạo job"; }
}

// ───────────── Cài đặt: cấu hình chung cho mọi job (core/batch_config) ─────────────
// Mỗi khoá cấu hình có NHÃN + MỘT DÒNG GIẢI THÍCH bằng lời thường + kiểu điều khiển hợp lý (thanh trượt, lựa chọn có mô tả,
// công tắc). Khoá lạ (backend thêm sau) tự rơi vào mục «Nâng cao» — không bao giờ hiện tên khoá ở mục cơ bản.
const pctFmt = (v) => `${Math.round(Number(v) * 100)}%`;
const xFmt = (v) => `${Number(v).toFixed(2).replace(/0$/, "")}×`;
const FIELDS = {
  voice: { label: "Giọng mặc định", hint: "Giọng clone dùng cho job mới (cả hai bộ đọc dùng chung danh sách). Tạo giọng mới ở tab «Giọng đọc» của một job.", type: "voice" },
  speed: { label: "Tốc độ đọc", hint: "1× là tốc độ chuẩn. Nhanh quá dễ nuốt chữ.", type: "range", min: 0.8, max: 1.3, step: 0.05, fmt: xFmt },
  tts_engine: { label: "Bộ đọc", type: "radio", options: [["vieneu", "VieNeu", "Nhanh, ổn định — nên dùng"], ["omnivoice", "OmniVoice", "Giọng tự nhiên hơn, chậm hơn"]] },
  asr_engine: { label: "Nhận dạng tiếng Trung", hint: "Chuyển lời thoại gốc thành chữ trước khi dịch.", type: "select",
    options: [["nano_fa", "Nano + căn thời gian (khuyên dùng)"], ["hybrid_fa", "Kết hợp nhiều model"], ["fun_asr_nano", "Fun-ASR Nano (chỉ chữ)"],
      ["paraformer_zh", "Paraformer (chỉ thời gian)"], ["sensevoice", "SenseVoice"], ["firered_asr", "FireRed ASR"]] },
  ocr: { label: "Đọc chữ trên hình", hint: "Dùng phụ đề Trung in sẵn trên video để sửa tên, chữ nghe nhầm.", type: "switch" },
  audio_mode: { label: "Âm nền", type: "radio", options: [
    ["voice_bgm", "Giọng Việt + nhạc thư viện", "Bỏ hẳn nhạc gốc. An toàn bản quyền — nên dùng"],
    ["sfx_lib", "Tiếng động gốc + nhạc kho", "Giữ tiếng động, thay nhạc theo cảnh. An toàn bản quyền"],
    ["duck_bgm", "Nhạc thư viện + tiếng gốc nhỏ", "Giữ chút tiếng động gốc dưới nhạc mới"],
    ["voice_only", "Chỉ giọng Việt", "Không nhạc nền"],
    ["duck", "Giữ tiếng gốc, hạ khi có lời", "Có thể dính bản quyền nhạc gốc"],
    ["demucs_clean", "Nhạc gốc, bỏ giọng Trung", "Có thể dính bản quyền nhạc gốc"],
    ["demucs_keep", "Nhạc gốc + giọng Trung nhỏ", "Có thể dính bản quyền nhạc gốc"]] },
  // voice_vol của render = GIỌNG TRUNG nền (chỉ demucs_keep) — KHÔNG phải giọng Việt (giọng Việt = cfg.levels.dub = RENDER_DUB_VOL)
  voice_vol: { label: "Giọng Trung nền", hint: "Giọng Trung gốc để nhỏ phía sau giọng Việt.", type: "range", min: 0, max: 1, step: 0.01, fmt: pctFmt },
  orig_vol: { label: "Tiếng gốc nền", hint: "Tiếng gốc (đã tách giọng nếu có) tự hạ khi giọng Việt nói. Ở «Nhạc thư viện + tiếng gốc nhỏ» tối đa 28%.", type: "range", min: 0, max: 1, step: 0.01, fmt: pctFmt },
  resolution: { label: "Độ phân giải", type: "radio", cols: true, options: [["source", "Giữ như video gốc", ""], ["1080", "Full HD 1080p", "Nâng lên 1080p"]] },
  vbitrate: { label: "Chất lượng nén", hint: "Cao = đẹp hơn, file nặng hơn.", type: "select", options: [["8M", "Cao"], ["4M", "Vừa"], ["2M", "Nhẹ"]] },
  fit: { label: "Khớp giọng với thời gian câu", hint: "Co giãn câu đọc cho vừa khoảng lời gốc.", type: "switch" },
  hardsub_blur: { label: "Che phụ đề Trung có sẵn", hint: "Làm mờ vùng chữ in trên video gốc.", type: "switch" },
  zoom: { label: "Phóng to khung hình", type: "select", options: [["off", "Không"], ["1.10", "10%"], ["1.12", "12%"], ["1.15", "15%"]], adv: "video" },
  motion: { label: "Chuyển động nhẹ", hint: "Khung hình trôi chậm, giúp khác video gốc.", type: "switch", adv: "video" },
  lut: { label: "Chỉnh màu", type: "switch", adv: "video" },
  grain: { label: "Hạt phim", type: "select", options: [["off", "Tắt"], ["weak", "Nhẹ"], ["medium", "Vừa"], ["strong", "Mạnh"]], adv: "video" },
  flip: { label: "Lật hình", type: "select", options: [["", "Không"], ["h", "Ngang"], ["v", "Dọc"], ["hv", "Cả hai"]], adv: "video" },
  render_speed: { label: "Tốc độ phát video", hint: "Tăng nhẹ tốc độ để khác video gốc.", type: "range", min: 0.9, max: 1.2, step: 0.01, fmt: xFmt, adv: "video" },
  brand_mode: { label: "Hiện tên kênh", type: "radio", options: [["off", "Không hiện", ""], ["drift", "Chữ trôi chậm", "Trôi quanh màn hình"],
    ["corners", "Đổi góc", "Lần lượt bốn góc"], ["drift_seed", "Chữ trôi ngẫu nhiên", "Mỗi video một đường trôi khác"]] },
  brand_text: { label: "Tên kênh", hint: "Chữ hiện trên video.", type: "text", placeholder: "vd: Phim Hay Mỗi Ngày" },
  brand_logo: { label: "Logo", type: "select", options: [["off", "Không"], ["auto", "Tự động (logo trong thư mục assets)"]] },
  frame: { label: "Khung viền", type: "select", options: [["off", "Không"], ["auto", "Tự động"]] },
  // Chỉ luồng dịch cũ (translate_pipeline=legacy) đọc hai khoá này — luồng V6 tự chọn cỡ lô theo model → không hiện.
  translate_mode: { hidden: true },
  review_backup: { hidden: true },
};
const CFG_PANES = [
  ["voice", "Giọng đọc", "Giọng và tốc độ lồng tiếng mặc định cho job mới.", [["Lồng tiếng", ["voice", "speed", "tts_engine"]], ["Nhận dạng lời thoại gốc", ["asr_engine", "ocr"]]]],
  ["audio", "Âm thanh", "Trộn giọng Việt với nhạc và tiếng động.", [["Âm nền", ["audio_mode"]], ["Âm lượng", ["voice_vol", "orig_vol"]]]],
  ["video", "Hình ảnh", "Chất lượng video xuất ra và các hiệu ứng giúp video khác bản gốc.", [["Xuất video", ["resolution", "vbitrate", "fit", "hardsub_blur"]]]],
  ["brand", "Thương hiệu", "Tên kênh, logo và khung viền trên video.", [["Nhận diện kênh", ["brand_mode", "brand_text", "brand_logo", "frame"]]]],
];
let cfg = null;

// ── Âm thanh: chế độ âm nền + CHỈ những mức âm lượng mà chế độ đó dùng (engines/render._build_audio_filter) ──
const SAFE_AUDIO = ["voice_bgm", "sfx_lib", "duck_bgm", "voice_only"];
const LEVEL_HINT = {
  dub: "100% = nguyên mức của bộ đọc. Mặc định 125%.",
  bgm: "Nhạc lấy từ thư mục assets/bgm.",
  lib_music: "Nhạc kho theo cảnh (assets/bgm_yt), tự hạ khi có lời.",
  sfx: "Tiếng động tách từ video gốc, tự hạ khi có lời.",
};
const VOL_ROWS = { orig_vol: ["duck", "duck_bgm"], voice_vol: ["demucs_keep"] };
function levelRow(name, label, hint, value, min, max, modes) {
  const n = Number(value);
  return `<div class="field" data-modes="${esc(modes.join(" "))}"><div class="lbl"><b>${esc(label)}</b>${hint ? `<span>${esc(hint)}</span>` : ""}</div>`
    + `<div class="ctl"><div class="range"><input type="range" name="${esc(name)}" min="${min}" max="${max}" step="0.01" value="${esc(n)}"><output>${esc(pctFmt(n))}</output></div></div></div>`;
}
function audioPane() {
  const v = String(cfg.opts.audio_mode ?? "");
  const opt = ([val, t, d]) => `<label class="choice"><input type="radio" name="audio_mode" value="${esc(val)}"${v === val ? " checked" : ""}><div><b>${esc(t)}</b>${d ? `<span>${esc(d)}</span>` : ""}</div></label>`;
  const all = FIELDS.audio_mode.options, safe = all.filter((o) => SAFE_AUDIO.includes(o[0])), risky = all.filter((o) => !SAFE_AUDIO.includes(o[0]));
  const rows = Object.entries(cfg.levels || {}).map(([k, L]) => levelRow("lvl_" + k, L.label, LEVEL_HINT[k], L.value, L.min, L.max, L.modes));
  for (const [k, modes] of Object.entries(VOL_ROWS)) if (k in cfg.defaults) rows.push(levelRow(k, FIELDS[k].label, FIELDS[k].hint, cfg.opts[k] ?? cfg.defaults[k], 0, 1, modes));
  return `<div class="sec"><h3>Âm nền</h3><div class="field stack"><div class="ctl"><div class="choices">${safe.map(opt).join("")}</div>`
    + `<details class="more"${risky.some((o) => o[0] === v) ? " open" : ""}><summary>Giữ nhạc gốc của phim (dễ dính bản quyền)</summary><div class="inner"><div class="choices">${risky.map(opt).join("")}</div></div></details></div></div></div>`
    + `<div class="sec"><h3>Âm lượng</h3><p class="sub">Chỉ hiện các mức mà chế độ âm nền đang chọn dùng tới.</p><div id="vol-rows">${rows.join("")}</div></div>`;
}
function syncAudioRows() {
  const m = document.querySelector('#form-cfg input[name="audio_mode"]:checked')?.value || "";
  document.querySelectorAll("#vol-rows [data-modes]").forEach((r) => { const ms = r.dataset.modes.split(" "); r.hidden = !(ms.includes("*") || ms.includes(m)); });
}
function levelsPatch() {
  const form = $("#form-cfg"), out = {};
  for (const [k, L] of Object.entries(cfg?.levels || {})) {
    const el = form.elements["lvl_" + k];
    if (el && Math.abs(Number(el.value) - Number(L.value)) > 1e-9) out[k] = Number(el.value);
  }
  return out;
}

function asBool(v) { return v === true || ["1", "true", "on", "yes"].includes(String(v).toLowerCase()); }
function cfgControl(k, f) {
  const v = cfg.opts[k], name = `name="${esc(k)}"`;
  if (f.type === "switch") return `<label class="switch"><input type="checkbox" ${name}${asBool(v) ? " checked" : ""}><span></span>${asBool(v) ? "Bật" : "Tắt"}</label>`;
  if (f.type === "range") {
    const n = Number(v ?? cfg.defaults[k]);
    return `<div class="range"><input type="range" ${name} min="${f.min}" max="${f.max}" step="${f.step}" value="${esc(n)}" data-fmt="${esc(k)}"><output>${esc(f.fmt(n))}</output></div>`;
  }
  if (f.type === "radio") {
    return `<div class="choices${f.cols ? " cols" : ""}">${f.options.map(([val, t, d]) =>
      `<label class="choice"><input type="radio" ${name} value="${esc(val)}"${String(v) === val ? " checked" : ""}><div><b>${esc(t)}</b>${d ? `<span>${esc(d)}</span>` : ""}</div></label>`).join("")}</div>`;
  }
  if (f.type === "voice") {
    const list = voices && voices.length ? (voices.includes(v) ? voices : [v, ...voices].filter(Boolean)) : [v].filter(Boolean);
    return `<div class="row-ctl"><select ${name}>${list.map((x) => `<option${x === v ? " selected" : ""}>${esc(x)}</option>`).join("") || '<option value="">(chưa có giọng)</option>'}</select>`
      + `<button type="button" class="btn sm" id="cfg-sample" title="Đọc một câu mẫu bằng giọng, tốc độ và bộ đọc đang chọn (lần đầu có thể mất vài chục giây)">▶ Nghe thử</button></div>`;
  }
  if (f.type === "select") {
    const opts = f.options.some(([val]) => val === String(v ?? "")) ? f.options : [...f.options, [String(v), String(v)]];
    return `<select ${name}>${opts.map(([val, t]) => `<option value="${esc(val)}"${String(v ?? "") === val ? " selected" : ""}>${esc(t)}</option>`).join("")}</select>`;
  }
  return `<input ${name} value="${esc(v ?? "")}" placeholder="${esc(f.placeholder || "")}">`;
}
function cfgField(k, solo = false) {
  const f = FIELDS[k] || { label: cfg.labels[k] || k, type: typeof cfg.defaults[k] === "boolean" ? "switch" : "text" };
  if (solo || f.type === "radio") {      // lựa chọn có mô tả: trải hết chiều ngang, nhãn ở trên (không lặp tiêu đề mục)
    return `<div class="field stack">${solo ? (f.hint ? `<p class="sub">${esc(f.hint)}</p>` : "")
      : `<div class="lbl"><b>${esc(f.label)}</b>${f.hint ? `<span>${esc(f.hint)}</span>` : ""}</div>`}<div class="ctl">${cfgControl(k, f)}</div></div>`;
  }
  return `<div class="field"><div class="lbl"><b>${esc(f.label)}</b>${f.hint ? `<span>${esc(f.hint)}</span>` : ""}</div><div class="ctl">${cfgControl(k, f)}</div></div>`;
}
function cfgSection(title, keys, sub) {
  const ks = keys.filter((k) => k in cfg.defaults);
  const solo = ks.length === 1 && (FIELDS[ks[0]]?.label === title);
  const fs = ks.map((k) => cfgField(k, solo)).join("");
  return fs ? `<div class="sec"><h3>${esc(title)}</h3>${sub ? `<p class="sub">${esc(sub)}</p>` : ""}${fs}</div>` : "";
}
function paintCfg() {
  const used = new Set([...CFG_PANES.flatMap(([, , , secs]) => secs.flatMap(([, ks]) => ks)), ...Object.keys(FIELDS).filter((k) => FIELDS[k].adv || FIELDS[k].hidden)]);
  const extra = Object.keys(cfg.defaults).filter((k) => !used.has(k));
  const advVideo = Object.keys(FIELDS).filter((k) => FIELDS[k].adv === "video");
  $("#cfg-body").innerHTML = CFG_PANES.map(([id, title, sub, secs]) => {
    let html = id === "audio" ? audioPane() : secs.map(([t, ks]) => cfgSection(t, ks)).join("");
    if (id === "video") {
      html += `<div class="sec"><details class="more"><summary>Chống trùng nội dung (phóng to, chuyển động, chỉnh màu, lật…)</summary>
        <div class="inner">${advVideo.filter((k) => k in cfg.defaults).map(cfgField).join("")}</div></details></div>`;
    }
    return `<div class="pane" data-pane="${id}" hidden><h2>${esc(title)}</h2><p class="sub">${esc(sub)}</p>${html}</div>`;
  }).join("") + (extra.length ? `<div class="pane" data-pane="advanced" hidden>${cfgSection("Cấu hình chung khác", extra, "Các tuỳ chọn ít dùng hoặc của luồng cũ.")}</div>` : "");
  $("#cfg-meta").textContent = cfg.updated_at ? `Áp dụng cho job chạy sau khi lưu · sửa lần cuối ${String(cfg.updated_at).replace("T", " ").slice(0, 16)}${cfg.updated_by ? ` (${cfg.updated_by})` : ""}` : "Áp dụng cho job chạy sau khi lưu.";
  $("#cfg-bar").hidden = true; $("#cfg-err").hidden = true;
  syncAudioRows();
  showPane(curPane);
}
function cfgPatch() {
  const form = $("#form-cfg"), patch = {};
  for (const k of Object.keys(cfg.defaults)) {
    const el = form.elements[k];
    if (!el) continue;
    let v = el.type === "checkbox" ? el.checked : el.value;
    if (typeof cfg.defaults[k] === "number" && el.type !== "checkbox") v = Number(v);
    const cur = typeof cfg.defaults[k] === "boolean" ? asBool(cfg.opts[k]) : cfg.opts[k];
    if (String(v) !== String(cur ?? "")) patch[k] = v;
  }
  return patch;
}
function onCfgInput(e) {
  const t = e.target;
  if (t.type === "range") t.nextElementSibling.textContent = (FIELDS[t.name]?.fmt || pctFmt)(t.value);
  if (t.type === "checkbox" && t.closest(".switch")) t.parentElement.lastChild.textContent = t.checked ? "Bật" : "Tắt";
  if (t.name === "audio_mode") syncAudioRows();
  $("#cfg-bar").hidden = !(Object.keys(cfgPatch()).length || Object.keys(levelsPatch()).length);
}
async function saveSettings(ev) {
  ev.preventDefault();
  const patch = cfgPatch(), levels = levelsPatch();
  if (!Object.keys(patch).length && !Object.keys(levels).length) { $("#cfg-bar").hidden = true; return; }
  const ok = $("#cfg-ok");
  ok.disabled = true;
  try {
    const r = await api("POST", "/api/v6/config", { patch, levels });
    const n = Object.keys(r.changed || {}).length + (r.levels_changed || []).length;
    cfg = await api("GET", "/api/v6/config"); paintCfg();
    say(n ? `Đã lưu ${n} thay đổi — áp dụng cho job chạy tiếp theo` : "Không có gì thay đổi");
  } catch (e) {
    $("#cfg-err").textContent = e.message; $("#cfg-err").hidden = false;
  } finally { ok.disabled = false; }
}

// ───────────── trang Cài đặt: chuyển mục ─────────────
let curPane = "ai";
function showPane(id) {
  curPane = id;
  document.querySelectorAll(".set-it").forEach((b) => b.classList.toggle("on", b.dataset.pane === id));
  document.querySelectorAll(".set-body .pane").forEach((p) => { p.hidden = p.dataset.pane !== id; });
  const hasCfg = !!document.querySelector(`#cfg-body .pane[data-pane="${id}"]`);
  $("#form-cfg").hidden = !hasCfg;
}
// ── Cài đặt → Thư mục (core/machine.PATH_KEYS, /api/v6/paths) ──
const PATH_LABEL = { LIBRARY_DIR: ["Thư viện phim", "Nơi Hongguo 2 lưu phim tải về / phim nguồn trên ổ."],
  YT_UPLOADER_DIR: ["Công cụ đăng YouTube (yt_uploader)", "Thư mục có accounts, client_secret.json, queue_state.json."],
  LIB_SAMPLE_DIR: ["Mẫu cắt thử bản quyền", "Để trống = mặc định."] };
async function paintPaths() {
  const d = await api("GET", "/api/v6/paths");
  const rows = Object.entries(d.paths).map(([k, p]) => {
    const [t, h] = PATH_LABEL[k] || [k, p.desc];
    const warn = p.value && !p.exists ? `<span class="err small">Không thấy thư mục này trên máy</span>` : "";
    return `<div class="field"><div class="lbl"><b>${esc(t)}</b><span>${esc(h)}</span>${warn}</div><div class="ctl"><input name="${esc(k)}" value="${esc(p.value)}" placeholder="${esc(p.default)}" data-orig="${esc(p.value)}"></div></div>`;
  }).join("");
  $("#paths-body").innerHTML = `<div class="sec"><h3>Thư mục dùng chung</h3>${rows}</div>`
    + `<div class="sec"><h3>Thư mục job</h3><div class="field"><div class="lbl"><b>Thư mục làm việc</b><span>Mỗi job một thư mục con. Đặt theo bản chạy (instances/&lt;tên&gt;.json → work_root), không sửa ở đây.</span></div><div class="ctl"><code>${esc(d.work_root)}</code></div></div></div>`;
}
$("#form-paths").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const paths = {};
  $("#form-paths").querySelectorAll("input[data-orig]").forEach((i) => { if (i.value.trim() !== i.dataset.orig) paths[i.name] = i.value.trim(); });
  if (!Object.keys(paths).length) return say("Không có gì thay đổi");
  const err = $("#paths-err"); err.hidden = true;
  try { await api("POST", "/api/v6/paths", { paths }); await paintPaths(); say("Đã lưu thư mục"); }
  catch (e) { err.textContent = e.message; err.hidden = false; }
});

async function openSettings() {
  const [c, a] = await Promise.all([api("GET", "/api/v6/config"), api("GET", "/api/v6/ai"), loadVoices().catch(() => null)]);
  cfg = c; ai = a;
  paintCfg(); paintAI(); paintAISimple();
  paintPaths().catch((e) => { $("#paths-body").innerHTML = `<p class="err">Không đọc được thư mục: ${esc(e.message)}</p>`; });
}

// ───────────── Cài đặt AI: model cho từng chức năng + nhà cung cấp (core/ai_models.py) ─────────────
let ai = null;          // snapshot GET /api/v6/ai
const NEEDS_VI = { tools: "cần model gọi được công cụ", aurora_file: "cần upload file — chỉ Aurora", aurora_image: "tạo ảnh — chỉ Aurora" };

function aiModelOptions() {
  const set = new Set(["chatgpt/gpt-6", "chatgpt/gpt-5-6-mini", "local"]);
  for (const p of ai.providers) p.models.forEach((m) => set.add(m));
  ai.local.llama.forEach((m) => set.add("llama/" + m));
  ai.local.ollama.forEach((m) => set.add(m));
  for (const f of ai.functions) if (f.value) set.add(f.value);
  return [...set];
}

function aiFuncRow(f) {
  const eff = f.effective ? `<b>${esc(f.effective)}</b> <span class="mut">· ${esc(f.provider || "")}</span>` : '<span class="mut">(không có)</span>';
  const src = `<div class="mut small">${esc(f.source)}${f.enabled === false ? ` · chức năng đang tắt (${esc(f.enabled_key)})` : ""}</div>`;
  const warn = f.problem ? `<div class="err small">⚠ ${esc(f.problem)}</div>` : "";
  const needs = f.needs ? `<div class="mut small">${esc(NEEDS_VI[f.needs] || f.needs)}</div>` : "";
  let input;
  if (f.fixed) {
    input = `<span class="mut">${esc(f.fixed)}</span>`;
  } else {
    input = `<input class="ai-model" data-fn="${esc(f.id)}" list="dl-models" value="${esc(f.value)}" placeholder="${esc(f.effective || "để trống = dự phòng")}" spellcheck="false">`;
    if (f.id === "plan") {
      const pm = ai.plan_mode;
      input += `<label class="row small">Chế độ <select id="ai-plan-mode">${["file", "sample"].map((x) =>
        `<option value="${x}"${pm.value === x ? " selected" : ""}>${x === "file" ? "file (gửi cả phụ đề — chỉ Aurora)" : "sample (gửi mẫu đầu/giữa/cuối)"}</option>`).join("")}</select></label>`
        + (pm.effective !== pm.value ? `<div class="mut small">đang chạy thực tế: ${esc(pm.effective)} (model không phải Aurora)</div>` : "");
    }
  }
  return `<tr data-fn="${esc(f.id)}"><td><b>${esc(f.label)}</b><div class="mut small">${esc(f.where)}</div></td>`
    + `<td>${input}</td><td>${eff}${src}${needs}${warn}</td>`
    + `<td>${f.fixed ? "" : `<button type="button" class="btn sm" data-ai="test" data-fn="${esc(f.id)}">Thử</button><div class="small ai-res"></div>`}</td></tr>`;
}

function aiProviderRow(p) {
  const status = p.has_key ? `<span class="tag ok">đã có key ${esc(p.key_hint)}</span>` : '<span class="tag warn">chưa có key</span>';
  return `<tr data-prov="${esc(p.name)}"><td><b>${esc(p.label)}</b><div class="mut small">${esc(p.key_key)}</div></td>`
    + `<td>${status}<input type="password" class="ai-key" placeholder="${p.has_key ? "Dán key mới để thay" : "Dán API key"}" autocomplete="off">`
    + `${p.has_key ? '<label class="row small"><input type="checkbox" class="ai-clear"> Xoá key</label>' : ""}</td>`
    + `<td><input class="ai-base" value="${esc(p.base_url)}" placeholder="${esc(p.default_base)}" spellcheck="false">`
    + `<label class="row small" title="max_tokens tối đa mỗi lần gọi (${esc(p.max_tokens_key)}). Lô dịch tự thu nhỏ cho vừa (~90 token/câu). Chỉ nâng khi model hỗ trợ — quá trần model, nhà cung cấp trả lỗi 400.">Trần output <input class="ai-maxtok" inputmode="numeric" size="7" value="${esc(p.max_tokens)}" placeholder="${esc(p.max_tokens_default)}"> token</label>`
    + `<label class="row small" title="Số lô dịch gửi CÙNG LÚC (${esc(p.concurrency_key)}). Cỡ lô tự tính = số câu ÷ số này (40–200 câu, không vượt trần output) để cả phim xong trong ~1 đợt.">Song song <input class="ai-conc" inputmode="numeric" size="4" value="${esc(p.concurrency)}" placeholder="${esc(p.concurrency_default)}"> request</label></td>`
    + `<td><button type="button" class="btn sm" data-ai="models" title="Hỏi ${esc(p.label)} (GET /models) các model key này dùng được — cũng là cách kiểm tra key">Lấy danh sách model</button>`
    + `<div class="small ai-res ${p.models.length ? "mut" : ""}">${p.models.length ? `${p.models.length} model đã lấy` : ""}</div></td></tr>`;
}

function paintAI() {
  $("#dl-models").innerHTML = aiModelOptions().map((m) => `<option value="${esc(m)}">`).join("");
  const main = ai.functions.filter((f) => !f.legacy), old = ai.functions.filter((f) => f.legacy);
  const groups = [...new Set(main.map((f) => f.group))];
  const funcs = groups.map((g) => `<tr class="grp"><td colspan="4">${esc(g)}</td></tr>` + main.filter((f) => f.group === g).map(aiFuncRow).join("")).join("");
  const oldRows = old.map(aiFuncRow).join("");
  const au = ai.aurora, lo = ai.local;
  const presets = ai.presets.map((p) => `<button type="button" class="btn sm" data-ai="preset" data-preset="${esc(p.id)}">${esc(p.label)}</button>`).join(" ");
  $("#ai-body").innerHTML = `
    <div class="sec"><h3>AI cho từng chức năng</h3>
    <p class="sub">Để trống = dùng mặc định (cột «Đang dùng»). Tiền tố: <code>deepseek/…</code> <code>alibaba/…</code> <code>chatgpt/…</code> (Aurora) <code>local</code>. Bộ cài sẵn: ${presets}</p>
    <table class="ai-tbl"><thead><tr><th>Chức năng · dùng ở đâu</th><th>Model</th><th>Đang dùng thực tế</th><th></th></tr></thead><tbody>${funcs}</tbody></table>
    ${old.length ? `<details class="ai-old"><summary>Luồng cũ / thử nghiệm đang tắt — KHÔNG dùng trong luồng dịch V6 (${old.length})</summary>
      <table class="ai-tbl"><tbody>${oldRows}</tbody></table></details>` : ""}
    </div>
    <div class="sec"><h3>Nhà cung cấp API</h3>
    <table class="ai-tbl"><thead><tr><th>Nhà cung cấp</th><th>API key</th><th>Địa chỉ · trần output · song song</th><th></th></tr></thead><tbody>${ai.providers.map(aiProviderRow).join("")}</tbody></table>
    <p class="mut small">Aurora: ${au.configured ? esc(au.base_url) : "chưa cấu hình AURORA_BASE_URL / CHATGPT_BASE_URL"} ·
      llama :8083: ${lo.llama.length ? esc(lo.llama.join(", ")) : "không chạy"} · Ollama: ${lo.ollama.length ? esc(lo.ollama.join(", ")) : "không có"}</p></div>`;
}

async function openAI() {
  ai = await api("GET", "/api/v6/ai");
  paintAI(); paintAISimple();
  $("#ai-err").hidden = true;
}

function aiCollect() {
  const models = {}, providers = {};
  for (const inp of document.querySelectorAll("#ai-body .ai-model")) {
    const f = ai.functions.find((x) => x.id === inp.dataset.fn);
    if (f && inp.value.trim() !== (f.value || "")) models[f.id] = inp.value.trim();
  }
  for (const tr of document.querySelectorAll("#ai-body tr[data-prov]")) {
    const p = ai.providers.find((x) => x.name === tr.dataset.prov), pc = {};
    const key = tr.querySelector(".ai-key").value.trim(), base = tr.querySelector(".ai-base").value.trim();
    if (key) pc.api_key = key;
    if (tr.querySelector(".ai-clear")?.checked) pc.clear_key = true;
    if (base !== p.base_url) pc.base_url = base;
    const mt = tr.querySelector(".ai-maxtok")?.value.trim() ?? "";
    if (mt !== String(p.max_tokens)) pc.max_tokens = mt;
    const cc = tr.querySelector(".ai-conc")?.value.trim() ?? "";
    if (cc !== String(p.concurrency)) pc.concurrency = cc;
    if (Object.keys(pc).length) providers[p.name] = pc;
  }
  const body = {};
  if (Object.keys(models).length) body.models = models;
  if (Object.keys(providers).length) body.providers = providers;
  const pm = $("#ai-plan-mode");
  if (pm && pm.value !== ai.plan_mode.value) body.plan_mode = pm.value;
  return body;
}

async function saveAI(ev) {
  ev.preventDefault();
  const body = aiCollect();
  if (!Object.keys(body).length) { say("Không có thay đổi nào"); return; }
  const ok = $("#ai-ok"); ok.disabled = true;
  try {
    ai = await api("POST", "/api/v6/ai", body);
    paintAI(); paintAISimple();
    $("#ai-err").hidden = true;
    say(`Đã lưu ${ai.changed.length} thay đổi — có hiệu lực ở lần gọi AI kế tiếp`);
  } catch (e) { $("#ai-err").textContent = e.message; $("#ai-err").hidden = false; } finally { ok.disabled = false; }
}

async function aiAction(btn) {
  if (btn.dataset.ai === "preset") {
    const label = btn.textContent;
    const prov = { deepseek: "DeepSeek", alibaba: "Alibaba", recommended: "DeepSeek" }[btn.dataset.preset];
    const note = prov ? `\nTên model lấy từ danh sách thật của ${prov} (ưu tiên bản «flash») — cần LƯU key trước.` : "";
    if (!confirm(`Áp dụng bộ «${label}» cho mọi chức năng chỉnh được?\n(Lưu ngay vào cấu hình; vẫn sửa lại từng dòng được.)${note}`)) return;
    return act(btn, `Đã áp dụng «${label}»`, async () => { ai = await api("POST", "/api/v6/ai", { preset: btn.dataset.preset }); aiPick = null; paintAI(); paintAISimple(); });
  }
  if (btn.dataset.ai === "models") {
    const tr = btn.closest("tr"), res = tr.querySelector(".ai-res");
    const p = ai.providers.find((x) => x.name === tr.dataset.prov);
    const body = { provider: p.name };
    const key = tr.querySelector(".ai-key").value.trim(), base = tr.querySelector(".ai-base").value.trim();
    if (key) body.api_key = key;                  // key vừa dán, chưa lưu: chỉ dùng để hỏi, không ghi
    if (base && base !== p.base_url) body.base_url = base;
    res.textContent = "đang hỏi…"; res.className = "small ai-res mut"; btn.disabled = true;
    try {
      const r = await api("POST", "/api/v6/ai/models", body);
      p.models = r.models;
      $("#dl-models").innerHTML = aiModelOptions().map((m) => `<option value="${esc(m)}">`).join("");
      res.innerHTML = `OK · ${r.models.length} model${r.suggested ? ` · gợi ý dịch/soát: <b>${esc(r.suggested)}</b>` : ""}`
        + `<details class="small"><summary>xem</summary>${r.models.map(esc).join("<br>")}</details>`;
      res.className = "small ai-res ok";
    } catch (e) { res.textContent = "Lỗi: " + e.message; res.className = "small ai-res err"; } finally { btn.disabled = false; }
    return;
  }
  if (btn.dataset.ai === "test") {
    const tr = btn.closest("tr"), res = tr.querySelector(".ai-res");
    let model = btn.dataset.model;
    if (!model) { const f = ai.functions.find((x) => x.id === btn.dataset.fn); model = tr.querySelector(".ai-model")?.value.trim() || f?.effective; }
    if (tr.dataset.prov && tr.querySelector(".ai-key").value.trim()) { res.textContent = "Lưu key trước rồi thử"; res.className = "small ai-res err"; return; }
    res.textContent = "đang thử…"; res.className = "small ai-res mut"; btn.disabled = true;
    try {
      const r = await api("POST", "/api/v6/ai/test", { model });
      res.textContent = r.ok ? `OK · ${r.ms} ms` : `Lỗi: ${r.error || "?"}`;
      res.className = "small ai-res " + (r.ok ? "ok" : "err");
    } catch (e) { res.textContent = "Lỗi: " + e.message; res.className = "small ai-res err"; } finally { btn.disabled = false; }
  }
}

// ───────────── Cài đặt → AI dịch (bản đơn giản: chọn nhà cung cấp → key → áp dụng) ─────────────
// Không bắt người dùng gõ tên model / tên khoá: chọn 1 nhà cung cấp, hệ thống áp bộ cài sẵn (core/ai_models.PRESETS)
// và tự lấy tên model thật của nhà cung cấp. Chỉnh từng chức năng: mục «Nâng cao».
const PROVS = [
  { id: "deepseek", preset: "recommended", name: "DeepSeek", pill: "Khuyến nghị", desc: "Nhanh, rẻ, dịch nhiều phần phim cùng lúc. Cần API key.", api: true },
  { id: "alibaba", preset: "alibaba", name: "Alibaba Qwen", desc: "Model Qwen của Alibaba Cloud. Cần API key.", api: true },
  { id: "aurora", preset: "aurora", name: "Aurora (ChatGPT)", desc: "Miễn phí qua pool tài khoản. Dễ chậm hoặc bị chặn.", warn: "Không ổn định" },
  { id: "local", preset: "local", name: "AI trên máy", desc: "Llama chạy trên GPU máy này. Không cần mạng, chất lượng thấp hơn.", warn: "Chất lượng thấp" },
];
const SPEEDS = [[8, "Tiết kiệm", "8 phần cùng lúc"], [32, "Cân bằng", "32 phần cùng lúc"], [64, "Tối đa", "64 phần cùng lúc"]];
const USE_VI = {
  review: "Dịch và soát toàn bộ lời thoại", plan: "Lập hồ sơ nhân vật, cách xưng hô — mỗi tập một lần",
  translate: "Dịch chữ trên hình, tiêu đề phim", hang_fix: "Gỡ job bị kẹt sau khi dịch",
  agent: "Trợ lý AI", agent_fallback: "Trợ lý AI khi AI chính lỗi", meta: "Tiêu đề, mô tả khi đăng YouTube", thumb: "Ảnh bìa YouTube",
};
const PROV_OF = (label) => (/deepseek/i.test(label) ? "deepseek" : /alibaba|qwen/i.test(label) ? "alibaba" : /aurora/i.test(label) ? "aurora" : "local");
let aiPick = null;

function niceModel(f) {
  if (!f.effective) return '<span class="err">chưa có</span>';
  const name = f.effective.includes("/") ? f.effective.split("/").slice(1).join("/") : f.effective;
  return `${esc(f.provider || "")}<small>${esc(name)}</small>`;
}
function paintAISimple() {
  const review = ai.functions.find((f) => f.id === "review") || {};
  const cur = PROV_OF(review.provider || "");
  if (!aiPick) aiPick = cur;
  const p = PROVS.find((x) => x.id === aiPick);
  const pr = ai.providers.find((x) => x.name === aiPick);
  const uses = ai.functions.filter((f) => !f.legacy && USE_VI[f.id]).map((f) =>
    `<div class="use"><div><b>${esc(USE_VI[f.id])}</b>${f.problem ? `<span class="err">${esc(f.problem)}</span>` : ""}</div><div class="m">${niceModel(f)}</div></div>`).join("");
  let detail = "";
  if (p.api) {
    const conc = pr?.concurrency || 32;
    detail = `
      <div class="field"><div class="lbl"><b>API key</b><span>Lấy ở trang tài khoản ${esc(p.name)}. Chỉ lưu trên máy này.</span></div>
        <div class="ctl"><div class="keyrow"><input type="password" id="prov-key" autocomplete="off" placeholder="${pr?.has_key ? `Đã lưu (${esc(pr.key_hint)}) — dán key mới để thay` : "Dán API key"}">
          <button type="button" class="btn" id="prov-test">Kiểm tra</button></div><div id="prov-status" class="small">${pr?.has_key ? '<span class="ok">✓ Đã có key</span>' : ""}</div></div></div>
      <div class="field"><div class="lbl"><b>Tốc độ dịch</b><span>Số phần phim dịch cùng lúc. Nhanh hơn không tốn thêm tiền.</span></div>
        <div class="ctl"><div class="seg">${SPEEDS.map(([n, t, d]) => `<label title="${esc(d)}"><input type="radio" name="prov-speed" value="${n}"${Math.abs(conc - n) === Math.min(...SPEEDS.map(([m]) => Math.abs(conc - m))) ? " checked" : ""}>${esc(t)}</label>`).join("")}</div></div></div>`;
  } else if (p.id === "aurora") {
    detail = `<div class="callout">${ai.aurora.configured ? `Aurora: <code>${esc(ai.aurora.base_url)}</code>` : "Chưa cấu hình địa chỉ Aurora trên máy này."}</div>`;
  } else {
    detail = `<div class="callout">${ai.local.llama.length ? `Llama đang chạy: ${esc(ai.local.llama.join(", "))}` : "Llama (cổng 8083) chưa chạy trên máy này."}</div>`;
  }
  const isCur = aiPick === cur;
  $("#ai-simple").innerHTML = `
    <h2>AI dịch</h2><p class="sub">Chọn AI làm việc dịch. Hệ thống tự phân việc cho từng bước và tự chọn model phù hợp.</p>
    <div class="sec"><h3>Nhà cung cấp</h3>
      <div class="prov">${PROVS.map((x) => `<label><input type="radio" name="prov" value="${x.id}"${x.id === aiPick ? " checked" : ""}>
        <b>${esc(x.name)}${x.pill ? `<span class="pill">${esc(x.pill)}</span>` : ""}${x.warn ? `<span class="pill warn">${esc(x.warn)}</span>` : ""}</b><span>${esc(x.desc)}</span></label>`).join("")}</div>
      ${detail}
      <div class="actions"><span class="sub grow">${isCur ? `Đang dùng ${esc(p.name)}.` : `Hiện đang dùng ${esc((PROVS.find((x) => x.id === cur) || {}).name || "")}.`}</span>
        <button type="button" class="btn primary" id="prov-apply">${isCur ? "Áp dụng lại" : `Chuyển sang ${esc(p.name)}`}</button></div>
    </div>
    <div class="sec"><h3>AI đang làm gì</h3><p class="sub">Muốn đổi riêng từng việc: mục «Nâng cao».</p><div class="uses">${uses}</div></div>`;
}
async function provSaveKey() {
  const key = $("#prov-key")?.value.trim();
  const speed = document.querySelector('input[name="prov-speed"]:checked')?.value;
  const pc = {};
  if (key) pc.api_key = key;
  const pr = ai.providers.find((x) => x.name === aiPick);
  if (speed && pr && String(pr.concurrency) !== speed) pc.concurrency = speed;
  if (Object.keys(pc).length) ai = await api("POST", "/api/v6/ai", { providers: { [aiPick]: pc } });
}
async function provTest(btn) {
  const st = $("#prov-status");
  st.innerHTML = '<span class="sub">Đang kiểm tra…</span>';
  btn.disabled = true;
  try {
    await provSaveKey();
    const r = await api("POST", "/api/v6/ai/models", { provider: aiPick });
    st.innerHTML = `<span class="ok">✓ Kết nối được — sẽ dùng model <b>${esc((r.suggested || "").split("/").slice(1).join("/") || "?")}</b></span>`;
    $("#prov-key").value = "";
  } catch (e) { st.innerHTML = `<span class="err">Không kết nối được: ${esc(e.message)}</span>`; } finally { btn.disabled = false; }
}
async function provApply(btn) {
  const p = PROVS.find((x) => x.id === aiPick);
  const what = p.id === "deepseek"
    ? "DeepSeek dịch & soát và chạy Trợ lý; Aurora lập hồ sơ nhân vật; AI trên máy dịch chữ trên hình."
    : `${p.name} làm mọi việc dịch, soát và Trợ lý.`;
  if (!confirm(`Dùng ${p.name}?\n\n${what}\n\nÁp dụng cho các job chạy sau.`)) return;
  return act(btn, `Đã chuyển sang ${p.name}`, async () => {
    if (p.api) await provSaveKey();
    ai = await api("POST", "/api/v6/ai", { preset: p.preset });
    paintAI(); paintAISimple();
  });
}
function onAISimpleClick(e) {
  const b = e.target.closest("button");
  if (b?.id === "prov-test") return provTest(b);
  if (b?.id === "prov-apply") return provApply(b);
}
function onAISimpleChange(e) {
  if (e.target.name === "prov") { aiPick = e.target.value; paintAISimple(); }
}

// ───────────── Xung đột bảng nhân vật của bộ (engines/series_glossary) ─────────────
let conf = [];
async function loadConflicts() {
  try {
    const d = await api("GET", "/api/v6/series/conflicts");
    conf = d.items || [];
  } catch (e) { conf = []; console.warn("xung đột bảng bộ:", e.message); }
  $("#btn-conf").hidden = !conf.length;
  $("#conf-n").textContent = conf.length ? String(conf.length) : "";
  if ($("#dlg-conf").open) paintConflicts();
}
function fmtVal(v) { return Array.isArray(v) ? v.join(" / ") : String(v ?? ""); }
function paintConflicts() {
  $("#conf-rows").innerHTML = conf.length ? conf.map((c) => `<tr>
    <td>${esc(c.core)}</td>
    <td><span class="tag mut">${esc(c.kind_vi)}</span> <b>${esc(c.key)}</b><div class="mut small">${esc(c.field_vi)}</div></td>
    <td><b>${esc(fmtVal(c.stored))}</b></td>
    <td>${esc(fmtVal(c.suggested))}${c.count > 1 ? ` <span class="mut small">×${esc(c.count)}</span>` : ""}</td>
    <td class="small">${esc((c.episodes || []).slice(-3).join(", "))}</td>
    <td class="nowrap"><button class="btn sm" data-cf="keep" data-core="${esc(c.core)}" data-id="${esc(c.id)}">Giữ</button>
      <button class="btn sm primary" data-cf="use_new" data-core="${esc(c.core)}" data-id="${esc(c.id)}">Dùng bản mới</button>
      <button class="btn sm ghost" data-cf="skip" data-core="${esc(c.core)}" data-id="${esc(c.id)}">Bỏ qua</button></td></tr>`).join("")
    : '<tr><td colspan="6" class="mut">Không còn xung đột nào.</td></tr>';
}
$("#btn-conf").addEventListener("click", () => { paintConflicts(); $("#dlg-conf").showModal(); });
$("#conf-close").addEventListener("click", () => $("#dlg-conf").close());
$("#conf-rows").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-cf]");
  if (!b) return;
  const msg = { keep: "Đã giữ bản đang dùng", use_new: "Đã dùng bản mới cho các tập sau", skip: "Đã bỏ qua" }[b.dataset.cf];
  act(b, msg, async () => {
    await api("POST", "/api/v6/series/conflicts/resolve", { core: b.dataset.core, id: b.dataset.id, action: b.dataset.cf });
    await loadConflicts();
  });
});

// ───────────── Thư viện phim + đăng YouTube (D7) ─────────────
const FILM_ST = { done: "xong", uploaded: "đã đăng", uploading: "đang đăng", running: "đang làm", failed: "lỗi", pending: "chờ" };
function fmtDur(sec) { const t = Number(sec) || 0; if (!t) return ""; const h = Math.floor(t / 3600), m = Math.round((t % 3600) / 60); return h ? `${h} giờ ${m} phút` : `${m} phút`; }

const BLOCK_ST = { clean: ["Sạch", "ok"], partially_blocked: ["Chặn một phần", "warn"], blocked: ["Bị chặn", "bad"], claim: ["Claim", "warn"], private: ["Riêng tư", ""], unknown: ["Chưa kiểm", ""] };

function filmsQuery() {
  return new URLSearchParams({ q: $("#films-q").value.trim(), status: $("#films-status").value, block_status: $("#films-block").value,
    work_exists: $("#films-hide-gone").checked ? "1" : "", limit: "500" });
}
async function loadFilms() {
  const q = filmsQuery();
  $("#films-csv").href = "/films.csv?" + q;
  const body = $("#films-rows");
  let d;
  try { d = await api("GET", "/films?" + q); } catch (e) {
    body.innerHTML = `<tr><td colspan="7" class="note">Không đọc được thư viện: ${esc(e.message)}${/404/.test(e.message) ? " — instance này chưa bật router «films» (instances/*.json → routers)" : ""}</td></tr>`;
    return;
  }
  const st = d.stats || {}, gone = Number(st.work_missing) || 0;
  $("#films-stats").textContent = [st.total != null ? `${st.total} phim` : "", st.uploaded != null ? `${st.uploaded} đã đăng` : "",
    st.done_7d != null ? `${st.done_7d} xong trong 7 ngày` : "", st.done_hours ? `${st.done_hours} giờ phim` : "", gone ? `${gone} mất thư mục` : ""].filter(Boolean).join(" · ");
  const purge = $("#films-purge"); purge.hidden = !gone; purge.textContent = `Dọn ${gone} phim mất thư mục`;
  const rows = d.films || [];
  body.innerHTML = rows.length ? rows.map((f) => {
    const title = f.title_vi || f.title || f.job_id;
    const yt = f.yt_url ? `<a href="${esc(f.yt_url)}" target="_blank" rel="noopener">mở</a>` : "";
    const canUp = !f.yt_url && f.status === "done" && f.work_exists !== 0;
    const [bt, bc] = f.yt_url ? (BLOCK_ST[f.block_status || "unknown"] || [f.block_status, ""]) : ["", ""];
    const block = bt ? `<span class="tag ${bc}" title="${esc(f.block_reason || "")}${f.checked_at ? ` · kiểm lúc ${esc(String(f.checked_at).slice(0, 16).replace("T", " "))}` : ""}">${esc(bt)}</span>` : "";
    return `<tr data-id="${esc(f.job_id)}"><td title="${esc(f.job_id)}"><b style="font-weight:500">${esc(title)}</b></td><td><span class="b ${esc(f.status)}">${esc(FILM_ST[f.status] || f.status || "")}</span></td>`
      + `<td>${esc(fmtDur(f.duration_sec))}</td><td>${esc(String(f.finished_at || "").slice(0, 16).replace("T", " "))}</td><td>${yt}</td><td>${block}</td>`
      + `<td class="acts">${canUp ? `<button class="btn sm primary" data-film="upload">Đăng YouTube</button>` : ""}`
      + `${f.yt_url ? `<button class="btn sm ghost" data-film="check" title="Kiểm lại bản quyền video này trên YouTube">Kiểm</button>` : ""}`
      + `${jobs.has(f.job_id) ? `<button class="btn sm" data-film="open">Mở job</button>` : ""}`
      + `${f.work_exists === 0 ? `<span class="mut small" title="Thư mục job không còn trên ổ (đã xoá hoặc thuộc máy / instance khác)">mất thư mục</span>` : ""}`
      // chỉ dòng KHÔNG còn là job trong hàng (routes_films /films/delete còn gỡ job khỏi hàng chờ)
      + `${jobs.has(f.job_id) ? "" : `<button class="btn sm ghost" data-film="delete" title="Xoá dòng này khỏi thư viện (không xoá file trên ổ)">Xoá</button>`}</td></tr>`;
  }).join("") : '<tr><td colspan="7" class="note">Không có phim nào khớp.</td></tr>';
}
const loadFilmsSoon = debounce(loadFilms, 300);

// ── Đăng YouTube: cấu hình tự đăng (agents/auto_publisher, /api/auto-publish/config) ──
let pubCfg = null;
async function loadPubCfg() {
  try {
    const r = await api("GET", "/api/auto-publish/config");
    pubCfg = r.config || r;

    $("#films-privacy").value = pubCfg.privacy || "unlisted";
    $("#pub-sched").value = String(Math.round(Number(pubCfg.schedule_hours) || 0));
    const off = r.instance_on === false;
    $("#pub-auto").checked = !off && !!pubCfg.enabled;      // bản này tắt tự đăng → công tắc tắt + khoá, không giả «bật»
    $("#pub-auto").disabled = off;
    $("#pub-note").textContent = off ? "Tự đăng đang tắt cho bản này (instances/<tên>.json → auto_publish). Vẫn đăng tay được." : "";
  } catch (e) { $("#pub-note").textContent = "Không đọc được cấu hình đăng: " + e.message; }
}
async function savePubCfg() {
  const body = { ...(pubCfg || {}), enabled: $("#pub-auto").disabled ? !!pubCfg?.enabled : $("#pub-auto").checked, privacy: $("#films-privacy").value, schedule_hours: Number($("#pub-sched").value) || 0 };
  try { const r = await api("POST", "/api/auto-publish/config", body); pubCfg = r.config || body; say("Đã lưu cấu hình đăng YouTube"); }
  catch (e) { say("Lưu cấu hình đăng lỗi: " + e.message, "err"); loadPubCfg(); }
}

async function filmAction(btn) {
  const tr = btn.closest("tr[data-id]"), id = tr?.dataset.id;
  if (!id) return;
  if (btn.dataset.film === "open") { showView("jobs"); selected = null; select(id); return; }
  if (btn.dataset.film === "check") {
    return act(btn, "", async () => {
      const r = await api("POST", "/films/check-block", { job_id: id });
      const x = (r.results || [])[0] || {};
      say(`Bản quyền: ${(BLOCK_ST[x.status] || [x.status || "?"])[0]}${x.reason ? " — " + x.reason : ""}`, x.status === "blocked" ? "err" : "");
      loadFilms();
    });
  }
  if (btn.dataset.film === "delete") {
    if (!confirm(`Xoá «${tr.cells[0].textContent}» khỏi thư viện?\n\nChỉ xoá dòng trong thư viện, không xoá thư mục job / video trên ổ.`)) return;
    return act(btn, "Đã xoá khỏi thư viện", async () => { await api("POST", "/films/delete", { job_ids: [id], delete_work: false }); loadFilms(); });
  }
  if (btn.dataset.film === "upload") {
    const priv = $("#films-privacy"), label = priv.options[priv.selectedIndex].text;
    const title = tr.cells[0].textContent;
    if (!confirm(`Đăng «${title}» lên YouTube ở chế độ «${label}»?\n\nViệc này gửi video ra ngoài (kênh YouTube đã cấu hình), không tự rút lại được.`)) return;
    return act(btn, "", async () => {
      // chuẩn bị metadata + thumbnail rồi mới upload — có thể mất cả phút; nút nói rõ đang làm gì
      btn.textContent = "Đang chuẩn bị đăng…";
      say("Đang chuẩn bị metadata / thumbnail rồi đăng — có thể mất vài phút, đừng tắt app");
      let r;
      try { r = await api("POST", "/api/auto-publish/trigger", { job_id: id, privacy: priv.value, schedule_hours: Number($("#pub-sched").value) || 0 }); } finally { btn.textContent = "Đăng YouTube"; }
      say(r.queued || r.enqueued ? "Đã xếp vào hàng đăng YouTube (đang có video khác đăng)" : (r.yt_url ? "Đã đăng: " + r.yt_url : "Đã gửi lệnh đăng YouTube"));
      loadFilms();
    });
  }
}

// ───────────── Chat Agent (D10) ─────────────
let agentHistory = [];
function agentAppend(role, text, meta) {
  const log = $("#agent-log");
  const hello = log.querySelector(".chat-hello"); if (hello) hello.hidden = true;
  const d = document.createElement("div");
  d.className = "msg " + role;
  d.textContent = text;
  if (meta) { const m = document.createElement("div"); m.className = "msg-meta"; m.textContent = meta; d.appendChild(m); }
  log.appendChild(d); log.scrollTop = log.scrollHeight;
  return d;
}
async function agentSend(ev) {
  ev.preventDefault();
  const box = $("#agent-msg"), msg = box.value.trim();
  if (!msg) return;
  agentAppend("me", msg);
  box.value = "";
  const wait = agentAppend("bot", "Đang nghĩ…");
  const btn = $("#agent-send"); btn.disabled = true;
  try {
    const r = await api("POST", "/api/agent/chat", { message: msg, history: agentHistory.slice(-10), allow_mutate: $("#agent-mutate").checked });
    wait.remove();
    const steps = Array.isArray(r.steps) ? r.steps.length : 0;
    agentAppend("bot", r.reply || "(không có trả lời)", [r.model, steps ? `${steps} bước công cụ` : ""].filter(Boolean).join(" · "));
    agentHistory.push({ role: "user", content: msg }, { role: "assistant", content: r.reply || "" });
  } catch (e) {
    wait.remove();
    const m = e.message || "";
    const hint = /Max retries|Connection refused|ConnectionError|Failed to establish|timed out/i.test(m)
      ? "Không kết nối được AI của trợ lý. Kiểm tra Cài đặt → AI dịch (key / nhà cung cấp)."
      : "";
    agentAppend("err", "Agent lỗi: " + (hint || m), hint ? m.slice(0, 200) : "");
  } finally { btn.disabled = false; box.focus(); }
}

// ───────────── SSE ─────────────
function connect() {
  const es = new EventSource("/api/events");
  es.onopen = () => { $("#conn").className = "conn on"; $("#conn").textContent = "Đã kết nối"; };
  es.onerror = () => { $("#conn").className = "conn off"; $("#conn").textContent = "Mất kết nối — đang nối lại"; };
  es.addEventListener("snapshot", (e) => {
    jobs.clear();
    for (const j of JSON.parse(e.data).jobs) jobs.set(j.job_id, j);
    fullRedraw = true; schedule();
    refreshQueue();                           // 1 lần mỗi lần (nối / nối lại)
    loadConflicts();                          // 1 lần mỗi lần nối; sau đó chỉ theo sự kiện series.conflict
    if (selected) { if (jobs.has(selected)) loadDetail(selected); else select(null); }
  });
  es.addEventListener("job.updated", (e) => upsert(JSON.parse(e.data)));
  es.addEventListener("job.removed", (e) => {
    const id = JSON.parse(e.data).job_id;
    jobs.delete(id); picked.delete(id);
    if (id === selected) select(null);
    fullRedraw = true; schedule(); refreshQueueSoon();
  });
  es.addEventListener("queue.changed", (e) => {
    refreshQueueSoon();
    try { const t = JSON.parse(e.data).text; if (t && /TẠM DỪNG|nhường ghế/.test(t)) say(t, "err"); } catch (_) { /* event không có text */ }
  });
  es.addEventListener("series.conflict", () => loadConflicts());
  es.addEventListener("job.touched", (e) => { if (JSON.parse(e.data).job_id === selected) refreshDetailSoon(); });
  es.addEventListener("stage.progress", (e) => {
    const d = JSON.parse(e.data), j = jobs.get(d.job_id);
    if (!j) return;
    if (d.cur_text) j.cur_text = d.cur_text;
    dirty.add(d.job_id); schedule();
    if (d.job_id === selected) refreshDetailSoon();
  });
}

// ───────────── gắn sự kiện ─────────────
$("#q").addEventListener("input", (e) => { filter = e.target.value; fullRedraw = true; schedule(); });
$("#rows").addEventListener("click", (e) => {
  const tr = e.target.closest("tr[data-id]");
  if (!tr) return;
  const id = tr.dataset.id;
  if (e.target.classList.contains("pick")) {
    if (e.target.checked) picked.add(id); else picked.delete(id);
    return syncToolbar();
  }
  if (e.target.closest("td.ck")) return;
  select(id);
});
$("#ck-all").addEventListener("change", (e) => {
  for (const j of visible()) { if (e.target.checked) picked.add(j.job_id); else picked.delete(j.job_id); }
  fullRedraw = true; schedule();
});
$("#detail").addEventListener("click", (e) => { const b = e.target.closest("button[data-act]"); if (b) detailAction(b); });
$("#detail").addEventListener("change", (e) => {
  if (e.target.matches("textarea.vi")) saveLine(e.target);
  if (e.target.matches("textarea.zh-edit")) saveZh(e.target);
  if (e.target.matches("input.clone-file")) { const n = e.target.form?.elements["clone-name"]; if (n && !n.value) n.value = (e.target.files[0]?.name || "").replace(/\.[^.]+$/, ""); }
  if (e.target.id === "sc-filter") { scriptFilter = e.target.value; scriptShown = PAGE; paintScript(false); }
});
$("#detail").addEventListener("keydown", (e) => {
  if (e.target.matches("textarea.vi, textarea.zh-edit") && e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); e.target.blur(); }
  if (e.target.matches("textarea.zh-edit") && e.key === "Escape") { e.target.value = e.target.defaultValue; e.target.hidden = true; }
});
const onScriptSearch = debounce(() => { scriptShown = PAGE; paintScript(false); }, 250);
$("#detail").addEventListener("input", (e) => { if (e.target.id === "sc-q") { scriptQ = e.target.value.trim(); onScriptSearch(); } });
$("#btn-enqueue").addEventListener("click", (e) => act(e.currentTarget, "Đã xếp vào hàng chờ", async () => {
  await api("POST", "/api/queue/jobs", { job_ids: [...picked] });
  picked.clear(); await refreshQueue();
}));
$("#btn-dequeue").addEventListener("click", (e) => act(e.currentTarget, "Đã gỡ khỏi hàng chờ", async () => {
  await api("POST", "/api/queue/jobs/remove", { job_ids: [...picked].filter(inQueue) });
  picked.clear(); await refreshQueue();
}));
$("#btn-q-stop").addEventListener("click", (e) => {
  if (!confirm("Dừng job đang chạy và xoá toàn bộ hàng chờ?")) return;
  act(e.currentTarget, "Đã dừng hàng chờ", async () => { await api("POST", "/api/queue/jobs/stop"); await refreshQueue(); });
});
$("#btn-q-resume").addEventListener("click", (e) => act(e.currentTarget, "Đã chạy tiếp hàng chờ", async () => {
  await api("POST", "/api/v6/queue/resume"); await refreshQueue();
}));
$("#btn-q-log").addEventListener("click", (e) => act(e.currentTarget, "", showQueueLog));
$("#log-close").addEventListener("click", () => $("#dlg-log").close());

// ───────────── chuyển màn hình (thanh bên) ─────────────
let curView = "jobs";
function showView(v) {
  curView = v;
  document.querySelectorAll(".nav-it").forEach((b) => b.classList.toggle("on", b.dataset.view === v));
  document.querySelectorAll(".view").forEach((s) => { s.hidden = s.id !== "view-" + v; });
  if (v === "films") { syncFilms(false).then(loadFilms); loadPubCfg(); }
  if (v === "settings") act(null, "", openSettings);
  if (v === "agent") $("#agent-msg").focus();
}
document.querySelectorAll(".nav-it").forEach((b) => b.addEventListener("click", () => showView(b.dataset.view)));
document.querySelectorAll(".set-it").forEach((b) => b.addEventListener("click", () => showPane(b.dataset.pane)));
$("#ai-simple").addEventListener("click", onAISimpleClick);
$("#ai-simple").addEventListener("change", onAISimpleChange);
$("#form-cfg").addEventListener("input", onCfgInput);
$("#form-cfg").addEventListener("change", onCfgInput);
$("#form-cfg").addEventListener("submit", saveSettings);
$("#cfg-cancel").addEventListener("click", () => paintCfg());
$("#form-cfg").addEventListener("click", (e) => {
  const btn = e.target.closest("#cfg-sample");
  if (!btn) return;
  const f = $("#form-cfg"), voice = f.elements.voice?.value || "";
  if (!voice) return say("Chưa có giọng để nghe", "err");
  const engine = f.querySelector('input[name="tts_engine"]:checked')?.value || "vieneu";
  const q = new URLSearchParams({ engine, voice, speed: f.elements.speed?.value || "1" });
  act(btn, "", async () => {
    btn.textContent = "Đang đọc…";
    try {
      const r = await fetch("/api/voice-sample?" + q);
      if (!r.ok) { let m = `HTTP ${r.status}`; try { m = (await r.json()).error || m; } catch (_) { /* không phải JSON */ } throw new Error(m); }
      player.src = URL.createObjectURL(await r.blob());
      await player.play();
    } finally { btn.textContent = "▶ Nghe thử"; }
  });
});
$("#form-ai").addEventListener("submit", saveAI);
$("#form-ai").addEventListener("click", (e) => { const b = e.target.closest("button[data-ai]"); if (b) aiAction(b); });

// Đồng bộ films.db = đọc lại MỌI manifest → chỉ làm khi lần trước đã cũ (FILM_SYNC_MS) hoặc người dùng bấm «Đồng bộ».
const FILM_SYNC_MS = 120000;
let filmSyncedAt = 0;
async function syncFilms(force) {
  if (!force && Date.now() - filmSyncedAt < FILM_SYNC_MS) return;
  $("#films-rows").innerHTML = '<tr><td colspan="7" class="note">Đang đồng bộ thư viện…</td></tr>';
  try { await api("POST", "/films/sync"); filmSyncedAt = Date.now(); }
  catch (e) { say("Không đồng bộ được thư viện: " + e.message, "err"); }
}
$("#films-sync").addEventListener("click", (e) => act(e.currentTarget, "Đã đồng bộ thư viện", async () => { await syncFilms(true); await loadFilms(); }));
$("#films-q").addEventListener("input", loadFilmsSoon);
$("#films-status").addEventListener("change", loadFilms);
$("#films-block").addEventListener("change", loadFilms);
$("#films-hide-gone").addEventListener("change", loadFilms);
["#pub-auto", "#films-privacy", "#pub-sched"].forEach((sel) => $(sel).addEventListener("change", savePubCfg));
$("#pub-skip").addEventListener("click", (e) => {
  if (!confirm("Bỏ qua video đang tải lên YouTube?")) return;
  act(e.currentTarget, "Đã bỏ qua video đang đăng", () => api("POST", "/api/uploader/skip-current"));
});
$("#pub-scan").addEventListener("click", (e) => {
  const label = $("#films-privacy").options[$("#films-privacy").selectedIndex].text;
  if (!confirm(`Đăng MỌI phim đã xong mà chưa có trên YouTube, chế độ «${label}»?\n\nVideo sẽ được gửi lên kênh YouTube đã cấu hình.`)) return;
  act(e.currentTarget, "Đã bắt đầu đăng các phim chưa đăng", async () => { await api("POST", "/api/auto-publish/scan"); loadFilms(); });
});
$("#films-check-all").addEventListener("click", (e) => act(e.currentTarget, "Đã kiểm bản quyền xong", async () => { await api("POST", "/films/check-block-all"); loadFilms(); }));
$("#films-purge").addEventListener("click", (e) => {
  if (!confirm("Xoá khỏi thư viện mọi phim mà thư mục job không còn trên ổ?\n\nChỉ xoá dòng trong thư viện (file trên ổ vốn đã không còn).")) return;
  act(e.currentTarget, "", async () => { const r = await api("POST", "/films/purge-missing", {}); say(`Đã dọn ${r.count || 0} phim`); loadFilms(); });
});
$("#films-rows").addEventListener("click", (e) => { const b = e.target.closest("button[data-film]"); if (b) filmAction(b); });
$("#agent-clear").addEventListener("click", () => {
  agentHistory = []; $("#agent-log").querySelectorAll(".msg").forEach((m) => m.remove());
  const h = $("#agent-log .chat-hello"); if (h) h.hidden = false;
});
$("#agent-log").addEventListener("click", (e) => { const c = e.target.closest(".chip[data-ask]"); if (c) { $("#agent-msg").value = c.dataset.ask; $("#form-agent").requestSubmit(); } });
$("#form-agent").addEventListener("submit", agentSend);
$("#agent-msg").addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); $("#form-agent").requestSubmit(); } });
function openNew() { $("#new-err").hidden = true; $("#dlg-new").showModal(); $("#new-src").focus(); }
$("#btn-new").addEventListener("click", openNew);
document.querySelector('[data-open="new"]').addEventListener("click", openNew);
$("#new-cancel").addEventListener("click", () => $("#dlg-new").close());
$("#form-new").addEventListener("submit", createJob);

fetch("/api/health").then((r) => r.json()).then((h) => { $("#inst").textContent = `${h.instance} · cổng ${h.port}`; }).catch((e) => console.warn("health:", e));
connect();
