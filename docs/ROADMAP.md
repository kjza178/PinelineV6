# ROADMAP — Pineline V6 thành sản phẩm chạy 24/7

_Lập 2026-11-14. Nguồn: review V4/V5/V6 (PR #1), `docs/V6_HANDOFF.md`, `docs/STATE.md`._
_Cách làm việc: `docs/WORKING_MODEL.md`. Mỗi mục có mã (vd `R1.3`) — commit / fix log ghi mã này._

Ký hiệu: `[x]` xong và đã kiểm · `[~]` code xong ở cloud, **chờ kiểm trên Windows** · `[ ]` chưa làm ·
**(chủ)** = chỉ chủ dự án làm được (cần máy Windows / GPU / tài khoản).

## Mục tiêu

1. **Một** codebase chạy production (bỏ V2/V4/V5).
2. Máy tự chạy hàng chờ **24/7 không cần người canh**: sự cố tự phục hồi, không tự phục hồi được thì báo rõ.
3. Làm trọn một bộ phim **chỉ bằng app desktop**, không mở trang web cũ.
4. Code tách lớp đúng thiết kế (`docs/design/2026-11-11-v6-architecture.md`), mọi thay đổi qua kiểm tra tự động.

## Hiện trạng thật (2026-11-14)

- Khung V6 có và tốt: `core/instance`, `state/`, `events/`, `workers/render_worker`. Lõi vẫn là `server.py`
  (≈6.900 dòng) + `engines/` + `agents/` của V5. `app/routes/`, `sidecar/` trống; `runtime/` chưa được dùng;
  `pipeline/` mới có 1 stage + 1 gate (dịch simple).
- **V6 chưa từng chạy một job thật.** Mọi pha đang ở trạng thái «xong ở cloud, chờ Windows».
- V2 vẫn là production và vẫn nhận tính năng mới → V6 phải port theo.
- Test: 728 pass / 0 fail (Linux). CI chạy trên Ubuntu + Windows từ PR #1.

---

## G0 — Chạy thật và chuyển hẳn sang V6

Điều kiện của mọi giai đoạn sau. **Mốc xong:** V6 là hệ thống duy nhất chạy production trong 7 ngày.

- [ ] **G0.1 (chủ)** Đổi API key 9router đang lộ trong `pineline.config.json`; tại máy `git rm --cached pineline.config.json`.
- [ ] **G0.2 (chủ)** Merge PR #1; pull về máy; chạy `verify_all.bat` → dán kết quả.
- [ ] **G0.3 (chủ)** 1 job ngắn (≤ 5 phút phim) chạy hết trên :8806 → có `output.mp4`. Checklist: `docs/V6_HANDOFF.md` mục 3.
- [ ] **G0.4 (chủ)** 3 job thật song song V2, so: thời lượng, lệch tiếng/sub, số câu HOLD.
- [ ] **G0.5** Chép `vendor/FireRedASR2S` + sửa `FIRERED_ROOT` để V6 không còn đọc cây V2.
- [ ] **G0.6 (chủ)** Cutover: dừng V2, chạy V6 với `--instance prod`. Từ đây **không sửa V2/V4/V5** nữa.
- [ ] **G0.7** Sau cutover: xoá `scripts/sync_from_v2.py`, subagent `port-sync-guardian`, script `test_v2_*`/`test_v4_*`/`test_v5_*`,
      cập nhật mô tả `.claude/agents/*` (đang ghi PinelineV2/V5).

## G1 — Độ bền 24/7

**Mốc xong:** chạy 72 giờ liên tục, ≥ 10 job, không cần người đụng vào; mọi lần dừng đều có thông báo nêu lý do.

### G1.1 Dịch (luồng simple) — bug đã tái hiện
- [x] **R1.1** «Soát lại» khi cloud chết vẫn báo `done` (`stage_review.run` đếm bằng cờ `rv` cũ).
- [x] **R1.2** Lập plan lỗi một lần → `glossary.json` rỗng bị lưu, các lần sau không lập lại; phim dịch không có bảng xưng hô mà cổng vẫn cho qua.
- [x] **R1.3** Lỗi code bị coi như lỗi cloud: park 3 × 5 phút rồi mới HOLD, mất traceback.
- [x] **R1.4** Giờ trong `stages.translate` (`_now()` giờ máy không múi giờ) → `core.timeutil.now_iso`.
- [x] **R1.5** Bộ kiểm xưng hô báo nhầm từ thân tộc chỉ người thứ ba? Đã đo 2026-11-14: **0 / 317** câu gold bị đánh cờ nhầm
      (lưu ý: bộ kiểm dựng từ cùng bộ luật với công cụ đo golden → chưa phải phép đo độc lập; đo lại trên job thật ở R1.6).
- [ ] **R1.6** Đo chất lượng trên job thật của V6 (golden hiện là nhãn «silver» do AI gán, 283 câu held-out).

### G1.2 Tiến trình và tắt/mở
- [~] **R2.1** Desktop: đóng app phải giết cả cây tiến trình (ffmpeg, ASR, render worker), không chỉ Python.
- [~] **R2.2** Desktop: backend chết sớm → báo ngay kèm đường dẫn log (trước: chờ đủ 180 s).
- [ ] **R2.3** Desktop: còn job đang chạy thì hỏi xác nhận trước khi đóng; cho chọn «thu nhỏ xuống khay, để chạy tiếp».
- [ ] **R2.4** Backend chạy như dịch vụ Windows / Task Scheduler: chết thì tự lên; hàng chờ chạy tiếp từ chỗ dở (`queue_autostart`).
- [ ] **R2.5** Khởi động lại giữa chừng: mỗi stage resume đúng (đã có `reset_running`; cần test đầu-cuối bằng engine giả).

### G1.3 Giám sát và cảnh báo
- [~] **R3.1** `/api/health` mở rộng: đã có hàng chờ, dung lượng ổ work/temp, phiên bản, danh sách cảnh báo. Còn: tuổi nhịp tim job đang chạy, VRAM.
- [ ] **R3.2** Watchdog: stage `running` không có nhịp tim quá ngưỡng → dừng cây tiến trình, đánh `failed` có lý do, hàng chờ đi tiếp.
- [ ] **R3.3** Kiểm ổ đĩa trước mỗi stage nặng; thiếu chỗ → giữ job, báo, không ghi hỏng file.
- [ ] **R3.4** Một kênh thông báo (Telegram): job xong / HOLD cần người / lỗi không tự phục hồi / backend vừa tự khởi động lại. Không spam (gộp, chống trùng).
- [ ] **R3.5** Log: `logging` ghi file xoay vòng theo instance; thay dần `except Exception: pass` (216 chỗ ở `server.py`) bằng `log.exception` — bắt đầu từ queue, manifest, publish.

### G1.4 Dữ liệu
- [x] **R4.1** `started_at`/`finished_at` một kiểu ISO UTC (`core/timeutil.py`).
- [x] **R4.2** Test không ghi vào `data/` thật; bỏ file runtime khỏi git.
- [ ] **R4.3** Sao lưu `data/films.db` + cấu hình hằng ngày, giữ 14 bản; lệnh khôi phục có test.
- [ ] **R4.4** Bỏ `data/films.db` khỏi git (làm tại máy: `git rm --cached`).
- [x] **R4.5** Auto-publish «job đang upload bị xếp hàng lần hai»: đã kiểm — scanner có chặn (`is_job_uploading`); test fail trước đây là do
      `is_pid_alive` chỉ chạy trên Windows (đã sửa), không phải lỗi logic.
- [ ] **R4.6** `core/manifest.py`: hết 5 s không lấy được khoá thì đang ghi không khoá → ghi log cảnh báo + đếm; cân nhắc từ chối ghi.

### G1.7 Hàng chờ
- [x] **R7.1** Ghi `_job_queue.json` lỗi (đĩa đầy / file bị khoá) làm chết worker, hàng chờ đứng im lặng.
- [x] **R7.2** Job đang giữ ghế (park/HOLD) bị job khác chen lên trước khi hàng chờ bị sắp lại.
- [~] **R7.3** HOLD cần người chặn cả hàng chờ vô thời hạn → có công tắc `QUEUE_HOLD_YIELD_SEC` (giây; **mặc định 0 = giữ luật Studio cũ**).
      Bật thì job HOLD cần người quá hạn nhường ghế cho job kế (park tự thử lại không bị ảnh hưởng). **(chủ quyết có bật không.)**
- [x] **R7.4** 3 job `failed` liên tiếp (`QUEUE_MAX_CONSEC_FAIL`, 0 = tắt) → tạm dừng hàng chờ + báo (UI + Telegram); «Chạy tiếp hàng chờ» để gỡ.
- [x] **R7.5** Nhật ký hàng chờ ra file `work/_job_queue_history.jsonl` (sống qua restart) + nút «Nhật ký» trên desktop.

### G1.6 Render
- [x] **R6.1** ffmpeg ≥ 8 bỏ `-filter_complex_script` → render tự dò cú pháp (`render.filter_script_opt`). Phát hiện nhờ CI Windows.
- [~] **R6.2** Phiên bản ffmpeg / Python đã có trong `/api/health`. Còn: `scripts/check_env.py`, cảnh báo khi khác bản đã kiểm.

### G1.5 Hiệu năng server
- [x] **R5.1** SSE async (trước: ~40 kết nối là server đứng).
- [x] **R5.2** Thay đổi manifest từ tiến trình khác lên UI chậm tới 30 s → watcher theo dõi mọi job chưa xong (≤ 500), lên UI trong ~1 s.
- [~] **R5.3** `GET /api/cookies` hết ghi đĩa. Còn `GET /api/voice-sample` (tạo file mẫu khi chưa có — đổi sang POST sẽ vỡ UI cũ, làm cùng D6).

## G2 — App desktop đủ chức năng

**Mốc xong:** làm trọn một bộ phim (tạo job → sửa dịch → lồng tiếng → render → upload) không mở `/ui3`, `/films`, `/hongguo`.
Luật: UI desktop **không polling** — 1 kết nối SSE, chỉ gọi API khi người dùng thao tác hoặc có event (test `test_tauri_opens_v6_ui`).

- [~] **D1** Bảng job, hàng chờ, thêm job, khung chi tiết: chạy tiếp / xếp hàng / dừng / chạy từng bước / mở thư mục / xoá.
- [ ] **D2** Xem và sửa bản dịch: danh sách câu (ZH ‖ VI), lọc câu bị cờ / chưa soát, sửa câu (đánh `manual`), nghe thử 1 câu.
- [~] **D3** «Làm lại» một bước đã xong (cờ `force`) có hộp xác nhận nêu rõ sẽ mất gì.
- [ ] **D4** Cấu hình: cấu hình batch (giọng, tốc độ, kiểu âm thanh, render) + ghim cấu hình riêng cho job.
- [ ] **D5** Hàng chờ: kéo đổi thứ tự, xem lịch sử, ước lượng thời gian.
- [ ] **D6** Giọng đọc: danh sách, nghe mẫu, thêm giọng clone.
- [ ] **D7** Phim: thư viện, trạng thái upload, upload YouTube (luôn hỏi xác nhận).
- [ ] **D8** Hongguo: tìm, tải, gộp tập.
- [ ] **D9** Cài đặt + kiểm máy lần đầu (`scripts/check_env.py` thành màn hình).
- [ ] **D10** Chat với Agent / Second Brain.
- [ ] **D11** Thông báo native + biểu tượng khay; xem log backend trong app.

## G3 — Cấu trúc và phát hành

**Mốc xong:** `server.py` < 1.000 dòng; không còn `importlib.reload`, `js.persist`, `import server` trong route; có bản cài đánh số phiên bản.

- [ ] **S1** Route: chuyển từng nhóm từ `server.py` → `app/routes/` (jobs, queue, stages, files, voices…), mỗi nhóm 1 commit + test.
- [ ] **S2** `runtime/`: `server.py` dùng `Runtime` thật thay `_PROCS` / `_STOP_JOBS` / `_STAGE_BUSY`; `queue_worker`, `engines.spawn`.
- [ ] **S3** `pipeline/`: đưa lần lượt download, asr, tts, separate, render thành Stage; các cổng thành Gate; xoá `_auto_gpu`.
- [ ] **S4** Bỏ `importlib.reload` (routes_hg 43, agent_tele_cmd 30, server 4) và `js.persist` (91 — ratchet chỉ giảm).
- [ ] **S5** `sidecar/telegram.py` mỏng thay `agents/agent_tele_cmd.py` (2.300 dòng, 11 thread).
- [ ] **S6** Tách `agents/` thành: agent AI (brain/heal/watch) · tích hợp ngoài (Hongguo, Canva, YouTube) · sidecar.
- [ ] **S7** Gộp file trùng: `agents/test_channel.py` ≡ `tools/test_channel.py` (cả hai đang được import), `films_ui.html` ×3, `index3.html` ×2.
- [ ] **S8** Test đầu-cuối: pipeline đầy đủ với engine giả (không GPU) chạy trong CI.
- [ ] **S9** `requirements.txt` ghim phiên bản, tách nhóm (lõi / GPU / tuỳ chọn).
- [ ] **S10** Phát hành: số phiên bản một nguồn, bộ cài NSIS, ghi chú phát hành sinh từ CHANGELOG, cập nhật tự động.

---

## Thứ tự làm

1. G0 (chủ dự án) chạy song song với **G1.1 + G1.2** (cloud tự làm và tự kiểm được).
2. Sau khi có job thật đầu tiên (G0.3): **G1.3 + D2**.
3. Sau cutover (G0.6): G0.7, rồi xen kẽ mỗi đợt **một mục G1/G3 + một mục G2**.

## Nhật ký tiến độ

| Ngày | Mục | Ghi chú |
|---|---|---|
| 2026-11-13 | R4.1 R4.2 R5.1 | PR #1: SSE async, timestamp, cách ly test, `.gitattributes` |
| 2026-11-14 | D1 | UI desktop có thao tác job + hàng chờ |
| 2026-11-14 | — | Test 728/0 trên Linux; thêm CI (Ubuntu + Windows + cargo check) |
| 2026-11-14 | R1.1–R1.4 R2.1 R2.2 | Xem `docs/fixes/2026-11-14-*` |
| 2026-11-14 | R6.1 | CI Windows lần đầu: 727 pass, 2 fail do ffmpeg 8 → sửa |
| 2026-11-14 | R7.1 R7.2 | Rà hàng chờ; CI xanh Ubuntu + Windows + cargo (xác nhận R6.1 trên ffmpeg 8) |
| 2026-11-14 | R7.3 R7.4 R7.5 R5.2 R5.3 R3.1 R6.2 D3 R1.5 R4.5 | Đợt «fix all»: xem `docs/fixes/2026-11-14-queue-24x7.md` |
