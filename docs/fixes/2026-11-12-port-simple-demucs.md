# Port V2 → V6: luồng dịch simple + Demucs trước ASR (F6, 2026-11-12)

- **Nguồn (chỉ đọc):** `Z:\PinelineV2` — `translate_simple/`, `data/lexicon/*.json`, `cloud_retry.py`, `separate.py`, `asr.py`,
  nhánh server (`_auto_gpu`, `_process_gpu`, `_run_stage("translate")`, `_translate_flow`, `_queue_park_ready`),
  `tools/{collect_manual_edits,simple_stats}.py`, test. Thiết kế V2: `docs/design/2026-11-12-translate-simple.md`
  (bản sao ở V6 cùng tên); fix/review V2: `Z:\PinelineV2\docs\{fixes,reviews}\2026-11-12-translate-simple*.md`,
  `Z:\PinelineV2\docs\fixes\2026-11-12-asr-demucs-default.md`.
- **Backup:** `.backup_edit_20261112_agentF6\` (giữ cấu trúc; file mới không có bản cũ).
- **Cách port:** biến đổi import phẳng → package bằng chính `scripts/sync_from_v2.py` (rewrite_imports), rồi sửa tay
  theo luật V6. Không đổi thuật toán.

## Đặt trong kiến trúc V6

| V2 | V6 | Ghi chú |
|---|---|---|
| `translate_simple/*.py` (16 file) | `engines/translate_simple/` | ghi manifest `pipeline._manifest_update` → `state.job_state.mutate`; `ScriptStore.save()` → `write()` (alias `save` giữ) để ratchet F1 không nhầm script.json với manifest; `ROOT` lexicon/pair_rules = `parents[2]`; `build_plan(before_local_llm=…)` chỉ truyền khi `engines.translate.build_plan` nhận (V6 chưa có tham số này — trước đó TypeError → plan rỗng) |
| — | `pipeline/stages/simple_translate.py` `TranslateSimple(Stage)` | gọi engine; lỗi không phải Dừng → kết quả `park` (như V2), «stopped by user»/`.stop`/`_STOP_JOBS` → `Stopped` |
| — | `pipeline/gates/simple_translate_gate.py` `SimpleTranslateGate(Gate)` | done → PASS · park → PARK(waiting_review, B2) · hold → HOLD(simple_gate) · quá `QUEUE_REVIEW_RETRY_MAX` → HOLD review_failed · `qa_override` cho qua |
| `server._auto_translate_simple` (Manifest.update) | `server._auto_translate_simple` = `Runner([TranslateSimple, SimpleTranslateGate])` | ghi agent qua `state.job_state.apply_simple_gate` (mới, 1 Manifest.update) |
| `_queue_park_ready` + simple_park | `server._queue_park_ready` + `state.job_state.park_ready` | park simple chờ `review_retry_at` kể cả unrev=0 |
| `_process_gpu` / `_run_stage("translate")` / `_translate_flow` / QA gate `_rev` | cùng tên trong `server.py` | rẽ nhánh như V2 |
| `data/lexicon/{idioms_zh_vi,pronoun_rules_zh_vi}.json` | `data/lexicon/` | |
| `cloud_retry.py` | `engines/cloud_retry.py` | |
| `separate.py` | `engines/separate.py` | chia khúc khoảng lặng + đúng số mẫu; tiến độ qua `js.mutate` |
| `asr.py` | `engines/asr/asr.py` | `resolve_asr_source` + fallback; `m.save()`→`js.persist` (ratchet 93 giữ nguyên) |
| `tools/collect_manual_edits.py`, `tools/simple_stats.py` | `tools/` | chỉ đọc work; `--work D:\PinelineV6_work` |
| `tests/test_translate_simple*.py`, `test_separate_demucs_chunks.py`, `test_asr_demucs.py` | `tests/` | chuỗi patch → `engines.*`; mặc định đổi theo V6 |
| — | `tests/test_f6_port_simple_demucs.py` (13 test) | Stage/Gate, apply_simple_gate, cờ instance, vị trí data, luật V6 |

## Mặc định / rollback (cờ instance)

| Việc | Mặc định | Rollback |
|---|---|---|
| Luồng dịch | **simple** (`instances/*.json` `"translate_pipeline": "simple"`) | `"translate_pipeline": "legacy"` rồi restart (apply_env → `PINELINE_TRANSLATE_PIPELINE`). Thứ tự đọc: env `TRANSLATE_PIPELINE` > instance > `pineline.config.json` > simple. Trống/`simple`/lạ = simple; `legacy/old/off/0/false/no` = luồng cũ (quy ước V4/V5) |
| Cờ con `SIMPLE_*` (zh_source, pair_rules, pair_fill, learn_inferred, pronoun_analyze, nmt_wait_plan…) | **0** (như V2) | env / `pineline.config.json` |
| Demucs trước ASR | **1** (`"asr_use_demucs": true` → `PINELINE_FLAG_ASR_USE_DEMUCS`), lỗi → fallback audio gốc | `"asr_use_demucs": false`; env `ASR_USE_DEMUCS` đặt tay thắng instance |

`scripts/golden_ab.py` / `golden_ab_simple.bat` / `data/golden*` KHÔNG đổi: công cụ đo đó chạy CODE V2 (biến thể `simple*` đặt
`TRANSLATE_PIPELINE=simple` cho tiến trình con V2). Đo V6: chấm `script.json` của job V6 bằng
`python scripts\eval_translation.py` (cùng golden) hoặc test e2e `tests/test_translate_simple.py::test_end_to_end_golden_window_echo`
(đặt `TS_GOLDEN_DIR`, `TS_GOLDEN_WORK=D:\PinelineV6_work`, `TS_EVAL_SCRIPT`).

## Test (cloud, `AGENT_BRAIN=0`, Python 3.13, thiếu fastapi)

- Trước: `--ignore=tests/v5` 244 passed / 2 skipped · tests/v5 11 failed / 88 passed / 12 errors (môi trường).
- Sau: 354 passed / 9 skipped (+110 test: 97 port + 13 V6) · tests/v5 y hệt (11/88/12; danh sách fail trùng).
- Với stub fastapi (chỉ ở cloud, không commit): 3 test rẽ nhánh server + toàn bộ test_translate_simple chạy PASS;
  1 fail `test_routes_ui_html_file_localized` có sẵn ở baseline (do stub).
- `tree_audit`: ERROR=0 · dups=1 (như trước). `verify_all --dry`: PASS. Ratchet: 0 `.save()`, persist 93.

## Rủi ro

- Job V6 mới đều chạy simple + Demucs: thêm 1 lần Demucs/job (GPU dùng chung V2; OOM → fallback, không hỏng job).
- Render `duck/duck_bgm` dùng `no_vocals.wav` khi có (job mới có sẵn từ bước ASR) — khác tiếng gốc hạ thấp (như V2).
- simple phụ thuộc cloud REVIEW (không fallback local): cloud chết → park B2 → sau 3 lượt HOLD review_failed.
- `engines/translate.py` V6 cũ hơn V2 (chưa có b1–b3); simple chỉ dùng hàm chung (build_plan, ensure_hard_terms,
  heal_obvious_internal_stutter…) — đã kiểm tồn tại; chất lượng plan/NMT có thể lệch V2 → so job thật trước cutover.
- Server branch test chỉ chạy với fastapi (Windows `verify_all.bat`).
