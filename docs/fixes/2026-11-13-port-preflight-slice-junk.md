# Port V2 → V6: preflight video nguồn + extract_pcm bền + FireRed slice khoảng lặng + câu rác / park part (agent Q6, 2026-11-13)

- **Nguồn (chỉ đọc):** `Z:\PinelineV2` — docs `docs/fixes/2026-11-12-video-preflight.md` (agent K + mục C K2),
  `2026-11-13-firered-slice-silence.md` (L), `2026-11-13-simple-junk-park.md` (M); code hiện tại + backup gốc
  `.backup_edit_20261112_agentK`, `.backup_edit_20261113_agentK2`, `.backup_edit_20261113_agentL`, `.backup_edit_20261113_agentM`.
  Kèm 2 sửa của CHỦ DỰ ÁN (12/11 19:0x, có trong backup K): `media_ts.extract_pcm_from_video` + chỗ gọi ở
  `asr.extract_audio` / `separate._extract_orig_wav` / `media_ts.ensure_job_audio_ts`; server: parent ghép từ part →
  `cur_text` «đã ghép từ N parts» (không còn «đang chạy…» treo trên parent).
- **Backup:** `.backup_edit_20261113_agentQ6\` (giữ cây; `_md5_before.txt`, `_md5_after.txt`, `_NEW_FILES.txt`).
- **Cách port:** biến đổi import phẳng → package bằng `scripts/sync_from_v2.py` (rewrite_imports/bootstrap), rồi
  **3-way merge** (diff3: V6 hiện tại · V2 backup trước sửa · V2 hiện tại) cho từng file engine; server.py vá tay theo hunk
  (V6 đã khác V2 nhiều). Không đổi thuật toán.

## Đặt trong kiến trúc V6

| V2 | V6 | Ghi chú |
|---|---|---|
| `media_ts.py` (extract_pcm_from_video + K/K2: ngưỡng full ±`EXTRACT_FULL_TOL_SEC`, full dài thừa → chunk lại, `_force_wav_frames`, `_decode_errors`/`_dead_zero_run`, khúc con 5 s) | `engines/media_ts.py` | V6 trước đó CHƯA có extract_pcm (còn ffmpeg trích thẳng) → nay = V2 + bootstrap |
| `asr.extract_audio`, `separate._extract_orig_wav` | `engines/asr/asr.py`, `engines/separate.py` | dùng `extract_pcm_from_video` (sửa chủ dự án) |
| MỚI `source_preflight.py` | MỚI `engines/source_preflight.py` | `_write_download` → `state.job_state.mutate` (không `Manifest.update` trực tiếp); cờ: env `VIDEO_*` → cờ instance `PINELINE_FLAG_VIDEO_*` → `pineline.config.json` → mặc định |
| — | `core/instance.py` `apply_env` | khoá tuỳ chọn `video_preflight` / `video_normalize` trong `instances/*.json` (chỉ khi ghi rõ; mặc định module = bật) |
| `download.download_video` → `after_download` | `engines/download.py` | fatal → `js.mark_failed(download)` + raise |
| `server._finalize_imported_source` (`preflight_pending`), `server._find_video` (chỉ đọc) | `server.py` cùng tên | GET/preview không side effect (luật route mỏng) |
| `_find_video` của asr/separate/ocr/hardsub/render, `asr.resolve_asr_source` | `engines/{separate,ocr,render}.py`, `engines/asr/{asr,hardsub_asr}.py` | `stage_source_video` (preflight lười 1 lần, khoá `.preflight.lock`) |
| `render._find_video` | dùng bởi `workers/render_worker.py` (tiến trình con) | render/mux V6 đi qua render worker → tự nhận nguồn chuẩn hoá |
| `mux._find_video` | — | V6 không còn `engines/mux.py` (B11, đã ở `_to_delete`); test bỏ phần mux |
| `split_parts` kế thừa preflight | `engines/split_parts.py` | `inherit_payload` |
| `agent_av_sync` | `agents/agent_av_sync.py` | resolve nguồn chuẩn hoá |
| `firered_asr.py` (L: slice tại khoảng lặng + đệm chồng lấn; M: `_parse_firered_result` lọc rác) | `engines/asr/firered_asr.py` | `m.save()` trong `_on_slice` → `js.persist(m)` (thay đúng 1 chỗ persist cũ của vòng slice — ratchet không tăng) |
| MỚI `text_junk.py` | MỚI `engines/text_junk.py` | |
| `translate_simple/{cfg,pipeline,stage_review,validate}.py` (M) | `engines/translate_simple/…` | `store.save()` → `store.write()` (ratchet); `_brain_line` tìm `agents.agent_brain` |
| — | `pipeline/gates/simple_translate_gate.py` | quyết định vẫn theo status engine; ghi nhận `junk` / `review_missing` / `unreviewed_accepted` vào `ctx.extra["simple_gate"]` + log |
| server `_SPLIT_WAIT`, `_split_wait_clear/_parts/_ready`, nhánh `_queue_park_ready`, 2 chỗ gọi trong `_check_and_run_auto_split_parts`, gỡ chờ khi merge heal OK | `server.py` cùng tên | ghi qua `js.try_mutate` (không `Manifest.update`) |
| `tools/preflight_scan.py`, `tools/asr_boundary_scan.py` | `tools/` cùng tên | mặc định work root = `PINELINE_WORK_ROOT` → `core.instance.load().work_root` (bỏ `D:/PinelineV2_work`, `ROOT/work` cứng) |

Không làm Stage/Gate preflight riêng trong `pipeline/`: Runner V6 hiện chỉ chạy đoạn dịch simple; preflight phải chạy ở
mọi stage đầu tiên (kể cả tiến trình con engine/render worker) nên giữ dạng hàm lười `stage_source_video` như V2.

## Test

- MỚI (từ V2, import package): `tests/test_video_preflight.py` (13 + 2 V6: cờ instance, ghi qua job_state),
  `test_extract_pcm_robust.py`, `test_extract_pcm_timing.py`, `test_firered_slice_silence.py`, `test_simple_junk_park.py`.
- Sửa: `test_translate_simple.py::test_partial_lines_only_returned_rows_rv`,
  `test_translate_simple_scenarios.py::test_foreign_i_ignored_never_touches_context_rows` đặt `SIMPLE_UNREVIEWED_TOLERANCE=0`.
- Cloud (bản sao, stub fastapi/yt_dlp/google/soundfile; ffmpeg 6.1.1; `AGENT_BRAIN=0 python -m pytest -q tests`):
  trước **12 failed / 577 passed / 11 skipped / 11 xfailed / 3 errors** → sau **12 failed / 667 passed / 11 skipped /
  11 xfailed / 3 errors** — cùng danh sách fail/error (môi trường: thiếu TestClient/httpx, desktop, sfx…).
  `test_f1_ratchet` pass (không `.save()` mới, `js.persist` không đổi). `scripts/tree_audit.py` ERROR=0 (như trước);
  `scripts/verify_all.py --dry` PASS.

## Recheck trên Windows (:8806, chưa chạy)

1. `run_tests.bat` → không thêm fail so với trước.
2. Import 1 video lành → `download.preflight_pending=true`; chạy ASR → log `[preflight] nguồn lành`, không có `source_norm.*`.
3. Import bản sao `merged.mp4.bak_aac_corrupt` → ASR: `đã chuẩn hoá → source_norm.mkv`; `audio.wav` ≈ 7741.45 s.
4. ASR FireRed job > 20' → log `[firered] N slice, cắt tại khoảng lặng: …`; `python tools\asr_boundary_scan.py` (work V6).
5. `python tools\preflight_scan.py --quick` → bảng job, work không đổi.

## Nạp code

Engine (ASR/separate/OCR/render worker) chạy tiến trình con → hiệu lực lần chạy kế. `server.py`, `translate_simple`,
`download`, `core/instance` → cần khởi động lại backend V6 (:8806) — agent KHÔNG restart.

## Còn treo

- [ ] Chạy thật trên Windows (ffmpeg 8.1.2 gyan.dev) các mục Recheck.
- [ ] Job V6 cũ không có `preflight_pending` → không tự kiểm lại (như V2); quét chỉ-đọc bằng `tools/preflight_scan.py`.
