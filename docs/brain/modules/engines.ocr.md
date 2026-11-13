# `engines.ocr`

[← Index](../generated/module-index.md) · [Brain home](../README.md)

<!-- AUTO:START — sinh bởi scripts/brain_build.py 2026-11-11 13:00. Đừng sửa tay khối này. -->
**File:** `engines/ocr.py` · **Lớp:** `asr` · **279 dòng** (231 code) · **8 hàm**, **0 class** · sha1 `1331cac5e6`

> ocr.py — STAGE OCR: đọc chữ TRÊN HÌNH (tên nhân vật / địa danh / caption / thẻ) mà KHÔNG nằm trong phụ đề thoại, rồi dịch sang tiếng Việt. Đầu vào : work/<job>/source.mp4 + asr.json (để trừ chữ trùng thoại)

### Phụ thuộc

- **Gọi tới (import nội bộ):** [core.manifest](./core.manifest.md), [engines.llm](./engines.llm.md), [engines.translate](./engines.translate.md), [engines.vram](./engines.vram.md), [state.job_state](./state.job_state.md)
- **Được dùng bởi:** [engines.render](./engines.render.md), [server](./server.md)
- **Được nhắc tên file `.py` (subprocess/script) bởi:** [server](./server.md)
- **Test:** `tests/v5/test_layout_paths.py`
- **Thư viện ngoài:** `rapidocr_onnxruntime`, `state`

```mermaid
flowchart LR
  engines_ocr[[engines.ocr]]
  engines_render[engines.render] --> engines_ocr
  server[server] --> engines_ocr
  engines_ocr --> core_manifest[core.manifest]
  engines_ocr --> engines_llm[engines.llm]
  engines_ocr --> engines_translate[engines.translate]
  engines_ocr --> engines_vram[engines.vram]
  engines_ocr --> state_job_state[state.job_state]
```

### Hàm & class top-level

| Tên | Dòng | Dài | Tham số | Docstring |
|---|---:|---:|---|---|
| `_meaningful` | 45 | 8 | `t` | Giữ chữ CÓ NGHĨA: ≥2 chữ Hán, hoặc số ≥3 chữ số (ID). Bỏ rác latin / 1 chữ đơn. |
| `_find_video` | 60 | 9 | `work_dir` |  |
| `_probe_size` | 71 | 10 | `video` |  |
| `extract_keyframes` | 83 | 34 | `video, out_dir, dur, stride` | Trích KEYFRAME + mốc giờ. Giải mã chỉ I-frame nên nhanh. stride: lấy cách quãng. |
| `_classify_zone` | 119 | 13 | `cx, cy, text` | Trả 'wm' (watermark), 'sub' (hardsub thoại), hoặc 'cand' (ứng viên). |
| `run_ocr` | 134 | 113 | `work_dir, dur, stride, model, force` |  |
| `_translate_entries` | 249 | 17 | `entries, model` | Dịch danh sách chữ trên hình sang tiếng Việt (1 lời gọi LLM). |
| `main` | 268 | 8 | `argv` |  |

**File/artifact nhắc tới:** `asr.json`, `ocr.json`

**Tín hiệu rủi ro (đếm tĩnh):** broad_except=3 · file_writes=1 · subprocess=3

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
