# Thí nghiệm: xác minh người nói bằng giọng (thay diarization) — 2026-11-12

_Agent C (Claude), user vắng — tự quyết, giả định ghi rõ. Thiết kế gốc: `docs/design/2026-11-11-translation-quality.md` (M2, CAM++)._
Script: `scratch/voice_exp/` (chạy lại được). Số liệu thô: `scratch/voice_exp/results.json`, `results_stdout.txt`, `confound_stdout.txt`.

## TL;DR
- **Không chạy được CAM++**: VM (2 lõi, 3 GB RAM) chỉ có `numpy` + `onnxruntime`; không torch/modelscope/funasr/speechbrain/librosa/scipy;
  **không có mạng** (pip/HF/ModelScope đều 403/không phân giải), và không tìm thấy model speaker-embedding nào (*.onnx, campplus, ecapa, wespeaker…) trong các thư mục mount.
  → Đo bằng **phương án dự phòng thuần numpy** (MFCC stats + GMM-UBM supervector + F0 YIN). Đây là **cận dưới**, *không* đại diện cho CAM++.
- Cận dưới **không đủ dùng**: MFCC-stats K=5 đạt 73.8% top-1 (≥2 ứng viên/cảnh) so với baseline tần suất 63.6% — nhưng **phần lớn lợi thế là nhiễu thời gian/nhạc nền**:
  khi mẫu buộc phải cách câu thử ≥60 s, accuracy rơi còn **63.3% ≈ baseline 63.0%**. EER cặp câu 32–40% (CAM++ công bố ~1–5% trên dữ liệu sạch).
- Giới tính từ giọng: ngưỡng F0 tuyệt đối **thất bại** (58% theo nhân vật; nam lồng tiếng hoạt hình/3D có F0 trung vị 170–360 Hz). Phân loại học được (leave-one-job-out) = đoán toàn nam.
  **F0 tương đối trong cảnh** (ai cao hơn là nữ) đúng **7/7** cặp nam–nữ — tín hiệu dùng được, mẫu nhỏ.
- **Kết luận: CHƯA đưa vào luồng simple.** Bước kế tiếp bắt buộc: chạy `embed_campplus.py` trên máy Windows (có modelscope) với cùng harness; tiêu chí nhận ở cuối.

## Dữ liệu
- Golden **silver** (nhãn do agent suy từ chữ, chưa người duyệt) — 4 job, 567 câu, 519 câu có `spk` ≠ `?`. Mốc thời gian từ `D:\PinelineV2_work\<job>\script.json` (khớp `i`+`zh`: 567/567).
- Audio: `audio.wav` gốc của job (16 kHz mono, **có nhạc nền/SFX** — **không có vocals Demucs** trong work dir). Chỉ cắt cửa sổ golden ±1 s: 3.5 / 6.9 / 7.2 / 5.0 phút (`scratch/voice_exp/data/*.wav`).
- Độ dài câu có nhãn: <1 s: 133 · 1–2 s: 251 · >2 s: 135. Câu ≥1.5 s (đủ làm mẫu): 248.
- Giả định: "nhân vật cùng cảnh" = tập `spk` gold của `scene` đó (oracle cast; thực tế lấy từ plan/cảnh). `canhsat` (nhiều cảnh sát?) và `dongdoi` coi là 1 người nói — làm giảm điểm giọng.

## Phương pháp
1. `prep.py` — cắt audio + bảng câu. `feats.py` — mỗi câu: (a) **st** = mean+std MFCC19+Δ (chuẩn hoá toàn cửa sổ, VAD năng lượng); (b) **sv** = siêu vectơ MAP GMM-UBM 64 thành phần (UBM học không nhãn trên chính audio job); (c) F0 trung vị (YIN đơn giản 70–450 Hz).
2. `exp.py` — mỗi nhân vật chọn ngẫu nhiên **K=3/5** câu ≥1.5 s làm mẫu "chắc" (50 seed); câu còn lại có gold = "khó". Mẫu = trung bình embedding; gán = cosine top-1 **trong nhân vật cùng cảnh**. Báo cáo trên câu có ≥2 ứng viên (câu cảnh 1 người: tầm thường, loại).
   Ngưỡng "không chắc" = hiệu điểm top1−top2 < t. Baseline: **freq** (người nói nhiều nhất trong cảnh — oracle đếm gold, lạc quan), **prev** (giống người nói câu trước cùng cảnh — dùng nhãn thật, = giả lập câu trước là câu chắc), **alt** (luân phiên: khác câu trước).
3. `check_confound.py` — như trên nhưng mẫu chỉ lấy từ câu cách câu thử ≥ GAP giây (đo phần "nhận cảnh/nhạc" thay vì nhận giọng).

## Kết quả gán người nói (gộp 4 job, K=5, 50 seed, ~423 câu khó/seed)
| Phương pháp | Acc | <1 s | 1–2 s | >2 s | 2 ứng viên | ≥3 ứng viên | cùng giới | khác giới |
|---|---|---|---|---|---|---|---|---|
| freq (baseline) | 63.6 | 59.5 | 65.9 | 64.2 | 65.4 | 60.5 | 66.2 | 62.5 |
| alt (luân phiên) | 27.9 | 40.5 | 24.5 | 16.4 | 31.2 | 22.2 | — | — |
| prev (giống câu trước, oracle) | 71.4 | 57.3 | 75.6 | 83.0 | 70.0 | 73.8 | 75.3 | 69.8 |
| giọng st (MFCC stats) | 73.8 | 75.2 | 75.0 | 68.7 | 80.1 | 63.2 | 67.5 | 76.4 |
| giọng sv (GMM-UBM) | 64.5 | 61.3 | 65.8 | 66.2 | 67.1 | 60.2 | 67.7 | 63.2 |
| st + prior câu trước (+0.2) | 78.3 | 76.2 | 80.0 | 77.3 | 82.6 | 70.9 | 75.1 | 79.6 |

K=3 thấp hơn K=5 ~1–3 điểm (st 71.6/73.8). Theo job (st K5): 84.0 / 67.8 / 71.4 / 69.1.
**Luân phiên lượt sai bét (28%)**: câu ASR bị cắt nhỏ, cùng một người nói nhiều cue liền nhau → giả thiết "đổi lượt mỗi câu" là sai; "giống câu trước" mới là prior đúng (71%).

**Kiểm nhiễu thời gian** (st K5, mẫu cách câu thử ≥ GAP): GAP 0 s 73.8 → 30 s 68.8 → **60 s 63.3** (freq tương ứng 60.4/61.3/63.0). sv: 65.9 → 59.6 → 59.5.
→ MFCC thuần đang nhận *đoạn phim* (nhạc/không gian), không phải *giọng*. Con số 74% ở trên **không dùng để quyết**.

**Ngưỡng margin (st K5)**: t=0.1 → phủ 86%, acc 77.7% (14% "không chắc"); t=0.2 → phủ 74%, acc 81.3%. sv: t=0.1 → phủ 22%, acc 84.6%. Không có ngưỡng nào cho ≥90% acc với độ phủ đáng kể.
**EER cặp câu cùng job** (≥1 s): st 32–40%, sv 35–47%.

## Giới tính
- F0 tuyệt đối (ngưỡng 165 Hz): theo câu 59.5% (m→f 117, f→m 3), theo nhân vật **11/19 = 58%**. Nam bị đoán nữ: Cố Thanh 184, Long Ngạo Thiên 207, LKT 363, Ngao Hoàng 216, Trần Phong 201, Đằng Xà 302 Hz. Job người đóng (d1e00) thì đúng 4/4 (nam ~121–127 Hz).
  Nguyên nhân: giọng lồng tiếng hoạt hình cao/gằn, nhạc nền làm YIN bắt nhầm, lỗi bội tần.
- Logistic (MFCC+logF0) leave-one-job-out: 68% theo nhân vật = đúng bằng tỉ lệ đa số (đoán gần như toàn nam; 5/5 nhân vật nữ sai).
- **F0 tương đối trong cảnh** (cặp nam–nữ đã biết có trong cảnh, nữ = F0 trung vị cao hơn): **7/7** (chênh 70–280 Hz). Dùng được như *tín hiệu phụ* phân biệt 2 người khác giới, không dùng để gán giới tuyệt đối.

## Thời gian CPU (VM 2 lõi, nice, OMP=2)
- Toàn bộ đặc trưng numpy: **~0.9–1.0 s CPU / phút audio** (MFCC 0.03, UBM 0.6–0.75, embed+F0 0.2). Cả phim 120 phút ≈ 2 phút CPU. `exp.py` 50 seed: < 1 phút.
- CAM++: **chưa đo** (không có model). Ước tính theo cỡ model (~7M tham số) cỡ vài giây CPU/phút thoại — cần đo bằng `embed_campplus.py` (ghi `data/cam_timing.json`).

## Khuyến nghị
1. **Không đưa vào luồng simple** ở trạng thái hiện tại (chỉ có cận dưới; tín hiệu giọng thật ≈ 0 sau khi khử nhiễu thời gian).
2. Chạy CAM++ trên máy Windows: `python scratch\voice_exp\embed_campplus.py` → `python exp.py` → `python check_confound.py cam` (script viết sẵn, **chưa chạy thử**; API `output_emb` có thể khác theo bản modelscope).
   Nên chạy thêm trên **vocals Demucs** (sửa `prep.py --work` trỏ tới bản tách giọng) vì nhạc nền là nhiễu lớn nhất.
3. **Tiêu chí nhận** (đề xuất): với GAP ≥60 s, K=5: acc top-1 ≥ 85% tổng và ≥ 75% câu <1 s, và tại ngưỡng margin chọn được: acc ≥ 92% với độ phủ ≥ 60%. EER cặp câu ≤ 15%.
4. Nếu đạt: dùng như **bộ xác nhận**, không như bộ gán: chỉ ghi đè `spk` khi margin ≥ t (t chọn trên golden để acc ≥ 92%), còn lại giữ người nói từ plan/LLM; kết hợp prior "giống câu trước" (+0.1–0.2 điểm cosine) — prior này một mình đã 71%.
   Giới tính: chỉ dùng F0/embedding **tương đối trong cảnh**; giới tuyệt đối lấy từ characters/plan.
5. Ngưỡng ban đầu đề xuất (cho CAM++, cần hiệu chỉnh lại): margin cosine top1−top2 ≥ 0.10; mẫu tối thiểu 3 câu ≥1.5 s/nhân vật; câu <0.8 s không xác minh.

## Rủi ro
- Golden là **silver** và nhỏ (519 câu, 19 nhân vật, 4 cửa sổ ~3–7 phút) → sai số ±3–5 điểm; nhãn `canhsat`/`dongdoi` gộp nhiều người.
- Cảnh lấy oracle từ gold; thực tế cast cảnh từ plan có thể thiếu/thừa người → acc thấp hơn.
- Nhiễu thời gian (nhạc/không gian) dễ làm số đẹp giả — **luôn báo kèm `check_confound`**.
- Diễn viên lồng nhiều vai / giọng biến đổi (gằn, hét, giả giọng) làm embedding lệch; nhân vật ít câu không có mẫu (100/21150 lượt thử không có mẫu).
- Câu <1 s (26% câu) là vùng khó nhất cho mọi phương pháp giọng.
