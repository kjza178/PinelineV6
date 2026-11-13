from core.instance import Instance
from runtime.context import Runtime


def test_claim_and_stop(tmp_path):
    rt = Runtime(Instance("t", 1, tmp_path, tmp_path))
    assert rt.claim("j", "asr") and not rt.claim("j", "asr") and not rt.claim("j", "__all__")
    rt.release("j", "asr")
    assert rt.claim("j", "__all__") and not rt.claim("j", "tts")
    rt.release("j", "__all__")
    assert rt.busy_stage("j") is None
    rt.request_stop("j")
    assert rt.is_stopped("j") and (tmp_path / "j" / ".stop").exists()
    rt.clear_stop("j")
    assert not rt.is_stopped("j") and not (tmp_path / "j" / ".stop").exists()
    rt.start_pools()
    assert rt.dl.submit(lambda: 2).result() == 2
    rt.shutdown()
