# 2026-11-11 13:08 — verify_all FAIL + test đổi preset llama THẬT

## Triệu chứng (scratch/verify_all_20261111_130827.log)
- a) 1 fail: test_app_integration::test_events_snapshot — StopIteration khi tìm route /api/events trong app.routes.
- b) 1 fail: check_batch3 — 2 kiểm tĩnh tìm chuỗi code cũ (batch_config.resolve_job_opts) đã đổi ở F2.
  Và faulthandler: tests/v5/test_review_fail_not_rv.py treo trong preset_switcher._switch_preset_unlocked.
- d) /ui3 còn ":8802" (nhãn chữ «Sidecar :8802 tắt», URL đã được đổi).

## Nghiêm trọng: test gọi switch_preset THẬT
Test giả lỗi `ValueError("boom (không phải quota)")`; `_review_chunk_with_fallback` dò chữ "quota" → coi là
hết hạn mức → `preset_switcher.switch_preset("qwen36-turbo")` → chạy `E:\models\presets\start-model.ps1`
→ **restart llama-server :8083 dùng chung V2**. Test có cả ở V2/V4/V5 (viết lúc fix B1).

## Sửa (V6)
- `tools/preset_switcher.switch_allowed()`: từ chối khi đang pytest, hoặc khi `PINELINE_FLAG_PRESET_SWITCH`=0.
- Cờ instance mới `preset_switch`: v6/test **false**, prod true (V6 chạy job thật không được đổi preset llama dùng chung).
- Test: câu lỗi không còn chữ "quota" + monkeypatch switch_preset ném lỗi nếu bị gọi.
- `tests/test_preset_guard.py` (4 test). test_f3_static cập nhật bộ cờ.
- localize_html đổi cả nhãn ":8802"/":8801" sang cổng instance.
- test_events_snapshot: tìm route đệ quy (FastAPI mới).
- check_batch3: kiểm theo code job_opts.
- V2/V4/V5: `scripts/fix_runall_decorator_v2v4v5.py` nay sửa thêm câu lỗi trong test (user tự chạy).
Backup: .backup_edit_20261111_presetguard/.

## Recheck
- Cloud: 196 passed, 2 skipped; test_review_fail_not_rv 4 passed (0,2 s, không treo).
- Windows: chờ verify_all.bat lần 2.
