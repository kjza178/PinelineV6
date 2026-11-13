"""Test kế thừa từ V5.

KNOWN_DRIFT: test hỏng TỪ TRƯỚC ở V2/V5 (code không có hàm test gọi, hoặc logic đã đổi mà test chưa sửa)
— kiểm 2026-11-11: các hàm film_db.get_many, job_queue.build_queue_items, server._run_translate_engine
không tồn tại ở cả V2/V4/V5; _is_modernish_genre(mode=mixed) cố ý trả False từ trước.
Đánh xfail (không strict) để test V6 có tín hiệu sạch; xử lý khi tách module ở F3–F5 (viết lại hoặc xoá test).
V5_ONLY: kiểm bố cục riêng của V5 (apps/run_v5.bat) — không áp dụng V6.
"""
import pytest

KNOWN_DRIFT = {
    "test_film_db_queue_meta.py::test_row_from_manifest_fills_series_ui": "KeyError group_index — test cũ",
    "test_film_db_queue_meta.py::test_sync_and_get_many_db_first": "film_db.get_many chưa từng có",
    "test_queue_http_state.py::test_http_queue_state_batch_and_current_detail": "job_queue.build_queue_items chưa từng có",
    "test_translate_force_default.py::test_translate_flow_force_false_does_not_pass_true": "server._run_translate_engine chưa từng có",
    "test_translate_force_default.py::test_translate_flow_force_true_passes_true": "server._run_translate_engine chưa từng có",
    "test_translate_force_default.py::test_translate_flow_review_only_skips_draft": "server._run_translate_engine chưa từng có",
    "test_pronoun_postpass.py::test_modernish_mixed": "mixed cố ý không detox (translate._is_modernish_genre)",
    "test_pronoun_postpass.py::test_detox_nguoi_ta": "theo test_modernish_mixed",
    "test_pronoun_consistency.py::test_filter_review_fixes_when_locked": "logic lock đổi, test chưa cập nhật",
    # Xác nhận bằng scripts/baseline_v5_tests.bat (11-11): cũng FAIL khi chạy trên chính cây V5.
    "test_restart_guard.py::test_kill_skips_self": "fail cả trên V5 (baseline 11-11)",
    "test_review_batches.py::test_review_batch[check_batch5]": "stub _chat_turn thiếu tool_choice — fail cả trên V5",
}
V5_ONLY = {
    "test_layout_paths.py::test_unity_apps_stub_and_pineline_shells_gone": "bố cục apps/run_v5.bat của V5",
    "test_layout_paths.py::test_find_index_absolute_even_if_cwd_windows": "routes_ui._HERE theo bố cục V5",
    "test_layout_paths.py::test_meta_thumbnail_dir_is_project_data": "routes_hg 'parent.parent' theo bố cục V5",
    "test_layout_paths.py::test_core_package_layout": "test tự mâu thuẫn: đòi import core.job_queue lỗi",
    "test_layout_paths.py::test_engines_phase_e_roots": "đòi import gốc lỗi theo bố cục V5",
    "test_review_batches.py::test_review_batch[check_batch2]": "kiểm apps/*.bat + .claude của cây V5",
}


@pytest.fixture(autouse=True)
def _no_background_publish_drain(request, monkeypatch):
    """auto_publisher.publish_job tự bật luồng drain nền khi hàng còn job; luồng của test trước đọc
    DATA_DIR (biến module đã bị test sau monkeypatch) → giành job của test sau → flaky
    ('job already queued/active in current process'). Test không được để luồng nền chạy."""
    if request.node.path.name == "test_auto_publisher_dedup.py":
        import agents.auto_publisher as ap
        monkeypatch.setattr(ap, "_drain_publish_queue_async", lambda *a, **k: None)
    yield


def pytest_collection_modifyitems(config, items):
    for it in items:
        key = f"{it.path.name}::{it.name}"
        if key in KNOWN_DRIFT:
            it.add_marker(pytest.mark.xfail(reason="V5 drift: " + KNOWN_DRIFT[key], strict=False))
        elif key in V5_ONLY:
            it.add_marker(pytest.mark.skip(reason=V5_ONLY[key]))
