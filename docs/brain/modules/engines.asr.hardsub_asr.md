# `engines.asr.hardsub_asr`

[← Index](../generated/module-index.md) · [Brain home](../README.md)

<!-- AUTO:START — sinh bởi scripts/brain_build.py 2026-11-11 13:00. Đừng sửa tay khối này. -->
**File:** `engines/asr/hardsub_asr.py` · **Lớp:** `asr` · **457 dòng** (396 code) · **12 hàm**, **0 class** · sha1 `ba87718170`

> hardsub_asr.py — OCR hardsub chỉ để RÀ SOÁT / SỬA sub ZH (asr · srt). Một nhiệm vụ: RapidOCR đọc dải hardsub → so với asr.json / sub.zh → báo thiếu·sai → vá chữ ZH

### Phụ thuộc

- **Gọi tới (import nội bộ):** [core.manifest](./core.manifest.md), [engines.render](./engines.render.md)
- **Được dùng bởi:** [server](./server.md)
- **Test:** ❌ chưa có test import trực tiếp
- **Thư viện ngoài:** `rapidocr_onnxruntime`

```mermaid
flowchart LR
  engines_asr_hardsub_asr[[engines.asr.hardsub_asr]]
  server[server] --> engines_asr_hardsub_asr
  engines_asr_hardsub_asr --> core_manifest[core.manifest]
  engines_asr_hardsub_asr --> engines_render[engines.render]
```

### Hàm & class top-level

| Tên | Dòng | Dài | Tham số | Docstring |
|---|---:|---:|---|---|
| `_find_video` | 36 | 9 | `work_dir` |  |
| `_probe_size` | 47 | 11 | `video` |  |
| `_norm_zh` | 60 | 4 | `s` |  |
| `zh_sim` | 66 | 10 | `a, b` | Độ giống 0..1 (chuỗi đã chuẩn hóa). |
| `_band_y_frac` | 78 | 25 | `work_dir` | (y0, y1) tỷ lệ chiều cao — ưu tiên hardsub_band.json. |
| `extract_hardsub_cues` | 105 | 64 | `work_dir, dur, fps, conf_min` | OCR crop đáy → list {start,end,zh,n,source}. |
| `_samples_to_cues` | 171 | 34 | `samples, gap` | Gộp frame liên tiếp cùng/gần text thành cue. |
| `merge_hardsub_into_asr` | 207 | 81 | `asr, hs_cues, min_sim` | Giữ timing ASR; chỉ thay text khi hardsub KHỚP chữ (tránh dính câu bên cạnh). |
| `_to_srt` | 290 | 22 | `rows, key` |  |
| `sync_script_zh` | 314 | 40 | `work_dir, asr_rows` | Cập nhật script.json zh theo asr (khớp i hoặc time). |
| `run_hardsub_asr` | 356 | 79 | `work_dir, dur, fps, apply, merge_only, force` |  |
| `main` | 437 | 17 | `argv` |  |

**File/artifact nhắc tới:** `asr.hardsub.json`, `asr.json`, `hardsub_band.json`, `hardsub_cues.json`, `script.json`, `sub.zh.hardsub.srt`

**Tín hiệu rủi ro (đếm tĩnh):** broad_except=4 · file_writes=5 · subprocess=2 · swallow_except=2

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
