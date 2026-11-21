"""Bộ tải Bilibili gốc (engines/bili) — Bilibili GIẢ LẬP (không gọi mạng)."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from engines.bili import api, cdn, cookies, fetch, wbi


# ── Bilibili giả ──
class Resp:
    def __init__(self, status=200, body=b"", js=None, headers=None, url=""):
        self.status_code, self.content, self._js, self.headers, self.url = status, body, js, headers or {}, url

    def json(self):
        if self._js is None:
            raise ValueError("not json")
        return self._js


class FakeBili:
    def __init__(self, video=b"V" * 300_000, audio=b"A" * 100_000, fail_host=None, pages=1):
        self.video, self.audio, self.fail_host, self.pages = video, audio, fail_host, pages
        self.calls = []

    def __call__(self):
        return self

    def get(self, url, params=None, headers=None, timeout=None, allow_redirects=True):
        self.calls.append(url)
        if "/x/web-interface/nav" in url:
            return Resp(js={"code": 0, "data": {"isLogin": "SESSDATA" in (headers or {}).get("Cookie", ""), "uname": "anh",
                                                "vip_label": {"text": "大会员"},
                                                "wbi_img": {"img_url": "https://i0/7cd084941338484aae1ad9425b84077c.png",
                                                            "sub_url": "https://i0/4932caff0ff746eab6f01bf08b70ac45.png"}}})
        if "/x/web-interface/view" in url:
            return Resp(js={"code": 0, "data": {"title": "Phim thử", "pic": "https://i0/pic.jpg", "owner": {"name": "up"},
                                                "duration": 600, "pages": [{"page": i + 1, "cid": 100 + i, "part": f"Tập {i + 1}",
                                                                            "duration": 300} for i in range(self.pages)]}})
        if "/x/player/wbi/playurl" in url:
            assert params and params.get("w_rid") and params.get("fnval") == "4048"
            return Resp(js={"code": 0, "data": {"dash": {
                "video": [{"id": 80, "height": 1080, "width": 1920, "codecid": 13, "codecs": "av01", "bandwidth": 9,
                           "baseUrl": "https://x-mcdn.example/v13.m4s"},
                          {"id": 80, "height": 1080, "width": 1920, "codecid": 7, "codecs": "avc1", "bandwidth": 5,
                           "baseUrl": "https://cn-gotcha.bilivideo.com/v7.m4s", "backupUrl": ["https://a.akamaized.net/v7.m4s"]},
                          {"id": 64, "height": 720, "width": 1280, "codecid": 7, "codecs": "avc1", "bandwidth": 3,
                           "baseUrl": "https://cn-gotcha.bilivideo.com/v720.m4s"}],
                "audio": [{"id": 30216, "bandwidth": 1, "baseUrl": "https://cn-gotcha.bilivideo.com/a1.m4s"},
                          {"id": 30280, "bandwidth": 9, "baseUrl": "https://cn-gotcha.bilivideo.com/a9.m4s"}]}}})
        if "pic.jpg" in url:
            return Resp(body=b"JPEG")
        if ".m4s" in url:
            host = re.match(r"https://([^/]+)", url).group(1)
            if self.fail_host and self.fail_host in host:
                return Resp(status=403)
            data = self.audio if "/a" in url.rsplit("/", 1)[-1][:2] else self.video
            a, b = map(int, (headers or {})["Range"].split("=")[1].split("-"))
            chunk = data[a:b + 1]
            return Resp(206, chunk, headers={"Content-Range": f"bytes {a}-{a + len(chunk) - 1}/{len(data)}"})
        raise AssertionError(f"URL lạ: {url}")


def test_wbi_sign_known_vector():
    key = wbi.derive_mixin_key("7cd084941338484aae1ad9425b84077c" + "4932caff0ff746eab6f01bf08b70ac45")
    assert key == "ea1db124af3c7062474693fa704f4ff8"            # ví dụ chuẩn của tài liệu WBI
    s = wbi.sign({"foo": "114", "bar": "514", "zab": 1919810}, key, now=1702204169)
    assert s["w_rid"] == "8f6f2b5b3d485fe1886cec6a0be8c5d4" and s["wts"] == "1702204169"


@pytest.mark.parametrize("url,exp", [
    ("https://www.bilibili.com/video/BV1xx411c7mD?p=3&x=1", {"type": "video", "bvid": "BV1xx411c7mD", "p": 3}),
    ("https://space.bilibili.com/123/lists/456?type=season", {"type": "collection", "mid": "123", "season_id": "456"}),
    ("https://space.bilibili.com/123/lists/9?type=series", {"type": "series", "mid": "123", "series_id": "9"}),
    ("https://space.bilibili.com/1/channel/collectiondetail?sid=77", {"type": "collection", "mid": "1", "season_id": "77"}),
    ("https://space.bilibili.com/1/lists?sid=77", {"type": "collection", "mid": "1", "season_id": "77"}),
])
def test_parse_link(url, exp):
    assert api.parse_link(url) == exp


def test_pick_streams_prefers_avc_under_cap_and_best_audio():
    dash = FakeBili().get("https://api.bilibili.com/x/player/wbi/playurl", params={"w_rid": 1, "fnval": "4048"}).json()["data"]["dash"]
    v, a = api.pick_streams(dash)
    assert v["codecid"] == 7 and v["height"] == 1080 and a["id"] == 30280      # không lấy AV1
    v, _ = api.pick_streams(dash, max_height=720)
    assert v["height"] == 720


def test_cdn_candidates_drop_mcdn_and_swap_akamai():
    c = cdn.expand_candidates(["https://x-mcdn.ex/v", "https://a.akamaized.net/v?x=1"])
    assert not any("mcdn" in u or "akamaized" in u for u in c) and c[0].endswith("/v?x=1") and len(c) == 4


def test_expand_multi_p_and_single_p():
    s = FakeBili(pages=3)
    r = api.expand("https://www.bilibili.com/video/BV1xx411c7mD", s, api.headers())
    assert r["kind"] == "multi_p" and [p["url"][-4:] for p in r["parts"]] == ["c7mD", "?p=2", "?p=3"]
    r = api.expand("https://www.bilibili.com/video/BV1xx411c7mD?p=2", s, api.headers())
    assert r["kind"] == "video" and r["parts"][0]["title"] == "Phim thử - Tập 2" and r["parts"][0]["url"].endswith("?p=2")


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="cần ffmpeg")
def test_download_end_to_end_with_mirror_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch, "CHUNK", 64 * 1024)
    # luồng thật phải là media hợp lệ để ffmpeg ghép — tạo 1s hình + 1s tiếng
    v, a = tmp_path / "v.mp4", tmp_path / "a.m4a"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=64x36:d=1", "-c:v", "libx264", str(v)], check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=d=1", "-c:a", "aac", str(a)], check=True)
    fake = FakeBili(video=v.read_bytes(), audio=a.read_bytes(), fail_host="cn-gotcha")   # node gốc chết → mirror UPOS
    wd = tmp_path / "job"
    wd.mkdir()
    prog = []
    meta = fetch.download("https://www.bilibili.com/video/BV1xx411c7mD", wd, cookie="SESSDATA=x", session_factory=fake,
                          on_progress=lambda d, t, sp: prog.append((d, t)), log=lambda *_: None)
    assert (wd / "source.mp4").stat().st_size > 0 and (wd / "source.jpg").read_bytes() == b"JPEG"
    assert meta["height"] == 1080 and meta["downloader"] == "bili_native" and meta["logged_in"]
    assert not list(wd.glob("*.m4s*")) and prog and prog[-1][0] == prog[-1][1]
    assert json.loads((wd / "source.info.json").read_text(encoding="utf-8"))["title"] == "Phim thử"


def test_stream_resume_skips_done_segments(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch, "CHUNK", 1000)
    data = bytes(range(256)) * 20                       # 5120 byte → 6 đoạn
    fake = FakeBili(video=data)
    sf = fetch.StreamFile(tmp_path / "x.video.m4s", ["https://h/v.m4s"], fake, {}, 2, log=lambda *_: None)
    n = [0]

    def stop():
        n[0] += 1
        return n[0] > 3                                 # dừng giữa chừng
    with pytest.raises(fetch.Stopped):
        sf.run(stop, lambda _: None)
    st = json.loads(sf.state.read_text(encoding="utf-8"))
    assert 0 < len(st["done"]) < 6
    before = len(fake.calls)
    sf2 = fetch.StreamFile(tmp_path / "x.video.m4s", ["https://h/v.m4s"], fake, {}, 2, log=lambda *_: None)
    sf2.run(lambda: False, lambda _: None)
    assert (tmp_path / "x.video.m4s").read_bytes() == data
    assert len(fake.calls) - before == 1 + 6 - len(st["done"])      # 1 lần đo + chỉ các đoạn còn thiếu


def test_cookies_parse_save_status(tmp_path, monkeypatch):
    monkeypatch.setattr(cookies, "COOKIE_DIR", tmp_path / "cookies")
    assert cookies.parse_input('[{"name":"SESSDATA","value":"s1"},{"name":"bili_jct","value":"j"}]') == {"SESSDATA": "s1", "bili_jct": "j"}
    assert cookies.parse_input("SESSDATA=s2; DedeUserID=9") == {"SESSDATA": "s2", "DedeUserID": "9"}
    with pytest.raises(ValueError):
        cookies.save("bili_jct=x")                      # thiếu SESSDATA
    cookies.save('{"SESSDATA": "s3", "buvid3": "b"}')
    txt = (tmp_path / "cookies" / "bilibili_cookies.txt").read_text(encoding="utf-8")
    assert txt.startswith("# Netscape") and "\tSESSDATA\ts3" in txt     # yt-dlp đọc được cùng file
    assert cookies.header() == "SESSDATA=s3; buvid3=b"
    st = cookies.status(session=FakeBili(), check=True)
    assert st["has_cookie"] and st["login"] == {"is_login": True, "uname": "anh", "mid": 0, "vip": "大会员"}
    assert "s3" not in json.dumps(st)                    # không lộ giá trị cookie


def test_download_video_uses_native_then_falls_back(tmp_path, monkeypatch):
    import engines.download as dl
    from engines.bili import fetch as bf
    wd = tmp_path / "job"
    wd.mkdir()
    (wd / "manifest.json").write_text("{}", encoding="utf-8")

    def ok(url, work_dir, **kw):
        (Path(work_dir) / "source.mp4").write_bytes(b"x")
        assert callable(kw["max_height"]) and kw["max_height"](5 * 3600) == 480   # phim dài → 480p như yt-dlp
        return {"title": "T", "height": 1080, "duration_sec": 60, "quality": "1080P"}
    monkeypatch.setattr(bf, "download", ok)
    monkeypatch.setattr(dl, "_cfg_max_height", lambda: None)
    monkeypatch.setattr("engines.source_preflight.after_download", lambda *a, **k: None, raising=False)
    r = dl.download_video("https://www.bilibili.com/video/BV1xx411c7mD", wd)
    assert r["status"] == "done" and r["video_path"].endswith("source.mp4") and r["title"] == "T"

    def boom(*a, **k):
        raise api.BiliError("hỏng")
    monkeypatch.setattr(bf, "download", boom)
    called = {}

    class YDL:
        def __init__(self, opts):
            called["opts"] = opts
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def extract_info(self, url, download=True):
            called["url"] = url
            (wd / "source.mp4").write_bytes(b"y")
            return {"title": "Y", "duration": 10, "requested_downloads": [{"filepath": str(wd / "source.mp4")}]}
    monkeypatch.setattr(dl.yt_dlp, "YoutubeDL", YDL)
    r = dl.download_video("https://www.bilibili.com/video/BV1xx411c7mD", wd, force=True)
    assert called["url"].startswith("https://www.bilibili.com") and r["title"] == "Y"      # rơi xuống yt-dlp
