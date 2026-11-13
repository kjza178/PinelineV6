# Thiết kế: Chất lượng dịch ZH→VI — xưng hô · văn phong · thành ngữ

_2026-11-11 · trạng thái: ĐỀ XUẤT (chưa code) · áp cho V6 (port V2/V4 sau khi đo đạt)_

## 1. Vấn đề

Ba loại lỗi chiếm phần lớn công sửa tay:

| Lỗi | Ví dụ | Gốc rễ |
|---|---|---|
| Xưng hô | anh–em nhảy sang tôi–ông trong cùng cảnh; “cô ta” thành “anh ta” | Tiếng Trung 我/你/他 không mang quan hệ; **hệ thống không biết AI nói với AI**; 他/她 đồng âm *tā* nên ASR không phân biệt được giới |
| Văn phong | đô thị mà nói giọng kiếm hiệp; thoại cứng, dịch từng chữ | Một `style_lock` cho cả phim; ví dụ mẫu không lấy từ bản đã duyệt |
| Thành ngữ / lóng | 没大没小 → «chưa lớn không nhỏ»; 脑子进水 → «não vào nước» | Chỉ có vài seed + luật viết tay trong prompt; ASR sai chữ thì thành ngữ hỏng từ gốc |

**Nguyên tắc thiết kế:** sửa ở ĐẦU VÀO (chữ Trung đúng, biết người nói/người nghe) trước khi sửa ở prompt; ràng buộc tất định + kiểm tất định thay cho “nhờ LLM nhớ”; mọi luật thành **dữ liệu** (từ điển, bible, bộ nhớ dịch) thay vì câu chữ trong prompt; **đo** trước và sau mỗi thay đổi.

## 2. Hiện trạng (đọc từ code V6, không sửa)

| Có rồi | Ở đâu | Thiếu |
|---|---|---|
| Plan: characters (giới, alias, self_ref), `pronouns` (who→to: self/call), `relationship_matrix`, `scenes`, `style_lock` | `translate.build_plan`, `_plan_block` | Không có người nói/người nghe theo câu → `pronouns` chỉ là gợi ý chung |
| Ledger xưng hô quét toàn file + khoá + QA “nhảy ngôi” (cửa sổ 6) | `pronoun_consistency.py` (“không cần diarization”) | Phát hiện nhảy ngôi nhưng không biết ngôi nào ĐÚNG |
| Policy danh xưng bậc (Môn chủ, Tiền bối… → «ngươi») | `pronoun_register_policy.py` | Luật cứng theo thể loại, không theo cặp nhân vật |
| Che đại từ khi REVIEW (#1+#3), vá câu đỏ | `review_batch`, `agent_pronoun_pass.py` | — |
| Khoá term cứng; seed vài 成语 | `ensure_hard_terms`, `_SEED…` | Thành ngữ phần lớn là luật lẻ trong `_meaning_rules`/`_style_rules` |
| Bảng dùng chung cả bộ (mọi mùa) | `series_glossary.py` → `data/series_glossary/<bộ>.json` | Chưa lưu quan hệ theo thời gian, chưa có bộ nhớ dịch |
| OCR hardsub để vá chữ ZH | `engines/asr/hardsub_asr.py` (chạy tay `--apply`) | Chưa tự chạy khi phim có hardsub |
| Ví dụ văn phong | `_style_exemplars(exemplars_pool)` | Lấy từ chính lô đang dịch, không từ câu người đã sửa |

## 3. Kiến trúc mới

```
download → ASR (FireRed) ─┐
                          ├─ M1 Nguồn ZH chuẩn (hardsub OCR / hotword / độ tin cậy)
video ────────────────────┘
        → M2 Người nói (diarization + vân giọng + giới)          [GPU nhẹ, ~1–2% thời lượng]
        → M3 Người nghe + ràng buộc xưng hô từng câu  (LLM theo cảnh + Series Bible M4)
        → Dịch nháp có ràng buộc (pipeline hiện tại + khối ràng buộc)
        → M6 Lớp thành ngữ/lóng (chú giải chèn theo câu)
        → REVIEW (pass1 + lô) — giữ như hiện tại
        → M5 Bộ kiểm tất định (xưng hô / thành ngữ / term) → vá đúng câu lệch
        → M8 Hàng duyệt (chỉ câu bị cờ) → người sửa
        → M7 Bộ nhớ dịch + cập nhật Bible  ──► tập/mùa sau kế thừa
M0 Bộ chuẩn + chỉ số: chạy sau mỗi thay đổi
```

Trong V6: M2, M3 là **Stage** mới giữa `asr` và `translate`; M5 là **Gate** sau REVIEW (lệch quá ngưỡng → HOLD `pronoun_qa`, dùng cơ chế park/hold đã có). Ghi trạng thái qua `state.job_state` như luật V6.

## 4. Mô-đun

### M0 — Bộ chuẩn & chỉ số (làm ĐẦU TIÊN)
- **Golden set:** 3–5 tập thuộc 3 thể loại (đô thị · tu tiên/cổ trang · mixed), sửa tay hoàn chỉnh: `vi`, người nói, người nghe, cặp xưng hô. Lưu `data/golden/<job>/script.gold.json`.
- **Chỉ số** (`scripts/eval_translation.py`):
  - *Pronoun pair accuracy*: % câu có (self, call) đúng theo cặp người nói→người nghe.
  - *Consistency*: số lần đổi cặp xưng hô giữa 2 nhân vật trong cùng cảnh khi bible không cho phép.
  - *Gender ref3*: % 他/她 → anh ta/cô ta… đúng.
  - *Idiom accuracy*: % thành ngữ trong golden được dịch đúng nghĩa (so với bảng chấp nhận).
  - *Edit rate*: tỉ lệ ký tự người phải sửa (chrF/TER so với bản gold) — tổng quát văn phong.
- Mọi thay đổi prompt/model/mô-đun phải kèm bảng chỉ số trước/sau.

### M1 — Nguồn ZH chuẩn
- **Hardsub tự động:** thêm bước dò nhanh (đã có `_classify_zone` 'sub' trong `ocr.py`): ≥ X% keyframe có chữ ở dải phụ đề → chạy `hardsub_asr --apply` tự động; chữ OCR thắng chữ ASR khi khớp thời gian, ASR giữ timing.
- **Hotword/context biasing:** tên nhân vật + term từ Bible đưa vào ASR nếu engine hỗ trợ (FireRed/FunASR có cơ chế hotword); không hỗ trợ → LLM sửa ASR có hướng dẫn bằng Bible (thay luật lẻ kiểu 娘→那).
- **Độ tin cậy câu:** lưu `asr_conf` mỗi câu (log FireRed đã có `confidence`) → dùng ở M8.

### M2 — Người nói (đòn bẩy lớn nhất)
- **Model:** 3D-Speaker **CAM++** / ERes2Net (ModelScope, mạnh với tiếng Trung, cùng hệ FunASR) — mặc định; pyannote 3.x làm phương án thay.
- **Luồng:** tách vocals (đã có Demucs) → embedding theo từng câu ASR (cắt theo start/end) → gom cụm (AHC/spectral, ngưỡng tự chỉnh theo số nhân vật trong plan) → `spk` + `spk_conf`.
- **Vân giọng xuyên tập/mùa:** kho `data/voiceprints/<bộ>.json` (centroid mỗi nhân vật). Tập mới: so cụm với kho trước khi gán tên → nhân vật giữ nhất quán cả bộ.
- **Gán tên cho cụm:** (1) khớp kho vân giọng; (2) tên được gọi ngay trước/sau lượt nói; (3) LLM theo cảnh với danh sách nhân vật; (4) không chắc → `spk="?"` + cờ.
- **Giới từ giọng:** phân loại giới (model nhỏ hoặc F0) theo cụm → điền `gender` nhân vật khi plan để `?`; dùng cho 他/她 (ref3) ở M3/M5.
- **Câu chồng tiếng / hét / nhạc:** `spk_conf` thấp → không ép, chỉ cờ.

### M3 — Người nghe & ràng buộc xưng hô từng câu
- **Người nghe (`to`)** — LLM theo cảnh (scene range từ plan), đầu vào: các câu có `spk`, nhân vật có mặt, lời gọi (vocative: 师父/哥/老公/名字…), lượt thoại. Ra `to`, `to_conf`. Luật tất định đi trước LLM: câu có vocative rõ → gán thẳng; độc thoại/nội tâm → `to="self"`; nói với đám đông → `to="group"`.
- **Ràng buộc:** tra Bible (M4) theo (spk, to, thời điểm) → `pron = {self, call, ref3?}`; chèn vào prompt dịch theo câu:
  `#42 [Lâm Phong→Tô Nhã | xưng: anh | gọi: em] 你怎么来了？`
- Chưa có cặp trong Bible → LLM đề xuất 1 lần cho cặp đó (có lý do: tuổi, vai vế, thân sơ, thái độ) → ghi Bible với `source="auto"` (người duyệt có thể chốt).

### M4 — Series Bible (mở rộng `series_glossary`)
```json
{
  "series": "…",
  "characters": [{"id": "lam_phong", "zh": "林风", "vi": "Lâm Phong", "gender": "nam",
                   "age_band": "20s", "aliases": ["小林"], "voiceprint": "lam_phong"}],
  "pairs": [{"spk": "lam_phong", "to": "to_nha", "self": "anh", "call": "em",
             "from": "S1E01", "until": null, "source": "human", "note": "người yêu"},
            {"spk": "lam_phong", "to": "to_nha", "self": "tôi", "call": "cô",
             "from": "S1E01", "until": "S1E07#c210", "source": "human", "note": "trước khi quen"}],
  "events": [{"at": "S1E07#c210", "what": "tỏ tình — đổi xưng hô"}],
  "terms": [], "idioms_override": [], "style": {"mode": "modern", "notes": "…"}
}
```
- Quan hệ **theo thời gian** (`from/until`) cho các bước ngoặt (thù→yêu, lộ thân phận, bái sư).
- `source`: `human` > `review` > `auto`; bản thấp không đè bản cao (giống `series_glossary.apply` hiện tại).

### M5 — Bộ kiểm tất định + vá đúng chỗ
- Tách đại từ ngôi 1/2/3 trong bản VI (danh sách `_SELF_FORMS`/`_CALL_FORMS` đã có, mở rộng) → so với `pron` của câu.
- Lệch → chỉ câu đó vào lô vá (prompt: “giữ nguyên nghĩa, chỉ đổi xưng hô sang …”). Tối đa 2 vòng; còn lệch → cờ M8.
- Kiểm thêm: thành ngữ có trong M6 nhưng bản VI không mang nghĩa đã duyệt (so khớp mềm với các bản chấp nhận); term khoá.
- **Gate V6:** % câu lệch sau vá > ngưỡng (vd 3%) → HOLD `pronoun_qa` (không TTS) — tái dùng park/hold.

### M6 — Lớp thành ngữ / tiếng lóng (dữ liệu, không luật prompt)
- **Từ điển:** `data/lexicon/chengyu.json` (bộ thành ngữ mở ~30k mục có giải nghĩa) + `data/lexicon/slang_zh.json` (网络用语 phim ngắn: 绿茶, 舔狗, 社死, 凡尔赛…).
- **Bảng tương đương Việt:** `data/lexicon/idiom_vi.json` — mỗi mục: `meaning`, `vi_hv` (Hán Việt), `vi_thuan` (thuần Việt / thành ngữ VN tương đương), `forbid` (bản dịch sai hay gặp):
  `对牛弹琴 → thuần: «đàn gảy tai trâu»`, `画蛇添足 → «vẽ rắn thêm chân»`, `没大没小 → «không biết trên dưới»; forbid: «chưa lớn không nhỏ»`.
- **Dò:** jieba + từ điển tuỳ chỉnh (khớp dài nhất) trên chữ ZH đã qua M1.
- **Chọn dạng theo style:** cổ trang/tu tiên → ưu tiên `vi_hv` khi phổ biến; đô thị → `vi_thuan`; luôn giữ nghĩa trước, hình ảnh sau.
- Chèn **chỉ cho câu có thành ngữ**: `成语: 对牛弹琴 = nói với kẻ không hiểu; gợi ý: «đàn gảy tai trâu»`.
- Chuyển các luật lẻ hiện có (吃醋, 脑子进水, 打天下, 穷酸, 眼馋…) từ `_meaning_rules` sang từ điển → prompt ngắn lại, dễ kiểm.
- Bảng tăng dần từ M8: người sửa một thành ngữ → đề xuất thêm mục.

### M7 — Bộ nhớ dịch (TM) & văn phong
- Mỗi câu người sửa/duyệt → `data/tm/<bộ>.jsonl` `{zh, vi, spk, to, style, job, ts}`.
- Khi dịch: lấy 3–5 câu TM gần nhất về nghĩa (BM25 trên chữ ZH; embedding sau nếu cần) **cùng cặp nhân vật** → làm ví dụ văn phong thay `exemplars_pool` hiện tại.
- Style theo **cảnh** (plan.scenes đã có `tone`) thay vì một `style_lock` cả phim cho phim mixed.

### M8 — Hàng duyệt có trọng tâm
- Câu bị cờ: `asr_conf` thấp · `spk/to_conf` thấp · M5 còn lệch · có thành ngữ chưa trong bảng · đổi cặp xưng hô lần đầu trong phim.
- UI: danh sách theo cảnh, nghe đoạn audio, sửa VI + chọn người nói/nghe + cặp xưng hô → ghi Bible/TM ngay.
- Mục tiêu: người chỉ xem **5–10% số câu**.

## 5. Dữ liệu mỗi câu (`script.json` mở rộng, tương thích ngược)

```json
{"i": 42, "start": 83.1, "end": 85.0, "zh": "你怎么来了？", "vi": "Sao em lại đến đây?",
 "asr_conf": 0.97, "zh_src": "ocr",
 "spk": "lam_phong", "spk_conf": 0.91, "to": "to_nha", "to_conf": 0.84,
 "pron": {"self": "anh", "call": "em"}, "idioms": [],
 "flags": [], "rv": true}
```
Trường mới đều tuỳ chọn → code cũ đọc `script.json` không vỡ.

## 6. Cấu hình (key mới, theo cơ chế `config.get` + cờ instance V6)

| Key | Mặc định | Ý nghĩa |
|---|---|---|
| `SPK_ENABLE` | `0` → `1` sau khi đạt M0 | Bật M2/M3 |
| `SPK_MODEL` | `campplus` | `campplus` / `eres2net` / `pyannote` |
| `SPK_DEVICE` | `cuda` | GPU dùng chung → chạy trong slot GPU hàng chờ |
| `HARDSUB_AUTO` | `1` | Tự OCR hardsub khi dò thấy |
| `IDIOM_LEXICON` | `data/lexicon` | Thư mục từ điển |
| `PRON_GATE_MAX` | `0.03` | Ngưỡng lệch xưng hô để HOLD |
| `TM_ENABLE` | `1` | Dùng bộ nhớ dịch làm ví dụ |

## 7. Chi phí & tài nguyên
- M2: CAM++ ~ vài trăm MB VRAM, nhanh (≪ thời lượng phim); chạy trong slot GPU hiện có, không song song ASR.
- M3: 1 lượt LLM theo cảnh (~ số cảnh × 1 request) — rẻ hơn REVIEW.
- M5: vá thường < 5% số câu → ít request.
- M6/M7: tra cứu cục bộ, không GPU.

## 8. Lộ trình (mỗi bước đo bằng M0 trước khi sang bước sau)

| Bước | Nội dung | Tiêu chí đạt |
|---|---|---|
| T0 | M0 golden 3–5 tập + `eval_translation.py`; đo baseline pipeline hiện tại | Có bảng baseline 5 chỉ số |
| T1 | M1 hardsub tự động + `asr_conf` | Idiom accuracy ↑ trên tập có hardsub; không tăng edit rate |
| T2 | M2 người nói + giới (chỉ ghi `spk`, chưa dùng cho dịch) | Độ đúng người nói ≥ 85% trên golden; ref3 ↑ |
| T3 | M3 + M4 (ràng buộc theo câu) + M5 (kiểm/vá) | Pronoun pair accuracy ≥ 90%; consistency lỗi ↓ ≥ 70% so baseline |
| T4 | M6 từ điển thành ngữ/lóng, dời luật lẻ khỏi prompt | Idiom accuracy ≥ 90%; prompt ngắn hơn |
| T5 | M7 TM + style theo cảnh; M8 hàng duyệt | Edit rate ↓; người duyệt ≤ 10% câu |
| T6 | Gate `pronoun_qa` bật trong pipeline; port V2/V4 | Chạy song song V2 ≥ 3 job thật, chỉ số không kém |

## 9. Rủi ro
- **Diarization sai** khi nhạc nền to / chồng tiếng / nhân vật giọng giống nhau → dùng vocals Demucs, `spk_conf`, không ép khi thấp, kho vân giọng giúp ổn định dần.
- **Người nghe mơ hồ** (nói với nhiều người, thoại qua điện thoại) → `to="group"`/cờ M8.
- **Quan hệ đổi giữa chừng** → `events` + `from/until` trong Bible; lần đầu đổi luôn đưa M8.
- **Ràng buộc quá cứng** làm câu mất tự nhiên (vd cảm xúc mạnh đổi «anh» → «mày» có chủ đích) → cho phép ngoại lệ có lý do từ REVIEW, ghi `note`, M8 duyệt.
- **GPU dùng chung V2** → M2 chạy trong slot GPU hàng chờ, `preset_switch` không đổi.
- **Bản quyền dữ liệu thành ngữ** → chọn bộ có giấy phép mở; bảng tương đương Việt tự xây.

## 10. Câu hỏi cần chủ dự án chốt
1. Golden set: chọn 3–5 tập nào (đại diện thể loại nào chiếm nhiều nhất)?
2. Ai sửa golden và hàng duyệt — một người cố định để văn phong đồng nhất?
3. Ưu tiên tốc độ hay chất lượng: chấp nhận thêm ~5–10% thời gian/job cho M2+M3?
4. Phim không có hardsub chiếm bao nhiêu % — quyết độ ưu tiên T1.
5. Có chấp nhận UI duyệt mới (M8) trong desktop V6 không, hay sửa trên `index3` hiện tại?
