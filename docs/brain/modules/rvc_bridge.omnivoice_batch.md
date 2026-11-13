# `rvc_bridge.omnivoice_batch`

[← Index](../generated/module-index.md) · [Brain home](../README.md)

<!-- AUTO:START — sinh bởi scripts/brain_build.py 2026-11-11 13:00. Đừng sửa tay khối này. -->
**File:** `rvc_bridge/omnivoice_batch.py` · **Lớp:** `tts` · **47 dòng** (38 code) · **1 hàm**, **0 class** · sha1 `c8f48ec784`

> Worker omnivoice-vietnamese (chạy trong D:/OmniVoiceStudio-v2/.venv). Đọc job json: {out_dir, ref, text, device, speed, model, lines:[[i,"vi"],...]} -> mỗi câu ghi seg_XXXX.wav. In "prog <done>/<total>" cho orchestrator.

### Phụ thuộc

- **Gọi tới (import nội bộ):** —
- **Được dùng bởi:** —
- **Được nhắc tên file `.py` (subprocess/script) bởi:** [engines.clone_tts](./engines.clone_tts.md)
- **Test:** ❌ chưa có test import trực tiếp
- **Thư viện ngoài:** `omnivoice`, `torch`, `torchaudio`

### Hàm & class top-level

| Tên | Dòng | Dài | Tham số | Docstring |
|---|---:|---:|---|---|
| `main` | 14 | 30 | `job_path` |  |

**Tín hiệu rủi ro (đếm tĩnh):** broad_except=1

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
