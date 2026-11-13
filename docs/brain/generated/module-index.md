# Index module

_Sinh tự động bởi `scripts/brain_build.py` lúc 2026-11-11 13:00. Đừng sửa tay — chạy lại script._


Tổng: **155 module** (+61 test), **72,200 dòng**. Cột *Manual* = thẻ đã có phần viết tay (✅) hay chưa (·).

## control (12)

| Module | Dòng | Hàm | Routes | Fix | Test | Manual | Tóm tắt |
|---|---:|---:|---:|---:|---:|:-:|---|
| [server](../modules/server.md) | 6626 | 186 | 68 | 3 | 10 | · | server.py — Backend web Phase 1 (FastAPI). Bọc engine (download -> asr -> translate) thành |
| [http_routes.routes_hg](../modules/http_routes.routes_hg.md) | 1018 | 56 | 58 | 1 | 1 | · | Cầu nối Hongguo Downloader (local :8000): /api/hg/*. Tách từ server.py (đợt 7). Chỉ được n |
| [http_routes.routes_uploader_settings](../modules/http_routes.routes_uploader_settings.md) | 659 | 29 | 36 |  | ❌ | · | routes_uploader_settings.py — API Router cho Quản lý Cài đặt (Settings) & YouTube Uploader |
| [http_routes.routes_agent](../modules/http_routes.routes_agent.md) | 644 | 40 | 16 |  | 1 | · | AI Agent nhúng (agent_core): hook cho Runtime + /api/agent/*. Tách từ server.py (đợt 7). C |
| [http_routes.routes_films](../modules/http_routes.routes_films.md) | 381 | 32 | 31 | 1 | 1 | · | FastAPI Router cho Thư viện Phim & Database (đồng bộ trực tiếp trong Pineline V4). Thay vì |
| [restart_server](../modules/restart_server.md) | 319 | 13 |  | 1 | ❌ | · | Restart server Pineline (giữ sidecar), khôi phục hàng đợi + Auto-pilot. Instance: `--insta |
| [app.main](../modules/app.main.md) | 162 | 4 | 5 | 2 | 1 | · | app/main.py — FastAPI app V6 (route mỏng; logic ở state/pipeline/services). F3 (tích hợp): |
| [http_routes.routes_ui](../modules/http_routes.routes_ui.md) | 116 | 11 | 14 |  | 2 | · | Trang UI: /, /ui3, /ui4, /hongguo, /hongguo2, /films/ui, /studio, /v5-map. /hongguo2 = ski |
| [tools.context_studio.app_streamlit](../modules/tools.context_studio.app_streamlit.md) | 108 | 0 |  |  |  | · | Streamlit UI stub — Context Detection trước khi dịch Pineline. Chạy: pip install streamlit |
| [app.__main__](../modules/app.__main__.md) | 85 | 3 |  | 1 | 1 | · | python -m app --instance v6 |
| [app.routes.__init__](../modules/app.routes.__init__.md) | 1 | 0 |  | 1 |  | · |  |
| [app.__init__](../modules/app.__init__.md) | 0 | 0 |  | 1 |  | · |  |

## agent (15)

| Module | Dòng | Hàm | Routes | Fix | Test | Manual | Tóm tắt |
|---|---:|---:|---:|---:|---:|:-:|---|
| [agents.agent_core](../modules/agents.agent_core.md) | 2080 | 30 |  | 1 | 3 | · | agent_core.py — AI Agent nhúng trong Pineline V2. Không gọi HTTP self. Tools gọi runtime đ |
| [agents.agent_hongguo](../modules/agents.agent_hongguo.md) | 1572 | 36 |  | 1 | ❌ | · | agent_hongguo.py — Tích hợp bộ tool Hongguo Downloader & Auto-chain cho Pineline AI Agent. |
| [agents.agent_brain](../modules/agents.agent_brain.md) | 1233 | 36 |  |  | ❌ | · | Second Brain — daemon nền full-silent. Sensors → incidents → policy → actuators. Bật: AGEN |
| [agents.agent_heal](../modules/agents.agent_heal.md) | 662 | 10 |  |  | ❌ | · | agent_heal.py — Chẩn đoán + heal job lỗi (an toàn theo cấp). safe: heal stale running, vá  |
| [agents.agent_av_sync](../modules/agents.agent_av_sync.md) | 567 | 11 |  |  | ❌ | · | AV sync diagnose + heal — phát hiện lệch sub/audio / audio cụt sau mux. Các class chính: a |
| [agents.agent_tool_loop](../modules/agents.agent_tool_loop.md) | 482 | 11 |  |  | 1 | · | Tool loop — điều khiển pipeline theo bước, chờ kết quả thật. Khác `pineline_run_stage` (fi |
| [agents.agent_watch](../modules/agents.agent_watch.md) | 375 | 19 |  | 2 | ❌ | · | Watcher A: định kỳ scan → heal safe + log + Telegram (tuỳ chọn). Env: AGENT_WATCH=1 |
| [agents.agent_hang_fix](../modules/agents.agent_hang_fix.md) | 368 | 9 |  |  | ❌ | · | Second Brain — pass LLM trace + xử lý high `hang` (đuôi treo / nửa câu). Không chỉ merge m |
| [agents.agent_quality](../modules/agents.agent_quality.md) | 356 | 6 |  |  | 1 | · | Deterministic VI quality scan + safe heals (Second Brain L2). Port từ D:\PinelineV2_tmp\_q |
| [agents.agent_stage_io](../modules/agents.agent_stage_io.md) | 286 | 14 |  |  |  | · | Peek / tóm tắt từng stage — chỉ đọc disk (an toàn). |
| [agents.agent_brain_context](../modules/agents.agent_brain_context.md) | 268 | 7 |  |  |  | · | System snapshot cho Second Brain LLM — toàn hệ, không chỉ 1 job. |
| [agents.agent_pronoun_pass](../modules/agents.agent_pronoun_pass.md) | 257 | 4 |  |  |  | · | Rà XƯNG HÔ trong bản dịch theo glossary.pronouns + genre. 1. Quét deterministic (cùng appl |
| [agents.agent_qa_gate](../modules/agents.agent_qa_gate.md) | 227 | 8 |  |  |  | · | Gate QA: translate → review → diff → mới cho TTS. |
| [agents.agent_name_pass](../modules/agents.agent_name_pass.md) | 135 | 5 |  |  |  | · | Rà TÊN NHÂN VẬT trong bản dịch theo bảng nhân vật (glossary.json). 1. Quét: câu nào zh có  |
| [agents.agent_error_class](../modules/agents.agent_error_class.md) | 121 | 3 |  |  |  | · | Phân loại lỗi theo log / cur_text / error (ASR · TTS · mux · …). |

## sidecar (3)

| Module | Dòng | Hàm | Routes | Fix | Test | Manual | Tóm tắt |
|---|---:|---:|---:|---:|---:|:-:|---|
| [agents.agent_tele_cmd](../modules/agents.agent_tele_cmd.md) | 2302 | 40 |  | 1 | 3 | · | Sidecar: Telegram lệnh + HTTP mỏng (cổng AUTOPILOT_PORT của instance; V2 cũ: :8802) KHÔNG  |
| [agents.agent_feeder](../modules/agents.agent_feeder.md) | 2072 | 69 |  | 1 | 1 | · | Feeder + Run-watch cho Auto-pilot (chạy trong sidecar :8802, chỉ gọi API :8801). Feeder —  |
| [agents.agent_autopilot](../modules/agents.agent_autopilot.md) | 332 | 16 |  | 1 | ❌ | · | Auto-pilot — queue need_queue + heal safe qua HTTP Pineline (server của instance, PINELINE |

## source (7)

| Module | Dòng | Hàm | Routes | Fix | Test | Manual | Tóm tắt |
|---|---:|---:|---:|---:|---:|:-:|---|
| [agents.hongguo_bridge](../modules/agents.hongguo_bridge.md) | 4222 | 118 |  |  | 1 | · | hongguo_bridge.py — client mỏng gọi Hongguo Downloader local API (:8000). App Hongguo phải |
| [agents.hongguo_rpc](../modules/agents.hongguo_rpc.md) | 1449 | 57 |  |  | ❌ | · | hongguo_rpc.py — JSON-RPC stdio client cho Hongguo WinUI 2.x helper.jar. Không dùng HTTP : |
| [engines.download](../modules/engines.download.md) | 698 | 20 |  |  | 1 | · | download.py — STAGE 1: Tải video (Douyin / Bilibili / TikTok / ...). Engine: yt-dlp. Tự re |
| [agents.hongguo_license_ensure](../modules/agents.hongguo_license_ensure.md) | 285 | 11 |  |  |  | · | Ensure Hongguo 2.x helper is license-proved (same process as hongguo_rpc). Uses DPAPI key  |
| [engines.playlist](../modules/engines.playlist.md) | 219 | 5 |  |  |  | · | playlist.py — Bóc 1 link thành DANH SÁCH video rồi tải hàng loạt. Vì sao tách riêng: Pinel |
| [agents.hg_ui_ctx](../modules/agents.hg_ui_ctx.md) | 46 | 4 |  |  |  | · | Per-request Hongguo UI backend context (không reload — tránh mất override). Hongguo1 vs Ho |
| [services.hongguo_flow](../modules/services.hongguo_flow.md) | 13 | 1 |  | 1 |  | · | services/hongguo_flow.py — điểm vào Hongguo cho server (V6 F4, khung). Mục tiêu (thiết kế  |

## asr (16)

| Module | Dòng | Hàm | Routes | Fix | Test | Manual | Tóm tắt |
|---|---:|---:|---:|---:|---:|:-:|---|
| [engines.asr.fun_asr_nano](../modules/engines.asr.fun_asr_nano.md) | 603 | 11 |  |  | ❌ | · | fun_asr_nano.py — STAGE ASR bằng Fun-ASR-Nano (chất lượng tiếng Trung cao nhất). Model : F |
| [engines.asr.asr_repair](../modules/engines.asr.asr_repair.md) | 551 | 16 |  |  | ❌ | · | asr_repair.py — Hậu kỳ Hybrid: A lấp lỗ + B sửa nghe sai + FA. A) Gap: Para có thoại, bản  |
| [engines.asr.asr_spam](../modules/engines.asr.asr_spam.md) | 469 | 14 |  |  | ❌ | · | asr_spam.py — Chặn ASR nuốt tiếng nền (cười/rên 哈·啊·呃 lặp). Không dựa vào check tay: sau m |
| [engines.asr.hardsub_asr](../modules/engines.asr.hardsub_asr.md) | 457 | 12 |  |  | ❌ | · | hardsub_asr.py — OCR hardsub chỉ để RÀ SOÁT / SỬA sub ZH (asr · srt). Một nhiệm vụ: RapidO |
| [engines.asr.asr](../modules/engines.asr.asr.md) | 448 | 13 |  |  | 1 | · | asr.py — STAGE 2 (phần đầu): tách audio + nhận diện lời (Whisper). Đầu vào : work/<job>/so |
| [engines.asr.asr_align_nano_para](../modules/engines.asr.asr_align_nano_para.md) | 389 | 10 |  |  | ❌ | · | asr_align_nano_para.py — Hybrid A: chữ Fun-ASR-Nano + timestamp Paraformer (char-ts). Đầu  |
| [engines.separate](../modules/engines.separate.md) | 376 | 8 |  |  | 1 | · | separate.py — STAGE 6 (Tách nền): dùng Demucs bỏ GIỌNG NÓI (tiếng Trung) khỏi audio gốc, G |
| [engines.asr.firered_asr](../modules/engines.asr.firered_asr.md) | 346 | 5 |  |  | ❌ | · | firered_asr.py — STAGE ASR bằng FireRedASR2-AED (system VAD+LID+ASR+Punc). Cần clone repo  |
| [engines.asr.nano_fa_asr](../modules/engines.asr.nano_fa_asr.md) | 343 | 5 |  |  | ❌ | · | nano_fa_asr.py — SOTA ASR: Fun-ASR-Nano (Chữ chuẩn nhất) + Direct Qwen3-ForcedAligner (Mốc |
| [engines.asr.asr_fix_align](../modules/engines.asr.asr_fix_align.md) | 335 | 7 |  |  | ❌ | · | asr_fix_align.py — Phương án 3: fix monotonic_fix Bước 1 (fallback): chỗ timing bịa/đảo →  |
| [engines.asr.paraformer_zh](../modules/engines.asr.paraformer_zh.md) | 315 | 2 |  |  | ❌ | · | paraformer_zh.py — STAGE ASR bằng FunASR Paraformer-zh (timestamp tốt cho phụ đề/TTS). Mod |
| [engines.ocr](../modules/engines.ocr.md) | 279 | 8 |  |  | 1 | · | ocr.py — STAGE OCR: đọc chữ TRÊN HÌNH (tên nhân vật / địa danh / caption / thẻ) mà KHÔNG n |
| [engines.asr.sensevoice_asr](../modules/engines.asr.sensevoice_asr.md) | 261 | 4 |  |  |  | · | sensevoice_asr.py — STAGE ASR bằng FunASR SenseVoiceSmall (nhanh, đa ngôn ngữ). Model : ii |
| [engines.asr.hybrid_asr](../modules/engines.asr.hybrid_asr.md) | 208 | 3 |  |  |  | · | hybrid_asr.py — ASR chốt: Nano chữ + Para timing + ForcedAlign + Repair. Pipeline (sidecar |
| [engines.asr.asr_vad_utils](../modules/engines.asr.asr_vad_utils.md) | 158 | 5 |  |  |  | · | asr_vad_utils.py — VAD kwargs FunASR thật + pad/overlap khi cắt đoạn. FunASR fsmn-vad (1.4 |
| [engines.asr.asr_hybrid_progress](../modules/engines.asr.asr_hybrid_progress.md) | 145 | 5 |  |  |  | · | asr_hybrid_progress.py — Đưa tiến độ sidecar (Nano/Para) vào stages.asr cho UI Hybrid. UI  |

## translate (10)

| Module | Dòng | Hàm | Routes | Fix | Test | Manual | Tóm tắt |
|---|---:|---:|---:|---:|---:|:-:|---|
| [engines.translate](../modules/engines.translate.md) | 4131 | 74 |  |  | 7 | · | translate.py — STAGE dịch: "lập phương án" (hồ sơ nhân vật + XƯNG HÔ) rồi dịch CÓ NGỮ CẢNH |
| [engines.llm](../modules/engines.llm.md) | 894 | 23 |  |  | 2 | · | llm.py — Gọi LLM qua 9router API (tương thích chuẩn OpenAI). Cấu hình qua config.json / pi |
| [engines.pronoun_consistency](../modules/engines.pronoun_consistency.md) | 871 | 30 |  |  | 1 | · | Đồng nhất xưng hô không cần diarization (A–C + vá câu đỏ). A) analyze_full_zh: quét TOÀN B |
| [engines.hv_auditor](../modules/engines.hv_auditor.md) | 475 | 15 |  |  | ❌ | · | Hán-Việt auditor — chống glossary khóa sai âm (vd 逆→Nghị thay vì Nghịch). 1) Unihan kVietn |
| [engines.translate_alts](../modules/engines.translate_alts.md) | 383 | 5 |  |  | ❌ | · | Phương án dịch tối ưu — ngữ cảnh + văn phong (trọng tâm Pineline). Quét câu dễ lệch / đa n |
| [engines.mt_zh_vi](../modules/engines.mt_zh_vi.md) | 302 | 14 |  |  | ❌ | · | NMT ZH→VI (HachimiMT) — DỊCH CHÍNH; LLM chỉ rà soát (run_review). Config: MT_ZH_VI=1       |
| [engines.pronoun_register_policy](../modules/engines.pronoun_register_policy.md) | 243 | 8 |  |  |  | · | Policy xưng hô tổng quát cho mix #1+#3 — không gắn 1 job/phim. Mục tiêu: - Phát hiện cửa s |
| [engines.series_glossary](../modules/engines.series_glossary.md) | 199 | 11 |  |  |  | · | Bảng nhân vật / thuật ngữ / xưng hô dùng chung cho CẢ BỘ (mọi mùa). - apply(work_dir, plan |
| [engines.script_lint](../modules/engines.script_lint.md) | 175 | 10 |  |  |  | · | Soát lỗi máy móc bản dịch VI (script.json) — chạy SAU review/polish, trước TTS. autofix_ro |
| [engines.title_mt](../modules/engines.title_mt.md) | 172 | 6 |  |  |  | · | Dịch thô tiêu đề Trung → Việt (Google gtx, không cần API key). Chỉ để duyệt Hongguo cho bi |

## tts (3)

| Module | Dòng | Hàm | Routes | Fix | Test | Manual | Tóm tắt |
|---|---:|---:|---:|---:|---:|:-:|---|
| [engines.clone_tts](../modules/engines.clone_tts.md) | 1116 | 38 |  |  | 2 | · | clone_tts.py — STAGE Lồng tiếng bằng TTS zero-shot clone tiếng Việt. Hai engine, cùng regi |
| [rvc_bridge.vieneu_batch](../modules/rvc_bridge.vieneu_batch.md) | 183 | 5 |  |  |  | · | Worker VieNeu-TTS v3-Turbo (chạy trong D:/vieneu_env). Đọc job json: {out_dir, ref, text,  |
| [rvc_bridge.omnivoice_batch](../modules/rvc_bridge.omnivoice_batch.md) | 47 | 1 |  |  |  | · | Worker omnivoice-vietnamese (chạy trong D:/OmniVoiceStudio-v2/.venv). Đọc job json: {out_d |

## render (9)

| Module | Dòng | Hàm | Routes | Fix | Test | Manual | Tóm tắt |
|---|---:|---:|---:|---:|---:|:-:|---|
| [engines.render](../modules/engines.render.md) | 3067 | 72 |  | 1 | 3 | · | render.py — STAGE 7 (Ghép + xuất): ráp video lồng tiếng "sạch như bản localize". Video (1  |
| [engines.subs](../modules/engines.subs.md) | 1106 | 34 |  |  | ❌ | · | subs.py — Sinh phụ đề .srt từ danh sách câu có timestamp. Dùng chung: asr.json (tiếng Trun |
| [engines.split_parts](../modules/engines.split_parts.md) | 834 | 17 |  |  | 1 | · | split_parts.py — Tự động tính toán và chia video dài (3h-12h+) thành các Part 2-2.5h để ch |
| [engines.render_fx](../modules/engines.render_fx.md) | 564 | 32 |  |  | 2 | · | Helpers: uniqueness filters + library BGM paths (copyright-safe audio). |
| [engines.sfx_lib](../modules/engines.sfx_lib.md) | 443 | 21 |  |  | 2 | · | sfx_lib.py — chế độ audio `sfx_lib`: giọng Việt + TIẾNG ĐỘNG gốc + NHẠC KHO SẠCH theo mức. |
| [engines.assemble](../modules/engines.assemble.md) | 422 | 16 |  |  | 1 | · | assemble.py — Ghép clip TTS thành MỘT track + kiểm khớp SRT. Hai chế độ: - assemble()      |
| [engines.media_ts](../modules/engines.media_ts.md) | 309 | 14 |  |  | ❌ | · | Mốc thời gian tiếng của file nguồn — sửa tiếng "dồn mẫu" ở file ghép nhiều tập. File ghép  |
| [workers.render_worker](../modules/workers.render_worker.md) | 300 | 8 |  | 1 | 5 | · | workers/render_worker.py — Render/mux chạy TIẾN TRÌNH CON (quyết định 11-11: render subpro |
| [engines.render_job](../modules/engines.render_job.md) | 186 | 3 |  | 1 | 1 | · | engines/render_job.py — Điều phối render 1 job (assemble nếu cần → run_render) — V6 F5. Tr |

## publish (8)

| Module | Dòng | Hàm | Routes | Fix | Test | Manual | Tóm tắt |
|---|---:|---:|---:|---:|---:|:-:|---|
| [agents.meta_studio](../modules/agents.meta_studio.md) | 1919 | 32 |  | 1 | 1 | · | meta_studio.py — Module sinh Metadata YouTube (Title / Description / Tags / AI Image Promp |
| [agents.auto_publisher](../modules/agents.auto_publisher.md) | 1568 | 36 |  |  | 4 | · | auto_publisher.py — Tự động hoá toàn trình: Sau khi render xong → Tự tạo Metadata SEO & Th |
| [agents.test_channel](../modules/agents.test_channel.md) | 1435 | 38 |  |  | ❌ | · | test_channel.py — Quản lý Kênh Phụ Test (Burner Channel) độc lập trong Pineline. Chức năng |
| [tools.test_channel](../modules/tools.test_channel.md) | 1435 | 38 |  |  | ❌ | · | test_channel.py — Quản lý Kênh Phụ Test (Burner Channel) độc lập trong Pineline. Chức năng |
| [agents.uploader_settings_api](../modules/agents.uploader_settings_api.md) | 670 | 31 |  |  | ❌ | · | Settings + YouTube Uploader API — dùng được từ sidecar :8802 (stdlib) và FastAPI. Trả về ( |
| [agents.canva_client](../modules/agents.canva_client.md) | 394 | 12 |  | 1 | ❌ | · | canva_client.py — Tích hợp Canva Connect API chính thức cho Pineline. Quản lý OAuth2 (PKCE |
| [agents.films_merge_api](../modules/agents.films_merge_api.md) | 174 | 10 |  |  |  | · | Gộp parts (split jobs) + scan job đã làm sẵn sàng merge — dùng từ sidecar :8802. |
| [services.film_db_sync](../modules/services.film_db_sync.md) | 35 | 2 |  | 1 |  | · | services/film_db_sync.py — đồng bộ DB phim định kỳ (sidecar cũ: filmdb_loop) — V6 F4. |

## core (10)

| Module | Dòng | Hàm | Routes | Fix | Test | Manual | Tóm tắt |
|---|---:|---:|---:|---:|---:|:-:|---|
| [core.film_db](../modules/core.film_db.md) | 1091 | 42 |  |  | 3 | · | Database phim đã làm (SQLite) — nguồn sự thật lâu dài, không mất khi xoá thư mục job. film |
| [core.job_queue](../modules/core.job_queue.md) | 669 | 32 |  |  | 6 | · |  |
| [core.manifest](../modules/core.manifest.md) | 294 | 7 |  | 1 | 4 | · | manifest.py — Trạng thái/checkpoint cho pipeline. Mỗi job (mỗi video) có một thư mục làm v |
| [core.job_opts](../modules/core.job_opts.md) | 281 | 17 |  | 1 | 2 | · | core/job_opts.py — MỘT nguồn quyết định tuỳ chọn job (V6 F2). Trước F2 tuỳ chọn job (giọng |
| [core.batch_config](../modules/core.batch_config.md) | 267 | 14 |  | 1 | 2 | · | Cấu hình batch dùng chung — 1 nguồn duy nhất cho Auto-pilot, Agent (queue_add), Telegram ( |
| [core.instance](../modules/core.instance.md) | 222 | 9 |  | 2 | 9 | · | core/instance.py — Cấu hình 1 instance Pineline (thay cho việc giữ 3 cây V2/V4/V5). Một co |
| [core.config](../modules/core.config.md) | 188 | 8 |  |  | 2 | · | config.py — Cấu hình/đường dẫn PORTABLE cho Pineline. Thứ tự ưu tiên khi lấy 1 giá trị: bi |
| [core.job_meta](../modules/core.job_meta.md) | 164 | 5 |  |  | 1 | · | Parse metadata hàng chờ (series/season UI) — dùng chung film_db sync + job_queue. |
| [core.fileio](../modules/core.fileio.md) | 124 | 4 |  | 1 | 1 | · | Ghi file an toàn giữa nhiều tiến trình (server :8801, sidecar :8802, script). atomic_write |
| [core.__init__](../modules/core.__init__.md) | 1 | 0 |  | 1 |  | · | PinelineV5 app core: config, queue, DB, manifest, fileio. |

## ops (23)

| Module | Dòng | Hàm | Routes | Fix | Test | Manual | Tóm tắt |
|---|---:|---:|---:|---:|---:|:-:|---|
| [scripts.brain_build](../modules/scripts.brain_build.md) | 857 | 23 |  |  | ❌ | · | brain_build.py — Sinh/làm mới phần TỰ ĐỘNG của Second Brain (docs/brain/). Đọc mã nguồn bằ |
| [scripts.check_batch4](../modules/scripts.check_batch4.md) | 458 | 6 |  |  | ❌ | · | Kiểm thử đợt 4 (tranh chấp ghi manifest, claim endpoint, single-instance, worker khởi động |
| [tools.lib_check](../modules/tools.lib_check.md) | 411 | 9 |  |  | ❌ | · | lib_check.py — Kiểm tra bản quyền / Block YouTube cho Phim trong Thư viện gốc (D:\Lib) trư |
| [tools.migrate_work](../modules/tools.migrate_work.md) | 396 | 20 |  | 1 | 2 | · | Chuyển Z:\PinelineV2\work sang ổ khác (mặc định D:\PinelineV2_work) giữa 2 job, không mất  |
| [scripts.verify_all](../modules/scripts.verify_all.md) | 371 | 12 |  |  | ❌ | · | scripts/verify_all.py — kiểm toàn bộ V6 MỘT LẦN trên máy Windows (gọi từ verify_all.bat ở  |
| [scripts.sync_from_v2](../modules/scripts.sync_from_v2.md) | 370 | 12 |  |  | ❌ | · | sync_from_v2.py — Đồng bộ code từ cây nguồn (V2, bố cục phẳng) sang cây đích. Thay cho việ |
| [engines.check_block](../modules/engines.check_block.md) | 350 | 4 |  |  | ❌ | · | check_block.py — Module kiểm tra trạng thái Bản quyền / Block YouTube (Cơ chế 1: Không cần |
| [scripts.check_batch5](../modules/scripts.check_batch5.md) | 347 | 6 |  |  | ❌ | · | Kiểm thử đợt 5 (agent an toàn): lệnh / bắt buộc, phân tầng tool + xác nhận ✅/❌, hg_auto_fl |
| [scripts.check_batch2](../modules/scripts.check_batch2.md) | 327 | 9 | 2 |  | ❌ | · | Kiểm thử đợt 2 (bảo mật + config 1 nguồn + LLM lỗi dừng stage). KHÔNG chạm server/sidecar  |
| [scripts.check_batch1](../modules/scripts.check_batch1.md) | 307 | 11 |  |  | ❌ | · | Test đợt 1 (chặn mất dữ liệu) — chỉ dùng thư mục tạm, không đụng job/queue/thư viện thật. |
| [scripts.tree_audit](../modules/scripts.tree_audit.md) | 226 | 9 |  | 1 |  | · | tree_audit.py — Kiểm tra sức khoẻ cây code Pineline (V2/V4/V5) sau mỗi lần sync/port. Chỉ  |
| [tools.preset_switcher](../modules/tools.preset_switcher.md) | 225 | 5 |  |  |  | · | preset_switcher.py — Tự động chuyển đổi Preset Model trên llama-server (:8083) Quản lý bộ  |
| [tools.pick_dirs_dialog](../modules/tools.pick_dirs_dialog.md) | 209 | 4 |  |  |  | · | Hộp thoại chọn NHIỀU folder cùng lúc (Listbox multi-select). Chạy: python pick_dirs_dialog |
| [scripts.check_batch3](../modules/scripts.check_batch3.md) | 202 | 1 |  |  |  | · | Kiểm thử đợt 3 (opts một nguồn X1, config file-first X2, ghi nguyên tử + khoá S13). Chạy t |
| [scripts.check_cookies](../modules/scripts.check_cookies.md) | 172 | 5 |  |  |  | · | check_cookies.py — Soi file cookie: còn hạn không, đã ĐĂNG NHẬP chưa, và có lấy được HD kh |
| [scripts.gen_sample_assets](../modules/scripts.gen_sample_assets.md) | 108 | 4 |  |  |  | · | Tạo sample assets cho Pineline (grain / LUT / BGM). Chạy:  python scripts/gen_sample_asset |
| [scripts.f1_convert_saves](../modules/scripts.f1_convert_saves.md) | 96 | 5 |  | 1 |  | · | F1 (2026-11-11): chuyển mọi Manifest.save() sang state.job_state. Chạy 1 lần; idempotent. |
| [engines.vram](../modules/engines.vram.md) | 89 | 4 |  |  |  | · | vram.py — Nhả VRAM trước MỖI bước dùng GPU. Ollama (Gemma ctx 128k), FunASR, Demucs, TTS,  |
| [scripts.brain_export](../modules/scripts.brain_export.md) | 67 | 2 |  |  |  | · | brain_export.py — Xuất dữ liệu Second Brain ra JSON gọn cho trang workflow (docs/brain/wor |
| [scripts.build_workflow_html](../modules/scripts.build_workflow_html.md) | 47 | 1 |  |  |  | · | build_workflow_html.py — Dựng docs/brain/workflow.html (trang workflow động) từ brain.json |
| [scripts.check_sidecar_lock](../modules/scripts.check_sidecar_lock.md) | 33 | 0 |  |  |  | · |  |
| [scripts.fix_runall_decorator_v2v4v5](../modules/scripts.fix_runall_decorator_v2v4v5.md) | 30 | 0 |  |  |  | · | Sửa decorator /run-all bị gắn nhầm vào _req_audio (lỗi do fix B6 ngày 11-11) ở V2/V4/V5. C |
| [scripts.download_firered_models](../modules/scripts.download_firered_models.md) | 29 | 1 |  |  |  | · | Download FireRedASR2S models into FIRERED_MODELS (default D:/AI_CACHE/firered). |

## misc (39)

| Module | Dòng | Hàm | Routes | Fix | Test | Manual | Tóm tắt |
|---|---:|---:|---:|---:|---:|:-:|---|
| [tools.context_studio.character_mapper](../modules/tools.context_studio.character_mapper.md) | 353 | 10 |  |  | ❌ | · | 02 — Character + relation map: ai tự xưng gì khi nói với ai. |
| [tools.context_studio.address_lexicon](../modules/tools.context_studio.address_lexicon.md) | 338 | 4 |  |  | ❌ | · | Kho xưng hô cổ trang / võ hiệp / tu tiên (VN dịch Trung). Nguồn tham chiếu (quy ước văn họ |
| [state.job_state](../modules/state.job_state.md) | 269 | 19 |  | 1 | 4 | · | state/job_state.py — NGUỒN DUY NHẤT ghi trạng thái job (B9). Mọi chỗ trước đây `m = Manife |
| [tools.context_studio.analyzer](../modules/tools.context_studio.analyzer.md) | 264 | 5 |  |  |  | · | 01 — Context analyzer: SRT Trung → glossary draft (+ style_lock). |
| [state.watcher](../modules/state.watcher.md) | 145 | 4 |  | 1 | 2 | · | state/watcher.py — Bắt các lần ghi manifest từ TIẾN TRÌNH CON (asr/tts/render worker). Hoo |
| [events.bus](../modules/events.bus.md) | 143 | 0 |  | 1 | 2 | · | events/bus.py — Bus sự kiện trong tiến trình + luồng SSE cho UI (thay polling 2 s). from e |
| [tools.context_studio.schema](../modules/tools.context_studio.schema.md) | 129 | 3 |  |  |  | · | Heuristic number / name flags — không sửa transcript. |
| [pipeline.base](../modules/pipeline.base.md) | 124 | 0 |  |  | 1 | · | pipeline/base.py — Stage + Gate tường minh (thay `server._auto_gpu` 300 dòng if/return). r |
| [services.feeder](../modules/services.feeder.md) | 120 | 8 |  | 1 | 1 | · | services/feeder.py — Feeder / Run-watch / thư viện (V6 F4): lớp mỏng trên agents.agent_fee |
| [state.index](../modules/state.index.md) | 106 | 1 |  | 1 | 5 | · | state/index.py — Danh sách job trong bộ nhớ (thay việc đọc lại mọi manifest mỗi 2 s). - `l |
| [tools.context_studio.srt_io](../modules/tools.context_studio.srt_io.md) | 99 | 4 |  |  |  | · | Parse SRT / asr.json → list[{i, start, end, text}]. |
| [pineline.server.routes.jobs](../modules/pineline.server.routes.jobs.md) | 79 | 1 |  |  | 1 | · | Jobs API: /api/jobs/* — LIVE registry trong khung pineline V5. Thân handler vẫn trong root |
| [runtime.context](../modules/runtime.context.md) | 79 | 0 |  |  | 1 | · | runtime/context.py — State toàn cục của 1 tiến trình server, gom về một object. Thay các b |
| [services.autopilot](../modules/services.autopilot.md) | 79 | 4 |  | 1 | 1 | · | services/autopilot.py — vòng Auto-pilot + config-live (sidecar cũ: autopilot_loop / config |
| [services.background](../modules/services.background.md) | 73 | 5 |  | 1 | 2 | · | services/background.py — khởi động việc nền TRONG server khi instance bật cờ `feeder` (V6  |
| [tools.context_studio.exporter](../modules/tools.context_studio.exporter.md) | 73 | 1 |  |  |  | · | 05 — Export context pack vào thư mục job Pineline (hoặc out/). |
| [tools.context_studio.__main__](../modules/tools.context_studio.__main__.md) | 67 | 1 |  | 1 |  | · | CLI: python -m tools.context_studio analyze <srt/asr.json> -o work/<job> Chỉ PRE-SCAN — kh |
| [pineline.server.routes.queue](../modules/pineline.server.routes.queue.md) | 38 | 1 |  |  | 1 | · | Queue API: /api/queue/* (+ alias /api/jobs/group) — LIVE registry. Thân handler giữ trong  |
| [pineline.server.routes.assets](../modules/pineline.server.routes.assets.md) | 32 | 1 |  |  |  | · | Assets + LLM models: cookies / voices / frames / brand / clone — LIVE registry. Thân handl |
| [services.publisher](../modules/services.publisher.md) | 26 | 2 |  | 1 | 1 | · | services/publisher.py — worker tự đăng YouTube (agents.auto_publisher) — V6 F4. Trước: wor |
| [pineline.server.routes.fs](../modules/pineline.server.routes.fs.md) | 23 | 1 |  |  |  | · | FS picker: /api/fs/* — LIVE registry (body trong server.py). |
| [services.__init__](../modules/services.__init__.md) | 8 | 0 |  | 1 |  | · | services/ — việc nền trước đây sidecar (agents/agent_tele_cmd.py) tự làm — V6 F4. Lớp MỎNG |
| [tools.context_studio.__init__](../modules/tools.context_studio.__init__.md) | 6 | 0 |  | 1 |  | · | Context Studio — PRE-SCAN SRT Trung → context pack cho Pineline dịch. Không dịch. Chỉ phân |
| [pineline.__init__](../modules/pineline.__init__.md) | 3 | 0 |  | 1 |  | · | PinelineV5 package khung — xem pineline/README.md. |
| [agents.__init__](../modules/agents.__init__.md) | 1 | 0 |  | 1 |  | · | Agent / sidecar modules (Phase B layout). |
| [engines.__init__](../modules/engines.__init__.md) | 1 | 0 |  | 1 |  | · | Pipeline engines (Phase C+D). |
| [engines.asr.__init__](../modules/engines.asr.__init__.md) | 1 | 0 |  | 1 |  | · | ASR engine variants. |
| [events.__init__](../modules/events.__init__.md) | 1 | 0 |  | 1 |  | · |  |
| [http_routes.__init__](../modules/http_routes.__init__.md) | 1 | 0 |  | 1 |  | · | HTTP route modules (Phase B layout). |
| [pineline.server.__init__](../modules/pineline.server.__init__.md) | 1 | 0 |  | 1 |  | · | HTTP server layer (FastAPI). Entry runtime vẫn là root `server.py` trong giai đoạn chuyển. |
| [pineline.server.routes.__init__](../modules/pineline.server.routes.__init__.md) | 1 | 0 |  | 1 |  | · | APIRouter modules. Nạp từ cuối `server.py` qua `app.include_router`. |
| [pipeline.__init__](../modules/pipeline.__init__.md) | 1 | 0 |  | 1 |  | · |  |
| [pipeline.gates.__init__](../modules/pipeline.gates.__init__.md) | 1 | 0 |  | 1 |  | · |  |
| [pipeline.stages.__init__](../modules/pipeline.stages.__init__.md) | 1 | 0 |  | 1 |  | · |  |
| [runtime.__init__](../modules/runtime.__init__.md) | 1 | 0 |  | 1 |  | · |  |
| [sidecar.__init__](../modules/sidecar.__init__.md) | 1 | 0 |  | 1 |  | · |  |
| [state.__init__](../modules/state.__init__.md) | 1 | 0 |  | 1 |  | · |  |
| [tools.__init__](../modules/tools.__init__.md) | 1 | 0 |  | 1 |  | · | Ops / helper tools (lib_check, test_channel, migrate_work, context_studio, …). |
| [workers.__init__](../modules/workers.__init__.md) | 1 | 0 |  | 1 |  | · |  |

## test

| Test | Kiểm module |
|---|---|
| `tests/__init__.py` | — |
| `tests/conftest.py` | [state.index](../modules/state.index.md) |
| `tests/fake_backend.py` | — |
| `tests/fake_render.py` | [state.job_state](../modules/state.job_state.md), [workers.render_worker](../modules/workers.render_worker.md) |
| `tests/test_app_integration.py` | [app.main](../modules/app.main.md), [core.instance](../modules/core.instance.md), [server](../modules/server.md) |
| `tests/test_b11_risky_modules.py` | [agents.agent_feeder](../modules/agents.agent_feeder.md), [agents.agent_tele_cmd](../modules/agents.agent_tele_cmd.md), [engines.clone_tts](../modules/engines.clone_tts.md) |
| `tests/test_events_bus.py` | [events.bus](../modules/events.bus.md) |
| `tests/test_f1_ratchet.py` | — |
| `tests/test_f1_write_path.py` | [core.manifest](../modules/core.manifest.md), [state.index](../modules/state.index.md), [state.job_state](../modules/state.job_state.md), [state.watcher](../modules/state.watcher.md) |
| `tests/test_f3_static.py` | [core.instance](../modules/core.instance.md), [events.bus](../modules/events.bus.md), [state.index](../modules/state.index.md) |
| `tests/test_f4_services.py` | [agents.agent_tele_cmd](../modules/agents.agent_tele_cmd.md), [core](../modules/core.md), [core.instance](../modules/core.instance.md), [services.background](../modules/services.background.md), [services.feeder](../modules/services.feeder.md), [services.publisher](../modules/services.publisher.md) |
| `tests/test_f5_render_job.py` | [core](../modules/core.md), [engines.render_job](../modules/engines.render_job.md), [state.job_state](../modules/state.job_state.md) |
| `tests/test_f5_render_subprocess.py` | [core.instance](../modules/core.instance.md), [workers.render_worker](../modules/workers.render_worker.md) |
| `tests/test_f6_final_audit.py` | [core.instance](../modules/core.instance.md), [state.index](../modules/state.index.md), [state.watcher](../modules/state.watcher.md), [workers.render_worker](../modules/workers.render_worker.md) |
| `tests/test_file_lock.py` | [core](../modules/core.md), [core.fileio](../modules/core.fileio.md) |
| `tests/test_instance.py` | [core.instance](../modules/core.instance.md) |
| `tests/test_job_opts.py` | [core](../modules/core.md), [core.batch_config](../modules/core.batch_config.md), [core.job_opts](../modules/core.job_opts.md), [core.job_queue](../modules/core.job_queue.md), [engines.render_fx](../modules/engines.render_fx.md) |
| `tests/test_live_sync_js.py` | — |
| `tests/test_pipeline_runner.py` | [pipeline.base](../modules/pipeline.base.md) |
| `tests/test_render_worker.py` | [workers.render_worker](../modules/workers.render_worker.md) |
| `tests/test_review1_f2f3.py` | [app.__main__](../modules/app.__main__.md), [core.instance](../modules/core.instance.md), [core.manifest](../modules/core.manifest.md) |
| `tests/test_review2_f4f5.py` | [agents.agent_tele_cmd](../modules/agents.agent_tele_cmd.md), [core.instance](../modules/core.instance.md), [http_routes.routes_ui](../modules/http_routes.routes_ui.md), [services.autopilot](../modules/services.autopilot.md), [services.background](../modules/services.background.md), [tools.migrate_work](../modules/tools.migrate_work.md), [workers.render_worker](../modules/workers.render_worker.md) |
| `tests/test_runtime.py` | [core.instance](../modules/core.instance.md), [runtime.context](../modules/runtime.context.md) |
| `tests/test_state_job_state.py` | [state.index](../modules/state.index.md), [state.job_state](../modules/state.job_state.md) |
| `tests/test_verify_all.py` | — |
| `tests/v5/__init__.py` | — |
| `tests/v5/conftest.py` | [agents.auto_publisher](../modules/agents.auto_publisher.md) |
| `tests/v5/test_agent_core_smoke.py` | [agents.agent_core](../modules/agents.agent_core.md), [core.manifest](../modules/core.manifest.md) |
| `tests/v5/test_agent_parser.py` | [agents.agent_core](../modules/agents.agent_core.md) |
| `tests/v5/test_agent_quality.py` | [agents.agent_quality](../modules/agents.agent_quality.md) |
| `tests/v5/test_agent_tool_loop.py` | [agents.agent_tool_loop](../modules/agents.agent_tool_loop.md) |
| `tests/v5/test_asr_demucs.py` | [core.manifest](../modules/core.manifest.md), [engines.asr.asr](../modules/engines.asr.asr.md) |
| `tests/v5/test_auto_publisher_dedup.py` | [agents.auto_publisher](../modules/agents.auto_publisher.md), [core.film_db](../modules/core.film_db.md) |
| `tests/v5/test_chatgpt_route.py` | [core.config](../modules/core.config.md), [engines.llm](../modules/engines.llm.md) |
| `tests/v5/test_film_db_queue_meta.py` | [core.film_db](../modules/core.film_db.md), [core.job_meta](../modules/core.job_meta.md), [core.job_queue](../modules/core.job_queue.md) |
| `tests/v5/test_hardsub_blur_toggle.py` | [core](../modules/core.md), [core.batch_config](../modules/core.batch_config.md), [core.job_opts](../modules/core.job_opts.md), [server](../modules/server.md) |
| `tests/v5/test_hg_merge_queue.py` | [agents.hongguo_bridge](../modules/agents.hongguo_bridge.md) |
| `tests/v5/test_job_queue.py` | [core.job_queue](../modules/core.job_queue.md) |
| `tests/v5/test_job_queue_worker.py` | [core.job_queue](../modules/core.job_queue.md) |
| `tests/v5/test_layout_paths.py` | [agents.agent_core](../modules/agents.agent_core.md), [core.config](../modules/core.config.md), [core.job_queue](../modules/core.job_queue.md), [engines.assemble](../modules/engines.assemble.md), [engines.clone_tts](../modules/engines.clone_tts.md), [engines.download](../modules/engines.download.md), [engines.llm](../modules/engines.llm.md), [engines.ocr](../modules/engines.ocr.md), [engines.render](../modules/engines.render.md), [engines.render_fx](../modules/engines.render_fx.md), [engines.separate](../modules/engines.separate.md), [engines.sfx_lib](../modules/engines.sfx_lib.md), [engines.translate](../modules/engines.translate.md), [http_routes.routes_films](../modules/http_routes.routes_films.md), [http_routes.routes_ui](../modules/http_routes.routes_ui.md), [server](../modules/server.md) |
| `tests/v5/test_meta_and_thumb.py` | [agents.auto_publisher](../modules/agents.auto_publisher.md), [agents.meta_studio](../modules/agents.meta_studio.md) |
| `tests/v5/test_optimize_b5_b6.py` | [engines.translate](../modules/engines.translate.md), [server](../modules/server.md) |
| `tests/v5/test_park_review_retry.py` | [server](../modules/server.md) |
| `tests/v5/test_pronoun_consistency.py` | [engines.pronoun_consistency](../modules/engines.pronoun_consistency.md) |
| `tests/v5/test_pronoun_postpass.py` | [engines.translate](../modules/engines.translate.md) |
| `tests/v5/test_pronoun_qa_slim_plan.py` | [engines.translate](../modules/engines.translate.md) |
| `tests/v5/test_queue_http_state.py` | [core.film_db](../modules/core.film_db.md), [core.job_queue](../modules/core.job_queue.md), [pineline.server.routes.jobs](../modules/pineline.server.routes.jobs.md), [pineline.server.routes.queue](../modules/pineline.server.routes.queue.md) |
| `tests/v5/test_queue_jobs_api.py` | [server](../modules/server.md) |
| `tests/v5/test_queue_run_one.py` | [server](../modules/server.md) |
| `tests/v5/test_render_expr.py` | [engines.render](../modules/engines.render.md) |
| `tests/v5/test_restart_guard.py` | [tools.migrate_work](../modules/tools.migrate_work.md) |
| `tests/v5/test_review_batches.py` | — |
| `tests/v5/test_review_fail_not_rv.py` | [engines.translate](../modules/engines.translate.md) |
| `tests/v5/test_review_parallel_workers.py` | [engines.translate](../modules/engines.translate.md) |
| `tests/v5/test_review_pass1_upload.py` | [engines.translate](../modules/engines.translate.md) |
| `tests/v5/test_server_split.py` | [http_routes.routes_agent](../modules/http_routes.routes_agent.md), [http_routes.routes_hg](../modules/http_routes.routes_hg.md), [server](../modules/server.md) |
| `tests/v5/test_sfx_lib.py` | [engines.render](../modules/engines.render.md), [engines.sfx_lib](../modules/engines.sfx_lib.md) |
| `tests/v5/test_source_link.py` | [server](../modules/server.md) |
| `tests/v5/test_split_enospc_guard.py` | [engines.split_parts](../modules/engines.split_parts.md) |
| `tests/v5/test_translate_force_default.py` | [server](../modules/server.md) |
| `tests/v5/test_upload_limit_stop.py` | [agents.auto_publisher](../modules/agents.auto_publisher.md) |
