# `rvc_bridge.vieneu_batch`

[← Index](../generated/module-index.md) · [Brain home](../README.md)

<!-- AUTO:START — sinh bởi scripts/brain_build.py 2026-11-11 13:00. Đừng sửa tay khối này. -->
**File:** `rvc_bridge/vieneu_batch.py` · **Lớp:** `tts` · **183 dòng** (150 code) · **5 hàm**, **0 class** · sha1 `f3bafd6faa`

> Worker VieNeu-TTS v3-Turbo (chạy trong D:/vieneu_env). Đọc job json: {out_dir, ref, text, device, batch_size, lines:[[i,"vi"],...]} -> mỗi câu ghi seg_XXXX.wav (48kHz). In "prog <done>/<total>" cho orchestrator.

### Phụ thuộc

- **Gọi tới (import nội bộ):** —
- **Được dùng bởi:** —
- **Được nhắc tên file `.py` (subprocess/script) bởi:** [engines.clone_tts](./engines.clone_tts.md), [scripts.check_batch4](./scripts.check_batch4.md)
- **Test:** ❌ chưa có test import trực tiếp
- **Thư viện ngoài:** `soundfile`, `torch`, `vieneu`

### Hàm & class top-level

| Tên | Dòng | Dài | Tham số | Docstring |
|---|---:|---:|---|---|
| `_cap_cpu_threads` | 29 | 12 | `` |  |
| `_lower_priority` | 46 | 11 | `` | Chỉ bật khi VIENEU_LOW_PRIORITY=1 (máy dễ treo). Mặc định TẮT để giữ tốc độ. |
| `_write_one` | 65 | 2 | `out_dir, i, audio, sr` |  |
| `_infer_group` | 69 | 21 | `tts, texts, voice_name, batch_size` | Dùng voice đã enroll — không truyền ref_audio (tránh encode lại mỗi lô). |
| `main` | 92 | 88 | `job_path` |  |

### Config / env keys (4)

`PYTHONEXECUTABLE`, `VIENEU_BATCH`, `VIENEU_CPU_THREADS`, `VIENEU_LOW_PRIORITY`

**Tín hiệu rủi ro (đếm tĩnh):** broad_except=8 · swallow_except=2

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
