import sys

from workers.render_worker import parse_lines, spawn


def test_parse_lines_ignores_noise():
    lines = ["frame=1 fps=30", '{"ev":"progress","percent":10}', "{bad", '{"x":1}', '{"ev":"done"}']
    assert [e["ev"] for e in parse_lines(iter(lines))] == ["progress", "done"]


def test_spawn_dry_run(tmp_path):
    got = []
    last = spawn(tmp_path, opts={"dry_run": True}, python=sys.executable, on_event=got.append)
    assert last["ev"] == "done" and [e["percent"] for e in got if e["ev"] == "progress"] == [33, 66, 99]


def test_spawn_error_reported(tmp_path, monkeypatch):
    # F5: không gọi render thật (engines.render_job) trong test — chọn hàm giả ném lỗi.
    monkeypatch.setenv("PINELINE_RENDER_IMPL", "tests.fake_render:boom")
    last = spawn(tmp_path, opts={}, python=sys.executable)
    assert last["ev"] == "error" and "track giọng" in last["error"]
