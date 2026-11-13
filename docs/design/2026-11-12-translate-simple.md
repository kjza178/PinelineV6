# Luồng dịch simple (`translate_simple/`) — kiến trúc, khối, cấu hình, prompt mẫu

> **Bản sao từ V2 (port F6 2026-11-12).** Ở V6: code tại `engines/translate_simple/`, Stage/Gate tại `pipeline/stages/simple_translate.py` + `pipeline/gates/simple_translate_gate.py`, **mặc định BẬT** (rollback instance `"translate_pipeline": "legacy"`). Xem `docs/fixes/2026-11-12-port-simple-demucs.md`.


- **Ngày:** 2026-11-12 · **Trạng thái:** trên đĩa V2, **mặc định TẮT** (`TRANSLATE_PIPELINE` không đặt = luồng cũ y nguyên). Bật: `TRANSLATE_PIPELINE=simple` (env hoặc `pineline.config.json`) **sau restart :8801**.
- **Nguồn:** thiết kế chủ dự án duyệt (2026-11-12) dựa trên baseline `Z:\PinelineV6\docs\reviews\2026-11-11-translation-baseline.md` (xưng hô 47 %: plan có ĐÚNG cặp nhưng NMT không nhận plan + mask khoá đại từ ở review + regex mixed ép tôi–cậu; 3 đường vá câu trống thừa; 6 cơ chế xưng hô đá nhau), review kiến trúc `Z:\PinelineV6\docs\design\2026-11-11-translate-architecture-review.md`, thiết kế chất lượng `Z:\PinelineV6\docs\design\2026-11-11-translation-quality.md`.
- **Fix log:** `docs/fixes/2026-11-12-translate-simple.md`.

## 1. Vì sao

Luồng cũ (`run_translate` → `run_review` → cổng vá) có ~6 cơ chế cùng đụng xưng hô (NMT không biết plan, REVIEW mask đại từ, `_filter_cosmetic_nmt_fixes`, `apply_style_polish`/`apply_pronoun_postpass` regex, pronoun-QA GPT, `pronoun_register_policy`) và 3 đường vá câu trống (fill trong run_translate, review bước 0, server gọi lại). Kết quả: plan đúng mà bản cuối sai. Luồng simple bỏ hết, để **một** lần gọi LLM có đủ thông tin quyết định xưng hô, rồi **một** bộ kiểm tất định đo lại.

## 2. Luồng 6 bước

```
asr.json ─► (2) stage_draft:  Plan (cloud, build_plan / glossary.json có sẵn) ∥ NMT nháp (GPU, mt_zh_vi.draft_rows)
          ─► (3) stage_review: lô ~30 câu (+ ±4 câu ngữ cảnh chỉ đọc) → 1 gọi/lô:
                 sửa nghĩa + ÁP XƯNG HÔ theo cặp plan + tên/thuật ngữ + thành ngữ + dịch câu trống
                 → mỗi câu {i, vi, spk, to, conf}; câu trả hợp lệ mới rv=1
          ─► (4) post: normalize_vi_thousands + apply_hard_term_locks (KHÔNG regex đại từ)
                 validate: cờ empty / cjk / pronoun (lệch cặp plan spk→to khi conf ≥ 0.7) / name
          ─► (5) repair: chỉ câu có cờ, ≤ 2 lượt (prompt vá có trường «loi»)
          ─► (6) gate: done | park | hold  → server (done: cổng TTS cũ; park: waiting_review B2; hold: HOLD sau dịch)
```

Ghi: `script.json` (1–1 với ASR, giữ mọi trường; câu `manual` không bao giờ đổi `vi`), `script.display.json` + `sub.vi.srt` (CUT như luồng cũ), `glossary.json` (+ `style_lock.json`), **`trace.jsonl`** (mỗi lần đổi vi: bước, i, cũ→mới, lý do; cờ; lô lỗi; cổng), `manifest.stages.translate` qua `Manifest.update`.
Trường mới trên hàng script: `s_spk`, `s_to`, `s_conf` (người nói/nghe do REVIEW xác định), `s_flags` (cờ còn lại), `s_name_ok` (REVIEW xác nhận tên đúng).

## 3. Khối

| File | Việc |
|---|---|
| `translate_simple/cfg.py` | mọi khoá `SIMPLE_*` qua `config.get` (bảng mục 4) |
| `script_store.py` | một cửa đọc/ghi script.json (atomic `fileio.atomic_write_json`), dựng hàng từ ASR (giữ hàng cũ theo i, script post-split không remap), `set_vi` chặn manual + ghi `trace.jsonl` |
| `stage_draft.py` | plan ∥ NMT bằng 2 thread. Plan: dùng lại `glossary.json` (trừ `force`; `context_pack.skip_plan` luôn giữ) hoặc `translate.build_plan(..., before_local_llm=None)`; rồi `series_glossary.apply` + `save_from`, `ensure_hard_terms`, (tuỳ chọn) pronoun-analyze. NMT: câu chưa có vi, không manual; tắt/lỗi → nháp rỗng (review dịch mới). Flavor NMT (base/QT) theo glossary cũ / bảng bộ / base vì plan tập này chưa có lúc NMT chạy |
| `plan_view.py` | đọc plan: nhân vật + alias (zh/vi/aliases → tên VI), cặp xưng hô `who→to` (tách «anh / học trưởng»), cảnh theo `range`, thuật ngữ; **khối plan thu gọn theo lô** (nhân vật có mặt theo ZH + cảnh phủ lô, cặp liên quan, cảnh, thuật ngữ xuất hiện) |
| `lexicon.py` + `data/lexicon/idioms_zh_vi.json` | 51 mục seed (từ `_meaning_rules`/`_style_rules`/`_DEFAULT_*_TERMS` của translate.py + lỗi thành ngữ golden: 我去, 手下留人, 苟延残喘, 没大没小, 脑子进水, 吃醋, 打天下…) + terms chengyu/idiom/style của plan; dò chuỗi con trong ZH của lô → «GỢI Ý THÀNH NGỮ» (dạng nên dùng + dạng CẤM) |
| `stage_review.py` | prompt tiếng Việt + JSON schema `{"lines":[{i,vi,spk,to,conf}]}`; parse CHẶT (rỗng/cụt/fence mở/thiếu `lines` = lô lỗi; câu thiếu/vi rỗng/i ngoài lô bị bỏ → không rv); song song `SIMPLE_WORKERS`; câu còn chưa rv → thử lại 1 lượt lô ½ tuần tự |
| `post.py` | chỉ chuẩn số + khoá thuật ngữ type=lock (trace) |
| `validate.py` | bộ trích đại từ gọn (port `Z:\PinelineV6\scripts\eval_translation.py`: khớp cụm dài nhất, loại ngôi 3/danh từ «anh ấy, con đường…», danh xưng + Tên riêng); cờ `pronoun` chỉ khi plan có cặp spk→to và `conf ≥ SIMPLE_CONF_MIN` |
| `repair.py` | gom câu có cờ thành lô → prompt vá → post + kiểm lại đúng các câu đó; ≤2 lượt; lô lỗi giữ nguyên |
| `gate.py` | `done` / `park` (chưa soát, trống, còn chữ Hán, thiếu câu so ASR) / `hold` (soát đủ nhưng câu còn cờ > `max(SIMPLE_GATE_FLAG_MAX hoặc 1, 3 %)`) |
| `pipeline.py` | `run(work_dir, target, *, force, rereview, progress, stop_check) -> dict` (`status`, `gate`, `draft`, `review`, `repair`, `model`, `t_total`); `.stop` → «stopped by user» |

## 4. Cấu hình (không sửa config.json)

| Khoá | Mặc định | Ý nghĩa |
|---|---|---|
| `TRANSLATE_PIPELINE` | (trống) | `simple` = server dùng luồng mới |
| `SIMPLE_MODEL` | REVIEW model như luồng cũ (`_resolve_review_model`: queue_opts Aurora∥ → `AURORA_REVIEW_MODEL`, không thì `REVIEW_MODEL`) | model cloud cho review + vá |
| `SIMPLE_BATCH` | 30 | câu / lô |
| `SIMPLE_CTX` | 4 | câu ngữ cảnh chỉ đọc mỗi phía |
| `SIMPLE_WORKERS` | 8 | lô song song |
| `SIMPLE_REPAIR_ROUNDS` | 2 (trần 2) | lượt vá |
| `SIMPLE_CONF_MIN` | 0.7 | ngưỡng conf để kiểm xưng hô |
| `SIMPLE_GATE_FLAG_RATIO` / `SIMPLE_GATE_FLAG_MAX` | 0.03 / (trống) | ngưỡng câu còn cờ để qua cổng (tối thiểu 1) |
| `SIMPLE_MAX_TOKENS` / `SIMPLE_TIMEOUT` / `SIMPLE_TEMPERATURE` | 8192 / 300 / 0.2 | tham số gọi |
| `SIMPLE_PRONOUN_ANALYZE` | 0 | 1 = chạy `pronoun_consistency.ensure_analyze_before_translate` sau plan (thêm 1 cơ chế xưng hô — mặc định tắt) |
| `SIMPLE_NMT_WAIT_PLAN` | 0 | 1 = NMT chờ plan (đúng flavor QT cho phim cổ thuần, mất song song) |
| `SIMPLE_NMT_UNLOAD` | 1 | nhả model NMT sau nháp |
| `REVIEW_CLOUD_RETRY` / `REVIEW_CLOUD_BACKOFF` | như b2 | dùng chung qua `cloud_retry` |

## 5. Retry — một chính sách, KHÔNG fallback local

Mọi gọi cloud (review + vá) qua `cloud_retry.call_with_cloud_retry` (429/limit/5xx/timeout → backoff + cửa chờ chung + ngân sách chờ ≈577 s, `.stop` ≤2 s). Hết lượt → lô lỗi → câu không rv → cổng `park` → server park `waiting_review` (B2: tự thử lại sau `QUEUE_REVIEW_RETRY_SEC`, tối đa `QUEUE_REVIEW_RETRY_MAX`, quá → `review_failed` cần người). **Không** đổi preset local, kể cả `REVIEW_LOCAL_FALLBACK=1`. Lý do: (a) đường C9 switch preset là nguồn treo/VRAM tranh chấp và chất lượng local thấp hơn hẳn cho đúng việc xưng hô; (b) park B2 đã chứng minh chạy được và không mất dữ liệu (câu chưa soát giữ nháp NMT); (c) một chính sách dễ đo. Review cloud chết hẳn (không lô nào qua) → bỏ bước vá (không đốt thêm budget chờ).

## 6. Rẽ nhánh server (`server.py`, tối thiểu)

- `_translate_pipeline_simple()` đọc `TRANSLATE_PIPELINE`. `_simple_translate_run()` gọi `translate_simple.pipeline.run`. `_auto_translate_simple()` (trong `_auto_gpu`): `done` → gỡ `waiting_review/simple_park` → **đi tiếp cổng TTS cũ** (`translate_ready_for_tts` → Second Brain → HOLD → QA gate → TTS); `park` → `agent.held_at=waiting_review`, `simple_park=True`, `review_retries/review_retry_at` (B2); `hold` → `held_at=simple_gate`, `hold_after_translate=True` (người xem; `agent.qa_override` → cho qua).
- `_auto_gpu`: nhánh simple thay `run_translate` + khối «vá miss/gap» + khối «còn câu chưa rv → run_review/park»; QA gate trong nhánh simple không gọi `run_review` cũ lần 2 (`_rev` no-op).
- `_process_gpu`, `_run_stage("translate")`, `_translate_flow` (nút Dịch / Soát lại trên UI: `review_only` → `rereview=True`): simple → `pipeline.run`.
- `_queue_park_ready`: job `simple_park` chờ tới hạn `review_retry_at` kể cả khi unrev=0 (tránh lặp nóng khi chỉ còn câu trống).
- Không đổi: `_run_requested_review` (Agent yêu cầu rà), agent name/pronoun pass, Second Brain, TTS.

## 7. So với luồng cũ

| | Luồng cũ | simple |
|---|---|---|
| Xưng hô | NMT mù plan → REVIEW mask/khoá đại từ → regex polish/postpass → pronoun-QA GPT → register policy | REVIEW nhận cặp plan + tự xác định spk/to → bộ kiểm đo theo cặp → vá câu lệch |
| Câu trống | 3 đường (fill NMT sót, review bước 0, server gọi lại dịch) | đánh `dich=true` trong chính lô review; còn trống → vá; còn nữa → park |
| Gọi LLM / câu | plan + pass1 full file + review (chỉ trả fix) + pronoun-QA + fill | plan + 1 review (trả đủ câu) + vá câu cờ |
| Output review | chỉ câu cần sửa (`fixes`) — im lặng = «đúng» | ĐỦ mọi câu + spk/to/conf → đo được; câu thiếu = chưa soát |
| Retry | cloud_retry + C9 local fallback + B1 thử lại + B2 park | cloud_retry → lô ½ thử lại → park |
| Trace | log rải rác | `trace.jsonl` từng câu |
| Bỏ qua | pass1 nonsense/half-cue, `apply_style_polish`, `apply_pronoun_postpass`, `run_post_translate_consistency`, `heal_script_broken_numbers`, `fix_hang_job`, halfcue merge | (có thể thêm lại sau nếu A/B cho thấy cần) |

Chi phí token: lô 30 câu trả đủ 30 dòng JSON (~1,5–2,5k token ra / lô) — nhiều hơn REVIEW cũ (chỉ trả fix) nhưng bỏ pass1 + pronoun-QA. Cần đo thật (golden A/B).

## 8. Prompt review mẫu (cửa sổ golden `32da3d8f225b`, câu 2972–3001)

Sinh bằng `stage_review.build_prompt` trên `glossary.json` + `script.json` THẬT của job (chỉ đọc). Cột `nhap` ở đây là vi hiện có (đã qua review cũ) đứng thay cho nháp NMT; câu 2977 cố ý để trống để thấy `dich=true`. Lưu ý quan sát được: plan **không có** cặp Hà Hải Khôn→Mị Nương (golden: tôi/cô) nên cảnh s1 chỉ dựa vào dòng «xưng hô cảnh» — chất lượng xưng hô của simple phụ thuộc trực tiếp độ đủ cặp trong plan.

```text
=== SYSTEM ===
Bạn là biên tập viên lồng tiếng phim Trung → Việt. Với MỖI câu trong DANH SÁCH, trả bản tiếng Việt CUỐI CÙNG để đọc lồng tiếng.
Mỗi câu có: i; zh = câu gốc (ASR, có thể nghe nhầm chữ đồng âm); nhap = bản nháp máy NMT (có thể sai nghĩa, sai xưng hô; rỗng = chưa có); sec = thời lượng (giây); dich=true = câu CHƯA có bản dịch, phải dịch mới từ zh; khoa=true = câu người đã sửa tay → trả y nguyên nhap.
Làm trong MỘT lượt, theo thứ tự ưu tiên:
1. NGHĨA: sửa chỗ nháp sai nghĩa so với zh (đảo chủ ngữ, hiểu ngược, dịch từng chữ vô nghĩa). Nháp đã đúng nghĩa và tự nhiên → GIỮ, không đổi chữ cho «hay hơn».
2. XƯNG HÔ: xác định người nói (spk) và người nghe (to) từ ngữ cảnh, cảnh và nhân vật có mặt. Nếu cặp spk→to có trong bảng XƯNG HÔ BẮT BUỘC → câu dùng ĐÚNG tự xưng / cách gọi của cặp đó (sửa cả khi nháp dùng đại từ khác). Không có cặp → theo thể loại + quan hệ, giữ nhất quán với các câu trước của cùng cặp. Độc thoại / nói với đám đông → to="*". Không đoán được → spk="?" và conf thấp.
3. TÊN / THUẬT NGỮ: tên nhân vật và thuật ngữ đúng chữ VI trong bảng; không phiên âm khác, không dịch nghĩa tên.
4. THÀNH NGỮ / LÓNG: dịch đúng Ý tiếng Việt thông dụng (xem GỢI Ý); CẤM dịch từng chữ, CẤM dạng bị cấm.
5. VĂN PHONG: thoại tự nhiên, ngắn gọn như phim lồng tiếng Việt, đúng thể loại; ~5 âm tiết/giây theo sec; số viết bằng chữ số Ả Rập (万=10 000, 亿=100 triệu); không thêm tình tiết, không giải thích.
Trả về DUY NHẤT một JSON object:
{"lines":[{"i":<int>,"vi":"<bản cuối>","spk":"<tên VI người nói | ?>","to":"<tên VI người nghe | * | ?>","conf":<0..1 = độ chắc về spk/to>}]}
Đủ MỌI i trong DANH SÁCH (kể cả câu giữ nguyên), đúng thứ tự, không thêm i ngoài DANH SÁCH, không trả câu NGỮ CẢNH. Không markdown, không chú thích.

=== USER ===
PHIM: thể loại: Đô thị dị năng, linh khí phục tô, huyền huyễn tu tiên, hành động chiến đấu, tiến hóa siêu phàm, dị tộc và thế giới song song; kết hợp đời sống đại học, tổ chức siêu phàm, tranh đấu quyền lực, tình cảm hài hước và yếu tố hệ thống. | văn phong: Văn phong hiện đại, trẻ trung, hài hước, có nhiều độc thoại nội tâm, cà khịa, tiếng lóng mạng và lời thoại tự tin, bá đạo. Khi chiến đấu sử dụng gi
STYLE: mode=mixed — Đô thị dị năng / tinh cầu / hệ thống + võ chiến (mixed) — CÓ CỔ PHONG: xưng hô đời thường = tôi·anh·em·mày·tao (CẤM mặc định tại hạ/bổn tọa/trẫm/bệ hạ). BẮT BUỘC giữ sắc CỔ PHONG Hán Việt cho: chiêu thức, khí tức, ấn ký, cấp bậc, tinh cầu, danh hiệu chiến; vài 成语 hay dùng (vd «Tiên hạ thủ vi cường») — không nhồi thành ngữ hiếm. Danh phận (công chúa, thiếu gia…) giữ khi là chức vị. «嘲讽» / «嘲讽看»: dịch theo ngữ cảnh (không khóa cứng); CẤM «nhạo báng xem»; đối chiếu câu trước/sau nếu ASR lạ.
GHI CHÚ XƯNG HÔ: Bối cảnh đô thị hiện đại là chủ đạo, tuyệt đối không mặc định Ta-Ngươi. Bạn bè, đồng đội và người cùng cấp ưu tiên tôi-cậu; người thân, người yêu và học trưởng-học muội sử dụng anh-em; quan hệ chị gái-hậu bối sử dụng chị-cậu hoặc chị-em theo thân phận. Quan hệ tiền bối-hậu bối trong giới tu luyện sử
NHÂN VẬT CÓ MẶT / LIÊN QUAN:
  - 顾青 = Cố Thanh · giới nam · Nhân vật chính, 21 tuổi, sinh viên Hải Thành Đại Học và thành viên Dạ Đình. Ban đầu là siê
  - 顾瑶 = Cố Dao · giới nữ · Em gái ruột của Cố Thanh, nữ sinh trung học chuẩn bị thi đại học. Hoạt bát, nghịch ngợm, t
  - 夏舒然 = Hạ Thư Nhiên · giới nữ · Học muội của Cố Thanh, nữ chính tình cảm, người Hạ gia, gia đình kinh doanh võ quán. Là ng
  - 柳知意 = Liễu Tri Ý · giới nữ · Đội trưởng chính thức của Dạ Đình, tiền bối và đồng minh quan trọng của Cố Thanh. Là siêu 
  - 神明少女 = Thần Minh Thiếu Nữ · giới nữ · Tồn tại thần bí có hình dạng thiếu nữ, thường xuất hiện cùng màn sương xám. Sở hữu sức mạn
  - 魔镜 = Ma Kính · giới không rõ · Pháp bảo có ý thức, chứa tàn hồn của một cường giả cổ đại từng được xưng là Diệt Quốc Ma N
  - 何海坤 = Hà Hải Khôn · giới nam · Đội trưởng Dạ Đình, tiền bối và người trực tiếp tiếp cận Cố Thanh sau cuộc tuyển chọn Phó 
  - 顾父 = Bố Cố Thanh · giới nam · Bố ruột của Cố Thanh và Cố Dao. Tính cách thân thiện, gần gũi, quan tâm con cái và yêu quý
  - 媚娘 = Mị Nương · giới nữ · Nhân vật có địa vị trong Hồ Nữ Nhất Tộc, liên hệ với Hà Hải Khôn khi bàn bạc về việc chiêu
  - 杨云彤 = Dương Vân Đồng · giới không rõ · Nhân vật bí ẩn xuất hiện tại trung tâm thương mại, có liên hệ với một luồng sức mạnh hắc m
  - 顾母 = Mẹ Cố Thanh · giới nữ · Mẹ ruột của Cố Thanh và Cố Dao. Nhiệt tình, quan tâm con cái, đặc biệt yêu quý Hạ Thư Nhiê
  - 胡二娘 = Hồ Nhị Nương · giới nữ · Một hồ nữ thuộc Hồ Nữ Nhất Tộc, được Hà Hải Khôn nhắc đến trong cuộc trò chuyện về nhiệm v
XƯNG HÔ BẮT BUỘC (người nói → người nghe: tự xưng / gọi người nghe):
  - Cố Thanh → Cố Dao: anh / em
  - Cố Dao → Cố Thanh: em / anh
  - Cố Thanh → Hạ Thư Nhiên: anh / em
  - Hạ Thư Nhiên → Cố Thanh: em / anh
  - Cố Dao → Hạ Thư Nhiên: em / chị
  - Hạ Thư Nhiên → Cố Dao: chị / em
  - Cố Thanh → Liễu Tri Ý: em / chị
  - Liễu Tri Ý → Cố Thanh: chị / cậu
  - Hạ Thư Nhiên → Liễu Tri Ý: em / chị
  - Liễu Tri Ý → Hạ Thư Nhiên: chị / em
  - Cố Thanh → Bố Cố Thanh: con / bố
  - Cố Thanh → Mẹ Cố Thanh: con / mẹ
  - Cố Dao → Bố Cố Thanh: con / bố
  - Cố Dao → Mẹ Cố Thanh: con / mẹ
  - Bố Cố Thanh → Cố Thanh: bố / con
  - Mẹ Cố Thanh → Cố Thanh: mẹ / con
  - Bố Cố Thanh → Cố Dao: bố / con
  - Mẹ Cố Thanh → Cố Dao: mẹ / con
  - Cố Thanh → Hà Hải Khôn: tôi / đội trưởng
  - Hà Hải Khôn → Cố Thanh: tôi / cậu
  - Cố Thanh → Ma Kính: tôi / ma kính
  - Ma Kính → Cố Thanh: ta / chủ nhân
  - Cố Thanh → Thần Minh Thiếu Nữ: tôi / ngài
  - Ma Kính → Thần Minh Thiếu Nữ: tôi / ngài
CẢNH:
  - [câu 2755→2999] Văn phòng Dạ Đình và căn cứ bí mật của Hồ Nữ Nhất Tộc. · có mặt: Cố Thanh, Hà Hải Khôn, Mị Nương, Hồ Nhị Nương · tone: Đấu trí, giả vờ hợp tác, hài hước đen tối, âm mưu và châm biếm. · xưng hô cảnh: Hà Hải Khôn gọi Cố Thanh là cậu; Cố Thanh gọi Hà Hải Khôn là Hà đội; với Mị Nương dùng cách xưng hô cấp dưới-bề trên.
  - [câu 2999→3435] Ký túc xá, trung tâm thương mại, nhà Cố Thanh và đấu trường Dạ Đình. · có mặt: Cố Thanh, Hạ Thư Nhiên, Cố Dao, Bố Cố Thanh, Mẹ Cố Thanh, Liễu Tri Ý, Dương Vân Đồng, Ma Kính · tone: Đời thường, lãng mạn nhẹ, hài hước, bí ẩn và mở ra tình tiết mới. · xưng hô cảnh: Cố Thanh-Hạ Thư Nhiên dùng anh-em; Cố Thanh-Cố Dao dùng anh-em; Cố Thanh-Liễu Tri Ý dùng em-chị; Ma Kính gọi Cố Thanh là chủ nhân.
TÊN / THUẬT NGỮ (dùng đúng chữ VI):
  - 夜庭 = Dạ Đình
  - 狐媚 = Hồ Mị

GỢI Ý THÀNH NGỮ / LÓNG (có trong lô):
  - 贪得无厌 → «tham lam vô độ / tham lam không đáy»
  - 将计就计 → «tương kế tựu kế»
  - 沉迷美色 → «sa vào mỹ sắc / mê đắm sắc đẹp» (CẤM: «thầm mê»)

NGỮ CẢNH TRƯỚC (chỉ đọc · i | zh | vi):
  2968 | 叶婷现有仪器测不出她身体有什么特别。 | Diệp Đình hiện có thiết bị không thể đo lường được cơ thể nàng có gì đặc biệt.
  2969 | 以他今天表现出的潜力， | Với tiềm năng mà hắn thể hiện hôm nay,
  2970 | 日后真觉醒出强能力。 | Ngày sau thật sự thức tỉnh ra năng lực mạnh.
  2971 | 房抵夜庭执事。 | Phòng tới Dạ Đình chấp sự.

DANH SÁCH (JSON):
[{"i": 2972, "zh": "媚娘。", "nhap": "Mị Nương.", "sec": 0.6}, {"i": 2973, "zh": "我给你们策反了个未来执事。", "nhap": "Tôi đã lôi kéo các cậu một chấp sự tương lai.", "sec": 1.7}, {"i": 2974, "zh": "这值多少报酬？", "nhap": "Cái này đáng giá bao nhiêu?", "sec": 1.0}, {"i": 2975, "zh": "要真这样，", "nhap": "Nếu quả đúng như thế.", "sec": 0.9}, {"i": 2976, "zh": "好处少不了你。", "nhap": "Lợi ích không thể thiếu cậu.", "sec": 1.3}, {"i": 2977, "zh": "你想要什么我们给什么。", "nhap": "", "sec": 1.8, "dich": true}, {"i": 2978, "zh": "除圣女外，", "nhap": "Thánh nữ là ngoại lệ.", "sec": 0.8}, {"i": 2979, "zh": "族里任何一只美狐娘随你折腾。", "nhap": "Bất kỳ mỹ hồ nương nào trong tộc đều tùy cậu giày vò.", "sec": 2.2}, {"i": 2980, "zh": "我只要超凡资源很多很多。", "nhap": "Tôi chỉ cần tài nguyên siêu phàm rất nhiều.", "sec": 1.9}, {"i": 2981, "zh": "我感觉离进化下一阶只剩一步没问题。", "nhap": "Tôi cảm thấy chỉ còn một bước nữa là đến bậc tiếp theo của tiến hóa không thành vấn đề.", "sec": 2.6}, {"i": 2982, "zh": "你找时间带他来。", "nhap": "Cậu tìm thời gian đưa hắn đến đây.", "sec": 1.3}, {"i": 2983, "zh": "让圣女探探他身体。", "nhap": "Để Thánh nữ thăm dò thân thể hắn.", "sec": 1.5}, {"i": 2984, "zh": "要真如你所说。", "nhap": "Nếu đúng như lời cậu nói.", "sec": 1.3}, {"i": 2985, "zh": "资源少不了。", "nhap": "Tài nguyên không thể thiếu.", "sec": 1.0}, {"i": 2986, "zh": "真当我是傻子？", "nhap": "Thật sự coi tôi là kẻ ngốc sao?", "sec": 1.2}, {"i": 2987, "zh": "等着。", "nhap": "Đợi đấy.", "sec": 0.6}, {"i": 2988, "zh": "三个月内我把你们这群贪得无厌的狐媚整窝端了。", "nhap": "Trong vòng ba tháng, tôi đã tiêu diệt sạch lũ Hồ Mị tham lam vô độ các cậu rồi.", "sec": 3.1}, {"i": 2989, "zh": "全杀了。", "nhap": "Giết sạch rồi.", "sec": 0.8}, {"i": 2990, "zh": "一直不留。", "nhap": "Vẫn không giữ lại.", "sec": 0.9}, {"i": 2991, "zh": "魔镜。", "nhap": "Ma Kính.", "sec": 0.6}, {"i": 2992, "zh": "我刚刚将计就计，", "nhap": "Tôi vừa mới tương kế tựu kế,", "sec": 0.9}, {"i": 2993, "zh": "假装被何海坤忽悠，", "nhap": "Giả vờ bị Hà Hải Khôn lừa gạt,", "sec": 1.0}, {"i": 2994, "zh": "同意跟他一起当双面间谍。", "nhap": "Đồng ý cùng hắn làm gián điệp hai mặt.", "sec": 1.4}, {"i": 2995, "zh": "后续会不会有危险？", "nhap": "Sau này liệu có nguy hiểm không?", "sec": 1.1}, {"i": 2996, "zh": "有沉迷美色的危险。", "nhap": "Có nguy hiểm đắm chìm trong sắc đẹp.", "sec": 1.6}, {"i": 2997, "zh": "那我就放心了。", "nhap": "Vậy thì tôi yên tâm rồi.", "sec": 0.9}, {"i": 2998, "zh": "沉迷美色。", "nhap": "Thầm mê sắc đẹp.", "sec": 0.9}, {"i": 2999, "zh": "那叫福利。", "nhap": "Đó gọi là phúc lợi.", "sec": 0.8}, {"i": 3000, "zh": "你怎么这么快一直在楼下等我，", "nhap": "Sao cậu lại đợi tôi ở dưới lầu nhanh như vậy", "sec": 1.6}, {"i": 3001, "zh": "没有看你微信才下来的，", "nhap": "Không xem WeChat của cậu mới xuống được,", "sec": 1.5}]

NGỮ CẢNH SAU (chỉ đọc · i | zh | nháp):
  3002 | 就是我用了一种超快下楼方式， | Chính là tôi đã dùng một phương thức xuống lầu cực nhanh
  3003 | 从阳台一跃而下， | Nhảy thẳng từ ban công xuống,
  3004 | 光速下楼， | Lao xuống lầu với tốc độ ánh sáng,
  3005 | 六楼二十多米， | Từ tầng sáu, cao hơn 20 mét,
```

Prompt vá (`REPAIR_PROMPT` trong `stage_review.py`) dùng cùng khối plan + mỗi câu thêm `"loi": ["pronoun: Lâm Phàm→Tô Tình phải «anh»–«em», vi có «Tôi, cậu»"]` và `spk/to` hiện tại.

> **Cập nhật review 2026-11-12** (`docs/reviews/2026-11-12-translate-simple-review.md`): dòng cặp nay in `A → B: tự xưng «x» hoặc «y» · gọi «z» (ghi chú cặp ≤100 ký tự)` thay cho `x / z` (mơ hồ khi nhiều dạng); system prompt thêm mục «CẶP MỚI» + khoá tuỳ chọn `new_pairs` → `plan_suggestions.json` (không chèn plan); gợi ý lexicon có điều kiện `re` (vd `我去` chỉ khi là thán từ đứng riêng).

## 9. Chỉ kiểm được khi chạy thật

- Model thật có trả ĐỦ câu + JSON trọn cho lô 30 không (nếu hay thiếu → hạ `SIMPLE_BATCH`), độ chính xác `spk/to/conf`, tỉ lệ cờ sau vá (ngưỡng hold 3 % có hợp lý không).
- Điểm golden: `Z:\PinelineV6\scripts\golden_ab.py --variants base,simple --yes` khi V2 rảnh (GPU + GPT).
- Thời gian/token thật so với luồng cũ; NMT ∥ plan upload trên máy thật (VRAM).
