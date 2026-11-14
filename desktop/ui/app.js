// Pineline V6 UI desktop — KHÔNG polling.
//   • Một kết nối SSE /api/events: snapshot (lúc nối) → job.updated / job.removed / job.touched / stage.progress.
//   • Request HTTP CHỈ phát sinh khi: người dùng bấm nút; mở chi tiết 1 job; job đang mở / hàng chờ thật sự đổi
//     (theo event, có gộp nhịp bằng setTimeout). Để yên = 0 request.
//   • Vẽ gộp theo requestAnimationFrame → 100 event/giây vẫn chỉ ~60 lần vẽ, chỉ đụng hàng đổi.
"use strict";
const STAGES = ["download", "asr", "translate", "tts", "mux"];
const STAGE_VI = { download: "Tải nguồn", asr: "Nhận dạng tiếng (ASR)", translate: "Dịch", tts: "Lồng tiếng (TTS)", mux: "Ghép / render" };
const jobs = new Map();          // job_id → hàng tóm tắt (từ SSE)
const dirty = new Set();
const picked = new Set();        // job_id được tick
let filter = "", rafQueued = false, fullRedraw = true;
let selected = null, detail = null, queue = { queue: [], current: null, busy: false };
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
  if (row.job_id === selected) { paintDetail(); refreshDetailSoon(); }
}

function syncToolbar() {
  for (const id of [...picked]) if (!jobs.has(id)) picked.delete(id);
  const n = picked.size;
  $("#sel-count").textContent = n ? `đã chọn ${n}` : "";
  $("#btn-enqueue").disabled = n === 0;
  $("#btn-dequeue").disabled = ![...picked].some(inQueue);
  $("#btn-q-stop").disabled = !queue.busy;
  const vis = visible();
  $("#ck-all").checked = vis.length > 0 && vis.every((j) => picked.has(j.job_id));
}

// ───────────── hàng chờ ─────────────
function paintQueue() {
  const cur = queue.current ? (jobs.get(queue.current)?.title || queue.current) : null;
  const n = (queue.queue || []).length;
  $("#q-state").textContent = cur ? `đang chạy: ${cur}${n ? ` · chờ ${n} job` : ""}` : (n ? `chờ ${n} job` : "trống");
  fullRedraw = true; schedule();
}
async function refreshQueue() {
  try { queue = await api("GET", "/api/queue/jobs"); paintQueue(); } catch (_) { /* SSE báo mất kết nối riêng */ }
}
const refreshQueueSoon = debounce(refreshQueue, 600);

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
  if (!selected || !j) { box.hidden = true; box.innerHTML = ""; return; }
  box.hidden = false;
  const running = j.status === "running";
  const held = j.status === "held";
  const stages = STAGES.map((s) => {
    const sum = j.stages?.[s] || "pending";
    const st = detail?.stages?.[s] || {};
    const text = st.error ? `<div class="err">${esc(st.error)}</div>` : (st.cur_text ? `<div class="sub">${esc(st.cur_text)}</div>` : "");
    return `<li><div class="st-head"><span class="b ${esc(sum)}">${esc(sum)}${esc(pct(st))}</span><b>${esc(STAGE_VI[s])}</b>`
      + `<button class="btn sm" data-act="stage" data-stage="${s}"${running ? " disabled" : ""} title="Chạy riêng bước này">${sum === "done" ? "Chạy lại" : "Chạy"}</button></div>${text}</li>`;
  }).join("");
  box.innerHTML = `
    <div class="d-head"><h2 title="${esc(j.job_id)}">${esc(j.title)}</h2><button class="btn sm" data-act="close" title="Đóng">✕</button></div>
    <div class="d-id">${esc(j.job_id)} · <span class="b ${esc(j.status)}">${esc(j.status)}</span>${inQueue(j.job_id) ? " · trong hàng chờ" : ""}</div>
    ${held ? `<div class="hold">⏸ Đang tạm dừng: <b>${esc(j.held_at || "")}</b><br>${esc(j.hold_reason || "")}</div>` : ""}
    ${err ? `<div class="err">Không đọc được chi tiết: ${esc(err)}</div>` : ""}
    <div class="d-actions">
      ${held ? '<button class="btn primary" data-act="resume" title="Gỡ tạm dừng và chạy tiếp">Chạy tiếp</button>' : ""}
      <button class="btn primary" data-act="enqueue"${running || inQueue(j.job_id) ? " disabled" : ""} title="Thêm vào hàng chờ, chạy hết các bước theo cấu hình batch">Xếp hàng chạy</button>
      <button class="btn danger" data-act="stop"${running ? "" : " disabled"}>Dừng</button>
      <button class="btn" data-act="reveal" title="Mở thư mục job trên máy">Mở thư mục</button>
      <button class="btn danger ghost" data-act="delete"${running ? " disabled" : ""}>Xoá job</button>
    </div>
    <ol class="stages">${stages}</ol>
    <p class="hint">Sửa lời thoại, chọn giọng, cấu hình render: <a href="/ui3">mở Studio cũ</a>.</p>`;
}

async function detailAction(btn) {
  const id = selected, a = btn.dataset.act;
  if (!id) return;
  const enc = encodeURIComponent(id);
  const title = jobs.get(id)?.title || id;
  if (a === "close") return select(null);
  if (a === "enqueue") return act(btn, "Đã xếp vào hàng chờ", async () => { await api("POST", "/api/queue/jobs", { job_ids: [id] }); await refreshQueue(); });
  if (a === "resume") return act(btn, "Đã gỡ tạm dừng, chạy tiếp", async () => { await api("POST", "/api/agent/resume/" + enc); await refreshQueue(); });
  if (a === "stop") return act(btn, "Đã gửi lệnh dừng", async () => { await api("POST", `/api/jobs/${enc}/stop`); await refreshQueue(); });
  if (a === "reveal") return act(btn, "", () => api("POST", `/api/jobs/${enc}/reveal`));
  if (a === "stage") return act(btn, `Đã chạy bước ${btn.dataset.stage}`, () => api("POST", `/api/jobs/${enc}/run/${btn.dataset.stage}`));
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
$("#btn-new").addEventListener("click", () => { $("#new-err").hidden = true; $("#dlg-new").showModal(); $("#new-src").focus(); });
$("#new-cancel").addEventListener("click", () => $("#dlg-new").close());
$("#form-new").addEventListener("submit", createJob);

fetch("/api/health").then((r) => r.json()).then((h) => { $("#inst").textContent = `${h.instance} :${h.port}`; }).catch(() => {});
connect();
