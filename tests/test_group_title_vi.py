"""2026-11-20: tên bộ phim (hợp tập Bilibili, tiếng Trung) dịch sang Việt cho danh sách job."""
import json

import pytest

import server
from state.index import summarize

GT = "【1-2季】全球死考：刚下刑场你让我考试"


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "WORK_ROOT", tmp_path)
    server._GROUP_TITLE_VI.clear()
    yield
    server._GROUP_TITLE_VI.clear()


def _job(tmp_path, jid):
    wd = tmp_path / jid
    wd.mkdir()
    data = {"request": {"group_title": GT, "group_index": 2, "group_total": 3}, "stages": {}}
    (wd / "manifest.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return wd, data


def test_group_title_translated_once_for_all_episodes(tmp_path):
    calls = []

    def chat(msgs, **_k):
        calls.append(msgs)
        return "Kỳ thi toàn cầu (mùa 1-2)"

    for jid in ("a1", "a2"):
        wd, data = _job(tmp_path, jid)
        assert server._translate_group_title(wd, data, chat) is True
        saved = json.loads((wd / "manifest.json").read_text(encoding="utf-8"))
        assert saved["group_title_vi"] == "Kỳ thi toàn cầu (mùa 1-2)"
        s = summarize(jid, saved)
        assert s["group_title"] == "Kỳ thi toàn cầu (mùa 1-2)" and s["group_title_orig"] == GT
    assert len(calls) == 1


def test_group_title_llm_error_returns_false(tmp_path):
    wd, data = _job(tmp_path, "b1")

    def chat(*_a, **_k):
        raise RuntimeError("down")
    assert server._translate_group_title(wd, data, chat) is False
    assert "group_title_vi" not in json.loads((wd / "manifest.json").read_text(encoding="utf-8"))
    assert summarize("b1", data)["group_title"] == GT


def test_no_cjk_no_call(tmp_path):
    wd = tmp_path / "c1"
    wd.mkdir()
    data = {"request": {"group_title": "Series A"}}
    assert server._translate_group_title(wd, data, lambda *a, **k: pytest.fail("không được gọi LLM")) is True
