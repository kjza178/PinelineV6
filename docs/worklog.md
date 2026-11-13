# Worklog V6

## 2026-11-11 — F0 khung
- Tạo khung: core/instance, events/bus (SSE + throttle + replay Last-Event-ID), state/job_state (nguồn ghi duy nhất, Manifest.update), state/index (JobIndex bộ nhớ), pipeline/base (Stage/Gate/Runner), runtime/context, workers/render_worker (subprocess, JSON-lines), app (FastAPI: health, jobs, events), instances/{v6,prod,test}.json.
- Desktop: Tauri 2 shell tự spawn backend, chờ cổng, chuyển WebView sang UI cùng origin; tắt app → kill backend nó spawn. UI SSE + requestAnimationFrame, không setInterval.
- Test: 31/31 pass (container, Python stdlib + pytest). Chưa build Rust (cần crate từ mạng — build trên Windows).
- instances/prod.json chỉ dùng lúc cutover F6 (cùng cổng 8801 với V2 → không chạy song song được, cố ý).
- 11-11: fix F0 startup (import fastapi đầu module) + encoding test/bat — docs/fixes/2026-11-11-f0-startup-encoding.md
- 11-11: F1 B9 — xem docs/fixes/2026-11-11-f1-b9-single-write-path.md
- 11-11: F2 + F3 tích hợp — docs/fixes/2026-11-11-f2-f3-integration.md (backup .backup_edit_20261111_f2f3/).
- 11-11: review độc lập F2+F3 — docs/reviews/2026-11-11-review-f2-f3.md (backup .backup_edit_20261111_review1/);
  cloud 105 passed / 1 skipped; tree_audit ERROR=0.
- 11-11: F4 services + sidecar thuần, F5 render tiến trình con — docs/fixes/2026-11-11-f4-f5.md
  (backup .backup_edit_20261111_f4f5/; agent_feeder.py dựng lại = V5 byte-for-byte). Cloud 144 passed / 1 skipped
  (trước 105/1); tests/v5 88/11/12 như cũ; ratchet persist 93; tree_audit ERROR=0, ports=0.
- 11-11: review2 độc lập F4+F5 — docs/reviews/2026-11-11-review-f4-f5.md (backup .backup_edit_20261111_review2/).
  2 nghiêm trọng (UI gọi :8802/:8801 V2; restart_server --sidecar giết sidecar V2 / PORT lạc giết server V2) + 3 cao
  (migrate_work, Dừng render không kill cháu → kẹt pipe, sidecar trần poll bot V2) + M/L — đã sửa, 16 test mới.
  Cloud 159 passed / 2 skipped (trước 144/1); tests/v5 88/11/12 như cũ; ratchet persist 93.
  VM: tree_audit ERROR=0 · ports=0 (import=7, dups=1 — WARN, chưa so với lần trước); ast.parse 11 file sửa: Pass.
- 11-11: F6 lượt kiểm cuối — docs/fixes/2026-11-11-f6-final.md (backup .backup_edit_20261111_final/). Cloud 192 passed /
  2 skipped (trước 159/2); tests/v5 88/11/12 như cũ; verify_all --only a,c,d: a PASS, c PASS, d SKIP (thiếu fastapi).
  Brain V6 sinh (155 thẻ). Bàn giao docs/V6_HANDOFF.md. Không cutover, không đụng V2.
- 11-11 13:2x: verify_all fix + chặn đổi preset llama trong test — docs/fixes/2026-11-11-verify-all-preset-switch.md
- 11-11: machine-config (V6 + port V5/V4 theo yêu cầu chủ dự án) — docs/fixes/2026-11-11-machine-config.md
  (backup .backup_edit_20261111_machine_cfg/ ở cả 3 cây). Cloud V6 210 passed / 2 skipped (+14 test); tests/v5 88/11/12 như cũ;
  tree_audit ERROR=0; doctor --offline chạy trên VM ở cả 3 cây; V4/V5 test port 13 passed mỗi cây (cloud).

## 2026-11-12 — port simple + Demucs từ V2 (agent F6)
- Port `translate_simple` (→ engines + Stage/Gate), `separate.py`/`asr.py` mới, lexicon, cloud_retry, tools, 6 file test V2
  + 13 test V6. Backup `.backup_edit_20261112_agentF6/`. Cloud trước 244/2 → sau 354/9; tests/v5 không đổi; tree_audit ERROR=0.
  docs/fixes/2026-11-12-port-simple-demucs.md

## 2026-11-12 — port brain G+H+I2 + F2 luồng theo job từ V2 (agent P6)
- 3-way merge agent_brain/heal/watch/hold/routes_agent/autopilot/tele_cmd; mới agent_brain_notify, agent_hold, js.try_mutate;
  server: gỡ HOLD tay 5 route, _auto_gpu tôn trọng hold_override, đuôi log _run_engine + render worker, job_pipeline.
  Backup `.backup_edit_20261112_agentP6/`. Cloud 440 → 494 pass (stub fastapi 501 → 562), cùng fail; ratchet 93 → 91;
  verify_all --dry PASS; tree_audit ERROR=0. docs/fixes/2026-11-12-port-brain-hold-pipeline.md

## 2026-11-12 — sửa 4 fail pytest Windows sau port F6/P6 (agent J)
- 1–2 `test_app_integration` (v6/jobs, snapshot SSE): JobIndex toàn cục nhận MỌI lần ghi manifest (hook) + `load_all` cộng
  dồn; 3 file test P6 mới (`test_agent_*`, xếp trước) ghi job tạm id thật-giống → lọt. Hồi quy do thứ tự test P6, lỗi
  thiết kế có từ F3. Sửa gốc: index gắn work root (`bind/owns/apply_dir/unbind`). Tái hiện cloud: bản gốc
  ['2937ffd7989f','f0834acf0cc0','aaaaaaaaaaaa','job-a','jB'] → sau sửa ['job-a']; work root "thật" giả lập (REAL_*) md5 không đổi.
- 3 ratchet: `_scratch*`, `scratch*`, `_to_delete*`, `.backup*` bỏ qua; nháp agent → `_to_delete/scratch_20261112/`.
- 4 restart probe: `encoding="utf-8", errors="replace"`, `PYTHONIOENCODING=utf-8` (verify_all.bat đặt PYTHONUTF8=1 nên
  trước giờ chỉ pytest trần trên Windows mới lỗi; restart_server không đổi).
- Backup `.backup_edit_20261112_agentJ/`. Cloud (stub fastapi): 563→566 pass, fail còn lại = môi trường cloud (không đổi).

## 2026-11-13 — port preflight video + extract_pcm + FireRed slice + câu rác/park part từ V2 (agent Q6)
- sync_from_v2 (import) + diff3 3-way cho engine; server vá tay 8 hunk (preflight import/_find_video, split_wait ×5,
  cur_text parent). Ghi manifest mới qua `js.mutate`/`js.try_mutate`/`js.mark_failed`; `.save()` → `persist`/`write`.
  Backup `.backup_edit_20261113_agentQ6/` (md5 trước/sau). Cloud 577 → 667 pass, cùng 12 fail + 3 error môi trường;
  ratchet giữ; tree_audit ERROR=0; verify_all --dry PASS. docs/fixes/2026-11-13-port-preflight-slice-junk.md

## 2026-11-13 — port brain bỏ job xong + phân loại park/HOLD từ V2 (agent R6)
- patch 3 chiều (base = V2 `.backup_edit_20261113_agentN`) lên V6: 1 hunk vá tay (`apply_actions` heal_job), import → `agents.*`.
  Không thêm `.save()`/`js.persist`. Backup `.backup_edit_20261113_agentR6/` (md5 trước/sau). Cloud 669 → 692 pass, cùng 10 fail +
  3 error môi trường; ratchet giữ; verify_all --dry PASS. docs/fixes/2026-11-13-port-brain-finished-hold.md
