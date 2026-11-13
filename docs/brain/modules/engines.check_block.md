# `engines.check_block`

[← Index](../generated/module-index.md) · [Brain home](../README.md)

<!-- AUTO:START — sinh bởi scripts/brain_build.py 2026-11-11 13:00. Đừng sửa tay khối này. -->
**File:** `engines/check_block.py` · **Lớp:** `ops` · **350 dòng** (296 code) · **4 hàm**, **0 class** · sha1 `9d72809dd3`

> check_block.py — Module kiểm tra trạng thái Bản quyền / Block YouTube (Cơ chế 1: Không cần đăng nhập). Kiểm tra qua YouTube oEmbed và YouTube Watch Page (ytInitialPlayerResponse). Phát hiện: Clean (Sạch), Blocked (Chặn t

### Phụ thuộc

- **Gọi tới (import nội bộ):** [core.film_db](./core.film_db.md)
- **Được dùng bởi:** [agents.agent_tele_cmd](./agents.agent_tele_cmd.md), [agents.test_channel](./agents.test_channel.md), [core.film_db](./core.film_db.md), [http_routes.routes_films](./http_routes.routes_films.md), [tools.lib_check](./tools.lib_check.md), [tools.test_channel](./tools.test_channel.md)
- **Test:** ❌ chưa có test import trực tiếp

```mermaid
flowchart LR
  engines_check_block[[engines.check_block]]
  agents_agent_tele_cmd[agents.agent_tele_cmd] --> engines_check_block
  agents_test_channel[agents.test_channel] --> engines_check_block
  core_film_db[core.film_db] --> engines_check_block
  http_routes_routes_films[http_routes.routes_films] --> engines_check_block
  tools_lib_check[tools.lib_check] --> engines_check_block
  tools_test_channel[tools.test_channel] --> engines_check_block
  engines_check_block --> core_film_db[core.film_db]
```

### Hàm & class top-level

| Tên | Dòng | Dài | Tham số | Docstring |
|---|---:|---:|---|---|
| `_utc_now` | 21 | 2 | `` |  |
| `extract_video_id` | 25 | 25 | `url_or_id` | Trích xuất Video ID (11 ký tự) từ link YouTube bất kỳ hoặc ID thô. |
| `check_youtube_block` | 52 | 265 ⚠️ | `url_or_id, timeout` | Kiểm tra video YouTube có bị Block hay dính bản quyền không (không cần cookie/đăng nhập). |
| `check_film_by_job` | 319 | 32 | `job_id` | Kiểm tra trạng thái bản quyền của một phim theo job_id và lưu kết quả vào film_db. |

**Hàm dài ≥120 dòng:** `check_youtube_block` (265, L52)

**Tín hiệu rủi ro (đếm tĩnh):** broad_except=4 · swallow_except=3

### Lịch sử (từ docs/)

- Fix logs: —
- Nhắc trong CHANGELOG: **0** lần · DECISIONS: **0** lần

<!-- AUTO:END -->

<!-- MANUAL:START -->
## Vai trò (viết tay)

_Chưa viết. 2–4 câu: module này chịu trách nhiệm gì trong pipeline, ai gọi nó, nó KHÔNG làm gì._

## Luồng chính

_Các hàm vào (entry) → bước chính → output/side effect. Ghi tên hàm thật._

## Bất biến & hợp đồng

_Điều luôn phải đúng (vd: chỉ ghi manifest qua `Manifest.update`; 1 job / ghế). Vi phạm = bug._

## Bẫy đã biết

_Lỗi từng gặp + link `docs/fixes/...`. Những chỗ dễ sửa sai._

## Ghi chú vận hành

_Config key quan trọng, cần restart gì khi sửa (:8801 / :8802 / hot-reload), cách test._
<!-- MANUAL:END -->
