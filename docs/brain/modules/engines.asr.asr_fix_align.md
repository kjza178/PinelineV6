# `engines.asr.asr_fix_align`

[← Index](../generated/module-index.md) · [Brain home](../README.md)

<!-- AUTO:START — sinh bởi scripts/brain_build.py 2026-11-11 13:00. Đừng sửa tay khối này. -->
**File:** `engines/asr/asr_fix_align.py` · **Lớp:** `asr` · **335 dòng** (293 code) · **7 hàm**, **0 class** · sha1 `05c3a6acef`

> asr_fix_align.py — Phương án 3: fix monotonic_fix Bước 1 (fallback): chỗ timing bịa/đảo → trả start/end về Nano VAD Bước 2 (forced align): Qwen3-ForcedAligner chỉ trên các đoạn vừa fallback

### Phụ thuộc

- **Gọi tới (import nội bộ):** [engines.asr.fun_asr_nano](./engines.asr.fun_asr_nano.md), [engines.subs](./engines.subs.md), [engines.vram](./engines.vram.md)
- **Được dùng bởi:** [engines.asr.asr_repair](./engines.asr.asr_repair.md), [engines.asr.hybrid_asr](./engines.asr.hybrid_asr.md)
- **Test:** ❌ chưa có test import trực tiếp
- **Thư viện ngoài:** `numpy`, `qwen_asr`, `soundfile`, `torch`

```mermaid
flowchart LR
  engines_asr_asr_fix_align[[engines.asr.asr_fix_align]]
  engines_asr_asr_repair[engines.asr.asr_repair] --> engines_asr_asr_fix_align
  engines_asr_hybrid_asr[engines.asr.hybrid_asr] --> engines_asr_asr_fix_align
  engines_asr_asr_fix_align --> engines_asr_fun_asr_nano[engines.asr.fun_asr_nano]
  engines_asr_asr_fix_align --> engines_subs[engines.subs]
  engines_asr_asr_fix_align --> engines_vram[engines.vram]
```

### Hàm & class top-level

| Tên | Dòng | Dài | Tham số | Docstring |
|---|---:|---:|---|---|
| `_load` | 46 | 2 | `path` |  |
| `_save_asr` | 50 | 8 | `work, tag, rows` |  |
| `step1_nano_fallback` | 60 | 48 | `aligned, nano` | Chỗ monotonic_fix (và đảo thời gian): lấy lại timing Nano theo chữ. |
| `_merge_indices_to_windows` | 110 | 22 | `rows, indices, gap_s` | Gộp index gần nhau thành cửa sổ FA. |
| `step2_forced_align` | 134 | 104 | `rows, wav, indices, device, pad_s, max_windows` | Forced-align từng cửa sổ chứa các câu nano_fallback. |
| `run_fix` | 240 | 75 | `work_dir, aligned_name, nano_name, device, step1_only, step2_only, …` |  |
| `main` | 317 | 15 | `argv` |  |

### Config / env keys (4)

`ASR_FA_PAD_S`, `HF_HUB_DISABLE_SYMLINKS`, `HF_HUB_DISABLE_SYMLINKS_WARNING`, `QWEN3_FORCED_ALIGNER`

**File/artifact nhắc tới:** `asr.aligned.json`, `asr.aligned_fa.meta.json`, `asr.aligned_fix.json`, `asr.aligned_fix.meta.json`, `asr.fun_nano.json`, `audio.wav`

**Tín hiệu rủi ro (đếm tĩnh):** broad_except=1 · file_writes=5

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
