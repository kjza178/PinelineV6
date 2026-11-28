# Mô hình làm việc — Pineline V6

_Áp dụng cho mọi người và mọi agent sửa repo này. Kế hoạch: `docs/ROADMAP.md`._

## 1. Nguyên tắc

1. **Một nguồn sự thật: git + GitHub.** Không backup bằng cách chép thư mục, không commit «Update: <giờ>».
   Lịch sử git là bản sao lưu; muốn quay lại thì `git revert`.
2. **Mỗi thay đổi nhỏ, một mục đích.** Một commit sửa một việc, có mã mục ROADMAP (vd `R1.2`).
3. **Không tin «xong» khi chưa có bằng chứng.** Bug phải có bước tái hiện trước khi sửa và test giữ nó sau khi sửa.
4. **Nói rõ đã kiểm ở đâu.** «Đã kiểm ở cloud» ≠ «đã chạy trên Windows» ≠ «đã chạy job thật».
5. **Không sửa V2/V4/V5** (đóng băng sau cutover — ROADMAP G0.6).

## 2. Vòng làm một việc

```
chọn mục ROADMAP → tái hiện (bug) / viết tiêu chí xong (tính năng)
   → sửa nhỏ nhất → test mới + cả bộ test → tự review diff
   → commit (mã mục) → push → CI xanh → cập nhật ROADMAP + CHANGELOG (+ fix log nếu là bug)
   → (nếu cần Windows) ghi vào «Chờ kiểm trên Windows» → chủ dự án chạy → đóng mục
```

### 2.1 Trước khi sửa
- Đọc `docs/ROADMAP.md` (mục liên quan), `CLAUDE.md` (luật code), thẻ module trong `docs/brain/modules/` nếu file lớn.
- Bug: viết bước tái hiện chạy được (script hoặc test đang fail). Không tái hiện được → ghi «nghi ngờ», không sửa mò.

### 2.2 Khi sửa
- Luật code trong `CLAUDE.md` (ghi manifest qua `state/job_state`, opts qua `core/job_opts`, cổng/đường dẫn qua
  `core/instance` + `core/machine`, không `importlib.reload` mới, kill tiến trình con bằng `kill_tree`).
- Không nuốt lỗi: `except Exception` phải ghi log đủ để tìm nguyên nhân, trừ khi có chú thích vì sao bỏ qua.
- Thứ gì người dùng thấy (thông báo, nhãn nút) viết tiếng Việt, nói cái gì xảy ra và làm gì tiếp.

### 2.3 Kiểm (bắt buộc trước khi commit)
```bash
python -m pytest -q tests                      # AGENT_BRAIN=0; phải 0 fail
python -m compileall -q -x "vendor|_to_delete|scratch" .
cd desktop/src-tauri && cargo check            # khi đụng desktop/src-tauri
```
- Đụng UI: mở trang bằng trình duyệt thật (Playwright/Chromium với backend `--instance test`), bấm đúng luồng vừa sửa,
  xem console không lỗi, đếm request khi để yên (UI desktop phải = 0).
- Test không được ghi ra ngoài thư mục tạm. Sau khi chạy: `git status` phải sạch.
- **Không** skip / xfail test để CI xanh. Chỉ skip theo điều kiện nền tảng thật (vd cần `cmd.exe`) và ghi lý do.

### 2.4 Commit và PR
- Tiêu đề: câu mệnh lệnh ngắn, nói thay đổi gì. Thân: vì sao + ảnh hưởng. Ví dụ: `R1.2 Do not persist an empty translation plan`.
- Làm trên nhánh, mở PR vào `main`. PR nhỏ (< ~400 dòng diff) dễ review hơn một PR khổng lồ.
- **CI phải xanh** (pytest Ubuntu + Windows, cargo check) trước khi merge. CI đỏ là việc phải sửa ngay, không phải «chờ review».
- Chỉ chủ dự án merge.

### 2.5 Tài liệu (gọn — git đã giữ lịch sử)
| Khi | Ghi ở đâu |
|---|---|
| Mọi thay đổi người dùng / vận hành thấy được | 1–3 dòng `docs/CHANGELOG.md` (nêu **cần restart / build lại gì**) |
| Sửa bug | `docs/fixes/<ngày>-<slug>.md`: triệu chứng · nguyên nhân · cách sửa · cách đã kiểm |
| Xong / đổi trạng thái một mục | đánh dấu trong `docs/ROADMAP.md` + dòng nhật ký |
| Chủ dự án chọn phương án | `docs/DECISIONS.md` (tạo khi có quyết định đầu tiên) |
| Trạng thái máy thật (đang chạy gì, chờ restart gì) | `docs/STATE.md` — chỉ chủ dự án / agent ngồi máy thật cập nhật |

## 3. Ba mức «đã kiểm»

| Mức | Ai | Nghĩa |
|---|---|---|
| **C — cloud** | agent | unit test + CI + (UI) trình duyệt với backend instance `test`, engine giả |
| **W — Windows** | chủ dự án | `verify_all.bat`, mở app Tauri, thao tác thật trên máy |
| **J — job thật** | chủ dự án | job chạy hết pipeline có GPU/LLM, so kết quả |

Mục ROADMAP ghi `[~]` khi mới đạt C mà bản chất cần W/J. Thứ **không kiểm được ở cloud**: GPU, ASR/TTS/render thật,
Hongguo, YouTube, cửa sổ Tauri/WebView2, hành vi giết tiến trình của Windows.

### Chờ kiểm trên Windows (chủ dự án chạy, dán kết quả)

| Mục | Cách kiểm | Kỳ vọng |
|---|---|---|
| PR #1 (SSE, timestamp, test) | `verify_all.bat` | PASS; `/ui3` và `/v6/` không «mất kết nối» |
| D1 UI desktop | `desktop\dev.bat` → thêm job, xếp hàng, dừng, chạy tiếp, xoá | Mọi nút có phản hồi; Network để yên = 0 request |
| R2.1 giết cây tiến trình | Đang render → đóng app → Task Manager | Không còn `ffmpeg.exe` / `python.exe` của job |
| R2.2 backend chết sớm | Đặt `PINELINE_PYTHON` sai → mở app | Báo lỗi trong vài giây, có đường dẫn log |
| R1.1–R1.3 dịch | 1 job thật; thử rút mạng lúc đang soát | Job park/hold đúng lý do, không báo «xong» giả |

## 4. Review định kỳ («review, check, autofix từng phần»)

Mỗi đợt làm việc, ngoài mục đang làm, rà **một khu vực** theo danh sách dưới — tìm bug có bằng chứng, sửa cái nhỏ ngay,
cái lớn ghi vào ROADMAP:

| Khu vực | File chính | Đã rà |
|---|---|---|
| Dịch simple | `engines/translate_simple/*`, `pipeline/*` | 2026-11-14 (R1.1–R1.3) |
| State / manifest / index | `core/manifest.py`, `state/*`, `events/bus.py` | 2026-11-13 |
| Desktop | `desktop/src-tauri`, `desktop/ui` | 2026-11-14 |
| Hàng chờ | `core/job_queue.py`, phần queue của `server.py` | 2026-11-14 (`job_queue.py`: R7.1–R7.2; phần trong `server.py` chưa) |
| Auto-publish / YouTube | `agents/auto_publisher.py` | một phần (2026-11-13) |
| ASR | `engines/asr/*` | — |
| TTS | `engines/clone_tts.py`, `rvc_bridge/*` | — |
| Render | `engines/render*.py`, `workers/render_worker.py` | một phần (worker) |
| Agent AI | `agents/agent_brain*.py`, `agent_core.py`, `agent_heal.py` | — |
| Sidecar Telegram | `agents/agent_tele_cmd.py` | — |
| Hongguo | `agents/hongguo_*.py`, `http_routes/routes_hg.py` | — |
| Film DB | `core/film_db.py`, `http_routes/routes_films.py` | — |
| UI cũ | `index*.html`, `films_ui.html`, `static/` | một phần (`index.html`) |

## 5. An toàn vận hành

- Không in / commit secret (`pineline.config.json`, token, cookies). Phát hiện secret trong git → báo chủ dự án đổi key.
- Không kill :8000 (Hongguo) và :8083 (llama-server). Restart backend chỉ khi hàng chờ rảnh hoặc chủ dự án đồng ý.
- Không xoá `work/`, không chạy lại ASR nặng, không render lại job đã xong, không upload YouTube khi chưa hỏi.
- Bỏ theo dõi git một file runtime (`git rm --cached`) phải làm **tại máy chủ dự án**: gỡ từ xa thì lần pull sẽ xoá file ở máy.
