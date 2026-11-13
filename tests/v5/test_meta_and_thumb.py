import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path
import json
import agents.meta_studio as meta_studio
import agents.auto_publisher as auto_publisher


def test_format_youtube_title():
    # Test title with embedded part in sentence
    raw_han_viet = "Trọng Sinh Mạt Thế, Tích Trữ Trăm Tỷ Vật Tư, Dẫn Đội Đột Kích Phá Đảo Phần 2"
    formatted = auto_publisher.format_youtube_title(raw_han_viet, season=2)
    assert formatted == raw_han_viet
    assert not formatted.startswith("Phần 2 | Phần 2")

    # Test removing markdown bold, duplicate season prefix, colon to hyphen
    raw = "**Phần 1 | Thiên Đạo Chi Nữ: Vừa Về Nhà Đã Bị Mẹ Kế Hãm Hại**"
    formatted = auto_publisher.format_youtube_title(raw, clean_name="Thiên Đạo Chi Nữ", season=1)
    assert "**" not in formatted
    assert " - " in formatted
    assert formatted.startswith("Phần 1 | ")


def test_generate_youtube_titles_fallback():
    # When LLM fails, fallbacks follow Premise + «Ta …» formula
    with patch("agents.meta_studio._call_chatgpt", side_effect=RuntimeError("LLM down")):
        res = meta_studio.generate_youtube_titles(
            series_name="Thiên Đạo Chi Nữ",
            season=1,
            episode_info="Tập 1 cô bé bị mẹ kế hãm hại nhưng gọi sấm sét",
            count=5,
        )
        assert res["ok"] is True
        assert len(res["titles"]) == 5
        for t in res["titles"]:
            assert "Nghịch thiên cải mệnh" not in t
            assert "Đồ thần" not in t
            assert t.startswith("Phần 1 | ")
            assert ", Ta " in t or ", Ta" in t


def test_generate_youtube_titles_success():
    fake_json = {
        "titles": [
            "Chuyển Sinh Thành Con Gái Thiên Đạo, Gọi Sấm Sét Vạch Trần Mẹ Kế Phần 1",
            "Con Gái Thiên Đạo Chuyển Sinh, Bị Vu Oan Trộm Ngọc Rồi Gọi Sấm Sét Phần 1",
            "Chuyển Sinh Thiên Đạo, Cô Bé Bị Vu Oan Trộm Ngọc Khiến Cả Gia Tộc Quỳ Lạy Phần 1",
            "Chuyển Sinh Thành Thiên Kim, Mẹ Kế Vu Oan Trộm Ngọc Liền Bị Sấm Sét Đánh Tan Phần 1",
            "Con Gái Thiên Đạo Chuyển Sinh, Nổi Giận Gọi Sấm Sét Vạch Trần Mẹ Kế Phần 1"
        ],
        "best": "Con Gái Thiên Đạo Chuyển Sinh, Nổi Giận Gọi Sấm Sét Vạch Trần Mẹ Kế Phần 1",
        "reason": "Giữ đúng từ khóa Chuyển Sinh, số Phần 1 và hai hook cụ thể là thân phận con gái Thiên Đạo cùng màn gọi sấm sét vạch trần mẹ kế."
    }
    with patch("agents.meta_studio._call_chatgpt", return_value=json.dumps(fake_json)):
        res = meta_studio.generate_youtube_titles(
            series_name="Con Gái Cưng Của Thiên Đạo",
            season=1,
            episode_info="Cô bé bị vu oan gọi sấm sét",
            count=5,
            title_zh="天道之女",
        )
        assert res["ok"] is True
        assert len(res["titles"]) == 5
        assert res["best"] == fake_json["best"]
        assert res["best_title"] == fake_json["best"]
        assert "Chuyển Sinh" in res["titles"][0]


def test_hooks_from_title_and_sanitize():
    title = "Võ Đạo Cộng Điểm: Lão Lục Thời Loạn Thành Thánh"
    hooks = meta_studio.hooks_from_series_title(title, "武道加点：乱世老六成圣")
    assert "LÃO" in hooks[1] and "LỤC" in hooks[1]
    assert "ẨN" not in "".join(hooks)
    # cliché → fallback tên phim
    assert "THỨC TỈNH" not in "".join(meta_studio.sanitize_hook_texts(["THỨC TỈNH", "TUYỆT THẾ"], title))
    # «ẨN NHẪN» không neo tên → thay bằng vế đúng
    fixed = meta_studio.sanitize_hook_texts(["CỘNG ĐIỂM VÕ ĐẠO", "ẨN NHẪN THÀNH THÁNH"], title)
    assert "ẨN NHẪN" not in fixed
    assert "LÃO" in "".join(fixed) or "CỘNG" in "".join(fixed)
    prompt = meta_studio.build_master_image_prompt(
        title_vi=title, story_moment="mine fight", badge_text="TRỌN BỘ THUYẾT MINH",
        hook_texts=["CỘNG ĐIỂM VÕ ĐẠO", "LÃO LỤC THÀNH THÁNH"],
    )
    assert "INTEGRATED 3D LATIN TYPOGRAPHY" in prompt
    assert "LAO LUC THANH THANH" in prompt  # không dấu — tránh AI vỡ chữ
    assert "LÃO LỤC" not in prompt or "diacritic" in prompt.lower()
    assert meta_studio.vi_to_image_latin("Võ Đạo Cộng Điểm") == "VO DAO CONG DIEM"
    assert meta_studio.pick_badge_text("TRỌN BỘ THUYẾT MINH") in ("NEW", "HOT")
    assert meta_studio.pick_badge_text("HOT") == "HOT"


def test_auto_publisher_no_fallback_thumb_on_ai_failure(tmp_path):
    """
    Test requirement 2: If thumb cannot be created because AI image fails,
    DO NOT generate ugly Pillow thumbnail fallback! Instead set thumb_path = None.
    """
    dummy_video = tmp_path / "output.mp4"
    dummy_video.write_bytes(b"dummy video content")

    row = {
        "job_id": "test_job_meta_thumb",
        "output_path": str(dummy_video),
        "status": "ready_to_upload",
        "title_vi": "Phim Test",
        "title": "Test ZH",
    }

    mock_db_con = MagicMock()
    mock_db_con.__enter__.return_value = MagicMock()

    with patch("agents.auto_publisher.load_config", return_value={"enabled": True, "privacy": "unlisted", "generate_ai_thumb": True}), \
         patch("agents.auto_publisher.film_db.get", return_value=row), \
         patch("agents.auto_publisher.film_db.query", return_value=[row]), \
         patch("agents.auto_publisher.film_db.sync_one"), \
         patch("agents.auto_publisher.film_db.connect", return_value=mock_db_con), \
         patch("agents.auto_publisher.update_auto_state"), \
         patch("agents.auto_publisher._sync_queue_state_badge"), \
         patch("agents.auto_publisher.touch_active_locks"), \
         patch("agents.meta_studio.get_series_key", return_value=("test_series", "Phim Test")), \
         patch("agents.meta_studio.get_series_meta", return_value=None), \
         patch("agents.meta_studio.get_series_thumbnail", return_value=None), \
         patch("agents.meta_studio.generate_metadata", return_value={"ok": True, "titles": ["Phim Test - Tình Huống Kịch Tính"], "ai_image_prompt": "prompt", "description": "desc", "tags": [], "hook_texts": ["PHIM TEST", "CAO TRÀO"]}), \
         patch("agents.meta_studio.generate_ai_image", return_value=None), \
         patch("agents.meta_studio.create_thumbnail") as mock_create_thumb, \
         patch("agents.meta_studio.save_series_thumbnail") as mock_save_thumb, \
         patch("agents.meta_studio.save_series_meta"), \
         patch("agents.agent_tele_cmd._fetch_poster_bytes", return_value=None), \
         patch("agents.auto_publisher.upload_to_youtube", return_value={"ok": True, "yt_url": "https://youtu.be/dummy", "video_id": "dummy"}) as mock_upload:

        res = auto_publisher._publish_job_internal("test_job_meta_thumb", privacy="unlisted", force=True)

        # mock_create_thumb should NOT be called to create fallback
        mock_create_thumb.assert_not_called()
        # mock_save_thumb should NOT be called since no AI image was generated
        mock_save_thumb.assert_not_called()

        # mock_upload should have been called with thumb_path=None
        assert mock_upload.called
        call_kwargs = mock_upload.call_args[1]
        assert call_kwargs.get("thumb_path") is None
        assert res.get("ok") is True
