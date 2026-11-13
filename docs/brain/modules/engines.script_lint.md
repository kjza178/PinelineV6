# `engines.script_lint`

[← Index](../generated/module-index.md) · [Brain home](../README.md)

<!-- AUTO:START — sinh bởi scripts/brain_build.py 2026-11-11 13:00. Đừng sửa tay khối này. -->
**File:** `engines/script_lint.py` · **Lớp:** `translate` · **175 dòng** (147 code) · **10 hàm**, **0 class** · sha1 `b11c4d71f8`

> Soát lỗi máy móc bản dịch VI (script.json) — chạy SAU review/polish, trước TTS. autofix_rows: sửa chắc chắn đúng (không cần LLM) · «ông tôi / cô tôi» khi zh là 他/她 (không phải họ hàng)

### Phụ thuộc

- **Gọi tới (import nội bộ):** [agents.agent_name_pass](./agents.agent_name_pass.md), [engines.subs](./engines.subs.md)
- **Được dùng bởi:** [engines.translate](./engines.translate.md)
- **Test:** ❌ chưa có test import trực tiếp

```mermaid
flowchart LR
  engines_script_lint[[engines.script_lint]]
  engines_translate[engines.translate] --> engines_script_lint
  engines_script_lint --> agents_agent_name_pass[agents.agent_name_pass]
  engines_script_lint --> engines_subs[engines.subs]
```

### Hàm & class top-level

| Tên | Dòng | Dài | Tham số | Docstring |
|---|---:|---:|---|---|
| `_fix_kin` | 38 | 4 | `zh, vi` |  |
| `_fix_punct` | 44 | 2 | `vi` |  |
| `_fix_num` | 48 | 2 | `vi` |  |
| `_glossary_terms` | 52 | 8 | `plan` |  |
| `_variant_re` | 62 | 6 | `vi` |  |
| `_chi_majority` | 70 | 9 | `rows` |  |
| `autofix_rows` | 81 | 29 | `rows, plan` |  |
| `lint_rows` | 112 | 29 | `rows, plan` | Đếm lỗi còn lại — không sửa. Dùng cho QA gate / agent / UI. |
| `run_lint` | 143 | 15 | `work_dir, fix` | Lint script.json của 1 job; fix=True thì autofix + ghi lại (+ SRT/display). |
| `write_display` | 160 | 9 | `work_dir, rows` |  |

**File/artifact nhắc tới:** `glossary.json`, `script.display.json`, `script.json`, `sub.vi.srt`

**Tín hiệu rủi ro (đếm tĩnh):** file_writes=4

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
