# Tree audit — PinelineV6

_Sinh bởi `scripts/tree_audit.py` lúc 2026-11-11 00:14. ERROR = sẽ hỏng khi chạy; WARN = nên xem._

**ERROR: 0** · import lỗi 7 · cổng lạ 8 · module trùng tên 1

## Import không resolve

| Mức | File | Dòng | Module |
|---|---|---:|---|
| WARN | `engines/translate.py` | 906 | `context_studio.schema` |
| WARN | `engines/translate.py` | 3615 | `context_studio.schema` |
| WARN | `server.py` | 4861 | `context_studio.analyzer` |
| WARN | `server.py` | 4862 | `context_studio.character_mapper` |
| WARN | `server.py` | 4863 | `context_studio.exporter` |
| WARN | `server.py` | 4864 | `context_studio.schema` |
| WARN | `server.py` | 4865 | `context_studio.srt_io` |

## Cổng của cây khác

| Mức | File | Dòng | Chi tiết |
|---|---|---:|---|
| WARN | `agents/agent_autopilot.py` | 21 | :8801 (cây này ?) — DEFAULT_BASE = (os.getenv("PINELINE_BASE") or "http://127.0.0.1:8801").rstrip("/") |
| WARN | `agents/agent_tele_cmd.py` | 74 | :8801 (cây này ?) — BASE = (os.getenv("PINELINE_BASE") or "http://127.0.0.1:8801").strip().rstrip("/") |
| WARN | `agents/agent_tool_loop.py` | 10 | :8801 (cây này ?) — python agent_tool_loop.py --base http://127.0.0.1:8801 --job ID --stages translate --dry |
| WARN | `agents/agent_tool_loop.py` | 449 | :8801 (cây này ?) — p.add_argument("--base", default=os.getenv("PINELINE_BASE") or "http://127.0.0.1:8801") |
| WARN | `http_routes/routes_films.py` | 279 | :8804 (cây này ?) — host = req.headers.get("host") or "127.0.0.1:8804" |
| WARN | `http_routes/routes_films.py` | 290 | :8804 (cây này ?) — host = req.headers.get("host") or "127.0.0.1:8804" |
| WARN | `http_routes/routes_films.py` | 313 | :8804 (cây này ?) — host = req.headers.get("host") or "127.0.0.1:8804" |
| WARN | `tools/migrate_work.py` | 35 | :8801 (cây này ?) — BASE = (os.getenv("PINELINE_BASE") or "http://127.0.0.1:8801").rstrip("/") |

## Module trùng tên

| Mức | Tên | Bản [md5] |
|---|---|---|
| INFO | `test_channel` | agents/test_channel.py[519d19], tools/test_channel.py[519d19] |
