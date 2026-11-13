# Review phần DỊCH THUẬT ZH→VI — Pineline V6

_2026-11-11 · chỉ đọc, không sửa code · nối vào `docs/design/2026-11-11-translation-quality.md` (M0–M8)_

## Mục lục

0. [Phạm vi, cách làm, mức tin cậy](#0-phạm-vi-cách-làm-mức-tin-cậy)
1. [Tóm tắt điều hành](#1-tóm-tắt-điều-hành)
2. [Luồng dịch hiện tại (đọc từ code)](#2-luồng-dịch-hiện-tại-đọc-từ-code)
3. [Bảng phát hiện theo mức](#3-bảng-phát-hiện-theo-mức)
4. [Chi tiết theo trục (có trích code)](#4-chi-tiết-theo-trục-có-trích-code)
   - 4.1 Đúng đắn / bug · 4.2 Chất lượng dịch · 4.3 Kiến trúc / bảo trì · 4.4 Hiệu năng / chi phí · 4.5 Đo lường
5. [Nối với thiết kế M0–M8](#5-nối-với-thiết-kế-m0m8)
6. [Đề xuất tách package `translate/`](#6-đề-xuất-tách-package-translate)
7. [Lộ trình phát triển](#7-lộ-trình-phát-triển)
8. [Việc cần xác minh trên Windows](#8-việc-cần-xác-minh-trên-windows)

---

## 0. Phạm vi, cách làm, mức tin cậy

- **Nguồn đọc:** bản pack `scratch/v6_src.tgz` giải nén ở cloud. Đã đối chiếu md5 `engines/translate.py` (`8d820326…`) và `engines/llm.py` (`4e2e8b6f…`) với máy user: **trùng**. Thiết kế `2026-11-11-translation-quality.md` đọc trực tiếp trên máy user (không có trong pack).
- **File đã đọc hết:** `engines/translate.py` (4131 dòng), `engines/pronoun_consistency.py`, `engines/pronoun_register_policy.py`, `agents/agent_pronoun_pass.py`, `agents/agent_name_pass.py`, `engines/series_glossary.py`, `engines/mt_zh_vi.py`, `engines/translate_alts.py` (phần lõi), `engines/llm.py` (chat/aurora/parse_json), `engines/script_lint.py`, `engines/hv_auditor.py` (phần audit/consistency/filter), `agents/agent_quality.py`, `agents/agent_qa_gate.py`, `engines/asr/hardsub_asr.py` (merge/sync), `server.py` (đoạn 1680–1960, 4035–4125 và các chỗ gọi).
- **Đã chạy ở cloud (không LLM/HTTP/GPU):**
  - `pytest tests/v5/test_pronoun_* test_review_* (trừ 2 file cần fastapi)` → **22 pass, 1 skip, 4 xfail, 2 fail** (2 fail là `test_review_batches[check_batch3/4]` vì thiếu `fastapi` ở cloud — không liên quan logic dịch).
  - Mô phỏng thuần (monkeypatch `chat`) để xác nhận C1, C3, C5 (xem từng mục).
- **Đã kiểm trên máy user (chỉ `ls`):** `data/unihan/` **không có** trong V6 (có trong V2); `context_studio/` chỉ có ở `tools/context_studio` trong V5/V6 (V2 ở gốc).
- Quy ước: điều đã thấy trong code/đã chạy → khẳng định; điều suy luận → ghi **(cần xác minh)**.

---

## 1. Tóm tắt điều hành

**Nhận định chung:** pipeline dịch hiện tại là _NMT câu-đơn (HachimiMT) làm bản chính → REVIEW LLM chỉ vá "lỗi cứng" → một chuỗi hậu kỳ regex/LLM (lock, style polish, pronoun postpass, register policy, pronoun-QA, name pass, lint, hang-fix)_. Nhiều lớp đã được vá theo lỗi của **vài phim cụ thể** nên (a) luật riêng của 1 phim chạy cho mọi phim, (b) các lớp xưng hô **đá nhau**, (c) có lỗ hổng làm **mất dữ liệu** (câu người sửa bị ghi đè, lô review rỗng vẫn bị đánh `rv`). Về thiết kế M0–M8: code đã có nhiều "nguyên liệu" (plan.scenes, pronouns, mask, QA flags, hold/park), nhưng **cản trở lớn nhất** là: không có dữ liệu theo câu xuyên suốt (run_translate dựng lại dict mỗi câu → mất mọi trường thêm), REVIEW bị "đóng băng ngôi" nên không thể là nơi áp ràng buộc xưng hô, và không có vết (trace) để đo M0.

**Top phát hiện mức Cao**

| ID | Tóm tắt |
|---|---|
| C1 | LLM trả **rỗng / JSON cụt** ở REVIEW → lô vẫn bị đánh `rv=1` (đã mô phỏng: trả `{}` chứ không `None`) |
| C2 | REVIEW (và name pass, register policy) **ghi đè câu người đã sửa tay** (`manual=1` bị bỏ `rv` → vào lô soát lại) |
| C3 | `enforce_remaining_cau` đổi máy «Cậu ấy» → «Ngươi ấy», «cậu ruột» → «ngươi ruột», kể cả câu `manual` (đã mô phỏng) |
| C4 | Các lớp xưng hô **ping-pong**: style polish (mixed) ngươi→cậu; register policy cậu→ngươi; kết quả phụ thuộc lớp nào chạy sau |
| C5 | **Luật/seed riêng của một phim** (máu 31 000 tỷ, 过山, 逆天而行, «Cú đấm 80 tỷ…», «hành tinh thứ hai»→«Tinh cầu hai sao») chạy cho mọi phim, được nhét vào prompt "BẮT BUỘC" và lưu sang kho series |
| C6 | `from context_studio.schema import …` **gãy ở V6** (package nằm ở `tools/`) — nuốt lỗi im lặng → style_lock không được điền → phim đô thị bị gán `mixed` |
| C8 | REVIEW **không thể sửa xưng hô** (mask + loại mọi fix đổi tập đại từ) trong khi bản chính là NMT câu-đơn không ngữ cảnh → lỗi xưng hô gần như chỉ được "vá" bằng regex |
| C9 | Fallback local khi cloud lỗi chạy **trong tối đa 24 luồng song song**: mỗi luồng tự `switch_preset` (GPU dùng chung), phân loại lỗi bằng chuỗi con ("token", "500"); lỗi 413 (prompt quá dài) cũng kích hoạt đổi preset |

**Top 5 hướng phát triển** (chi tiết §7)

1. **Tuần này (S, không cần golden):** vá C1, C2, C3, C9 + bộ test thuần cho các hàm hậu kỳ (hiện **0 test** cho style polish, mask, filter cosmetic, hard lock, register policy).
2. **Dọn luật riêng-phim khỏi code → dữ liệu theo bộ** (bước đầu M4/M6), sửa import `context_studio`, chép `data/unihan/kVietnamese.json` sang V6; thống nhất định dạng số.
3. **Nền đo đạc cho M0:** `ScriptStore` (một cửa ghi script, tôn trọng `manual`, giữ mọi trường) + `translate_trace.jsonl` (ai đổi câu nào, model thật, finish_reason, token) + `LLMResult`.
4. **Golden set + `eval_translation.py` (M0) trước mọi thay đổi prompt/mask/NMT**; đo đóng góp từng lớp (NMT, review, polish, postpass) để bỏ lớp vô ích.
5. **Một `PronounResolver` duy nhất** (ưu tiên: manual > `pron` theo câu (M3) > cặp khoá (M4) > mặc định thể loại); đổi mask "đóng băng" thành mask "ràng buộc"; chuẩn bị trường `spk/to/pron` chạy xuyên suốt — sẵn sàng cho diarization (M2–M5).

---

## 2. Luồng dịch hiện tại (đọc từ code)

```
run_translate (translate.py:3498)
 ├─ PLAN: glossary.json có sẵn → dùng lại; không → build_plan
 │    file-mode: upload SRT ZH (Aurora) + 1 chat (PLAN_MODEL) ; lỗi → sample-mode (≤2 chat)
 ├─ series_glossary.apply → save_from (ghi kho bộ NGAY, trước khi dịch/duyệt)        :3655-3659
 ├─ ensure_hard_terms (seed + hv_auditor)                                            :3662
 ├─ pronoun analyze: upload SRT + 1 chat ; lỗi → ≤10 lô×220 câu → glossary "locked"  :3665
 ├─ prefill câu cười/thán từ (_trivial_vi)
 ├─ NMT HachimiMT (MT_ZH_VI=1, role mặc định PRIMARY) → vi chính, cờ mt=1            :3791-3836
 ├─ LLM translate_batch chỉ cho câu NMT trống/còn Hán (+ ≤3 vòng vá missing)
 ├─ hard locks → style polish (+stutter, half-cue, script_lint autofix)
 ├─ run_post_translate_consistency: pronoun postpass + jumps QA + GPT vá đỏ
 ├─ heal số gãy → script.json, script.display.json, sub.vi.srt
 └─ AUTO_REVIEW → run_review (translate.py:3010)
      ├─ vá câu trống · stutter/half-cue heal
      ├─ Pass1: upload cặp ZH|VI → style_brief + nonsense_i + half_cue_pairs
      ├─ review_batch theo lô 40 (±1 câu), song song tới 24 luồng nếu aurora_parallel
      │    mask đại từ → LLM → unmask → loại fix đổi ngôi → lọc "cosmetic" → lọc lock
      ├─ hard locks → style polish → pronoun consistency (GPT)
      ├─ series_glossary.save_from · agent_hang_fix
server (server.py:1784-1960): kiểm missing/coverage → còn câu chưa rv → run_review lại / PARK
   → agent_quality.scan_and_heal_job (HOLD nếu high) → hold_after_translate
   → _run_requested_review (name pass → pronoun pass → run_review force=True)
   → QA gate (snapshot → run_review → diff) → translate_ready_for_tts → TTS
```

---

## 3. Bảng phát hiện theo mức

Trục: **Đ** = đúng đắn/bug · **CL** = chất lượng dịch · **KT** = kiến trúc/bảo trì · **HN** = hiệu năng/chi phí · **ĐL** = đo lường.

### 3.1 Mức Cao

| ID | Trục | File:dòng | Mô tả | Hậu quả | Đề xuất |
|---|---|---|---|---|---|
| C1 | Đ | translate.py:2795-2807, 167-172, 3199-3204; llm.py:757 | `chat()` trả `""` sau 2 lần rỗng; `review_batch` gặp lỗi parse thì `_salvage_items("")` → `{}`; `_review_chunk_with_fallback` trả `{}` (không phải `None`) → `_apply_result` đánh `rv=1` cả lô. JSON bị cắt (max_tokens=4096) cũng vậy: các fix còn lại mất, cả lô vẫn `rv`. | Câu chưa từng được soát đi thẳng TTS; bản vá B1 (lô lỗi không rv) bị lách | `review_batch` trả `None` khi không parse được hoặc thiếu khoá `fixes`; coi salvage là "một phần" → `None`; đọc `finish_reason` (cần `chat` trả meta) |
| C2 | Đ/CL | server.py:4051-4053; translate.py:3099, 3199-3202; server.py:1705; agent_name_pass.py:49-58 | Sửa tay: bỏ `rv`, đặt `manual=1` → lần REVIEW sau đưa câu vào `todo`, LLM sửa và `_apply_result` ghi đè không kiểm `manual`. `_run_requested_review` gọi `run_review(force=True)`. Name pass `find_issues` cũng không bỏ qua `manual`. | Mất công người sửa; phá nền M7 (TM từ câu người sửa) và M8 | Một hàm `apply_fix(row, new, stage)` duy nhất, **từ chối mọi ghi lên `manual`** (trừ nguồn `human`); test hồi quy |
| C3 | Đ/CL | pronoun_register_policy.py:33, 158-175, 214-243 | `INTIMATE_2ND_RE` khớp mọi «cậu», kể cả ngôi 3 («cậu ấy/cậu ta») và danh từ («cậu ruột»); `enforce_remaining_cau` thay máy trong cửa sổ có danh xưng bậc, không kiểm `manual`. Mô phỏng: «Cậu ấy là ai? Cậu ruột của tôi à?» → «Ngươi ấy là ai? Ngươi ruột của tôi à?» (câu `manual=True`). | Câu vô nghĩa đi TTS; sửa tay bị phá | Loại trừ «cậu ấy/ta/ruột/mợ…», bỏ qua `manual`, chỉ đổi khi `pron.call` (M3) yêu cầu; nếu chưa có M3 → chỉ **cờ**, không thay máy |
| C4 | CL | translate.py:1714-1722 (mixed: Ngươi→Cậu, Ta→Tôi); pronoun_register_policy.py:101-106 (mixed → preferred «ngươi»); agent_pronoun_pass.py:196-234; translate.py:3286-3293 | Cùng một phim `mixed`: hậu kỳ style polish ép ngươi→cậu; register policy ép cậu→ngươi ở cửa sổ có danh xưng; REVIEW chạy lại (QA gate / yêu cầu agent) lại chạy style polish. Prompt cũng mâu thuẫn: `_mixed_address_rules` "CẤM … ta/ngươi" vs `prompt_rules` "CẤM giữ cậu → ngươi". | Xưng hô dao động giữa các lần chạy — đúng loại lỗi thiết kế muốn diệt; không tái lập được kết quả để đo | Gộp về `PronounResolver` một nguồn quyết định; các lớp còn lại chỉ **phát hiện**; thứ tự ưu tiên ghi rõ (xem §5 M3/M5) |
| C5 | CL/KT | translate.py:1171-1186, 1232-1261, 1604-1761, 251-272; series_glossary.py:162-170 | `_DEFAULT_HARD_TERMS` là **nguyên câu** của một phim («我一共三万一千一血»→«Tổng cộng tôi có 31 000 tỷ máu.»…), được `ensure_hard_terms` thêm vào **mọi** glossary không phải `modern` (mô phỏng: +16 term), in vào prompt mục "THÀNH NGỮ / LOCK (BẮT BUỘC)", rồi `save_from` lưu `type=lock` vào kho series. `apply_style_polish` thay câu cứng («Cú đấm 80 tỷ của tôi mà mẹ kiếp cậu vẫn chưa chết.»), và «hành tinh thứ hai»→«Tinh cầu hai sao» **không cần điều kiện ZH** (mô phỏng xác nhận). | Ô nhiễm prompt mọi phim, sửa sai nghĩa ở phim khác, lan sang kho bộ | Chuyển toàn bộ sang `data/series/<bộ>/overrides.json` (chỉ áp cho bộ đó); seed mặc định chỉ giữ luật phổ quát; script dọn kho series hiện có |
| C6 | Đ/CL | translate.py:906-909, 3615-3618; pronoun_consistency.py:101-105, 690 | `context_studio` ở V5/V6 nằm dưới `tools/` → `ModuleNotFoundError` (đã thử ở cloud), bị `except: pass`. Hệ quả dây chuyền: `style_lock.mode/forbid/prefer/address_bank` không được điền; `mark_locked` gán `mode="mixed"` cho mọi genre có «đô thị/hiện đại/modern»; `mixed` làm `filter_review_fixes_for_lock` bỏ lọc. | Phim đô thị thuần chạy luật mixed (seed HV, ép Ta→Tôi…), khoá xưng hô vô hiệu | Sửa import (`tools.context_studio.schema`) + log WARN khi import hỏng; test import (cũng áp cho server.py:4769) |
| C8 | CL | translate.py:2739-2760, 2808-2819; mt_zh_vi.py:291-302; translate.py:3807-3829 | REVIEW thay đại từ bằng token ⟦Pn⟧ và **bỏ mọi fix làm đổi tập đại từ** (`_pronoun_forms`), trong khi prompt (B) lại yêu cầu sửa "đảo ngôi". Bản chính là NMT dịch từng câu, không ngữ cảnh/không plan. Mask còn che cả danh từ («anh trai», «em gái», «người ta» — mô phỏng). | Lỗi xưng hô của NMT gần như không bao giờ được LLM sửa; fix loại (D)/(F) chuyển ý giữa câu cũng bị loại nếu đụng đại từ | Không gỡ ngay (cần M0 đo). Hướng: mask **ràng buộc** — token mang giá trị đích từ `pron` (M3) thay vì đóng băng; chỉ đóng băng khi chưa có ràng buộc |
| C9 | Đ/HN | translate.py:151-210, 3243-3262; llm.py:281-285, 300-302 | Khi cloud lỗi, **mỗi luồng** (tối đa 24) tự gọi `preset_switcher.switch_preset(…, 180s)` rồi bắn request local song song (trái với `_review_parallel_workers` local=1). Phân loại "hết quota" bằng chuỗi con (`"token"`, `"500"`, `"exceeded"`…). Aurora 400/413/422 → `LLMUnavailable` → cũng bị coi là "out" → đổi preset GPU thay vì chia nhỏ lô. | GPU dùng chung (V2/llama :8083) bị đổi preset dồn dập; lô quá dài không bao giờ tự chia | Một khoá toàn cục + "circuit breaker" (đổi preset tối đa 1 lần/job, sau đó tuần tự); phân loại theo status code; 413 → chia lô |

### 3.2 Mức Trung bình

| ID | Trục | File:dòng | Mô tả | Hậu quả | Đề xuất |
|---|---|---|---|---|---|
| T1 | Đ | translate.py:4087-4103 | `run_translate` nuốt lỗi REVIEW (khác stop) rồi `mark_done` (ghi đè `mark_failed` của run_review). Pipeline auto có lưới (server.py:1803-1850 đếm `rv`), các đường gọi tay (server.py:3422, 5961) **cần xác minh**. | Stage "done" dù review dở | Trả trạng thái `reviewed=False` rõ; để gate quyết |
| T2 | CL | translate.py:261-262 vs 1375-1388, 1762; script_lint.py:46-47; translate_alts.py (prompt "31.001") | Prompt bắt viết số bằng **dấu cách** và "CẤM 31.000"; 3 hậu kỳ đổi về **dấu chấm**; prompt alts lại lấy «31.001» làm mẫu; `hard_term_lock_map` coi «31.001» là sai. Lock «31 000» bị đổi thành «31.000» rồi lần chạy sau lock ghi lại. | LLM tốn token cho luật bị huỷ; churn mỗi lần chạy | Chốt 1 định dạng (theo TTS/sub: dấu chấm) và sửa prompt |
| T3 | CL/HN | translate.py:419-426 & 1023-1026; 1006-1012 & 2034/2057, 2678/2738 | Khối xưng hô mixed/ancient chèn **2 lần** (trong `_translate_rules` và `_plan_block`); `lock_banner` chèn 2 lần. Thành ngữ 没大没小/打天下/脑子进水/穷酸 lặp ở ≥8 chỗ (rules, style rules, translate_batch sys, review sys, `_DEFAULT_STYLE_TERMS`, polish, `_heuristic_nonsense_flags`, agent_quality, translate_alts). | Prompt phình, trọng số luật lệch; sửa một chỗ quên chỗ khác | `PromptBuilder` khử trùng lặp; luật → dữ liệu (M6) |
| T4 | CL | translate.py:546-555 | `_salvage_mixed_vi` xoá thẳng ≤8 ký tự Hán còn sót trong câu VI (sau khi thay vài thuật ngữ). | Có thể mất tên/thuật ngữ mà không cờ | Thay vì xoá: giữ câu + cờ `cjk_salvaged` cho M8/review |
| T5 | HN/Đ | translate.py:2106-2109, 2135-2139 | Lô 20 câu thiếu ≥2 câu → raise → chia đôi → **dịch lại cả hai nửa**, bỏ 18 câu đã đúng; nửa sau dùng cùng `prev_ctx` (không thấy nửa đầu). Lô ≥7 câu thiếu 1 câu → chấp nhận im lặng (vòng vá sau). | Tốn request; mạch thoại đứt ở điểm chia | Giữ kết quả tốt, chỉ dịch lại câu thiếu (kèm ngữ cảnh) |
| T6 | Đ | llm.py:738-741 vs 281-285; translate.py:2121 | Cùng lỗi 413: đường OpenAI → `RuntimeError` (splitter chia lô); đường Aurora → `LLMUnavailable` → `_translate_safe` re-raise → dừng cả stage. | Hành vi khác nhau theo model | Hợp đồng lỗi chung: `PayloadTooLarge`, `Unavailable`, `BadOutput` |
| T7 | Đ/ĐL | llm.py:747-749, 817; llm.py:252-306 | `chat()` lấy `reasoning_content` làm nội dung khi `content` rỗng → parser/salvage có thể vớt object từ bản nháp suy luận. `_chatgpt_chat` âm thầm đổi model theo chuỗi `[m, default, gpt-5-6-mini, gpt-5-6, auto]`; caller ghi `review_model` là model yêu cầu chứ không phải model thật. | Kết quả lạ khó truy; số liệu M0 gán sai model | `LLMResult{content, model_used, finish_reason, usage}`; không dùng reasoning làm output JSON |
| T8 | CL | translate.py:1903-1912; agent_pronoun_pass.py:62-66 | Hậu kỳ cặp xưng hô coi "tên `who` xuất hiện trong ZH" là dấu hiệu `who` đang nói; thực tế tên trong câu thường là **người được gọi** (hô ngữ). | Áp nhầm self/call của cặp ngược | Chỉ dùng khi có `spk` (M2); trước đó dùng hô ngữ để suy `to`, không suy `spk` |
| T9 | CL | pronoun_consistency.py:732-784, 821-847 | `find_pronoun_jumps` xét cửa sổ ±6 câu không biết người nói: A–B dùng anh/em, C–D dùng ông là hợp lệ nhưng bị cờ; «anh trai», «ông nội», «cô giáo» bị đếm như đại từ. Câu bị cờ được GPT "vá" theo khối plan chung. | Có thể **tạo** lỗi xưng hô (đồng nhất hoá sai) | Với M3: so `pron` từng câu thay cho cửa sổ; trước đó chỉ cờ, không tự vá |
| T10 | Đ | pronoun_consistency.py:275, 588; agent_name_pass.py:123; agent_pronoun_pass.py:217, 233; translate_alts.py:287-297; hardsub_asr.py:352, 406; series_glossary.py:186 | Nhiều nơi ghi `script.json` / `glossary.json` / kho series bằng `write_text` (không atomic, không `file_lock`), song song với run_review dùng `atomic_write_json`. | Mất cập nhật nếu 2 tác vụ chạy cùng lúc (**cần xác minh** UI có cho chạy song song) | `ScriptStore`/`GlossaryStore` có khoá (`core.fileio.file_lock`) |
| T11 | CL/KT | series_glossary.py:107-123, 126-170; translate.py:3659 | Không có nguồn gốc/ưu tiên: `apply` → series thắng; `save_from` → tập mới thắng; plan **tự động** được lưu vào kho ngay sau khi lập (trước dịch/duyệt); pronouns analyze tự động đè cặp cũ. Đường dẫn `ROOT/data` không qua `core.instance`. | Lỗi của 1 tập lan cả bộ; trái luật V6 về đường dẫn | M4: `source` human>review>auto, `from/until`; chỉ lưu sau duyệt |
| T12 | ĐL | agent_qa_gate.py:83-88, 101-103 | `review_ran` luôn True sau `snapshot_pre_review`; điều kiện `changed_n >= 0` luôn đúng; cho phép thiếu VI 2% (trong khi `translate_ready_for_tts` đòi 0); không kiểm tỉ lệ `rv`, không có chỉ số xưng hô/term. | Gate gần như chỉ kiểm missing + qa_brain | Gate đo thật: `rv` 100%, `pron_mismatch`, `term_missing`, ngưỡng cấu hình (M5) |
| T13 | Đ | translate.py:3164-3176, 3187, 3199-3202 | REVIEW song song: lô được phép sửa câu ±1 thuộc lô khác (có thể đã `rv`); ngữ cảnh lấy từ bản trước review; thứ tự hoàn thành quyết định bản cuối. | Kết quả không tái lập giữa các lần chạy | Chỉ áp fix cho câu thuộc lô; câu biên → hàng đợi "cross-boundary" xử lý tuần tự |
| T14 | CL | mt_zh_vi.py:229-234, 291-302; hardsub_asr.py:314-353 | NMT base: `no_repeat_ngram_size=2` cấm lặp bigram (có thể bóp méo «từ từ», tên lặp, nhấn mạnh) — **cần xác minh trên golden**. NMT không nhận glossary/tên. `sync_script_zh` đổi `zh` nhưng giữ `vi` + `rv` cũ, ghi cả câu `manual`. | Bản dịch lệch nguồn mới vẫn coi là đã soát | Đổi `zh` ⇒ bỏ `rv`, cờ `zh_changed`; thử `no_repeat=0` trên golden |
| T15 | Đ | translate.py:3766-3789 vs 4001 | Sau `heal_script_broken_numbers` (gộp 2 câu, giữ `i` câu đầu), lần resume không phải post-split sẽ thấy `i` câu thứ hai "thiếu" và dịch lại nó. | Có thể lặp nội dung số (**cần xác minh**) | Ghi `merged_into` thay vì xoá hàng |
| T16 | KT | translate.py:56-59, 213-215, 3921; các file | ~49 khoá cấu hình rải 3 file; trộn `os.getenv` và `_cfg_get`; `CTX_LINES`, `ONESHOT_MAX` đóng băng lúc import; tên model cứng («chatgpt/gpt-6», «gpt-5-6-mini», «qwen36-turbo», «gemma4-12b-qat-mtp-fast»). | Khó tái lập một lần chạy; cấu hình instance không phủ hết | `TranslateConfig` đọc 1 lần/job, ghi snapshot vào trace |
| T17 | Đ/CL | data/unihan (máy user); hv_auditor.py:380-381, 294-369, 451-475 | `data/unihan/kVietnamese.json` **không có trong V6** → `audit_and_fix_plan` trả "thiếu" → bảo vệ bẫy HV (逆→Nghị…) và hygiene kho series tắt im lặng. Khi có dữ liệu: `enforce_char_consistency` tự đổi tên VI nhân vật không ghi nguồn; `drop_suspect` xoá mục khỏi kho. | Mất lớp bảo vệ tên; khi bật lại có thể đổi tên đã duyệt | Chép dữ liệu từ V2; không auto-đổi mục `source=human` |
| T18 | KT | translate.py:3781-3784, 3814-3817, 3860-3863, 3929-3932, 4033-4041 | Mỗi câu được dựng lại dict `{i,start,end,zh,vi}` → **mọi trường thêm từ asr.json bị bỏ** (sẽ chặn `asr_conf`, `spk`, `to`, `pron` của thiết kế §5). | Chặn M1–M3 | Copy hàng ASR rồi cập nhật, không dựng mới |

### 3.3 Mức Thấp

| ID | File:dòng | Mô tả / đề xuất |
|---|---|---|
| L1 | translate.py:3510-3515 | `except Exception` đứng trước `except ValueError` → nhánh sau không bao giờ chạy; `int(os.getenv(...))` trong except có thể ném tiếp. |
| L2 | translate.py:455-480 | Chế độ `auto` luôn trả `batch` — code chết; oneshot gần như không dùng → cân nhắc bỏ. |
| L3 | translate.py:485 vs 488 | `_has_cjk` (U+4E00…) khác `_CJK_RUN_RE` (từ U+3400). |
| L4 | translate.py:1499-1504, 1523-1524, 1540/1575/1614/1707/1715/1747 | Nhánh `if … pass` chết, dòng `replace` lặp, `import re` trong vòng lặp. |
| L5 | translate_alts.py:210 | `from engines.llm import free_gpu_vram` — tên này chỉ import **trong hàm** ở llm.py:539 → `ImportError` khi alts chạy model local. |
| L6 | translate.py:2281-2284 | `_pass1_chat_model` ép tiền tố `chatgpt/` cho mọi model (vd `kr/claude…` → `chatgpt/claude…`). |
| L7 | translate.py:1123-1125, 1016 | Cảnh không có `range` khớp mọi lô; `_plan_block` chỉ in 5 cảnh đầu (phim dài: cảnh sau không có trong tổng quan). |
| L8 | translate.py:2571-2579 | Pass1 ghi `style_lock.review_brief` vào glossary vĩnh viễn; brief của lần chạy hỏng dính cho lần sau; `review_pass1.json` ghi không atomic. |
| L9 | translate.py:3041-3061 | Vá câu trống gom các câu **không liền nhau** nhưng prompt nói "đoạn thoại LIỀN". |
| L10 | translate.py:2626, 2660, 1369; pronoun_consistency.py:27-28, 718, 732 | 5 bộ nhận diện đại từ khác nhau (ngoại lệ «ông ta/người ta» khác nhau). |
| L11 | agent_quality.py:65 | `_MEIDAMEIXIAO_WANT = "Cậu còn không biết trên dưới à."` — câu cứng làm fallback. |

---

## 4. Chi tiết theo trục (có trích code)

### 4.1 Đúng đắn / bug

**C1 – lô rỗng vẫn được đánh "đã soát".** Chuỗi dẫn tới lỗi:

```python
# llm.py:757          if empty_seen >= 2: return ""
# translate.py:2806   except Exception: raw = _salvage_items(out)  # rỗng = không câu nào cần sửa
# translate.py:3203   if ii in chunk_ids: r["rv"] = 1
```
Mô phỏng (cloud): `chat` trả `""` → `({}, 'kr/x')`; trả `'{"fixes":[{"i":1,"vi":"Xin chào cậu nhé'` → `({}, 'kr/x')`. Cả hai đều không phải `None` nên không vào `failed_jobs`. `max_tokens=4096` cho lô 40 câu + mô hình có thinking làm khả năng cụt là có thật.

**C2 – câu người sửa không được bảo vệ ở REVIEW.**

```python
# server.py:4051    if "rv" in hit: hit.pop("rv", None)
# server.py:4053    hit["manual"] = 1
# translate.py:3099 todo = [r for r in rows if force or not r.get("rv")]
```
`_apply_result` (3199-3202) không xét `manual`. Các hậu kỳ regex thì có (`apply_hard_term_locks`, `apply_style_polish`, `apply_pronoun_postpass` đều `if r.get("manual"): continue`) — tức là bất biến "manual là bất khả xâm phạm" đã được ngầm hiểu nhưng không được thực thi ở lớp LLM.

**C9 – fallback trong luồng song song.**

```python
# translate.py:178  w in err_str for w in ("429", "token", "quota", "exhausted", "rate limit",
# translate.py:183  or isinstance(e, LLMUnavailable)
# translate.py:195  if not preset_switcher.switch_preset(local_rev, max_wait_sec=180):
```
`LLMUnavailable` được ném cả cho 400/413/422 (llm.py:284, 302) — tức là prompt quá dài cũng đổi preset GPU. Không có khoá giữa các luồng.

**T1, T6, T7, T13, T15** xem bảng 3.2. Điểm chung: hợp đồng giữa `llm.chat` và caller quá nghèo (chỉ chuỗi `str`), nên mỗi caller tự đoán "rỗng/cụt/lỗi/quá dài" theo cách riêng.

**Điểm tốt đã có (giữ nguyên):** bản vá B1/B4/B5/B7 (lô lỗi không rv, bỏ fix ngoài lô, kiểm `.stop` 2 s, không review lặp) có test (`tests/v5/test_review_fail_not_rv.py`, `test_review_parallel_workers.py`) và đều pass ở cloud; salvage ghi đủ khung ASR khi dừng giữa chừng (4020-4062) đúng hướng.

### 4.2 Chất lượng dịch

**Prompt.** System prompt của `translate_batch` ≈ 5,4–6,3 nghìn ký tự luật (đo ở cloud: `_translate_rules` = 5 432 ký tự, mixed = 6 329), cộng `_plan_block` (đo với 30 nhân vật/30 cặp = 4 416 ký tự; chú thích ở 1034 ghi bản đầy đủ có thể ~97 KB). Vấn đề:
- **Luật lẻ theo phim** chiếm phần lớn `_meaning_rules`: máu/HP «31 000 tỷ», «Thánh Trảm Thủy Mộng», «过山», «嘲讽看»… Đây là hướng dẫn cho đúng **một** phim, nhưng đọc như luật chung với chữ "bắt buộc".
- **Mâu thuẫn:** định dạng số (T2); "CẤM Ta–Ngươi" (mixed) vs "đổi cậu → ngươi" (register policy) (C4); REVIEW "sửa đảo ngôi" vs mask đóng băng ngôi (C8); "吃醋 thường là ghen, nhưng uống giấm có thể đúng" lặp ở 3 nơi với giọng khác nhau.
- **Trùng lặp** khối xưng hô và lock banner (T3) → mô hình thấy cùng luật 2 lần ở 2 vị trí khác nhau, khó biết luật nào ưu tiên.

```python
# translate.py:421   out = _meaning_rules() + _style_rules() + _character_rules()
# translate.py:1023  if _is_mixed_genre(p): lines.append(_mixed_address_rules().rstrip())
```

**Chia lô & ngữ cảnh.** Dịch LLM: lô 20 + 6 câu trước (`CTX_LINES`), **không có câu sau** (trừ alts). REVIEW: lô 40 ±1 câu + 6 câu trước + 5 "mẫu giọng" lấy từ chính bản máy (`_style_exemplars(..., exemplars_pool=rows)`). Với NMT là bản chính, LLM translate chỉ chạm câu NMT hỏng → phần lớn câu **không bao giờ** được dịch với ngữ cảnh cảnh/plan; ngữ cảnh chỉ đến qua REVIEW (mà REVIEW được dặn "GIỮ NGUYÊN").

**Plan.** Có 2 đường (file upload / sample). Khi plan lỗi hoàn toàn → dịch với plan rỗng (3652-3654) — chấp nhận được, nhưng không có cờ để gate biết "dịch không có bảng nhân vật". Sample-mode có từ khoá riêng-phim (`"苏", "林", "战", "嫣", …` ở 812). Scenes chỉ được in 5 cảnh đầu trong plan_block (L7); `_resolve_scene_context` thì lọc theo lô — tốt, đây là nền cho "style theo cảnh" của M7.

**Xưng hô — 6 cơ chế chồng nhau** (đây là điểm yếu lớn nhất về chất lượng):

| Cơ chế | Nơi | Biết người nói? | Hành động |
|---|---|---|---|
| Plan `pronouns` + analyze "lock" | prompt dịch/review | Không | gợi ý chung |
| Ledger 6 câu trước | prompt | Không | "giữ cùng thanh ngữ" (có thể sai khi đổi người nói) |
| Mask ⟦Pn⟧ + freeze | review_batch | — | **cấm** đổi ngôi |
| Postpass genre/cặp | translate.py:1868 | Đoán bằng tên trong ZH (sai hướng, T8) | regex |
| Style polish mixed | translate.py:1714 | Không | ngươi→cậu, ta→tôi |
| Register policy | pronoun_register_policy.py | Không | cậu→ngươi/ông (C3) |
| Jumps QA + GPT vá | pronoun_consistency.py:810 | Không | LLM sửa theo plan chung (T9) |

Không có thứ tự ưu tiên khai báo; mỗi lớp "sửa" theo giả định riêng. Đúng như thiết kế §1 nhận định: _hệ thống không biết AI nói với AI_. Hệ quả thực tế: lớp sau có thể đảo lớp trước (C4), và các lớp regex có thể phá câu (C3).

**Thành ngữ.** Có 3 kiểu: luật trong prompt, `_DEFAULT_STYLE_TERMS` (đã gần dạng dữ liệu: zh → vi + ghi chú cấm), và regex sửa sau trong `apply_style_polish`/`agent_quality.apply_safe_heals`. Bộ phủ ~10 cụm, đều từ vài phim. Không có dò thành ngữ tổng quát (jieba/từ điển) → mọi thành ngữ ngoài danh sách phụ thuộc NMT.

**Style polish.** Phần lớn là `str.replace` có điều kiện ZH — an toàn tương đối; nhưng có các thay **không điều kiện** (C5: «hành tinh thứ hai», «Tôi đã đến từ», «Ấn ký 18 tầng») và thay **cả câu** («Cú đấm 80 tỷ…», «Trong hồ sơ lịch sử huyết thống của chúng tôi,», «Khiêu khích.» khi `嘲讽` và câu dài > 28 ký tự). Trường hợp `\bta\b → tôi` được che bằng `_TA_KEEP_RE` nhưng danh sách khác với `_forms_in` (L10).

**NMT nháp → REVIEW có thực sự giúp?** Chưa đo được (không có trace — §4.5). Về cơ chế: REVIEW chỉ được giữ fix khi qua `_filter_cosmetic_nmt_fixes` (A: thêm tên; C: bỏ Hán; D: rút ngắn ≥25 % với overlap ≥0,65; E: stutter; F: nửa câu) **và** không đổi tập đại từ **và** qua lọc lock. Lỗi nghĩa (B) — loại lỗi quan trọng nhất — **không có nhánh chấp nhận riêng** trong filter: một fix (B) không chèn tên, không rút ngắn, không thuộc E/F sẽ bị coi là "cosmetic" và bỏ (rơi xuống `dropped += 1` ở 3003). Đây là suy luận từ code (**cần xác minh** bằng log "bỏ N fix cosmetic" trên job thật): REVIEW hiện chủ yếu sửa tên/Hán sót/độ dài/nửa câu, ít sửa nghĩa.

```python
# translate.py:2999  if over and shortened and overlap >= 0.65 and new_ratio <= 0.15 and ratio >= 0.45:
# translate.py:3003  dropped += 1
```

**translate_alts.** Cơ chế tốt cho M8 (2–3 phương án + lý do), nhưng ứng viên chọn bằng regex riêng-phim (`_AMB_RE` gồm 公主, 奴隶, 印记, 觉醒…), prompt mang luật số mâu thuẫn (T2), và lỗi import khi chạy local (L5).

### 4.3 Kiến trúc / bảo trì

- **Hàm khổng lồ:** `run_translate` ~610 dòng (3498-4107), `run_review` ~340 dòng, `apply_style_polish` ~280 dòng, `review_batch` ~160 dòng; mỗi hàm vừa I/O, vừa gọi LLM, vừa ghi manifest, vừa hậu kỳ.
- **Trùng lặp:** `_plan_block` / `_plan_block_for_qa` (~60 dòng giống), `apply_style_polish` mixed branch / `_detox_modern_pronouns`, `agent_quality.apply_safe_heals` / `apply_style_polish` (没大没小, 穷酸, 打天下…), `_heuristic_nonsense_flags` / `_has_nonsense_halfcue` / `agent_quality.HANG` (cùng regex đuôi treo 3 nơi), 5 bộ nhận diện đại từ (L10), 2 hàm `_ts` SRT.
- **Phụ thuộc ngầm/vòng:** `pronoun_consistency` import `translate` (apply_pronoun_postpass, _plan_block_for_qa) trong khi `translate` import `pronoun_consistency` (lock_banner…) — tránh vòng bằng import trong hàm. `agent_pronoun_pass` import hàm "riêng" `_is_modernish_genre/_pronoun_pairs/_char_aliases` của translate.
- **Nuốt lỗi:** ≥20 khối `except Exception: pass/return ""` quanh các bước quan trọng (context_studio, hv_auditor, lint, pronoun QA, series). C6 là ví dụ một lỗi port nằm im nhờ cơ chế này.
- **Ghi trạng thái:** `engines/translate.py` còn 6 `js.persist`, pronoun_consistency 1, agent_pronoun_pass 1, agent_name_pass 1, translate_alts 1, agent_qa_gate 2 — đúng luật F1 (qua `state.job_state`), nhưng mẫu `m.data["stages"]["translate"].update(...) ; js.persist(m)` lặp ~10 lần → nên có `js.update_stage(work_dir, "translate", **fields)`.
- **Khả năng test:** phần lớn logic hậu kỳ **đã là hàm thuần** (rows, plan → rows) — điểm cộng lớn, nhưng **chưa có test**: `apply_style_polish`, `_mask_pronouns_vi`, `_filter_cosmetic_nmt_fixes`, `apply_hard_term_locks`, `normalize_vi_thousands`, `_salvage_items`, `_salvage_mixed_vi`, `enforce_remaining_cau`, `autofix_rows`, `merge_hardsub_into_asr`, `heal_obvious_internal_stutter` (đã grep `tests/` và `scripts/`).

### 4.4 Hiệu năng / chi phí

**Số request LLM mỗi job** (NMT primary, N câu, không lỗi):

| Bước | Request | Ghi chú |
|---|---|---|
| Plan | 1 upload + 1 chat (fallback ≤2 chat) | chỉ khi chưa có glossary hoặc force |
| Pronoun analyze | 1 upload + 1 chat (fallback ≤10 lô) | bỏ qua nếu đã "locked" |
| LLM translate | ⌈miss/20⌉ (+ chia đôi khi lỗi) | miss = câu NMT trống/Hán |
| Pass1 | 1 upload + 1 chat (hoặc ⌈N/1000⌉) | |
| REVIEW | ⌈N/40⌉ (+ thử lại lô lỗi) | song song tới 24 |
| Pronoun QA | ⌈jumps/20⌉ | |
| hang-fix, name/pronoun pass | tuỳ | ngoài phạm vi đếm |
| QA gate → run_review | 0 nếu đủ rv (B7) | |

Ví dụ N = 1 000: ≈ 25 request review + ~6 request khác + vá miss. **Kích thước prompt** chi phối: mỗi request review mang ~9–10 nghìn ký tự luật + plan_block (vài KB tới hàng chục KB) + 5 mẫu + 6 ngữ cảnh + 42 câu. Plan/rules **lặp nguyên văn** ở mọi lô → chi phí ∝ (N/40) × |plan|.

Cơ hội giảm: (1) cắt plan theo lô (chỉ nhân vật/cặp có mặt trong cảnh — `_resolve_scene_context` đã lọc được); (2) khử trùng lặp T3; (3) chỉ chèn thành ngữ cho câu có thành ngữ (M6); (4) prompt caching phía nhà cung cấp nếu có (**cần xác minh** Aurora/9router hỗ trợ); (5) T5 — đừng dịch lại câu đã đúng khi chia lô; (6) cache plan theo hash ASR (hiện cache theo "glossary.json tồn tại", force là lập lại).

### 4.5 Đo lường

**Đang có:**

| Nguồn | Chỉ số | Ghi chú |
|---|---|---|
| `agent_quality.scan_script` → `qa_brain.json` | calque (4 mẫu), vi_pattern (vạn, CJK…), hang, stutter, name_echo, nonsense_shape; theo sev | dùng HOLD; mẫu theo vài phim |
| `agent_qa_gate.review_diff` | missing, số `rv`, số câu đổi so với snapshot | T12: tiêu chí yếu |
| `pronoun_qa.json` | n_det, jumps còn lại, n_gpt_fixed | jumps không biết người nói |
| `script_lint.lint_rows` | kin_toi, punct, num, term_variant, term_missing, tail_frag | tốt cho M5 phần term |
| `hv_auditor` meta | n_checked/fixed/suspect | đang **tắt** (T17) |
| Log review | "bỏ N fix cosmetic", "pronouns_locked: bỏ N fix" | chỉ in stdout |

**Thiếu cho M0:**
1. **Vết theo câu** (ai đổi gì): hiện các cờ `mt/pr/nm/rv` bị ghi đè, không có trước/sau theo lớp → không thể trả lời "REVIEW/polish/postpass giúp hay hại".
2. **Model thật + token + finish_reason** mỗi request (T7).
3. **Tính tất định**: song song (T13) và fallback (C9) làm 2 lần chạy cùng đầu vào cho kết quả khác → khó so trước/sau. Cần chế độ eval: tuần tự, nhiệt độ 0, ghi/replay phản hồi LLM ("cassette") để chạy lại hậu kỳ thuần không tốn request.
4. **Golden + `eval_translation.py`** (pronoun pair acc., consistency, ref3, idiom acc., chrF/TER) — chưa có gì.
5. Seed/luật riêng-phim (C5) làm **lệch số đo** trên phim gốc của chúng (điểm cao giả) → nên dọn trước khi lấy baseline, hoặc lấy baseline 2 lần (có/không seed).

---

## 5. Nối với thiết kế M0–M8

| Mô-đun | Code hiện tại **hỗ trợ** | Code hiện tại **cản trở** | Việc chuẩn bị (không cần diarization) |
|---|---|---|---|
| **M0** Bộ chuẩn | Hậu kỳ phần lớn là hàm thuần; `scan_script`, `lint_rows`, `find_pronoun_jumps` có thể làm chỉ số phụ | Không trace (§4.5); không tất định (T13, C9); seed riêng-phim (C5) | `translate_trace.jsonl`; `LLMResult`; chế độ eval tuần tự + cassette; dọn C5 trước baseline |
| **M1** Nguồn ZH | `hardsub_asr.merge_hardsub_into_asr` giữ timing, có ngưỡng sim; `asr_patch_rules` trong plan | `sync_script_zh` không bỏ `rv` (T14); run_translate bỏ trường ASR (T18); luật 娘→那 nằm trong prompt | Sửa T14/T18; chuyển `asr_patch_rules` + luật 娘→那 thành dữ liệu "sửa ZH" chạy trước dịch |
| **M2** Người nói | — (không có `spk` ở đâu) | T8: hậu kỳ đoán người nói bằng tên trong câu | Thêm trường tuỳ chọn `spk/spk_conf` được bảo toàn (T18); tắt suy luận T8 khi có `spk` |
| **M3** Người nghe + ràng buộc | `plan.scenes` (characters, pronouns_in_scene), `_resolve_scene_context` lọc ma trận theo cảnh; payload `translate_batch` dễ thêm `pron` từng câu | Bản chính là NMT (không nhận ràng buộc); REVIEW đóng băng ngôi (C8); ledger "giữ thanh ngữ câu trước" mâu thuẫn khi đổi người nói | Thiết kế "mask ràng buộc": token ⟦Pn⟧ → giá trị đích từ `pron`; luật hô ngữ (vocative) tất định để suy `to` một phần ngay cả khi chưa có M2 |
| **M4** Series Bible | `series_glossary` (kho bộ, SQLite đồng bộ), `_merge_list`; `hv_auditor` hygiene | Không `source`/thời gian (T11); lưu plan tự động trước duyệt; seed riêng-phim lọt vào kho (C5); `_merge_pronouns` khoá tĩnh `who->to` | Bổ sung `source`, `from/until` (tương thích ngược); chỉ `save_from` sau duyệt; script dọn kho |
| **M5** Kiểm tất định + vá | `_fix_batch` ("chỉ đổi đại từ") dùng lại được; `js.set_hold` + park/hold (server.py:1890-1946) | 6 cơ chế xưng hô chồng nhau (C4); regex thay máy phá câu (C3); gate yếu (T12) | `PronounResolver` + `pron_check(row) → mismatch`; mọi lớp regex hiện tại chuyển thành **detector**; gate `pronoun_qa` dùng `js.set_hold` |
| **M6** Thành ngữ/lóng | `_DEFAULT_STYLE_TERMS` đã có dạng zh→vi+cấm; `type: chengyu/idiom` trong plan → mục "THÀNH NGỮ/LOCK" | Luật thành ngữ rải ≥8 nơi (T3); không có bộ dò | Gom 10 cụm hiện có thành `data/lexicon/idiom_vi.json` (meaning, vi_hv, vi_thuan, forbid) + detector "forbid" thay `CALQUE_RULES`/polish |
| **M7** TM + style | `_style_exemplars` (cơ chế chèn ví dụ đã có); `scenes.tone` | Ví dụ lấy từ bản máy; câu người sửa không được bảo vệ (C2) nên chưa thể làm TM | Sửa C2; ghi `data/tm/<bộ>.jsonl` mỗi lần sửa tay (từ endpoint server.py:4038-4053) — rẻ, làm được ngay |
| **M8** Hàng duyệt | Nguồn cờ sẵn: Pass1 `nonsense_i`, `qa_brain.flags`, `pronoun_qa.jumps`, `vi_alts.json`, `hv_suspect` | Cờ rải nhiều file, định dạng khác nhau | Chuẩn hoá `row.flags: [{kind, sev, src}]` trong script (tuỳ chọn, tương thích ngược) |

---

## 6. Đề xuất tách package `translate/`

Mục tiêu: tách **không đổi hành vi** (refactor có test), `engines/translate.py` giữ lại làm lớp tương thích re-export cho `server.py` và các agent.

```
translate/
  __init__.py          # re-export run_translate, run_review, translate_ready_for_tts, …
  config.py            # TranslateConfig (đọc 1 lần/job; snapshot vào trace) — gom ~49 khoá
  store.py             # ScriptStore/GlossaryStore: load/save có file_lock, apply_fix() tôn trọng manual,
                       #   giữ mọi trường ASR, ghi trace
  trace.py             # translate_trace.jsonl {i, stage, before, after, model_used, ts}
  llm_io.py            # LLMResult, parse_items()/parse_fixes() trả None khi hỏng/cụt, phân loại lỗi
  plan/
    build.py           # file-mode, sample-mode, normalize, remap scenes
    render.py          # một hàm render(plan, profile="full"|"qa"|"batch", rows=None) thay 2 bản copy
  prompts/
    builder.py         # ghép system/user, khử trùng lặp, ngân sách ký tự
    rules/*.md|yaml    # luật phổ quát (nghĩa, văn phong, nhân vật, mixed, ancient)
  batch.py             # translate_batch, translate_safe (chỉ dịch lại câu thiếu), oneshot (hoặc bỏ)
  review/
    pass1.py
    batch.py           # review_batch + mask
    filters.py         # _filter_cosmetic_nmt_fixes, lọc lock
    runner.py          # lập lịch lô, song song, fallback có khoá/circuit breaker
  postpass/
    locks.py  numbers.py  stutter.py  halfcue.py
    style_polish.py    # điều khiển bằng data/lexicon + data/series/<bộ>/overrides.json
    pronoun.py         # PronounResolver (một nguồn quyết định) + detectors
  gates.py             # missing, coverage, ready_for_tts, qa metrics
data/
  lexicon/idiom_vi.json  slang_zh.json
  translate/seed_terms.json            # chỉ mục phổ quát
  series/<bộ>/bible.json  overrides.json
```

Thứ tự tách đề xuất (mỗi bước có test thuần trước khi chuyển): `llm_io` → `store` + `trace` → `postpass/*` → `plan/render` → `review/*` → `batch` → `prompts`.

---

## 7. Lộ trình phát triển

Ký hiệu công sức: **S** ≤ 1 ngày · **M** 2–5 ngày · **L** > 1 tuần. Cột "Điều kiện": **—** làm ngay; **G** cần golden set (M0) để chứng minh không tụt chất lượng; **D** phụ thuộc diarization (M2).

### 7.1 Ngắn hạn (1–2 tuần) — rủi ro thấp, không đổi văn phong

| # | Việc | Nối | Công | Điều kiện |
|---|---|---|---|---|
| N1 | C1: `review_batch` trả `None` khi rỗng/cụt/thiếu `fixes`; test mô phỏng | M0 | S | — |
| N2 | C2: `apply_fix` tôn trọng `manual` ở run_review, name pass, register policy, hardsub sync; test | M7/M8 | S | — |
| N3 | C3: sửa regex «cậu» (loại ngôi 3/danh từ), bỏ qua `manual`, tạm **chỉ cờ** thay vì thay máy | M5 | S | — |
| N4 | C9: khoá toàn cục + đổi preset tối đa 1 lần/job; phân loại lỗi theo status; 413 → chia lô (T6) | — | S | — |
| N5 | C6 + T17: sửa import `context_studio`, chép `kVietnamese.json`, log WARN khi import hỏng | M4 | S | — |
| N6 | C5: chuyển seed/luật riêng-phim sang `data/series/<bộ>/overrides.json`; script dọn kho series | M4/M6 | M | — (khuyến nghị chạy trước baseline M0) |
| N7 | T2: chốt định dạng số, sửa prompt + alts | — | S | — |
| N8 | Test thuần cho các hàm hậu kỳ (danh sách §4.3) — chụp hành vi hiện tại (characterization tests) | M0 | M | — |
| N9 | T18 + `ScriptStore` + `translate_trace.jsonl` + `LLMResult` (model thật, token, finish_reason) | M0/M1–M3 | M | — |
| N10 | Ghi TM từ endpoint sửa tay (`data/tm/<bộ>.jsonl`) — chỉ ghi, chưa dùng | M7 | S | — |

### 7.2 Trung hạn (2–6 tuần) — cần golden set

| # | Việc | Nối | Công | Điều kiện |
|---|---|---|---|---|
| T-a | Golden 3–5 tập + `scripts/eval_translation.py` + chế độ eval tuần tự/cassette; đo baseline **có/không** từng lớp hậu kỳ | M0 | M–L | người sửa golden |
| T-b | `PromptBuilder`: khử trùng lặp (T3), cắt plan theo lô/cảnh, luật → dữ liệu; đo token & chất lượng | M6/M7 | M | G |
| T-c | Lexicon thành ngữ v0 (gom ~10 cụm hiện có + bổ sung), detector `forbid` thay `CALQUE_RULES`/polish | M6 | M | G |
| T-d | `PronounResolver` một nguồn; chuyển style polish/register policy/postpass thành detector; mask "ràng buộc" thay "đóng băng" khi có cặp chắc chắn | M3/M5 | M–L | G |
| T-e | Bộ suy `to` tất định từ hô ngữ (师父/哥/tên…) — làm được trước diarization, cho phần câu có hô ngữ | M3 | M | G |
| T-f | Hardsub tự động + bỏ `rv` khi `zh` đổi + `asr_conf` vào script | M1 | M | G (đo idiom acc.) |
| T-g | Bible schema (`source`, `from/until`), `save_from` chỉ sau duyệt | M4 | M | — |
| T-h | Quyết định NMT primary vs LLM primary vs LLM-có-hint (`MT_ZH_VI_ROLE=hint` đã có sẵn) bằng số đo; thử `no_repeat=0` | — | S–M | G |
| T-i | QA gate dựa chỉ số (rv 100 %, `pron_mismatch`, `term_missing`, ngưỡng) | M5 | S | G (chọn ngưỡng) |
| T-j | Tách package `translate/` theo §6 (từng bước, có test) | — | L | sau N8 |

### 7.3 Dài hạn — phụ thuộc diarization

| # | Việc | Nối | Công | Điều kiện |
|---|---|---|---|---|
| D-a | Diarization CAM++ + giới theo cụm + vân giọng bộ | M2 | L | D, GPU slot |
| D-b | Người nghe theo cảnh (LLM) + `pron` từng câu chèn vào dịch **và** review | M3 | L | D, G |
| D-c | Gate `pronoun_qa` HOLD theo ngưỡng (park/hold sẵn có) | M5 | S | D, G |
| D-d | ref3 他/她 theo giới người được nhắc (ASR không phân biệt tā) | M2/M5 | M | D |
| D-e | TM dùng làm ví dụ theo cặp nhân vật + style theo cảnh | M7 | M | G, (D để lọc theo cặp) |
| D-f | Hàng duyệt M8 (gom cờ chuẩn hoá, nghe audio, ghi Bible/TM) | M8 | L | N2, N9, N10 |

**Thứ tự gợi ý:** N1–N5 (tuần 1) → N6–N10 (tuần 2) → T-a (baseline) → T-b/T-c/T-g song song → T-d/T-e → D-*.

---

## 8. Việc cần xác minh trên Windows

1. C6: chạy trong tiến trình V6 thật `python -c "import context_studio"` (hoặc xem log có dòng plan nhưng không bao giờ thấy lexicon `address_bank`) — có thể có `sys.path`/PYTHONPATH ngoài `run_v6.bat` mà pack không thấy.
2. C1: grep log V2/V6 tìm lô review có "sửa 0 câu" ngay sau dòng `[llm] rỗng — thử lại` hoặc JSON cụt.
3. REVIEW lỗi nghĩa (B) bị lọc: đếm "bỏ N fix cosmetic" trên vài job thật; lấy mẫu các fix bị bỏ để xem tỉ lệ là sửa nghĩa thật.
4. T10: UI có cho chạy name pass / pronoun pass / alts trong lúc job đang review không.
5. T15: tìm job có `heal_script_broken_numbers` gộp > 0 rồi được resume.
6. T14: chất lượng NMT với `no_repeat_ngram_size=2` vs 0 trên golden.
7. T1: các đường gọi `run_translate` ngoài pipeline auto (server.py:3422, 5961) có kiểm `rv` sau đó không.
8. Prompt caching ở Aurora/9router (ảnh hưởng ước lượng chi phí §4.4).
