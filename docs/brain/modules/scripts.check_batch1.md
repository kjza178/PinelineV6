# `scripts.check_batch1`

[← Index](../generated/module-index.md) · [Brain home](../README.md)

<!-- AUTO:START — sinh bởi scripts/brain_build.py 2026-11-11 13:00. Đừng sửa tay khối này. -->
**File:** `scripts/check_batch1.py` · **Lớp:** `ops` · **307 dòng** (258 code) · **11 hàm**, **0 class** · sha1 `57ac0abf8c`

> Test đợt 1 (chặn mất dữ liệu) — chỉ dùng thư mục tạm, không đụng job/queue/thư viện thật.

### Phụ thuộc

- **Gọi tới (import nội bộ):** [agents.hongguo_bridge](./agents.hongguo_bridge.md), [core.job_queue](./core.job_queue.md), [engines.assemble](./engines.assemble.md), [engines.media_ts](./engines.media_ts.md)
- **Được dùng bởi:** —
- **Test:** ❌ chưa có test import trực tiếp
- **Thư viện ngoài:** `numpy`, `soundfile`

```mermaid
flowchart LR
  scripts_check_batch1[[scripts.check_batch1]]
  scripts_check_batch1 --> agents_hongguo_bridge[agents.hongguo_bridge]
  scripts_check_batch1 --> core_job_queue[core.job_queue]
  scripts_check_batch1 --> engines_assemble[engines.assemble]
  scripts_check_batch1 --> engines_media_ts[engines.media_ts]
```

### Hàm & class top-level

| Tên | Dòng | Dài | Tham số | Docstring |
|---|---:|---:|---|---|
| `check` | 24 | 4 | `name, cond, extra` |  |
| `extract` | 30 | 7 | `path, names, g` |  |
| `ffmk` | 39 | 4 | `path, dur` |  |
| `touch` | 45 | 2 | `p, t` |  |
| `test_s1` | 50 | 51 | `` |  |
| `test_s6` | 104 | 32 | `` |  |
| `test_hongguo` | 139 | 55 | `` |  |
| `test_pick` | 197 | 21 | `` |  |
| `test_feeder_sids` | 220 | 6 | `` |  |
| `test_m2` | 229 | 34 | `` |  |
| `test_m5` | 266 | 28 | `` |  |

**File/artifact nhắc tới:** `_job_queue.json`, `a.wav`, `audio.ts.wav`, `audio.wav`, `b.wav`, `c.wav`, `concat.txt`, `full_merged.mp4`, `manifest.json`, `nope.mp4`, `output.mp4`, `script.display.json`, `script.json`, `trailer.mp4`, `tts.json`, `tts_fit.json`, `x.json`, `第001集.mp4`, `第002集.mp4`

**Tín hiệu rủi ro (đếm tĩnh):** broad_except=1 · eval_exec=1 · file_writes=19 · sleep=1 · subprocess=2

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
