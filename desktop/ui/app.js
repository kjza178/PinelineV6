// Pineline V6 UI — KHÔNG polling. Một kết nối SSE /api/events:
//   snapshot (lúc nối) → job.updated / job.removed / stage.progress.
// Vẽ lại gộp theo requestAnimationFrame → 100 event/giây vẫn chỉ ~60 lần vẽ, chỉ đụng hàng đổi.
"use strict";
const STAGES = ["download", "asr", "translate", "tts", "mux"];
const jobs = new Map();          // job_id → row data
const dirty = new Set();
let filter = "", rafQueued = false, fullRedraw = true;
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

function schedule() {
  if (!rafQueued) { rafQueued = true; requestAnimationFrame(paint); }
}

function rowHtml(j) {
  const cells = STAGES.map((s) => `<td><span class="b ${esc(j.stages?.[s])}">${esc(j.stages?.[s] || "-")}</span></td>`).join("");
  const note = j.status === "held" ? `⏸ ${j.held_at || ""} ${j.hold_reason || ""}` : (j.cur_text || "");
  return `<td title="${esc(j.job_id)}">${esc(j.title)}</td><td><span class="b ${esc(j.status)}">${esc(j.status)}</span></td>${cells}<td class="note">${esc(note)}</td>`;
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
    tbody.innerHTML = list.map((j) => `<tr data-id="${esc(j.job_id)}">${rowHtml(j)}</tr>`).join("");
    $("#count").textContent = `${list.length}/${jobs.size} job`;
    return;
  }
  for (const id of dirty) {
    const tr = tbody.querySelector(`tr[data-id="${CSS.escape(id)}"]`);
    const j = jobs.get(id);
    if (!tr || !j) { fullRedraw = true; continue; }
    tr.innerHTML = rowHtml(j);
    tr.classList.remove("flash"); void tr.offsetWidth; tr.classList.add("flash");
  }
  dirty.clear();
  if (fullRedraw) schedule();
}

function upsert(row) {
  const prev = jobs.get(row.job_id);
  jobs.set(row.job_id, { ...prev, ...row });
  if (!prev || prev.status !== row.status) fullRedraw = true;   // đổi thứ tự sắp xếp
  else dirty.add(row.job_id);
  schedule();
}

function connect() {
  const es = new EventSource("/api/events");
  es.onopen = () => { $("#conn").className = "conn on"; $("#conn").textContent = "trực tiếp"; };
  es.onerror = () => { $("#conn").className = "conn off"; $("#conn").textContent = "mất kết nối — tự nối lại"; };
  es.addEventListener("snapshot", (e) => {
    jobs.clear();
    for (const j of JSON.parse(e.data).jobs) jobs.set(j.job_id, j);
    fullRedraw = true; schedule();
  });
  es.addEventListener("job.updated", (e) => upsert(JSON.parse(e.data)));
  es.addEventListener("job.removed", (e) => { jobs.delete(JSON.parse(e.data).job_id); fullRedraw = true; schedule(); });
  es.addEventListener("stage.progress", (e) => {
    const d = JSON.parse(e.data), j = jobs.get(d.job_id);
    if (!j) return;
    if (d.cur_text) j.cur_text = d.cur_text;
    dirty.add(d.job_id); schedule();
  });
}

$("#q").addEventListener("input", (e) => { filter = e.target.value; fullRedraw = true; schedule(); });
fetch("/api/health").then((r) => r.json()).then((h) => { $("#inst").textContent = `${h.instance} :${h.port}`; }).catch(() => {});
connect();
