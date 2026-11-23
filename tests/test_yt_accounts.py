"""2026-11-21: kênh YouTube đăng phim — nhiều kênh, chọn kênh khi đăng (services/yt_accounts, yt_publish)."""
import json

import pytest

from services import yt_accounts as ya


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(ya, "DATA", tmp_path)
    monkeypatch.setattr(ya, "ACCOUNTS", tmp_path / "accounts.json")
    monkeypatch.setattr(ya, "TOKENS", tmp_path / "tokens")
    return tmp_path


def _add(store, acc_id, title, default=False):
    d = ya._read()
    d["accounts"].append({"id": acc_id, "channel_id": acc_id, "title": title})
    if default or not d.get("default"):
        d["default"] = acc_id
    ya._write(d)
    (store / "tokens").mkdir(exist_ok=True)
    (store / "tokens" / f"{acc_id}.json").write_text("{}", encoding="utf-8")


def test_accounts_default_remove(store):
    assert ya.list_accounts()["accounts"] == [] and ya.default_id() is None
    _add(store, "UC1", "Kênh A")
    _add(store, "UC2", "Kênh B")
    assert ya.default_id() == "UC1" and ya.has("UC2") and ya.has("default")
    ya.set_default("UC2")
    lst = ya.list_accounts()
    assert lst["default"] == "UC2" and [a["default"] for a in lst["accounts"]] == [False, True]
    ya.remove("UC2")
    assert ya.default_id() == "UC1" and not (store / "tokens" / "UC2.json").exists()
    with pytest.raises(ValueError):
        ya.set_default("nope")


def test_token_path_rejects_traversal(store):
    with pytest.raises(ValueError):
        ya._token_path("../..")


def test_publish_many_uses_chosen_channel_and_skips_uploaded(store, monkeypatch):
    from services import yt_publish
    import agents.auto_publisher as ap
    _add(store, "UC1", "Kênh A")
    _add(store, "UC2", "Kênh B")
    calls = []
    monkeypatch.setattr(ap, "enqueue_publish", lambda jid, **k: calls.append((jid, k["account_id"], k["force"])) or {"ok": True})
    monkeypatch.setattr(yt_publish, "_uploaded_url", lambda jid: "https://youtu.be/x" if jid == "done1" else "")
    r = yt_publish.publish_many(["j1", "done1", "j2"], account_id="UC2", privacy="private")
    assert r["queued"] == ["j1", "j2"] and r["channel"] == "Kênh B"
    assert calls == [("j1", "UC2", True), ("j2", "UC2", True)]
    assert r["refused"][0]["job_id"] == "done1"
    r = yt_publish.publish_many(["j3"])                       # không chọn → kênh mặc định
    assert r["account_id"] == "UC1"


def test_publish_many_without_channel_errors(store):
    from services import yt_publish
    with pytest.raises(ValueError, match="chưa có kênh"):
        yt_publish.publish_many(["j1"])


def test_auto_publisher_prefers_v6_channel(store, monkeypatch):
    import agents.auto_publisher as ap
    _add(store, "UC1", "Kênh A")
    monkeypatch.setattr(ya, "service", lambda acc: f"svc:{acc}")
    assert ap.get_youtube_service("UC1") == "svc:UC1"
    assert ap._default_v6_account() == "UC1"


def test_enqueue_record_keeps_account(tmp_path, monkeypatch):
    import agents.auto_publisher as ap
    q = tmp_path / "q.json"
    monkeypatch.setattr(ap, "_read_publish_queue", lambda: json.loads(q.read_text()) if q.exists() else [])
    monkeypatch.setattr(ap, "_write_publish_queue", lambda items: q.write_text(json.dumps(items)))
    monkeypatch.setattr(ap, "is_auto_split_part", lambda j: False)
    items = ap._enqueue_job_record("j1", privacy="private", account_id="UC9")
    assert items[0]["account_id"] == "UC9"


def test_meta_falls_back_from_aurora_to_main_ai(monkeypatch):
    from agents import meta_studio
    import engines.llm as llm
    from engines.translate_simple import cfg
    monkeypatch.setattr(meta_studio, "_call_aurora", lambda *a, **k: (_ for _ in ()).throw(ConnectionError("3377")))
    monkeypatch.setattr(cfg, "review_model", lambda wd=None: "deepseek/x")
    monkeypatch.setattr(cfg, "fallback_models", lambda m: [])
    monkeypatch.setattr(llm, "chat", lambda msgs, model=None, **k: f"ok:{model}")
    assert meta_studio._call_chatgpt([{"role": "user", "content": "hi"}]) == "ok:deepseek/x"


# ── mỗi job một kênh: gán trước, render xong tự đăng ──
def _job(root, jid, **data):
    wd = root / jid
    wd.mkdir(parents=True)
    (wd / "manifest.json").write_text(json.dumps({"stages": {}, **data}), encoding="utf-8")
    return wd


def test_targets_per_job_and_publish_on_done(store, tmp_path, monkeypatch):
    from services import yt_publish
    import agents.auto_publisher as ap
    import core.job_queue as jq
    from state.index import summarize
    work = tmp_path / "work"
    monkeypatch.setattr(yt_publish, "_work_dir", lambda jid: work / jid)
    monkeypatch.setattr(yt_publish, "_uploaded_url", lambda jid: "")
    monkeypatch.setattr(jq, "is_wired_done", lambda wd: wd.name == "done1")
    _add(store, "UC1", "Kênh A")
    _add(store, "UC2", "Kênh B")
    for j in ("a", "b", "done1"):
        _job(work, j)
    calls = []
    monkeypatch.setattr(ap, "enqueue_publish", lambda jid, **k: calls.append((jid, k["account_id"])) or {"ok": True})
    r = yt_publish.set_targets(["a"], "UC1")
    r2 = yt_publish.set_targets(["b", "done1"], "UC2", privacy="private")
    assert r["set"] == ["a"] and r2["published_now"] == ["done1"] and calls == [("done1", "UC2")]
    data = json.loads((work / "a" / "manifest.json").read_text(encoding="utf-8"))
    assert summarize("a", data)["publish_to"] == "Kênh A"
    assert yt_publish.publish_on_done("a") is True and calls[-1] == ("a", "UC1")
    yt_publish.set_targets(["b"], None)                     # bỏ gán
    assert "publish_to" not in json.loads((work / "b" / "manifest.json").read_text(encoding="utf-8"))
    assert yt_publish.publish_on_done("b") is False
    with pytest.raises(ValueError):
        yt_publish.set_targets(["a"], "UC_khong_co")


def test_save_client_from_downloaded_json(store):
    raw = json.dumps({"web": {"client_id": "123-abc.apps.googleusercontent.com", "client_secret": "s3cr3t"}})
    ya.save_client(raw)
    saved = json.loads((store / "client_secret.json").read_text(encoding="utf-8"))
    assert saved["web"]["client_id"].startswith("123-") and ya.client_info()["ok"] is True
    cfg, src = ya._client_config("http://127.0.0.1:8806/test-channel/callback")
    assert src == "own" and cfg["web"]["redirect_uris"] == ["http://127.0.0.1:8806/test-channel/callback"]
    with pytest.raises(ValueError):
        ya.save_client('{"web": {"client_id": "x"}}')
    with pytest.raises(ValueError):
        ya.save_client("không phải json")
