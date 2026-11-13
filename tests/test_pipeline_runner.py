from pathlib import Path

from pipeline.base import Context, Gate, GateResult, Runner, Stage


class S(Stage):
    def __init__(self, name, done=False, boom=False, log=None):
        self.name, self._done, self.boom, self.log = name, done, boom, log if log is not None else []

    def done(self, ctx):
        return self._done

    def run(self, ctx):
        if self.boom:
            raise RuntimeError("nổ")
        self.log.append(self.name)


class G(Gate):
    def __init__(self, name, res):
        self.name, self.res = name, res

    def check(self, ctx):
        return self.res


def ctx(tmp_path, **kw):
    return Context("j", Path(tmp_path), log=lambda *_: None, **kw)


def test_runs_and_skips_done(tmp_path):
    log = []
    r = Runner([S("a", done=True, log=log), S("b", log=log), G("g", GateResult.ok()), S("c", log=log)]).run(ctx(tmp_path))
    assert r.status == "done" and log == ["b", "c"]
    assert [s[1] for s in r.steps] == ["skip", "ok", "pass", "ok"]


def test_gate_park_keeps_seat(tmp_path):
    log = []
    r = Runner([S("a", log=log), G("rv", GateResult.park("waiting_review", retry_at=5.0)), S("tts", log=log)]).run(ctx(tmp_path))
    assert r.status == "park" and r.at == "rv" and r.retry_at == 5.0 and r.keeps_seat and log == ["a"]


def test_gate_hold_and_fail(tmp_path):
    assert Runner([G("q", GateResult.hold("qa_gate", "x"))]).run(ctx(tmp_path)).status == "hold"
    r = Runner([G("q", GateResult.fail("hỏng"))]).run(ctx(tmp_path))
    assert r.status == "fail" and not r.keeps_seat


def test_exception_becomes_fail(tmp_path):
    r = Runner([S("a"), S("b", boom=True), S("c")]).run(ctx(tmp_path))
    assert r.status == "fail" and r.at == "b" and "nổ" in r.detail


def test_stop_flag_and_stop_file(tmp_path):
    flag = {"v": False}
    log = []

    class Stopper(S):
        def run(self, c):
            super().run(c)
            flag["v"] = True
    r = Runner([Stopper("a", log=log), S("b", log=log)]).run(ctx(tmp_path, is_stopped=lambda: flag["v"]))
    assert r.status == "stopped" and log == ["a"]
    (tmp_path / ".stop").write_text("1")
    assert Runner([S("x")]).run(ctx(tmp_path)).status == "stopped"
