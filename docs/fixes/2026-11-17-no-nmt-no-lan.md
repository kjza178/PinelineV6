# 2026-11-17 — Bỏ NMT, không gọi LAN dự phòng, job dở không bị kéo về luồng cũ

## Triệu chứng (log user)
```
[fill] script thiếu ~3490 câu so với ASR → dịch tiếp (không skip)
[preset_switcher] ⛔ KHÔNG đổi preset 'gemma4-12b-qat-mtp-fast' … llama http://127.0.0.1:18082
[mt-zh-vi] PRIMARY 3490 câu · base · ngocdang83/HachimiMT-60-zh-vi
[mt-zh-vi] bỏ qua: [WinError 3] The system cannot find the path specified: 'E:\\'
[trans] Model=local | mode=batch | cần dịch 3490/3669 câu, lô 20
[trans] lô 20 câu parse lỗi/thiếu … -> chia đôi thử lại
```
Config đặt DeepSeek nhưng job chạy NMT + local; vẫn có gọi LAN-cũ.

## Root cause
1. `engines/translate_simple/cfg._infer_job_pipeline`: job chưa đóng dấu mà có `script.json` / stage translate đã chạy
   → «legacy» (`translate_pipeline_why=inferred`). Luồng cũ = `engines/translate.run_translate`: NMT (MT_ZH_VI=1 trong
   config chép từ V2) rồi LLM `TRANSLATE_MODEL` — preset «Khuyến nghị» gán `local` cho «Dịch phụ» → gemma local.
   DeepSeek chỉ là `SIMPLE_MODEL`/`REVIEW_MODEL` của luồng simple.
2. NMT còn trong `run_translate`.
3. `core/machine.py` mặc định `AURORA_FALLBACK_URLS` = LAN-cũ → mọi gọi `chatgpt/…` (plan) thử LAN khi
   :3377 lỗi; `LLM_BASE_URL`/`CLOUD_LLM_BASE_URL` trong config cũ trỏ 9router LAN-cũ.

## Sửa
- `cfg._infer_job_pipeline` chỉ còn suy «simple»; `job_pipeline` coi dấu `legacy/inferred` là cũ khi mặc định là simple.
- `translate.run_translate`: xoá khối NMT.
- `core/machine.py`: `is_lan_url`, `lan_llm_allowed` (`ALLOW_LAN_LLM`, mặc định 0), `no_lan`; lọc
  `AURORA_FALLBACK_URLS`, `cloud_llm_base_url`; `core/config.llm_settings/agent_llm` LAN → 127.0.0.1:8083;
  `engines/llm.py` (model `ollama/…-cloud`) dùng `no_lan`. URL đặt rõ làm CHÍNH (`AURORA_BASE_URL`, `LLAMA_BASE_URL`)
  không bị lọc.
- Test: `tests/test_translate_simple.py` (test_p6_inflight_script_job_goes_simple, test_p6_stale_inferred_legacy_stamp_reevaluated),
  `tests/test_machine_config.py` (test_lan_urls_skipped_unless_allowed, test_llm_settings_lan_base_falls_back_local).

## Cần trên Windows
- Restart :8806. Job đang kẹt (vd 18d869dd0c3f): Dừng rồi Chạy tiếp → log phải là `[simple] …` (không còn `[mt-zh-vi]`,
  `[trans] Model=local`).
- Tuỳ chọn dọn `pineline.config.json`: `MT_ZH_VI*`, `LLM_BASE_URL`/`CLOUD_LLM_BASE_URL` LAN-cũ (đã vô hiệu bằng code).
