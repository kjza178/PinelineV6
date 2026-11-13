"""
agent_core.py — AI Agent nhúng trong Pineline V2.

Không gọi HTTP self. Tools gọi runtime đã bind từ server.py
(list jobs, queue, manifest, draft YT).

LLM Agent: AGENT_BASE_URL / AGENT_API_KEY / AGENT_MODEL (fallback LLM_*).
Job translate vẫn dùng LLM_* local riêng.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import requests

import core.batch_config as batch_config
from agents.agent_stage_io import json_dump as _stage_json, peek_all, peek_stage, peek_style_pack

WIRED = ("download", "asr", "translate", "tts", "mux")

SYSTEM = """Bạn là Pineline Agent (V2) — can thiệp sâu vào pipeline lồng tiếng.

Stages UI: download → asr → translate → [QA gate / HOLD?] → tts → ocr? → mux → output.mp4

ĐỌC (luôn): status, job, queue, voices, yt_draft, script_*, stage_peek, stage_peek_all, style_pack_peek,
review_diff, diagnose_job, scan_failed, av_sync_check, av_sync_scan.
GHI (cần allow_mutate=true):
- script_edit_*, script_review (rà văn phong — bám glossary+style_lock)
- qa_gate_set (Gate QA trước TTS — luồng chính V2; override_pass=true = cho qua gate)
- hold_after_translate (tuỳ chọn phụ), stop_job
- tts_set_voice, tts_run, mux_run, asr_*, ocr_run, run_stage, queue_*
- heal_job / heal_failed_batch / watch_once
- av_sync_heal / av_sync_heal_batch

Quy tắc:
1. Fail/kẹt → diagnose_job (xem classifications: cuda_oom, missing_voice, …).
2. heal safe khi class cho phép; notify_only (giọng/ổ đĩa) → báo user, không spam.
3. QA gate fail → review_diff + sửa script / Context Studio; qa_gate_set override_pass=true chỉ khi user
   nói rõ «cho qua / bỏ qua QA».
4. Heal kỹ thuật ≠ QA nội dung.
5. Lệch sub/audio / mất tiếng giữa phim / «fix hết lỗi AV»:
   → av_sync_scan → av_sync_heal_batch apply=true (tuần tự). Không hỏi menu remux/merge.
   Remux two-pass (không TTS lại) trừ khi elastic_drift cần assemble_fit.
6. Xếp hàng (queue_add): dùng group_id khi user nói cả series/group. Truyền đủ field user yêu cầu
   (voice, speed, tts_engine, asr_engine, audio_mode, orig_vol, translate_mode…); sau khi xếp,
   báo lại applied_opts cho user.
7. CONFIG BATCH = cấu hình chung lưu bền cho Auto-pilot + queue_add.
   User hỏi «config đang là gì» → config_get. User nói «từ giờ / mặc định / cho auto-pilot / cả batch
   dùng X» → config_set (chỉ field được nhắc) rồi báo changed. Chỉ «xếp group này với X» → queue_add
   truyền X (không đổi config batch). Giá trị lạ/không chắc → hỏi lại, không đoán.
   MỖI lần user hỏi/đòi đọc config → GỌI LẠI config_get (UI đổi config liên tục), không chép từ tin cũ.
   Báo config theo field "text" của config_get (đủ mọi dòng, đúng nhãn). orig_vol = âm lượng TIẾNG GỐC
   nền khi ducking (không phải nhạc nền); voice_vol = âm lượng giọng Việt; brand_* = watermark tên kênh.
8. «job đang chạy tới đâu / có treo không» → running_check (trả nguyên văn text).
9. TỰ THÊM JOB khi hết hàng chờ (feeder, chạy trong sidecar Auto-pilot):
   user mô tả muốn thêm gì/bao nhiêu/từ đâu («hết job thì làm 2 bộ hot điểm >8.5», «làm hết phim đã ghép»,
   «tải thêm phim người đóng từ Hongguo») → feed action=set, request=NGUYÊN VĂN câu user (sidecar tự hiểu).
   «đang tự thêm theo yêu cầu gì / lần tới thêm phim nào» → feed action=show. Bật/tắt → on/off.
   «thêm ngay» → now. Báo lại đúng text tool trả về.
10. «đã làm những phim gì / phim X làm chưa / bao nhiêu phim chưa upload» → films (database lâu dài,
   gồm cả job đã xoá thư mục). Có job_id → films job_id=… để xem chi tiết + lịch sử.
11. THƯ VIỆN CHƯA LÀM (phim đã tải/ghép nhưng chưa thành job) → library:
   «còn phim gì chưa làm / nên làm bộ nào tiếp» → action=list (top gợi ý có số thứ tự + điểm + lý do).
   «xem bộ X» → show target=X. «làm bộ số 2 / thêm bộ X» → add. «ưu tiên bộ X» → star, «bỏ ưu tiên» → unstar,
   «đừng làm bộ X» → skip, «lấy lại bộ X» → unskip, «ghép bộ X» → merge.
   target = số thứ tự theo danh sách vừa list, hoặc tên (gốc Trung hoặc tên Việt), nhiều số cách nhau dấu cách.
   Báo lại đúng text tool trả về.
12. «rà soát văn phong / tên nhân vật / xưng hô trước khi lồng tiếng» → script_review NGAY (kể cả khi đang
   dịch — tool tự hẹn chạy lúc dịch xong 100%, rà xong mới lồng tiếng). KHÔNG bật hold, KHÔNG chờ.
   Không nói job_id → job đang chạy (running_check / queue_status). Báo lại đúng "message".
   «duyệt xong / chạy tiếp / cho lồng tiếng đi» với job đang HOLD / QA block → resume_job.
   Mỗi bộ có bảng nhân vật chung: mùa sau tự kế thừa tên/xưng hô đã duyệt của mùa trước.
13. HONGGUO DOWNLOADER & WORKFLOW TỰ ĐỘNG TỪ A-Z:
   - Tìm phim trên Hongguo: hg_search (query=...).
   - Tra cứu full các mùa / phần của 1 bộ phim: hg_series (query_or_id=...).
   - Bảng xếp hạng phim hot thịnh hành: hg_rank.
   - Xem thử không cần tải: hg_preview (series_id=ID | số thứ tự BXH | tên, ep=...). Chỉ các tập miễn phí
     (thường 1–3); gửi user watch_url. Phim đã tải trong D:\\Lib: hg_preview (folder_name=...).
   - Tải phim: hg_download (series_ids=[...]). Tự động kiểm tra/bật Hongguo Downloader nếu chưa chạy.
   - Tiến độ tải: hg_status.
   - Xem thư viện tải D:\\Lib: hg_library.
   - Gộp tập video: hg_merge (folder_name=...).
   - Dọn dẹp tập mảnh: hg_clean (folder_name=...).
   - Tự động toàn bộ từ A-Z: hg_auto_flow (query_or_id=..., mode=name|lib|rank, voice=..., tts_engine=...).
     mode=lib khi user nói «bộ chưa làm trong thư viện», mode=rank khi «top/BXH», còn lại mode=name.
     Chạy nền: trả "started" là xong lượt này — tiến độ tự báo Telegram, không gọi lại để hỏi.
   - Folder trong D:\\Lib chưa có job mà user bảo «add/xếp hàng/làm bộ X» → library action=add target=X
     (đã có merged) hoặc hg_auto_flow (chưa gộp). queue_add chỉ nhận job_id đã có.
14. HÀNH ĐỘNG = GỌI TOOL. Biết phải làm gì và user đã yêu cầu → gọi tool ngay trong lượt này.
   KHÔNG viết «Action: tool(...)» / bảo user tự chạy lệnh. Chỉ hỏi lại khi thiếu thông tin thật sự.
15. Tool trả status=awaiting_user_confirm → thao tác CHƯA chạy, user sẽ thấy nút ✅/❌. Báo ngắn
   «đã gửi nút xác nhận: <việc gì>», KHÔNG gọi lại tool đó, KHÔNG nói là đã làm xong.
"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "pineline_status",
            "description": "Tổng hợp jobs: running/pending/failed/done + need_queue.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pineline_job",
            "description": "Chi tiết 1 job (stages + progress + hold flag).",
            "parameters": {
                "type": "object",
                "properties": {"job_id": {"type": "string"}},
                "required": ["job_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pineline_queue_status",
            "description": "Hàng chờ run-all hiện tại.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pineline_queue_add",
            "description": (
                "Xếp job vào hàng chờ tuần tự — MỌI job dùng chung 1 bộ cấu hình. "
                "Truyền job_ids HOẶC group_id (xếp cả series theo P1→Pn). "
                "Field bỏ trống = lấy từ CONFIG BATCH (pineline_config_get). "
                "Field truyền vào chỉ áp cho lần xếp này (job bị ghim, không theo config batch khi batch đổi), "
                "không sửa config batch. Không truyền field nào → job theo config batch mới nhất tới lượt chạy. "
                "Cần allow_mutate."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "job_ids": {"type": "array", "items": {"type": "string"}},
                    "group_id": {"type": "string", "description": "Xếp cả group/series"},
                    "voice": {"type": "string"},
                    "speed": {"type": "number"},
                    "tts_engine": {"type": "string", "enum": ["vieneu", "omnivoice"]},
                    "asr_engine": {"type": "string", "enum": [
                        "nano_fa", "hybrid_fa", "fun_asr_nano", "paraformer_zh", "sensevoice", "firered_asr"]},
                    "audio_mode": {"type": "string", "enum": [
                        "duck_bgm", "sfx_lib", "voice_bgm", "voice_only", "duck", "demucs_keep", "demucs_clean"]},
                    "orig_vol": {"type": "number"},
                    "translate_mode": {"type": "string", "enum": ["batch", "auto", "oneshot"]},
                    "ocr": {"type": "boolean"},
                    "fit": {"type": "boolean"},
                    "zoom": {"type": "number"},
                    "lut": {"type": "boolean"},
                    "motion": {"type": "boolean"},
                    "grain": {"type": "string"},
                    "flip": {"type": "string"},
                    "vbitrate": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pineline_queue_stop",
            "description": "Dừng hàng chờ + job đang chạy trong queue. Cần allow_mutate.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pineline_yt_draft",
            "description": "Draft title + mô tả YouTube từ script/manifest job.",
            "parameters": {
                "type": "object",
                "properties": {"job_id": {"type": "string"}},
                "required": ["job_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pineline_voices",
            "description": "List giọng TTS.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pineline_script_peek",
            "description": "Tóm tắt script.json: số câu, thiếu vi, mẫu đầu/giữa/cuối, cờ hold.",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string"},
                    "sample": {"type": "integer", "description": "số câu mẫu mỗi vùng (mặc định 5)"},
                },
                "required": ["job_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pineline_script_search",
            "description": "Tìm câu trong script theo từ khóa (zh hoặc vi) hoặc chỉ câu thiếu vi.",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string"},
                    "query": {"type": "string", "description": "từ khóa; để trống nếu chỉ lấy missing"},
                    "missing_only": {"type": "boolean"},
                    "limit": {"type": "integer"},
                },
                "required": ["job_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pineline_script_get_lines",
            "description": "Lấy đoạn câu theo chỉ số i (inclusive).",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string"},
                    "i_from": {"type": "integer"},
                    "i_to": {"type": "integer"},
                },
                "required": ["job_id", "i_from", "i_to"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pineline_script_edit_line",
            "description": "Sửa 1 câu script (vi và/hoặc zh). Cần allow_mutate. Gọi API script-line nội bộ.",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string"},
                    "i": {"type": "integer"},
                    "vi": {"type": "string"},
                    "zh": {"type": "string"},
                },
                "required": ["job_id", "i"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pineline_script_edit_many",
            "description": "Sửa nhiều câu: edits=[{i, vi?, zh?}]. Cần allow_mutate. Tối đa 40 câu/lần.",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string"},
                    "edits": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "i": {"type": "integer"},
                                "vi": {"type": "string"},
                                "zh": {"type": "string"},
                            },
                            "required": ["i"],
                        },
                    },
                },
                "required": ["job_id", "edits"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pineline_hold_after_translate",
            "description": "Bật/tắt HOLD: sau translate dừng, không chạy TTS đến khi clear. Cần allow_mutate.",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string"},
                    "enabled": {"type": "boolean"},
                },
                "required": ["job_id", "enabled"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pineline_run_stage",
            "description": "Chạy 1 stage (asr|translate|tts|mux|ocr|…) — fire-and-forget. Cần allow_mutate.",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string"},
                    "stage": {"type": "string"},
                    "voice": {"type": "string"},
                    "force": {"type": "boolean"},
                    "engine": {"type": "string"},
                },
                "required": ["job_id", "stage"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pineline_run_stage_wait",
            "description": (
                "TOOL LOOP: chạy 1 stage rồi CHỜ tới done|failed|stopped (poll manifest trong tool). "
                "force=false + đã done → skip, không wipe. Cần allow_mutate."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string"},
                    "stage": {"type": "string"},
                    "voice": {"type": "string"},
                    "force": {"type": "boolean"},
                    "engine": {"type": "string"},
                    "timeout_sec": {"type": "number"},
                    "poll_sec": {"type": "number"},
                },
                "required": ["job_id", "stage"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pineline_pipeline_loop",
            "description": (
                "TOOL LOOP: chạy tuần tự nhiều stage (vd translate,tts,mux); mỗi bước chờ xong mới bước sau. "
                "force=false → skip stage đã done. Cần allow_mutate."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string"},
                    "stages": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "vd [\"translate\",\"tts\",\"mux\"]",
                    },
                    "voice": {"type": "string"},
                    "force": {"type": "boolean"},
                    "engine": {"type": "string"},
                    "timeout_sec": {"type": "number"},
                    "poll_sec": {"type": "number"},
                },
                "required": ["job_id", "stages"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pineline_stage_peek",
            "description": "Đọc an toàn 1 bước: download|asr|translate|tts|ocr|mux.",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string"},
                    "stage": {"type": "string"},
                },
                "required": ["job_id", "stage"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pineline_stage_peek_all",
            "description": "Đọc tóm tắt cả 6 bước của 1 job.",
            "parameters": {
                "type": "object",
                "properties": {"job_id": {"type": "string"}},
                "required": ["job_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pineline_stop_job",
            "description": "Dừng mọi việc đang chạy của 1 job. Cần allow_mutate.",
            "parameters": {
                "type": "object",
                "properties": {"job_id": {"type": "string"}},
                "required": ["job_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pineline_tts_set_voice",
            "description": "Lưu giọng/speed/engine TTS. Cần allow_mutate.",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string"},
                    "voice": {"type": "string"},
                    "speed": {"type": "number"},
                    "engine": {"type": "string"},
                },
                "required": ["job_id", "voice"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pineline_tts_run",
            "description": "Chạy TTS; force=true đọc lại. Cần allow_mutate.",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string"},
                    "voice": {"type": "string"},
                    "force": {"type": "boolean"},
                    "engine": {"type": "string"},
                },
                "required": ["job_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pineline_mux_run",
            "description": "Ghép+xuất mux/render với opts. Cần allow_mutate.",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string"},
                    "audio_mode": {"type": "string"},
                    "orig_vol": {"type": "number"},
                    "voice_vol": {"type": "number"},
                    "zoom": {"type": "number"},
                    "flip": {"type": "string"},
                    "grain": {"type": "string"},
                    "brand_text": {"type": "string"},
                    "brand": {"type": "boolean"},
                    "vbitrate": {"type": "string"},
                    "add_sub": {"type": "boolean"},
                    "resolution": {"type": "string"},
                },
                "required": ["job_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pineline_asr_set_engine",
            "description": "Chọn engine ASR. Cần allow_mutate.",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string"},
                    "engine": {"type": "string"},
                },
                "required": ["job_id", "engine"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pineline_asr_run",
            "description": "Chạy ASR (nặng GPU); force=true nhận diện lại. Cần allow_mutate.",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string"},
                    "engine": {"type": "string"},
                    "force": {"type": "boolean"},
                },
                "required": ["job_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pineline_ocr_run",
            "description": "Chạy OCR chữ trên hình. Cần allow_mutate.",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string"},
                    "force": {"type": "boolean"},
                },
                "required": ["job_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pineline_style_pack_peek",
            "description": "Đọc pack kịch bản/văn phong: glossary, style_lock, qa_flags, relation_matrix, context_pack (nếu có).",
            "parameters": {
                "type": "object",
                "properties": {"job_id": {"type": "string"}},
                "required": ["job_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pineline_script_review",
            "description": (
                "Rà soát bản dịch TRƯỚC LỒNG TIẾNG: rà tên nhân vật theo bảng nhân vật rồi rà văn phong "
                "toàn bộ (bám glossary/style_lock). Job đang dịch/đang chạy → tự hẹn: dịch xong 100% "
                "mới rà, rà xong mới lồng tiếng (deferred=true). Job đang đứng → rà ngay. "
                "Không dịch lại từ đầu. Cần allow_mutate. Nặng LLM."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string"},
                    "review_model": {
                        "type": "string",
                        "description": "vd local/gemma3:12b hoặc model local :8083; để trống = mặc định REVIEW_MODEL",
                    },
                },
                "required": ["job_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pineline_resume_job",
            "description": (
                "Duyệt xong bản dịch (job đang HOLD / QA block / dừng giữa chừng) → bỏ chặn và xếp lên đầu "
                "hàng chờ: bỏ qua bước đã xong, tự chạy tiếp lồng tiếng + render. Cần allow_mutate."
            ),
            "parameters": {
                "type": "object",
                "properties": {"job_id": {"type": "string"}},
                "required": ["job_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pineline_diagnose_job",
            "description": (
                "Chẩn đoán job lỗi/kẹt: failed stages, thiếu script/TTS/output, đề xuất heal safe vs aggressive. "
                "Chỉ đọc — không sửa."
            ),
            "parameters": {
                "type": "object",
                "properties": {"job_id": {"type": "string"}},
                "required": ["job_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pineline_heal_job",
            "description": (
                "Tự review lỗi + (tuỳ chọn) fix 1 job. apply=false chỉ plan; apply=true chạy heal. "
                "level=safe|aggressive. Cần allow_mutate khi apply=true."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string"},
                    "apply": {"type": "boolean"},
                    "level": {"type": "string"},
                },
                "required": ["job_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pineline_scan_failed",
            "description": (
                "Quét TẤT CẢ job — bắt fail/kẹt/thiếu TTS-mux-dịch. Chỉ đọc, không sửa."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pineline_heal_failed_batch",
            "description": (
                "Heal HÀNG LOẠT mọi job lỗi đã scan. apply=false = plan cả đống; "
                "apply=true = heal safe/aggressive tuần tự (max_jobs). "
                "Cần allow_mutate khi apply=true. Không chồng 2 batch."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "apply": {"type": "boolean"},
                    "level": {"type": "string"},
                    "max_jobs": {"type": "integer"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pineline_reset_job_state",
            "description": (
                "Reset sạch trạng thái các stage bị kẹt/lỗi (stuck/failed) của 1 job. "
                "Xóa artifact dở, gỡ cờ .stop, đưa manifest về pending để chạy lại mượt mà. "
                "stages=['translate', 'tts', 'mux'] hoặc ['all']. "
                "restart=true để tự động kích hoạt chạy lại stage ngay sau khi reset. "
                "Cần allow_mutate=true."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string"},
                    "stages": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Danh sách stage cần reset (vd: ['translate', 'tts', 'mux'] hoặc ['all'])",
                    },
                    "keep_asr": {
                        "type": "boolean",
                        "description": "Giữ kết quả download và ASR (mặc định true)",
                    },
                    "restart": {
                        "type": "boolean",
                        "description": "Tự động kích hoạt chạy lại stage sau khi reset (mặc định false)",
                    },
                },
                "required": ["job_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pineline_review_diff",
            "description": (
                "So script.json vs script.pre_review.json + thống kê rv/missing/QA. "
                "Dùng sau review / khi QA gate block."
            ),
            "parameters": {
                "type": "object",
                "properties": {"job_id": {"type": "string"}},
                "required": ["job_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pineline_qa_gate_set",
            "description": (
                "Bật/tắt QA gate (review trước TTS) cho job. "
                "override_pass=true = bỏ block, cho phép TTS. Cần allow_mutate."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string"},
                    "enabled": {"type": "boolean"},
                    "override_pass": {"type": "boolean"},
                },
                "required": ["job_id", "enabled"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pineline_watch_once",
            "description": (
                "Chạy 1 vòng watcher: scan → heal safe (nếu GPU rảnh) + log/Telegram. "
                "Cần allow_mutate."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pineline_av_sync_check",
            "description": (
                "Chẩn đoán lệch sub/audio / mất tiếng giữa phim (RMS + A/V duration + tts_fit drift)."
            ),
            "parameters": {
                "type": "object",
                "properties": {"job_id": {"type": "string"}},
                "required": ["job_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pineline_av_sync_heal",
            "description": (
                "Plan/apply fix AV 1 job: remux two-pass (audio_truncated) hoặc assemble_fit+mux. "
                "apply=true cần allow_mutate."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string"},
                    "apply": {"type": "boolean"},
                },
                "required": ["job_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pineline_av_sync_scan",
            "description": (
                "Quét AV nhiều job (nhanh |v-a|). job_ids / group_id / trống=mọi output. "
                "deep=true thêm RMS."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "job_ids": {"type": "array", "items": {"type": "string"}},
                    "group_id": {"type": "string"},
                    "deep": {"type": "boolean"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pineline_av_sync_heal_batch",
            "description": (
                "Scan rồi heal job broken đầu tiên (GPU tuần tự). "
                "apply=true cần allow_mutate. Gọi lại sau mỗi mux xong."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "job_ids": {"type": "array", "items": {"type": "string"}},
                    "group_id": {"type": "string"},
                    "apply": {"type": "boolean"},
                    "deep": {"type": "boolean"},
                },
            },
        },
    },
]

_CFG_PROPS = {
    k: v for k, v in next(
        t for t in TOOLS if t["function"]["name"] == "pineline_queue_add"
    )["function"]["parameters"]["properties"].items()
    if k not in ("job_ids", "group_id")
}
_CFG_PROPS.update({
    "voice_vol": {"type": "number"},
    "resolution": {"type": "string", "enum": ["source", "1080"]},
    "brand_mode": {"type": "string", "enum": ["off", "drift", "corners", "drift_seed"]},
    "brand_text": {"type": "string"},
    "brand_logo": {"type": "string"},
    "frame": {"type": "string"},
})
TOOLS += [
    {
        "type": "function",
        "function": {
            "name": "pineline_config_get",
            "description": (
                "Đọc CONFIG BATCH hiện tại — bộ cấu hình chung mà Auto-pilot và queue_add dùng "
                "khi xếp hàng (âm nền, giọng, engine, render…). Gọi trước khi báo user config đang là gì."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pineline_config_set",
            "description": (
                "Sửa CONFIG BATCH (lưu bền, áp cho mọi lần xếp hàng sau — Auto-pilot, queue_add, Telegram). "
                "Chỉ truyền field user muốn đổi; reset=true để về mặc định. "
                "Không ảnh hưởng job đang chạy. Cần allow_mutate."
            ),
            "parameters": {
                "type": "object",
                "properties": {**_CFG_PROPS, "reset": {"type": "boolean"}},
            },
        },
    },
]

TOOLS += [
    {
        "type": "function",
        "function": {
            "name": "pineline_running_check",
            "description": "Job đang chạy: stage, %, ETA, thời gian không hoạt động (phát hiện treo) + hàng chờ.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pineline_feed",
            "description": (
                "Feeder tự thêm job mới khi mọi job xong (nguồn: phim đã ghép trong thư viện Hongguo, "
                "tự ghép tập lẻ, tải bộ mới từ Hongguo). action=show: xem yêu cầu + phim sẽ thêm lần tới; "
                "set: lưu yêu cầu mới (request = câu user nguyên văn); on/off; now: thêm ngay; preview. "
                "set/on/off/now cần allow_mutate."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["show", "set", "on", "off", "now", "preview"]},
                    "request": {"type": "string"},
                },
                "required": ["action"],
            },
        },
    },
]


TOOLS += [
    {
        "type": "function",
        "function": {
            "name": "pineline_films",
            "description": (
                "Database phim đã làm (lâu dài, kể cả job đã xoá thư mục). Không tham số → thống kê + 10 phim mới nhất. "
                "q: tìm theo tên gốc/tên Việt/bộ; status: done|running|pending|failed|uploaded|not_uploaded; "
                "job_id: chi tiết + lịch sử 1 phim."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "q": {"type": "string"},
                    "status": {"type": "string"},
                    "job_id": {"type": "string"},
                    "limit": {"type": "integer"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pineline_library",
            "description": (
                "Thư viện Hongguo: các bộ đã tải/ghép nhưng CHƯA thành job, xếp theo điểm gợi ý "
                "(nối bộ đang làm, mùa kế tiếp, đã ghép đủ). action=list: top gợi ý đánh số; all: cả danh sách; "
                "show: chi tiết 1 bộ; add: thêm các mùa ghép sẵn của bộ vào hàng chờ; star/unstar: ưu tiên cho "
                "feeder; skip/unskip: feeder không tự lấy / lấy lại; merge: ghép các mùa còn tập lẻ. "
                "target = số thứ tự (theo lần list gần nhất) hoặc tên bộ. add/star/unstar/skip/unskip/merge cần allow_mutate."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string",
                               "enum": ["list", "all", "show", "add", "star", "unstar", "skip", "unskip", "merge"]},
                    "target": {"type": "string"},
                },
                "required": ["action"],
            },
        },
    },
]

from agents.agent_hongguo import HONGGUO_TOOLS, HONGGUO_MUTATE_TOOLS, handle_hongguo_tool
TOOLS += HONGGUO_TOOLS


def _films_tool(args: dict) -> str:
    import core.film_db as film_db  # noqa: PLC0415

    film_db.sync()
    if args.get("job_id"):
        return json.dumps({"text": film_db.format_detail(str(args["job_id"]))}, ensure_ascii=False)
    q, status = str(args.get("q") or "").strip(), str(args.get("status") or "").strip()
    if not q and not status:
        return json.dumps({"text": film_db.format_stats() + "\n\nMới nhất:\n"
                           + film_db.format_rows(film_db.query(limit=10))}, ensure_ascii=False)
    rows = film_db.query(q=q, status=status, limit=int(args.get("limit") or 40), order="series" if q else "recent")
    return json.dumps({"n": len(rows), "text": film_db.format_rows(rows)}, ensure_ascii=False)


def _service_cmd(kind: str, rest: str = "") -> str:
    """V6 F4: gọi THẲNG services/feeder (trong tiến trình server) — bỏ vòng server → POST sidecar /cmd
    → sidecar gọi ngược API server. kind: running | lib | feed."""
    try:
        import services.feeder as svc_feeder
        if kind == "running":
            text = svc_feeder.running_text()
        elif kind == "lib":
            text = svc_feeder.lib_command(rest)[0]
        elif kind == "feed":
            text = svc_feeder.feed_command(rest)
        else:
            return json.dumps({"error": f"lệnh service lạ: {kind}"}, ensure_ascii=False)
        return json.dumps({"text": text}, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return json.dumps({"error": f"services.feeder lỗi: {e}"}, ensure_ascii=False)


MUTATE_TOOLS = frozenset({
    "pineline_resume_job",
    "pineline_config_set",
    "pineline_queue_add",
    "pineline_queue_stop",
    "pineline_script_edit_line",
    "pineline_script_edit_many",
    "pineline_hold_after_translate",
    "pineline_run_stage",
    "pineline_run_stage_wait",
    "pineline_pipeline_loop",
    "pineline_stop_job",
    "pineline_tts_set_voice",
    "pineline_tts_run",
    "pineline_mux_run",
    "pineline_asr_set_engine",
    "pineline_asr_run",
    "pineline_ocr_run",
    "pineline_script_review",
    "pineline_qa_gate_set",
    "pineline_watch_once",
    "pineline_av_sync_heal",
    "pineline_av_sync_heal_batch",
    "pineline_reset_job_state",
}) | HONGGUO_MUTATE_TOOLS

CONFIRM_TOOLS = frozenset({
    "pineline_queue_stop",
    "pineline_stop_job",
    "hg_merge",
    "hg_clean",
    "hg_auto_flow",
})
FORCE_CONFIRM_TOOLS = frozenset({
    "pineline_run_stage",
    "pineline_run_stage_wait",
    "pineline_pipeline_loop",
    "pineline_tts_run",
    "pineline_asr_run",
    "pineline_ocr_run",
})
CONFIRM_TTL_S = 15 * 60

_PENDING: dict[str, dict] = {}
_PENDING_LOCK = threading.Lock()


def _truthy(v: Any) -> bool:
    return v is True or str(v).strip().lower() in ("1", "true", "yes", "on")


def tool_tier(name: str, args: dict | None) -> str:
    """read | mutate | confirm. confirm = phá huỷ/tốn lâu, cần user bấm ✅ khi gọi từ Telegram."""
    a = args or {}
    if name in CONFIRM_TOOLS:
        return "confirm"
    if name in FORCE_CONFIRM_TOOLS and _truthy(a.get("force")):
        return "confirm"
    if name == "pineline_config_set" and _truthy(a.get("reset")):
        return "confirm"
    if name == "pineline_script_edit_many" and len(a.get("edits") or []) > 20:
        return "confirm"
    if name in ("pineline_heal_failed_batch", "pineline_av_sync_heal_batch") and _truthy(a.get("apply")):
        return "confirm"
    if name == "pineline_heal_job" and _truthy(a.get("apply")) and str(a.get("level") or "") == "aggressive":
        return "confirm"
    act = str(a.get("action") or "").strip().lower()
    if name == "pineline_library" and act == "merge":
        return "confirm"
    if name == "pineline_feed" and act in ("on", "off", "set"):
        return "confirm"
    if name in MUTATE_TOOLS:
        return "mutate"
    if name == "pineline_library" and act not in ("", "list", "all", "show"):
        return "mutate"
    if name == "pineline_feed" and act == "now":
        return "mutate"
    if name in ("pineline_heal_job",) and _truthy(a.get("apply")):
        return "mutate"
    return "read"


def describe_action(name: str, args: dict | None) -> str:
    a = {k: v for k, v in (args or {}).items() if v not in (None, "", [], {})}
    if name == "pineline_script_edit_many":
        a = {"job_id": a.get("job_id"), "edits": f"{len(a.get('edits') or [])} dòng"}
    s = json.dumps(a, ensure_ascii=False)
    return f"{name} {s[:300]}"


def _prune_pending(now: float) -> None:
    for cid in [k for k, v in _PENDING.items() if now - v["at"] > CONFIRM_TTL_S]:
        _PENDING.pop(cid, None)


def park_action(name: str, args: dict) -> dict:
    """Giữ tool call lại chờ user xác nhận; trùng (name, args) chưa hết hạn thì dùng lại id cũ."""
    sig = name + "|" + json.dumps(args or {}, ensure_ascii=False, sort_keys=True)
    now = time.time()
    with _PENDING_LOCK:
        _prune_pending(now)
        for cid, v in _PENDING.items():
            if v["sig"] == sig:
                return {"id": cid, "tool": name, "summary": v["summary"]}
        cid = secrets.token_hex(4)
        summary = describe_action(name, args)
        _PENDING[cid] = {"tool": name, "args": dict(args or {}), "sig": sig, "at": now, "summary": summary}
    return {"id": cid, "tool": name, "summary": summary}


def pending_actions() -> list[dict]:
    with _PENDING_LOCK:
        _prune_pending(time.time())
        return [{"id": k, "tool": v["tool"], "summary": v["summary"]} for k, v in _PENDING.items()]


def confirm_action(cid: str, approve: bool) -> dict:
    with _PENDING_LOCK:
        _prune_pending(time.time())
        item = _PENDING.pop(str(cid or ""), None)
    if not item:
        return {"ok": False, "error": "Không còn thao tác chờ (hết hạn 15 phút hoặc đã xử lý)."}
    if not approve:
        return {"ok": True, "cancelled": True, "tool": item["tool"], "summary": item["summary"]}
    result = _dispatch(item["tool"], item["args"], allow_mutate=True)
    try:
        parsed: Any = json.loads(result)
    except (json.JSONDecodeError, TypeError):
        parsed = result
    return {"ok": True, "tool": item["tool"], "summary": item["summary"], "result": parsed}


QUEUE_DEFAULT_OPTS: dict[str, Any] = dict(batch_config.DEFAULTS)


@dataclass
class Runtime:
    work_root: Path
    list_jobs: Callable[[], list[dict]]
    get_job: Callable[[str], dict]
    queue_state: Callable[[], dict]
    queue_add: Callable[[list[str], dict], dict]
    queue_stop: Callable[[], dict]
    list_voices: Callable[[], Any]
    draft_yt: Callable[[str], dict] | None = None
    edit_script_line: Callable[[str, int, str | None, str | None], Any] | None = None
    set_hold_after_translate: Callable[[str, bool], Any] | None = None
    run_stage: Callable[[str, str, dict], Any] | None = None
    get_hold: Callable[[str], dict] | None = None
    stop_job: Callable[[str], Any] | None = None
    tts_set_voice: Callable[[str, dict], Any] | None = None
    mux_run: Callable[[str, dict], Any] | None = None
    asr_set_engine: Callable[[str, str], Any] | None = None
    script_review: Callable[[str, str | None], Any] | None = None
    resume_job: Callable[[str], Any] | None = None
    heal_hooks: dict | None = None
    av_sync_hooks: dict | None = None
    notify: Callable[[str], Any] | None = None


_rt: Runtime | None = None


def bind(runtime: Runtime) -> None:
    global _rt
    _rt = runtime


def _require_rt() -> Runtime:
    if _rt is None:
        raise RuntimeError("Agent chưa bind runtime (server chưa khởi tạo).")
    return _rt


def summarize_job(j: dict, work_root: Path) -> dict:
    stages = j.get("stages") or {}
    jid = j.get("job_id") or ""
    out = work_root / jid / "output.mp4"
    brief = {
        s: {
            "status": (stages.get(s) or {}).get("status"),
            "cur_text": (stages.get(s) or {}).get("cur_text"),
            "progress": (stages.get(s) or {}).get("progress"),
        }
        for s in WIRED
    }
    hold = _read_hold(work_root, jid)
    return {
        "job_id": jid,
        "title": j.get("title"),
        "status": j.get("status"),
        "overall": j.get("overall"),
        "has_output": out.is_file() and out.stat().st_size > 0,
        "stages": brief,
        "group_id": j.get("group_id"),
        "group_title": j.get("group_title"),
        "hold_after_translate": hold.get("hold_after_translate"),
        "held_at": hold.get("held_at"),
        "qa_gate": hold.get("qa_gate"),
        "qa_blocked": hold.get("qa_blocked"),
        "qa_last": hold.get("qa_last"),
    }


def _load_script(work_root: Path, job_id: str) -> list[dict]:
    sp = work_root / job_id / "script.json"
    if not sp.is_file():
        raise FileNotFoundError(f"chưa có script.json cho {job_id}")
    rows = json.loads(sp.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError("script.json không phải list")
    return [r for r in rows if isinstance(r, dict)]


def _line_brief(r: dict) -> dict:
    return {
        "i": r.get("i"),
        "start": r.get("start"),
        "zh": (r.get("zh") or "")[:200],
        "vi": (r.get("vi") or "")[:200],
        "manual": bool(r.get("manual")),
        "empty_vi": not (r.get("vi") or "").strip(),
    }


def _read_hold(work_root: Path, job_id: str) -> dict:
    rt = _rt
    if rt and rt.get_hold:
        try:
            return rt.get_hold(job_id) or {}
        except Exception:  # noqa: BLE001
            pass
    mf = work_root / job_id / "manifest.json"
    if not mf.is_file():
        return {}
    try:
        data = json.loads(mf.read_text(encoding="utf-8"))
        return dict(data.get("agent") or {})
    except Exception:  # noqa: BLE001
        return {}


def script_peek(work_root: Path, job_id: str, sample: int = 5) -> dict:
    rows = _load_script(work_root, job_id)
    n = len(rows)
    missing = [int(r.get("i", i)) for i, r in enumerate(rows) if not (r.get("vi") or "").strip()]
    sample = max(1, min(int(sample or 5), 20))
    head = [_line_brief(r) for r in rows[:sample]]
    mid0 = max(0, n // 2 - sample // 2)
    mid = [_line_brief(r) for r in rows[mid0: mid0 + sample]]
    tail = [_line_brief(r) for r in rows[-sample:]] if n else []
    hold = _read_hold(work_root, job_id)
    return {
        "job_id": job_id,
        "n_lines": n,
        "n_missing_vi": len(missing),
        "missing_i_sample": missing[:30],
        "hold": hold,
        "head": head,
        "mid": mid,
        "tail": tail,
    }


def script_search(
    work_root: Path,
    job_id: str,
    query: str = "",
    missing_only: bool = False,
    limit: int = 20,
) -> dict:
    rows = _load_script(work_root, job_id)
    limit = max(1, min(int(limit or 20), 50))
    q = (query or "").strip().lower()
    hits = []
    for r in rows:
        vi = (r.get("vi") or "")
        zh = (r.get("zh") or "")
        empty = not vi.strip()
        if missing_only and not empty:
            continue
        if q and q not in vi.lower() and q not in zh.lower():
            continue
        if not missing_only and not q:
            continue
        hits.append(_line_brief(r))
        if len(hits) >= limit:
            break
    return {"job_id": job_id, "query": query, "missing_only": missing_only, "hits": hits, "n_hits": len(hits)}


def script_get_lines(work_root: Path, job_id: str, i_from: int, i_to: int) -> dict:
    rows = _load_script(work_root, job_id)
    lo, hi = int(i_from), int(i_to)
    if hi < lo:
        lo, hi = hi, lo
    if hi - lo > 80:
        hi = lo + 80
    by_i = {int(r.get("i", -1)): r for r in rows}
    out = []
    for i in range(lo, hi + 1):
        r = by_i.get(i)
        if r:
            out.append(_line_brief(r))
    return {"job_id": job_id, "i_from": lo, "i_to": hi, "lines": out}


def classify_jobs(jobs: list[dict], work_root: Path) -> dict:
    buckets: dict[str, list] = {
        "running": [], "pending": [], "failed": [], "done": [], "need_queue": [],
    }
    for j in jobs:
        s = summarize_job(j, work_root)
        st = s["status"] or "pending"
        buckets.setdefault(st, []).append(s)
        # Failed / HOLD / QA chặn chờ người duyệt — autopilot xếp lại sẽ lặp lỗi hoặc bỏ qua duyệt.
        if st == "pending" and not s.get("held_at") and not s.get("qa_blocked"):
            buckets["need_queue"].append(s["job_id"])
    buckets["need_queue"] = list(dict.fromkeys(buckets["need_queue"]))
    return {
        "counts": {
            "running": len(buckets["running"]),
            "pending": len(buckets["pending"]),
            "failed": len(buckets["failed"]),
            "done": len(buckets["done"]),
            "need_queue": len(buckets["need_queue"]),
        },
        "running": buckets["running"],
        "pending": buckets["pending"],
        "failed": buckets["failed"],
        "done_sample": buckets["done"][:5],
        "need_queue": buckets["need_queue"],
    }


def default_yt_draft(work_root: Path, job_id: str, max_lines: int = 12) -> dict:
    work = work_root / job_id
    title = job_id
    mf = work / "manifest.json"
    if mf.is_file():
        data = json.loads(mf.read_text(encoding="utf-8"))
        dl = (data.get("stages") or {}).get("download") or {}
        title = data.get("title_vi") or dl.get("title") or title
    lines: list[str] = []
    script = work / "script.json"
    if script.is_file():
        rows = json.loads(script.read_text(encoding="utf-8"))
        for r in rows:
            if isinstance(r, dict):
                vi = (r.get("vi") or "").strip()
                if vi:
                    lines.append(vi)
            if len(lines) >= max_lines:
                break
    hook = lines[0] if lines else title
    return {
        "job_id": job_id,
        "source_title": title,
        "titles": [
            f"{title} | Full Vietsub",
            f"Review: {title}",
            f"{hook[:60]}… — {title}",
            f"[Vietsub] {title}",
            f"{title} — tập đủ",
        ],
        "description": (
            f"{title}\n\nHook: {hook}\n\nNội dung (trích):\n"
            + "\n".join(f"- {x}" for x in lines[:8])
            + "\n\n#vietsub #review #phimtrung"
        ),
        "tags": ["vietsub", "review", "phim trung", "long tieng"],
        "note": "Draft — sửa trước khi đăng.",
    }


def _dispatch(name: str, args: dict, allow_mutate: bool) -> str:
    rt = _require_rt()
    if name in MUTATE_TOOLS and not allow_mutate:
        return json.dumps({
            "error": "mutate_blocked",
            "hint": "Bật «Cho phép sửa script / chạy-dừng» trên panel Agent rồi gửi lại.",
        }, ensure_ascii=False)

    try:
        if name == "pineline_status":
            return json.dumps(
                classify_jobs(rt.list_jobs(), rt.work_root),
                ensure_ascii=False, indent=2,
            )
        if name == "pineline_job":
            return json.dumps(
                summarize_job(rt.get_job(args["job_id"]), rt.work_root),
                ensure_ascii=False, indent=2,
            )
        if name == "pineline_queue_status":
            return json.dumps(rt.queue_state(), ensure_ascii=False, indent=2)
        if name == "pineline_queue_add":
            ids = [str(x) for x in (args.get("job_ids") or []) if str(x).strip()]
            gid = str(args.get("group_id") or "").strip()
            if gid:
                mem = [j for j in rt.list_jobs() if str(j.get("group_id") or "") == gid]
                mem.sort(key=lambda j: (j.get("group_index") or 0, str(j.get("job_id"))))
                ids = [str(j["job_id"]) for j in mem] + [x for x in ids if x not in {str(j["job_id"]) for j in mem}]
                if not mem:
                    return json.dumps({"error": f"group {gid} không có job"}, ensure_ascii=False)
            if not ids:
                return json.dumps({"error": "cần job_ids hoặc group_id"}, ensure_ascii=False)
            opts = batch_config.load()
            overrides = {k: args[k] for k in batch_config.DEFAULTS
                         if args.get(k) is not None and args.get(k) != ""}
            opts.update(overrides)
            if overrides:
                # config riêng cho lần xếp này — sidecar không đồng bộ đè theo config batch
                opts["pin_opts"] = True
            opts["brand"] = opts.get("brand_mode", "off") != "off"
            opts["skip_done"] = True
            res = rt.queue_add(ids, dict(opts))
            if isinstance(res, dict):
                res = {**res, "applied_opts": {k: v for k, v in opts.items() if k not in ("skip_done", "pin_opts")},
                       "pinned": bool(overrides)}
            return json.dumps(res, ensure_ascii=False, indent=2)
        if name == "pineline_config_get":
            return json.dumps({
                "opts": batch_config.load(), **batch_config.meta(),
                "text": batch_config.describe(),
                "choices": {k: list(v) for k, v in batch_config.CHOICES.items()},
            }, ensure_ascii=False, indent=2)
        if name == "pineline_config_set":
            if args.get("reset"):
                return json.dumps({"ok": True, "opts": batch_config.reset(by="agent")}, ensure_ascii=False, indent=2)
            patch = {k: v for k, v in args.items() if k in batch_config.DEFAULTS}
            try:
                r = batch_config.update(patch, by="agent")
            except ValueError as e:
                return json.dumps({"error": str(e)}, ensure_ascii=False)
            return json.dumps({"ok": True, **r}, ensure_ascii=False, indent=2)
        if name == "pineline_running_check":
            return _service_cmd("running")
        if name == "pineline_films":
            return _films_tool(args)
        if name == "pineline_library":
            act = str(args.get("action") or "list").strip().lower()
            target = str(args.get("target") or "").strip()
            if act not in ("list", "all", "show") and not allow_mutate:
                return json.dumps({"error": "mutate_blocked",
                                   "hint": "Bật «Cho phép sửa script / chạy-dừng» trên panel Agent rồi gửi lại."},
                                  ensure_ascii=False)
            if act in ("show", "add", "star", "unstar", "skip", "unskip", "merge") and not target:
                return json.dumps({"error": "thiếu target (số thứ tự hoặc tên bộ)"}, ensure_ascii=False)
            return _service_cmd("lib", {"list": "", "all": "all", "show": target}.get(act)
                                if act in ("list", "all", "show") else f"{act} {target}")
        if name == "pineline_feed":
            act = str(args.get("action") or "show").strip().lower()
            if act in ("set", "on", "off", "now") and not allow_mutate:
                return json.dumps({"error": "mutate_blocked",
                                   "hint": "Bật «Cho phép sửa script / chạy-dừng» trên panel Agent rồi gửi lại."},
                                  ensure_ascii=False)
            if act == "set":
                req = str(args.get("request") or "").strip()
                if not req:
                    return json.dumps({"error": "thiếu request"}, ensure_ascii=False)
                return _service_cmd("feed", req)
            return _service_cmd("feed", "" if act == "show" else act)
        if name == "pineline_queue_stop":
            return json.dumps(rt.queue_stop(), ensure_ascii=False, indent=2)
        if name == "pineline_yt_draft":
            fn = rt.draft_yt or (lambda jid: default_yt_draft(rt.work_root, jid))
            return json.dumps(fn(args["job_id"]), ensure_ascii=False, indent=2)
        if name == "pineline_voices":
            return json.dumps(rt.list_voices(), ensure_ascii=False, indent=2)
        if name == "pineline_script_peek":
            return json.dumps(
                script_peek(rt.work_root, args["job_id"], int(args.get("sample") or 5)),
                ensure_ascii=False, indent=2,
            )
        if name == "pineline_script_search":
            return json.dumps(
                script_search(
                    rt.work_root,
                    args["job_id"],
                    query=args.get("query") or "",
                    missing_only=bool(args.get("missing_only")),
                    limit=int(args.get("limit") or 20),
                ),
                ensure_ascii=False, indent=2,
            )
        if name == "pineline_script_get_lines":
            return json.dumps(
                script_get_lines(
                    rt.work_root,
                    args["job_id"],
                    int(args["i_from"]),
                    int(args["i_to"]),
                ),
                ensure_ascii=False, indent=2,
            )
        if name == "pineline_script_edit_line":
            if not rt.edit_script_line:
                return json.dumps({"error": "edit_script_line chưa bind"})
            res = rt.edit_script_line(
                args["job_id"],
                int(args["i"]),
                args.get("vi"),
                args.get("zh"),
            )
            return json.dumps(res, ensure_ascii=False, indent=2, default=str)
        if name == "pineline_script_edit_many":
            if not rt.edit_script_line:
                return json.dumps({"error": "edit_script_line chưa bind"})
            edits = args.get("edits") or []
            if len(edits) > 40:
                return json.dumps({"error": "tối đa 40 edits/lần"})
            results = []
            for e in edits:
                if not isinstance(e, dict) or "i" not in e:
                    continue
                results.append(rt.edit_script_line(
                    args["job_id"], int(e["i"]), e.get("vi"), e.get("zh"),
                ))
            return json.dumps({"ok": True, "n": len(results), "results": results[:40]},
                              ensure_ascii=False, indent=2, default=str)
        if name == "pineline_hold_after_translate":
            if not rt.set_hold_after_translate:
                return json.dumps({"error": "hold chưa bind"})
            res = rt.set_hold_after_translate(args["job_id"], bool(args["enabled"]))
            return json.dumps(res, ensure_ascii=False, indent=2, default=str)
        if name == "pineline_run_stage":
            if not rt.run_stage:
                return json.dumps({"error": "run_stage chưa bind"})
            opts = {
                "voice": args.get("voice"),
                "force": bool(args.get("force")),
                "engine": args.get("engine"),
            }
            res = rt.run_stage(args["job_id"], str(args["stage"]), opts)
            return json.dumps(res, ensure_ascii=False, indent=2, default=str)
        if name == "pineline_run_stage_wait":
            if not rt.run_stage:
                return json.dumps({"error": "run_stage chưa bind"})
            from agents.agent_tool_loop import run_stage_wait
            opts = {
                "voice": args.get("voice"),
                "force": _truthy(args.get("force")),
                "engine": args.get("engine"),
            }
            res = run_stage_wait(
                rt.run_stage, rt.work_root, args["job_id"], str(args["stage"]), opts,
                timeout_sec=float(args.get("timeout_sec") or 3600),
                poll_sec=float(args.get("poll_sec") or 2),
            )
            return json.dumps(res, ensure_ascii=False, indent=2, default=str)
        if name == "pineline_pipeline_loop":
            if not rt.run_stage:
                return json.dumps({"error": "run_stage chưa bind"})
            from agents.agent_tool_loop import pipeline_loop
            stages = args.get("stages") or []
            if isinstance(stages, str):
                stages = [s.strip() for s in stages.split(",") if s.strip()]
            opts = {
                "voice": args.get("voice"),
                "force": _truthy(args.get("force")),
                "engine": args.get("engine"),
            }
            res = pipeline_loop(
                rt.run_stage, rt.work_root, args["job_id"], list(stages), opts,
                timeout_sec=float(args.get("timeout_sec") or 7200),
                poll_sec=float(args.get("poll_sec") or 2),
            )
            return json.dumps(res, ensure_ascii=False, indent=2, default=str)
        if name == "pineline_stage_peek":
            return _stage_json(peek_stage(rt.work_root, args["job_id"], str(args["stage"])))
        if name == "pineline_stage_peek_all":
            return _stage_json(peek_all(rt.work_root, args["job_id"]))
        if name == "pineline_stop_job":
            if not rt.stop_job:
                return json.dumps({"error": "stop_job chưa bind"})
            return json.dumps(rt.stop_job(args["job_id"]), ensure_ascii=False, indent=2, default=str)
        if name == "pineline_tts_set_voice":
            if not rt.tts_set_voice:
                return json.dumps({"error": "tts_set_voice chưa bind"})
            return json.dumps(
                rt.tts_set_voice(args["job_id"], {
                    "voice": args.get("voice"),
                    "speed": args.get("speed"),
                    "engine": args.get("engine"),
                }),
                ensure_ascii=False, indent=2, default=str,
            )
        if name == "pineline_tts_run":
            if not rt.run_stage:
                return json.dumps({"error": "run_stage chưa bind"})
            opts = {
                "voice": args.get("voice"),
                "force": bool(args.get("force")),
                "engine": args.get("engine"),
            }
            res = rt.run_stage(args["job_id"], "tts", opts)
            return json.dumps(res, ensure_ascii=False, indent=2, default=str)
        if name == "pineline_mux_run":
            if not rt.mux_run:
                return json.dumps({"error": "mux_run chưa bind"})
            opts = {k: args[k] for k in (
                "audio_mode", "orig_vol", "voice_vol", "zoom", "flip", "grain",
                "brand_text", "brand", "vbitrate", "add_sub", "resolution",
            ) if k in args and args[k] is not None}
            return json.dumps(rt.mux_run(args["job_id"], opts), ensure_ascii=False, indent=2, default=str)
        if name == "pineline_asr_set_engine":
            if not rt.asr_set_engine:
                return json.dumps({"error": "asr_set_engine chưa bind"})
            return json.dumps(
                rt.asr_set_engine(args["job_id"], str(args["engine"])),
                ensure_ascii=False, indent=2, default=str,
            )
        if name == "pineline_asr_run":
            if not rt.run_stage:
                return json.dumps({"error": "run_stage chưa bind"})
            opts = {
                "engine": args.get("engine"),
                "force": bool(args.get("force")),
            }
            return json.dumps(
                rt.run_stage(args["job_id"], "asr", opts),
                ensure_ascii=False, indent=2, default=str,
            )
        if name == "pineline_ocr_run":
            if not rt.run_stage:
                return json.dumps({"error": "run_stage chưa bind"})
            return json.dumps(
                rt.run_stage(args["job_id"], "ocr", {"force": bool(args.get("force"))}),
                ensure_ascii=False, indent=2, default=str,
            )
        if name == "pineline_style_pack_peek":
            return _stage_json(peek_style_pack(rt.work_root, args["job_id"]))
        if name == "pineline_resume_job":
            if not rt.resume_job:
                return json.dumps({"error": "resume_job chưa bind"})
            return json.dumps(rt.resume_job(args["job_id"]), ensure_ascii=False, indent=2, default=str)
        if name == "pineline_script_review":
            if not rt.script_review:
                return json.dumps({"error": "script_review chưa bind"})
            return json.dumps(
                rt.script_review(args["job_id"], args.get("review_model")),
                ensure_ascii=False, indent=2, default=str,
            )
        if name == "pineline_diagnose_job":
            from agents.agent_heal import diagnose_job
            return json.dumps(
                diagnose_job(rt.work_root, args["job_id"]),
                ensure_ascii=False, indent=2, default=str,
            )
        if name == "pineline_heal_job":
            from agents.agent_heal import heal_job
            apply = bool(args.get("apply"))
            if apply and not allow_mutate:
                return json.dumps({
                    "error": "mutate_blocked",
                    "hint": "heal apply=true cần bật Cho phép sửa/chạy trên panel. Hoặc apply=false để xem plan.",
                }, ensure_ascii=False)
            return json.dumps(
                heal_job(
                    rt.work_root,
                    args["job_id"],
                    apply=apply,
                    level=str(args.get("level") or "safe"),
                    hooks=rt.heal_hooks or {},
                ),
                ensure_ascii=False, indent=2, default=str,
            )
        if name == "pineline_scan_failed":
            from agents.agent_heal import scan_problem_jobs
            return json.dumps(
                scan_problem_jobs(rt.work_root, list_jobs=rt.list_jobs),
                ensure_ascii=False, indent=2, default=str,
            )
        if name == "pineline_heal_failed_batch":
            from agents.agent_heal import heal_failed_batch
            apply = bool(args.get("apply"))
            if apply and not allow_mutate:
                return json.dumps({
                    "error": "mutate_blocked",
                    "hint": "heal_failed_batch apply=true cần tick Cho phép sửa/chạy.",
                }, ensure_ascii=False)
            return json.dumps(
                heal_failed_batch(
                    rt.work_root,
                    apply=apply,
                    level=str(args.get("level") or "safe"),
                    max_jobs=int(args.get("max_jobs") or 20),
                    list_jobs=rt.list_jobs,
                    hooks=rt.heal_hooks or {},
                ),
                ensure_ascii=False, indent=2, default=str,
            )
        if name == "pineline_reset_job_state":
            from agents.agent_heal import reset_job_state
            if not allow_mutate:
                return json.dumps({
                    "error": "mutate_blocked",
                    "hint": "pineline_reset_job_state cần bật Cho phép sửa/chạy trên panel.",
                }, ensure_ascii=False)
            stages = args.get("stages")
            keep_asr = args.get("keep_asr", True)
            restart = bool(args.get("restart", False))
            return json.dumps(
                reset_job_state(
                    rt.work_root,
                    args["job_id"],
                    stages=stages,
                    keep_asr=keep_asr,
                    restart=restart,
                    hooks=rt.heal_hooks or {},
                ),
                ensure_ascii=False, indent=2, default=str,
            )
        if name == "pineline_review_diff":
            from agents.agent_qa_gate import review_diff
            return json.dumps(
                review_diff(rt.work_root / args["job_id"], args["job_id"]),
                ensure_ascii=False, indent=2, default=str,
            )
        if name == "pineline_qa_gate_set":
            from agents.agent_qa_gate import set_qa_gate
            return json.dumps(
                set_qa_gate(
                    rt.work_root / args["job_id"],
                    bool(args["enabled"]),
                    override_pass=args.get("override_pass"),
                ),
                ensure_ascii=False, indent=2, default=str,
            )
        if name == "pineline_watch_once":
            from agents.agent_watch import watch_once
            return json.dumps(
                watch_once(
                    rt.work_root,
                    list_jobs=rt.list_jobs,
                    hooks=rt.heal_hooks or {},
                    max_jobs=int(os.getenv("AGENT_WATCH_MAX_JOBS") or "10"),
                ),
                ensure_ascii=False, indent=2, default=str,
            )
        if name == "pineline_av_sync_check":
            from agents.agent_av_sync import diagnose_av_sync
            return json.dumps(
                diagnose_av_sync(rt.work_root, args["job_id"]),
                ensure_ascii=False, indent=2, default=str,
            )
        if name == "pineline_av_sync_heal":
            from agents.agent_av_sync import heal_av_sync
            apply = bool(args.get("apply"))
            if apply and not allow_mutate:
                return json.dumps({
                    "error": "mutate_blocked",
                    "hint": "av_sync_heal apply=true cần Cho phép sửa/chạy.",
                }, ensure_ascii=False)
            return json.dumps(
                heal_av_sync(
                    rt.work_root,
                    args["job_id"],
                    apply=apply,
                    hooks=rt.av_sync_hooks or {},
                ),
                ensure_ascii=False, indent=2, default=str,
            )
        if name == "pineline_av_sync_scan":
            from agents.agent_av_sync import scan_av_sync
            return json.dumps(
                scan_av_sync(
                    rt.work_root,
                    args.get("job_ids"),
                    group_id=args.get("group_id"),
                    deep=bool(args.get("deep")),
                ),
                ensure_ascii=False, indent=2, default=str,
            )
        if name == "pineline_av_sync_heal_batch":
            from agents.agent_av_sync import heal_av_sync_batch
            apply = bool(args.get("apply"))
            if apply and not allow_mutate:
                return json.dumps({
                    "error": "mutate_blocked",
                    "hint": "heal_batch apply=true cần Cho phép sửa/chạy.",
                }, ensure_ascii=False)
            return json.dumps(
                heal_av_sync_batch(
                    rt.work_root,
                    args.get("job_ids"),
                    group_id=args.get("group_id"),
                    apply=apply,
                    hooks=rt.av_sync_hooks or {},
                    deep=bool(args.get("deep")),
                ),
                ensure_ascii=False, indent=2, default=str,
            )
        if name.startswith("hg_"):
            return handle_hongguo_tool(rt, name, args, allow_mutate)
        return json.dumps({"error": f"unknown tool {name}"})
    except Exception as e:  # noqa: BLE001
        return json.dumps({"error": str(e)}, ensure_ascii=False)


def _llm_cfg() -> tuple[str, str, str]:
    """Cấu hình LLM cho Agent chat (có thể tách khỏi job translate) — config.agent_llm()."""
    import core.config as _cfg
    c = _cfg.agent_llm()
    return c["base"], c["key"], c["model"]


def _chat_turn(
    messages: list[dict],
    use_tools: bool = True,
    tool_choice: Any = "auto",
) -> dict:
    """Một turn OpenAI chat/completions. Trả message dict (content + tool_calls).

    AGENT_FALLBACK_MODEL: thử model này khi model chính lỗi (timeout, 429, 5xx, quota).
    tool_choice: "auto" | "required" | {"type":"function","function":{"name":...}}
    """
    import core.config as _cfg
    c = _cfg.agent_llm()
    models = [c["model"]] + ([c["fallback"]] if c["fallback"] else [])
    last: Exception | None = None
    for i, m in enumerate(models):
        try:
            return _chat_turn_model(messages, use_tools, m, tool_choice=tool_choice)
        except Exception as e:  # noqa: BLE001
            last = e
            if i + 1 < len(models):
                print(f"[agent] LLM {m} lỗi ({str(e)[:160]}) → thử {models[i + 1]}", flush=True)
    raise last  # type: ignore[misc]


def _chat_turn_model(
    messages: list[dict],
    use_tools: bool,
    model: str,
    tool_choice: Any = "auto",
) -> dict:
    base, key, _ = _llm_cfg()
    url = f"{base}/chat/completions"
    enable_think = (os.getenv("LLM_ENABLE_THINKING") or "").strip() in ("1", "true", "yes")
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": 2048,
        "stream": False,
    }
    # chat_template_kwargs chỉ local (llama.cpp) — 9router/OpenAI thường reject
    if enable_think or "127.0.0.1" in base or "localhost" in base:
        payload["chat_template_kwargs"] = {"enable_thinking": enable_think}
    if use_tools:
        payload["tools"] = TOOLS
        payload["tool_choice"] = tool_choice if tool_choice is not None else "auto"
    r = requests.post(
        url,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json=payload,
        timeout=180,
    )
    if r.status_code >= 400:
        # Một số model local không hỗ trợ tools → fallback text-only + JSON protocol
        if use_tools and r.status_code in (400, 422):
            return _chat_turn_text_protocol(messages, model)
        raise RuntimeError(f"LLM HTTP {r.status_code}: {r.text[:400]}")
    choice = (r.json().get("choices") or [{}])[0]
    return choice.get("message") or {}


def _chat_turn_text_protocol(messages: list[dict], model: str | None = None) -> dict:
    """Fallback khi model không tool-call: ép JSON {tool,args} hoặc {final}."""
    extra = {
        "role": "system",
        "content": (
            "Model không có native tools. Mỗi lượt CHỈ trả JSON thuần:\n"
            '{"tool":"pineline_status","args":{}} hoặc '
            '{"tool":"pineline_job","args":{"job_id":"..."}} hoặc '
            '{"final":"câu trả lời cho user"}'
        ),
    }
    msgs = [extra, *messages]
    base, key, cfg_model = _llm_cfg()
    model = model or cfg_model
    r = requests.post(
        f"{base}/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "messages": msgs,
            "temperature": 0.2,
            "max_tokens": 1024,
            "stream": False,
            "response_format": {"type": "json_object"},
        },
        timeout=120,
    )
    if r.status_code >= 400:
        # bỏ json_mode
        r = requests.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": msgs,
                "temperature": 0.2,
                "max_tokens": 1024,
                "stream": False,
            },
            timeout=120,
        )
    r.raise_for_status()
    content = ((r.json().get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    content = content.strip()
    m = re.search(r"\{[\s\S]*\}", content)
    if not m:
        return {"content": content}
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {"content": content}
    if obj.get("final"):
        return {"content": str(obj["final"])}
    if obj.get("tool"):
        # giả lập 1 tool_call
        return {
            "content": None,
            "tool_calls": [{
                "id": "textproto1",
                "type": "function",
                "function": {
                    "name": obj["tool"],
                    "arguments": json.dumps(obj.get("args") or {}, ensure_ascii=False),
                },
            }],
        }
    return {"content": content}


_TOOL_NAMES = {t["function"]["name"] for t in TOOLS}


_ACTION_PREFIX = re.compile(r"(?i)(?:action|hành động)\s*:?$")


def _parse_pycall_tool_calls(content: str) -> list[dict]:
    """Model hay in «Action: `hg_auto_flow(query_or_id="…", auto_run=True)`» thay vì gọi tool.

    Chỉ nhận lời gọi đứng riêng cả tin nhắn hoặc ngay sau nhãn «Action:» — tên tool nhắc trong
    câu kể/tóm tắt («đã gọi hg_auto_flow(...)») không được chạy.
    """
    import ast
    content = content or ""
    calls: list[dict] = []
    for m in re.finditer(r"\b([a-z][a-z0-9_]*)\s*\(", content):
        name = m.group(1)
        if name not in _TOOL_NAMES and f"pineline_{name}" in _TOOL_NAMES:
            name = f"pineline_{name}"
        if name not in _TOOL_NAMES:
            continue
        depth, end = 0, None
        for j in range(m.end() - 1, len(content)):
            ch = content[j]
            depth += ch == "("
            depth -= ch == ")"
            if depth == 0:
                end = j + 1
                break
        if end is None:
            continue
        line_start = content.rfind("\n", 0, m.start()) + 1
        prefix = content[line_start:m.start()].strip(" \t*`>-")
        whole = not content[:m.start()].strip(" \t\r\n*`>-") and not content[end:].strip(" \t\r\n*`.")
        if not whole and not _ACTION_PREFIX.search(prefix):
            continue
        try:
            node = ast.parse(content[m.start():end].replace(m.group(1), "f", 1), mode="eval").body
            if not isinstance(node, ast.Call) or node.args:
                continue
            args = {kw.arg: ast.literal_eval(kw.value) for kw in node.keywords if kw.arg}
            arguments = json.dumps(args, ensure_ascii=False)
        except (SyntaxError, ValueError, TypeError):
            continue
        calls.append({
            "id": f"textcall{len(calls)}",
            "type": "function",
            "function": {"name": name, "arguments": arguments},
        })
    return calls


def _parse_text_tool_calls(content: str) -> list[dict]:
    """Model qua proxy đôi khi in tool call dạng text (```json [{"name":..,"arguments":..}]```)."""
    s = (content or "").strip()
    if not s:
        return []
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", s)
    if fence:
        s = fence.group(1).strip()
    elif not s.startswith(("[", "{")):
        return _parse_pycall_tool_calls(s)
    try:
        obj = json.loads(s)
    except json.JSONDecodeError:
        return _parse_pycall_tool_calls(content)
    items = obj if isinstance(obj, list) else [obj]
    calls: list[dict] = []
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            return []
        if isinstance(it.get("function"), dict):
            it = it["function"]
        name = str(it.get("name") or it.get("tool") or "").strip()
        if name and name not in _TOOL_NAMES and f"pineline_{name}" in _TOOL_NAMES:
            name = f"pineline_{name}"
        if name not in _TOOL_NAMES:
            return []
        args = it.get("arguments", it.get("args", {}))
        calls.append({
            "id": f"textcall{i}",
            "type": "function",
            "function": {
                "name": name,
                "arguments": args if isinstance(args, str) else json.dumps(args or {}, ensure_ascii=False),
            },
        })
    return calls


TOOL_RESULT_MAX = int(os.getenv("AGENT_TOOL_RESULT_MAX") or 6000)


def clip_tool_result(result: str, limit: int = TOOL_RESULT_MAX) -> str:
    if len(result) <= limit:
        return result
    return (result[:limit] + f"\n…[cắt {len(result) - limit} ký tự — gọi tool chi tiết hơn "
            "(job_id/limit/page) nếu cần phần còn lại]")


@dataclass
class AgentResult:
    reply: str
    steps: list[dict] = field(default_factory=list)
    model: str = ""
    confirms: list[dict] = field(default_factory=list)


def run_agent(
    message: str,
    *,
    history: list[dict] | None = None,
    allow_mutate: bool = False,
    max_steps: int = 8,
    require_confirm: bool = False,
    force_first_tool: str | None = None,
    force_first_args: dict | None = None,
) -> AgentResult:
    """Chạy agent. history = [{role, content}, ...] (user/assistant only).

    require_confirm: tool tier "confirm" không chạy ngay mà đỗ lại (confirms) chờ user bấm ✅.
    force_first_tool: tên tool ép gọi ở lượt 1 (Brain / silent daemon — tránh model chỉ viết text).
    force_first_args: args JSON cho tool ép (vd {"job_id":"..."}).
    """
    _, _, model = _llm_cfg()
    messages: list[dict] = [{"role": "system", "content": SYSTEM}]
    if history:
        for h in history[-12:]:
            role = h.get("role")
            if role in ("user", "assistant") and h.get("content"):
                messages.append({"role": role, "content": str(h["content"])})
    messages.append({"role": "user", "content": message})

    steps: list[dict] = []
    confirms: list[dict] = []
    first_tool = (force_first_tool or "").strip() or None
    if first_tool and first_tool not in _TOOL_NAMES:
        first_tool = None
    first_args = dict(force_first_args or {}) if isinstance(force_first_args, dict) else {}
    for step_i in range(max_steps):
        choice: Any = "auto"
        if step_i == 0 and first_tool:
            choice = {"type": "function", "function": {"name": first_tool}}
        msg = _chat_turn(messages, use_tools=True, tool_choice=choice)
        tool_calls = msg.get("tool_calls") or []
        if not tool_calls and not steps:
            tool_calls = _parse_text_tool_calls(msg.get("content") or "")
            if tool_calls:
                msg = {"role": "assistant", "content": None, "tool_calls": tool_calls}
        # Brain: nếu ép tool mà model vẫn text → synthesize 1 call (proxy đôi khi bỏ tool_choice)
        if not tool_calls and step_i == 0 and first_tool:
            tool_calls = [{
                "id": "forcefirst1",
                "type": "function",
                "function": {
                    "name": first_tool,
                    "arguments": json.dumps(first_args, ensure_ascii=False),
                },
            }]
            msg = {"role": "assistant", "content": None, "tool_calls": tool_calls}
        if not tool_calls:
            reply = (msg.get("content") or "").strip() or "(không có nội dung)"
            return AgentResult(reply=reply, steps=steps, model=model, confirms=confirms)

        messages.append(msg)
        for tc in tool_calls:
            fn = tc.get("function") or {}
            name = fn.get("name") or ""
            raw = fn.get("arguments") or "{}"
            try:
                args = json.loads(raw) if isinstance(raw, str) else (raw or {})
            except json.JSONDecodeError:
                args = {}
            if require_confirm and allow_mutate and tool_tier(name, args) == "confirm":
                parked = park_action(name, args)
                if all(c["id"] != parked["id"] for c in confirms):
                    confirms.append(parked)
                result = json.dumps({
                    "status": "awaiting_user_confirm",
                    "confirm_id": parked["id"],
                    "message": "CHƯA chạy. Đã gửi nút ✅/❌ cho user — báo user bấm xác nhận, "
                               "KHÔNG gọi lại tool này.",
                }, ensure_ascii=False)
            else:
                result = _dispatch(name, args, allow_mutate=allow_mutate)
            steps.append({"tool": name, "args": args, "result_preview": result[:800]})
            messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id") or name,
                "content": clip_tool_result(result),
            })

    done = [s for s in steps if tool_tier(s["tool"], s["args"]) != "read"
            and '"awaiting_user_confirm"' not in s["result_preview"]]
    reply = "Hết số bước. Thu hẹp câu hỏi hoặc tăng max_steps."
    if done:
        reply += "\nĐã chạy (thay đổi trạng thái):\n" + "\n".join(
            f"- {describe_action(s['tool'], s['args'])}" for s in done)
    return AgentResult(
        reply=reply,
        steps=steps,
        model=model,
        confirms=confirms,
    )


def status_snapshot() -> dict:
    rt = _require_rt()
    return classify_jobs(rt.list_jobs(), rt.work_root)
