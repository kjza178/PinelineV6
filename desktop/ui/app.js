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

function rowHtml(j) {
  const cells = STAGES.map((s) => `<td><span class="b ${esc(j.stages?.[s])}">${esc(j.stages?.[s] || "-")}</span></td>`).join("");
  const q = queue.current === j.job_id ? "▶ " : (inQueue(j.job_id) ? "⏳ " : "");
  const note = j.status === "held" ? `⏸ ${j.held_at || ""} ${j.hold_reason || ""}` : (j.cur_text || "");
  return `<td class="ck"><input type="checkbox" class="pick"${picked.has(j.job_id) ? " checked" : ""}></td>`
    + `<td title="${esc(j.job_id)}">${q}${esc(j.title)}</td><td><span class="b ${esc(j.status)}">${esc(j.status)}</span></td>${cells}<td class="note">${esc(note)}</td>`;
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
    $("#count").textContent = `${list.length}/${jobs.size} job`;
    $("#empty").hidden = jobs.size > 0;
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
  let text = cur ? `đang chạy: ${cur}${n ? ` · chờ ${n} job` : ""}` : (n ? `chờ ${n} job` : "trống");
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
  const rows = (d.history || []).map((h) => `<tr><td>${esc(h.at || "")}</td><td><span class="b ${esc(h.status === "sidelined" ? "held" : h.status)}">${esc(HIST_VI[h.status] || h.status)}</span></td>`
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
    <div class="d-head"><h2 title="${esc(j.job_id)}">${esc(j.title)}</h2><button class="btn sm" data-act="close" title="Đóng">✕</button></div>
    <div class="d-id">${esc(j.job_id)} · <span class="b ${esc(j.status)}">${esc(j.status)}</span>${inQueue(j.job_id) ? " · trong hàng chờ" : ""}${running && j.cur_text ? ` · ${esc(j.cur_text)}` : ""}</div>
    ${held ? `<div class="hold">⏸ Đang tạm dừng: <b>${esc(j.held_at || "")}</b><br>${esc(j.hold_reason || "")}</div>` : ""}
    ${err ? `<div class="err">Không đọc được chi tiết: ${esc(err)}</div>` : ""}
    <div class="d-actions">
      ${held ? '<button class="btn primary" data-act="resume" title="Gỡ tạm dừng và chạy tiếp">Chạy tiếp</button>' : ""}
      <button class="btn primary" data-act="enqueue"${running || inQueue(j.job_id) ? " disabled" : ""} title="Thêm vào hàng chờ, chạy hết các bước theo cấu hình batch">Xếp hàng chạy</button>
      <button class="btn danger" data-act="stop"${running ? "" : " disabled"}>Dừng</button>
      <button class="btn" data-act="reveal" title="Mở thư mục job trên máy">Mở thư mục</button>
      <button class="btn danger ghost" data-act="delete"${running ? " disabled" : ""}>Xoá job</button>
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
    return `<li><div class="st-head"><span class="b ${esc(sum)}">${esc(sum)}${esc(pct(st))}</span><b>${esc(STAGE_VI[s])}</b>`
      + (sum === "done"
        ? `<button class="btn sm" data-act="stage" data-stage="${s}" data-force="1"${running ? " disabled" : ""} title="Bỏ kết quả cũ của bước này và làm lại từ đầu">Làm lại</button>`
        : `<button class="btn sm" data-act="stage" data-stage="${s}"${running ? " disabled" : ""} title="Chạy riêng bước này (làm tiếp phần còn dở)">Chạy</button>`)
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
  if (r.s_spk && r.s_spk !== "?") tags.push(`<span class="tag mut">${esc(r.s_spk)} → ${esc(r.s_to || "?")}</span>`);
  const has = script.wav.has(Number(r.i));
  return `<div class="ln" data-i="${esc(r.i)}">
    <div class="ln-meta"><b>#${esc(r.i)}</b> <span class="mut">${fmtTime(r.start)}</span> ${tags.join(" ")}</div>
    <div class="zh">${esc(r.zh || "")}</div>
    <textarea class="vi" data-i="${esc(r.i)}" data-junk="${r.junk ? 1 : 0}" rows="2" spellcheck="false"${locked || r.junk ? " readonly" : ""}>${esc(r.vi || "")}</textarea>
    <div class="ln-act"><button class="btn sm" data-act="play" data-i="${esc(r.i)}"${has ? "" : " disabled"} title="${has ? "Nghe giọng đọc câu này" : "Câu này chưa có giọng đọc"}">▶ Nghe</button>
    <button class="btn sm" data-act="clip" data-i="${esc(r.i)}" title="Đọc lại riêng câu này bằng giọng của job">Đọc lại câu</button>
    <span class="st" data-st="${esc(r.i)}"></span></div></div>`;
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
  const n = { all: rows.length, flag: 0, unrev: 0, manual: 0, empty: 0 };
  for (const r of rows) {
    if (r.s_flags && r.s_flags.length) n.flag++;
    if (!r.rv && !r.junk) n.unrev++;
    if (r.manual) n.manual++;
    if (!(r.vi || "").trim()) n.empty++;
  }
  if (rebuild || !body.querySelector("#sc-list")) {
    const opt = (k, label) => `<option value="${k}"${scriptFilter === k ? " selected" : ""}>${label} (${n[k]})</option>`;
    body.innerHTML = `<div class="sc-bar">
        <select id="sc-filter" aria-label="Lọc câu">${opt("all", "Tất cả")}${opt("flag", "Bị cờ")}${opt("unrev", "Chưa soát")}${opt("empty", "Trống")}${opt("manual", "Đã sửa tay")}</select>
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
      <div class="actions"><button class="btn primary" data-act="voice-save" type="button">Lưu cho job này</button></div>
      <p class="hint">Áp dụng cho lần lồng tiếng tiếp theo của job này. Đã lồng tiếng rồi thì sang tab Tổng quan bấm «Làm lại» ở bước Lồng tiếng
      (hoặc «Đọc lại câu» ở tab Bản dịch cho từng câu). Giọng mặc định cho mọi job mới: nút «Cài đặt» trên cùng.</p>
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

// ───────────── Cài đặt: cấu hình batch (D4) ─────────────
// Nhóm + thứ tự hiển thị. Khoá không có ở đây vẫn hiện ở nhóm «Khác» (backend thêm khoá mới không bị mất).
const CFG_GROUPS = [
  ["Lồng tiếng", ["voice", "speed", "tts_engine"]],
  ["Nhận dạng & dịch", ["asr_engine", "translate_mode", "review_backup", "ocr"]],
  ["Âm thanh", ["audio_mode", "voice_vol", "orig_vol"]],
  ["Hình ảnh & render", ["resolution", "fit", "zoom", "motion", "lut", "grain", "flip", "hardsub_blur", "vbitrate", "render_speed"]],
  ["Thương hiệu", ["brand_mode", "brand_text", "brand_logo", "frame"]],
];
let cfg = null;

function cfgField(k) {
  const v = cfg.opts[k], d = cfg.defaults[k], label = esc(cfg.labels[k] || k);
  const name = `name="${esc(k)}"`;
  if (typeof d === "boolean") {
    return `<label class="row"><input type="checkbox" ${name}${v === true || v === "true" ? " checked" : ""}> ${label}</label>`;
  }
  let input;
  if (k === "voice" && voices && voices.length) {
    const list = voices.includes(v) ? voices : [v, ...voices];
    input = `<select ${name}>${list.map((x) => `<option${x === v ? " selected" : ""}>${esc(x)}</option>`).join("")}</select>`;
  } else if (cfg.choices[k]) {
    input = `<select ${name}>${cfg.choices[k].map((x) => `<option${String(x) === String(v) ? " selected" : ""}>${esc(x)}</option>`).join("")}</select>`;
  } else if (typeof d === "number") {
    input = `<input type="number" step="0.01" ${name} value="${esc(v)}">`;
  } else {
    input = `<input ${name} value="${esc(v ?? "")}">`;
  }
  return `<label>${label}${input}</label>`;
}

async function openSettings() {
  const [c] = await Promise.all([api("GET", "/api/v6/config"), loadVoices().catch(() => null)]);
  cfg = c;
  const used = new Set(CFG_GROUPS.flatMap(([, ks]) => ks));
  const extra = Object.keys(cfg.defaults).filter((k) => !used.has(k));
  const groups = extra.length ? [...CFG_GROUPS, ["Khác", extra]] : CFG_GROUPS;
  $("#cfg-body").innerHTML = groups.map(([g, ks]) => {
    const fs = ks.filter((k) => k in cfg.defaults).map(cfgField).join("");
    return fs ? `<fieldset><legend>${esc(g)}</legend>${fs}</fieldset>` : "";
  }).join("");
  $("#cfg-meta").textContent = cfg.updated_at ? `Sửa lần cuối: ${cfg.updated_at}${cfg.updated_by ? ` · bởi ${cfg.updated_by}` : ""}` : "";
  $("#cfg-err").hidden = true;
  $("#dlg-cfg").showModal();
}

async function saveSettings(ev) {
  ev.preventDefault();
  const form = $("#form-cfg"), patch = {};
  for (const k of Object.keys(cfg.defaults)) {
    const el = form.elements[k];
    if (!el) continue;
    let v = el.type === "checkbox" ? el.checked : el.value;
    if (typeof cfg.defaults[k] === "number" && el.type !== "checkbox") v = Number(v);
    if (String(v) !== String(cfg.opts[k])) patch[k] = v;
  }
  if (!Object.keys(patch).length) { $("#dlg-cfg").close(); return; }
  const ok = $("#cfg-ok");
  ok.disabled = true;
  try {
    const r = await api("POST", "/api/v6/config", { patch });
    $("#dlg-cfg").close();
    const n = Object.keys(r.changed || {}).length;
    say(n ? `Đã lưu ${n} thay đổi — job đang chờ (chưa ghim cấu hình riêng) sẽ chạy theo cấu hình mới` : "Không có gì thay đổi");
  } catch (e) {
    $("#cfg-err").textContent = e.message; $("#cfg-err").hidden = false;
  } finally { ok.disabled = false; }
}

// ───────────── SSE ─────────────
function connect() {
  const es = new EventSource("/api/events");
  es.onopen = () => { $("#conn").className = "conn on"; $("#conn").textContent = "trực tiếp"; };
  es.onerror = () => { $("#conn").className = "conn off"; $("#conn").textContent = "mất kết nối — tự nối lại"; };
  es.addEventListener("snapshot", (e) => {
    jobs.clear();
    for (const j of JSON.parse(e.data).jobs) jobs.set(j.job_id, j);
    fullRedraw = true; schedule();
    refreshQueue();                           // 1 lần mỗi lần (nối / nối lại)
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
  if (e.target.id === "sc-filter") { scriptFilter = e.target.value; scriptShown = PAGE; paintScript(false); }
});
$("#detail").addEventListener("keydown", (e) => {
  if (e.target.matches("textarea.vi") && e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); e.target.blur(); }
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
$("#btn-cfg").addEventListener("click", (e) => act(e.currentTarget, "", openSettings));
$("#cfg-cancel").addEventListener("click", () => $("#dlg-cfg").close());
$("#form-cfg").addEventListener("submit", saveSettings);
$("#btn-new").addEventListener("click", () => { $("#new-err").hidden = true; $("#dlg-new").showModal(); $("#new-src").focus(); });
$("#new-cancel").addEventListener("click", () => $("#dlg-new").close());
$("#form-new").addEventListener("submit", createJob);

fetch("/api/health").then((r) => r.json()).then((h) => { $("#inst").textContent = `${h.instance} :${h.port}`; }).catch(() => {});
connect();
