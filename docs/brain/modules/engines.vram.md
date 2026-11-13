# `engines.vram`

[← Index](../generated/module-index.md) · [Brain home](../README.md)

<!-- AUTO:START — sinh bởi scripts/brain_build.py 2026-11-11 13:00. Đừng sửa tay khối này. -->
**File:** `engines/vram.py` · **Lớp:** `ops` · **89 dòng** (76 code) · **4 hàm**, **0 class** · sha1 `0c23090f49`

> vram.py — Nhả VRAM trước MỖI bước dùng GPU. Ollama (Gemma ctx 128k), FunASR, Demucs, TTS, NVENC tranh nhau 12GB. Model Ollama sống dai sau bước dịch; torch cache sống dai trong cùng process.

### Phụ thuộc

- **Gọi tới (import nội bộ):** —
- **Được dùng bởi:** [engines.asr.asr](./engines.asr.asr.md), [engines.asr.asr_fix_align](./engines.asr.asr_fix_align.md), [engines.asr.asr_repair](./engines.asr.asr_repair.md), [engines.asr.firered_asr](./engines.asr.firered_asr.md), [engines.asr.fun_asr_nano](./engines.asr.fun_asr_nano.md), [engines.asr.hybrid_asr](./engines.asr.hybrid_asr.md), [engines.asr.nano_fa_asr](./engines.asr.nano_fa_asr.md), [engines.asr.paraformer_zh](./engines.asr.paraformer_zh.md), [engines.asr.sensevoice_asr](./engines.asr.sensevoice_asr.md), [engines.clone_tts](./engines.clone_tts.md), [engines.llm](./engines.llm.md), [engines.ocr](./engines.ocr.md), [engines.render](./engines.render.md), [engines.separate](./engines.separate.md), [engines.translate](./engines.translate.md), [server](./server.md)
- **Test:** ❌ chưa có test import trực tiếp
- **Thư viện ngoài:** `torch`

```mermaid
flowchart LR
  engines_vram[[engines.vram]]
  engines_asr_asr[engines.asr.asr] --> engines_vram
  engines_asr_asr_fix_align[engines.asr.asr_fix_align] --> engines_vram
  engines_asr_asr_repair[engines.asr.asr_repair] --> engines_vram
  engines_asr_firered_asr[engines.asr.firered_asr] --> engines_vram
  engines_asr_fun_asr_nano[engines.asr.fun_asr_nano] --> engines_vram
  engines_asr_hybrid_asr[engines.asr.hybrid_asr] --> engines_vram
  engines_asr_nano_fa_asr[engines.asr.nano_fa_asr] --> engines_vram
  engines_asr_paraformer_zh[engines.asr.paraformer_zh] --> engines_vram
  engines_asr_sensevoice_asr[engines.asr.sensevoice_asr] --> engines_vram
  engines_clone_tts[engines.clone_tts] --> engines_vram
  engines_llm[engines.llm] --> engines_vram
  engines_ocr[engines.ocr] --> engines_vram
  engines_render[engines.render] --> engines_vram
  engines_separate[engines.separate] --> engines_vram
  engines_translate[engines.translate] --> engines_vram
```

### Hàm & class top-level

| Tên | Dòng | Dài | Tham số | Docstring |
|---|---:|---:|---|---|
| `_ollama_loaded` | 14 | 11 | `` |  |
| `_stop_ollama` | 27 | 9 | `` |  |
| `_empty_torch` | 38 | 16 | `` | Xả cache CUDA — KHÔNG synchronize (dễ treo vô hạn khi GPU kẹt). |
| `free_gpu_vram` | 56 | 34 | `reason` | Stop Ollama + xả torch cache. An toàn gọi nhiều lần, kể cả không có GPU. |

### Config / env keys (1)

`SKIP_VRAM_FREE`

**Tín hiệu rủi ro (đếm tĩnh):** broad_except=6 · sleep=1 · subprocess=2 · swallow_except=3 · threads=1

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
