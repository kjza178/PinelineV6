"""Focused tests: hardsub_blur toggle (MuxReq + RunAllReq + batch_config + paths)."""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core.batch_config as batch_config
from server import MuxReq, RunAllReq, mux_job, run_all


def test_muxreq_hardsub_blur_default_true():
    assert MuxReq().hardsub_blur is True


def test_muxreq_hardsub_blur_false_accepted():
    m = MuxReq(hardsub_blur=False)
    assert m.hardsub_blur is False


def test_runallreq_hardsub_blur_default_true():
    assert RunAllReq().hardsub_blur is True


def test_runallreq_hardsub_blur_false_accepted():
    r = RunAllReq(hardsub_blur=False)
    assert r.hardsub_blur is False


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("on", True),
        ("off", False),
        ("true", True),
        ("false", False),
        ("1", True),
        ("0", False),
        (True, True),
        (False, False),
        ("bật", True),
        ("tắt", False),
    ],
)
def test_batch_config_coerce_hardsub_blur(raw, expected):
    assert batch_config._coerce("hardsub_blur", raw) is expected


def test_batch_config_render_kwargs_hardsub_blur_false():
    kw = batch_config.render_kwargs({"hardsub_blur": False})
    assert kw["hardsub_blur"] is False


def test_batch_config_render_kwargs_hardsub_blur_string_false():
    kw = batch_config.render_kwargs({"hardsub_blur": "false"})
    assert kw["hardsub_blur"] is False


def test_batch_config_as_bool_string_false_trap():
    assert bool("false") is True  # Python trap
    assert batch_config.as_bool("false") is False
    assert batch_config.as_bool("off") is False
    assert batch_config.as_bool(None, True) is True


def test_batch_config_render_kwargs_hardsub_blur_default_true():
    kw = batch_config.render_kwargs({})
    assert kw["hardsub_blur"] is True


def test_run_all_render_kw_hardsub_blur_from_req():
    """Mirror run_all's render_kw build: hardsub_blur comes from RunAllReq."""
    for val in (True, False):
        req = RunAllReq(hardsub_blur=val)
        hardsub_blur = batch_config.as_bool(getattr(req, "hardsub_blur", True), True)
        assert hardsub_blur is val


def test_mux_path_hardsub_blur_from_req():
    """Mirror mux_job's hs_blur via as_bool(getattr(req, ...)) from MuxReq."""
    for val in (True, False):
        req = MuxReq(hardsub_blur=val)
        hs_blur = batch_config.as_bool(getattr(req, "hardsub_blur", True), True)
        assert hs_blur is val


def test_run_all_source_builds_hardsub_blur_render_kw():
    """V6 F2: run_all quyết opts qua core.job_opts (trường gửi > queue_opts ghim > batch)."""
    src = inspect.getsource(run_all)
    assert "render_kw = job_opts.pipeline_render_kw(opts)" in src
    # V6 F1/F2: ghim bộ render/âm thanh (gồm hardsub_blur) qua state.job_state
    assert "js.set_queue_opts(work_dir, job_opts.pin_subset(opts), merge=True)" in src
    from core import job_opts
    for val in (True, False):
        o = job_opts.normalize(job_opts.resolve(job_opts.explicit_from_req(RunAllReq(hardsub_blur=val)), batch={}))
        assert job_opts.pipeline_render_kw(o)["hardsub_blur"] is val
        assert job_opts.pin_subset(o)["hardsub_blur"] is val


def test_mux_job_source_passes_hardsub_blur():
    src = inspect.getsource(mux_job)
    assert "js.set_queue_opts(work_dir, job_opts.pin_subset(opts), merge=True)" in src
    assert 'rk["hardsub_blur"])' in src  # last arg to _submit_claimed / _do_render
    from core import job_opts
    for val in (True, False):
        o = job_opts.normalize(job_opts.resolve(job_opts.explicit_from_req(MuxReq(hardsub_blur=val)), batch={}))
        assert job_opts.pipeline_render_kw(o)["hardsub_blur"] is val
