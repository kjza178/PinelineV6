// static/live_sync.js — V6 F3: UI cũ (index3.html / index.html) HẾT polling 2 s.
// Một kết nối SSE /api/events (bus của state): snapshot / job.updated / job.removed → refresh() GỘP ~300 ms;
// stage.progress / job.touched (tiến độ, ghi manifest liên tục) → gộp ~2 s (= nhịp poll cũ khi chạy, không
// dồn /api/jobs nặng lên 3 lần/giây). Không bao giờ chạy chồng.
// Dự phòng: refresh mỗi 30 s; có job đang chạy (opts.busy()) → mỗi 5 s (tiến độ ASR hybrid đọc từ file
// asr.*.progress.json, KHÔNG qua manifest → không có event). EventSource lỗi liên tục (≥ 3 lần) hoặc bị
// trình duyệt ĐÓNG hẳn (readyState=CLOSED: HTTP lỗi / sai content-type) → poll 5 s, thử SSE lại sau 60 s.
// Tab ẩn → không refresh (đánh dấu cũ); hiện lại → refresh ngay.
// Thuần JS, không phụ thuộc DOM → chạy được trong Node để test (tests/test_live_sync_js.py).
(function (root) {
  "use strict";

  const FAST_EVENTS = ["snapshot", "job.updated", "job.removed"];
  const SLOW_EVENTS = ["stage.progress", "job.touched"];
  const LIVE_EVENTS = FAST_EVENTS.concat(SLOW_EVENTS);
  const ES_CLOSED = 2;

  function defaultTimers() {
    return {
      setTimeout: (f, ms) => setTimeout(f, ms),
      clearTimeout: (h) => clearTimeout(h),
      setInterval: (f, ms) => setInterval(f, ms),
      clearInterval: (h) => clearInterval(h),
    };
  }

  // kick(ms?): hẹn chạy fn sau ms (mặc định delayMs; nhiều kick dồn lại = 1 lần; kick NGẮN hơn hẹn đang chờ
  // → hẹn lại sớm hơn). Đang chạy mà có kick → chạy thêm ĐÚNG 1 lần sau khi xong. now(): chạy ngay (bỏ hẹn).
  // Lỗi trong fn → console.error, không vỡ vòng.
  function makeCoalescer(fn, delayMs, timers) {
    const T = timers || defaultTimers();
    let timer = null, timerMs = 0, inflight = false, again = false, runs = 0;
    function fire() {
      timer = null;
      if (inflight) { again = true; return Promise.resolve(); }
      inflight = true; runs++;
      let p;
      try { p = Promise.resolve(fn()); } catch (e) { p = Promise.reject(e); }
      return p.catch((e) => { try { console.error("[live] refresh lỗi:", e); } catch (_) { /* */ } })
        .then(() => {
          inflight = false;
          if (again) { again = false; kick(); }
        });
    }
    function kick(ms) {
      const d = (typeof ms === "number" && ms >= 0) ? ms : delayMs;
      if (timer !== null) {
        if (d >= timerMs) return;
        T.clearTimeout(timer); timer = null;
      }
      timerMs = d;
      timer = T.setTimeout(fire, d);
    }
    function now() {
      if (timer !== null) { T.clearTimeout(timer); timer = null; }
      return fire();
    }
    return {
      kick, now,
      get pending() { return timer !== null; },
      get inflight() { return inflight; },
      get runs() { return runs; },
    };
  }

  function startLive(opts) {
    const o = Object.assign({
      url: "/api/events", debounceMs: 300, slowMs: 2000, fallbackMs: 30000, busyMs: 5000, pollMs: 5000,
      maxErrors: 3, retrySseMs: 60000, busy: null,
    }, opts || {});
    if (typeof o.refresh !== "function") throw new Error("startLive: thiếu refresh()");
    const ES = o.EventSource !== undefined ? o.EventSource : root.EventSource;
    const doc = o.document !== undefined ? o.document : root.document;
    const T = o.timers || defaultTimers();
    const co = makeCoalescer(o.refresh, o.debounceMs, T);
    let es = null, errs = 0, pollTimer = null, retryTimer = null, mode = "sse", closed = false, stale = false;
    const setMode = (m) => { if (mode !== m) { mode = m; if (o.onMode) { try { o.onMode(m); } catch (_) { /* */ } } } };
    const hidden = () => !!(doc && doc.hidden);
    const isBusy = () => { try { return !!(o.busy && o.busy()); } catch (_) { return false; } };
    function kick(ms) {
      if (hidden()) { stale = true; return; }   // tab ẩn: không gọi API; hiện lại sẽ refresh
      co.kick(ms);
    }
    // Dự phòng: nhịp busyMs; đang có job chạy → refresh mỗi nhịp, rảnh → mỗi fallbackMs.
    let idle = 0;
    const tick = Math.min(o.busyMs, o.fallbackMs);
    const fallbackTimer = T.setInterval(() => {
      idle += tick;
      if (isBusy() || idle >= o.fallbackMs) { idle = 0; kick(); }
    }, tick);
    let onVis = null;
    if (doc && typeof doc.addEventListener === "function") {
      onVis = () => { if (!doc.hidden && stale) { stale = false; co.now(); } };
      doc.addEventListener("visibilitychange", onVis);
    }

    function startPoll() {
      if (pollTimer === null) pollTimer = T.setInterval(() => kick(), o.pollMs);
      setMode("poll");
    }
    function stopPoll() {
      if (pollTimer !== null) { T.clearInterval(pollTimer); pollTimer = null; }
    }
    function giveUpSse() {
      if (es) { try { es.close(); } catch (_) { /* */ } }
      es = null;
      startPoll();
      if (retryTimer === null) {
        retryTimer = T.setTimeout(() => { retryTimer = null; errs = 0; open(); }, o.retrySseMs);
      }
    }
    function open() {
      if (closed) return;
      if (!ES) { startPoll(); return; }
      let cur;
      try { cur = es = new ES(o.url); } catch (e) { es = null; startPoll(); return; }
      cur.onopen = () => { if (es !== cur) return; errs = 0; stopPoll(); setMode("sse"); };   // snapshot đi ngay sau → refresh
      cur.onerror = () => {
        if (es !== cur) return;          // sự kiện muộn của kết nối đã bỏ
        errs++;
        // CLOSED: trình duyệt KHÔNG tự nối lại nữa (HTTP lỗi / sai content-type) → bỏ ngay, không chờ đủ 3 lỗi.
        if (errs >= o.maxErrors || cur.readyState === ES_CLOSED) giveUpSse();
      };
      for (const t of FAST_EVENTS) cur.addEventListener(t, () => kick(o.debounceMs));
      for (const t of SLOW_EVENTS) cur.addEventListener(t, () => kick(o.slowMs));
    }
    open();
    co.now();   // tải lần đầu ngay
    return {
      kick: co.kick,
      now: co.now,
      get mode() { return mode; },
      get errors() { return errs; },
      get runs() { return co.runs; },
      get stale() { return stale; },
      close() {
        closed = true;
        T.clearInterval(fallbackTimer); stopPoll();
        if (retryTimer !== null) { T.clearTimeout(retryTimer); retryTimer = null; }
        if (es) { try { es.close(); } catch (_) { /* */ } es = null; }
        if (onVis && doc && typeof doc.removeEventListener === "function") doc.removeEventListener("visibilitychange", onVis);
      },
    };
  }

  const api = { makeCoalescer, startLive, LIVE_EVENTS, FAST_EVENTS, SLOW_EVENTS };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.PinelineLive = api;
})(typeof window !== "undefined" ? window : globalThis);
