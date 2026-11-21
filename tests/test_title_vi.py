"""2026-11-21: «Tên phim tiếng Việt» — dịch tên job bằng AI dịch chính (+ dự phòng), bật ở Cài đặt."""
import json

import pytest

import server


@pytest.fixture
def wr(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "WORK_ROOT", tmp_path)
    server._title_tried.clear()
    server._title_pending.clear()
    yield tmp_path
    server._title_tried.clear()


def _job(root, jid, title, **extra):
    wd = root / jid
    wd.mkdir()
    (wd / "manifest.json").write_text(json.dumps({"stages": {"download": {"status": "done", "title": title}}, **extra},
                                                 ensure_ascii=False), encoding="utf-8")
    return wd


def test_title_uses_main_model_then_backup(wr, monkeypatch):
    import engines.llm as llm
    from engines.translate_simple import cfg
    monkeypatch.setattr(server, "_title_model", lambda: "deepseek/x")
    monkeypatch.setattr(cfg, "fallback_models", lambda m: ["alibaba/q"])
    used = []

    def chat(msgs, model=None, **k):
        used.append(model)
        if model == "deepseek/x":
            raise ConnectionError("402")
        return "Một tờ hôn thư: Thứ tử hầu phủ cưới nhầm huyện chủ"
    monkeypatch.setattr(llm, "chat", chat)
    wd = _job(wr, "t1", "一纸婚书：侯府庶子错娶县主")
    server._translate_title("t1")
    assert used == ["deepseek/x", "alibaba/q"]
    assert json.loads((wd / "manifest.json").read_text(encoding="utf-8"))["title_vi"].startswith("Một tờ hôn thư")


def test_switch_from_settings_enables_translation(monkeypatch):
    import core.config as C
    monkeypatch.setenv("PINELINE_FLAG_TITLE_TRANSLATE", "0")
    monkeypatch.setattr(C, "get", lambda k, d=None: {"TITLE_TRANSLATE": "1"}.get(k, d))
    assert server._title_translate_on() is True
    monkeypatch.setattr(C, "get", lambda k, d=None: d)
    assert server._title_translate_on() is False


def test_service_status_and_toggle(wr, monkeypatch):
    from services import title_vi
    import core.config as C
    saved = []
    monkeypatch.setattr(C, "save_config", lambda u: saved.append(u))
    monkeypatch.setattr(server, "_title_backfill_start", lambda force=False: 0)
    monkeypatch.setattr(server, "_title_model", lambda: "deepseek/x")
    _job(wr, "a", "一纸婚书")
    _job(wr, "b", "一纸婚书", title_vi="Một tờ hôn thư")
    _job(wr, "c", "English title")
    st = title_vi.status()
    assert st["jobs"] == 3 and st["chinese_titles"] == 2 and st["translated"] == 1 and st["model"] == "deepseek/x"
    title_vi.set_enabled(True)
    title_vi.set_enabled(False)
    assert saved == [{"TITLE_TRANSLATE": "1"}, {"TITLE_TRANSLATE": None}]
