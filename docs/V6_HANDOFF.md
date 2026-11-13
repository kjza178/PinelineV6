# Pineline V6 — bàn giao (2026-11-11)

Đọc nhanh cho chủ dự án. Chi tiết từng pha: `docs/fixes/2026-11-11-*.md`, review: `docs/reviews/2026-11-11-*.md`,
thiết kế: `docs/design/2026-11-11-v6-architecture.md` (mục 5 = gate).
**Chưa cutover**: V2 production (:8801/:8802) không bị đụng; `instances/prod.json` chỉ là cấu hình chuẩn bị.

## 1. Trạng thái pha so với gate (thiết kế mục 5)

| Pha | Gate | Trạng thái |
|---|---|---|
| F0 khung | pytest khung + V5 · tree_audit 0 · :8806 start · 1 job ngắn ra output.mp4 | **Đạt ở cloud – chờ Windows** (job ngắn chưa chạy) |
| F1 B9 | 0 `.save()` ngoài state/ · test đồng thời · job thật có HOLD/park/stop | **Đạt ở cloud – chờ Windows** (ratchet xanh, persist 93; job thật chưa) |
| F2 A | 1 job từ UI/queue/agent cùng opts | **Đạt** (test `test_job_opts`) |
| F3 D | route không `import server` · `_auto_gpu` xoá · job thật khớp V2 | **Chưa** — mới tích hợp V5+V6 một cổng; server.py vẫn là lõi, Stage/Gate chưa thay `_auto_gpu` |
| F3b UI | 0 polling định kỳ · CPU server idle < 2 % với 100 job | **Đạt ở cloud – chờ Windows** (SSE + live_sync; còn poll dự phòng khi SSE đứt / 5 s khi có job chạy; CPU chưa đo) |
| F4 C | không /cmd vòng · feeder/film_db trong server | **Đạt ở cloud – chờ Windows** (cờ `feeder` v6/test tắt, prod bật — chưa chạy thật) |
| F5 E | 0 importlib.reload · sửa render có hiệu lực job kế | **Đạt ở cloud cho đường render – chờ Windows**; toàn cục **Chưa** (còn 77 reload, xem mục 6) |
| F6 | B11 + docs + brain + chuyển production (≥ 3 job thật song song V2 khớp) | B11 phần an toàn + docs + brain: **Đạt**; chuyển production: **Chưa** (việc của user) |

Test cloud lần kiểm cuối: `python3 -m pytest -q -p no:cacheprovider --ignore=tests/v5 tests` → **192 passed, 2 skipped**
(2 skip cần fastapi). `tests/v5`: 88 passed / 11 failed / 12 errors (thiếu numpy/fastapi… ở cloud — như các lần trước).
tree_audit: ERROR=0 · ports=0.

## 2. Cách chạy

- **Desktop (Tauri)**: `desktop\dev.bat` (dev) / `desktop\build.bat` (installer). Tự spawn `python -m app --instance v6`
  (cổng đọc từ `instances\v6.json` = 8806), mở `/v6/`. Log backend: `scratch\backend_v6.log`.
- **Backend trần**: `run_v6.bat` → http://127.0.0.1:8806/ (UI cũ), `/ui3` (index3 SSE), `/v6/` (UI mới).
- **Sidecar V6** (tuỳ chọn): `run_sidecar_v6.bat` → :8816, Telegram TẮT, không chạy vòng nền.
- **Kiểm toàn bộ**: `verify_all.bat` (gốc cây) — xem mục 3.
- Instance: `instances\v6.json` (:8806, work `D:\PinelineV6_work`), `test.json` (:8899, `scratch\test_work`),
  `prod.json` (:8801, work V2 — CHỈ dùng khi cutover). Mọi cờ gọi ra ngoài (brain/watch/telegram/auto_publish/
  hg_merge_resume/title_translate/queue_autostart/feeder) TẮT ở v6/test.

## 3. Checklist kiểm trên Windows (theo thứ tự)

1. **`verify_all.bat`** — chạy 1 lần, không cần V2 rảnh (không job thật, không GPU, chỉ cổng 8899).
   Kết quả cuối màn hình + `scratch\verify_all_<thời gian>.log`. Cần: a) pytest V6 PASS (gồm `test_app_integration`),
   b) tests\v5 (so với mốc V5: 18 fail có sẵn — fail mới mới là vấn đề), c) tree_audit ERROR=0, d) smoke PASS hết.
   Bước d tắt đúng PID nó khởi (`scratch\verify_all_backend.pid`); log backend `scratch\verify_all_backend.log`.
2. **Mở desktop** (`desktop\dev.bat`): cửa sổ lên `/v6/`, danh sách job hiện, không «mất kết nối». Mở thêm `/ui3`
   trên :8806 — bấm Auto-pilot / Lưu config KHÔNG được ảnh hưởng V2 (kiểm `netstat -ano | findstr 8802` trước/sau).
3. **1 job ngắn trên V6 khi V2 rảnh** (GPU dùng chung): tạo job ngắn ở :8806 → chạy hết → `output.mp4`. Trong lúc render:
   tiến độ mux phải lên UI trong ~1–2 s (sửa F6), bấm **Dừng** giữa render → worker + ffmpeg chết hết, stage về pending.
4. **So job thật song song V2** (≥ 3 job, khi V2 rảnh): cùng nguồn, so thời lượng, lệch sub/tiếng, số câu HOLD.
5. **Bật `render_subprocess` ở prod** (`instances\prod.json`) chỉ sau bước 4 khớp.
6. `run_sidecar_v6.bat` → log «Telegram OFF», «không chạy vòng nền», bind :8816.
7. Thử cờ `feeder=true` trên một instance thử (không phải prod) → vòng autopilot/feeder chạy sau 15 s, không trùng sidecar.

## 4. Việc user phải quyết

- **Bug /run-all ở V2/V4/V5** (decorator POST /run-all gắn nhầm vào `_req_audio`, server.py:4229): chạy
  `scripts\fix_runall_decorator_v2v4v5.bat` (có backup) rồi restart V2 lúc rảnh. Agent KHÔNG chạy.
- **Khoá file rò ở V2** (`core/fileio.file_lock`, docs/fixes/2026-11-11-file-lock-leak.md): chỉ thay khi restart TOÀN BỘ
  V2 lúc hàng chờ rảnh (bản cũ + mới không loại trừ nhau).
- **Cutover**: khi nào chạy V6 thay V2 trên :8801 (dừng V2 trước — cùng cổng, cố ý không chạy song song được).
  `restart_server.py --instance prod` bị từ chối trừ `--allow-foreign-port`.
- **prod `sidecar_port`**: `prod.json` chưa có → mặc định 8811; sidecar V2 là 8802. Đặt `"sidecar_port": 8802` (giữ link
  Canva OAuth `…:8802/canva/callback`) hoặc đổi app Canva.
- **Feeder ở prod**: `prod.json` `feeder=true` → autopilot/feeder/film_db chạy TRONG server, sidecar không chạy vòng nền.
  Xác nhận muốn vậy trước cutover.
- **FireRedASR**: `config.json`/`pineline.config.json` `FIRERED_ROOT = Z:/PinelineV2/vendor/FireRedASR2S` (V6
  `vendor\FireRedASR2S` trống/không đọc được từ VM) → V6 vẫn đọc model ở cây V2. Trước khi bỏ cây V2: chép vendor sang V6
  rồi sửa key.
- **Không chạy `scripts/sync_from_v2.py` nữa** cho server.py: sẽ ghi đè các sửa V6 (F1–F6) bằng bản V2.

## 5. Bug tìm thấy ở V2 production trong quá trình làm V6 (CHƯA sửa ở V2)

1. POST `/run-all` gắn nhầm decorator vào `_req_audio` (V2/V4/V5) — script sửa ở mục 4.
2. Khoá file rò `core/fileio.file_lock` (Windows `unlink` lỗi khi bên khác mở → `.lock` ở lại → chờ 60 s / ghi manifest
   KHÔNG khoá). Có sẵn ở V2.
3. UI V2 có thể «mất kết nối»/khựng: index3 poll `/api/jobs` mỗi 2 s, `list_jobs` đọc manifest + `script.json` của MỌI job
   mỗi lần (≈100 job → hàng chục file/giây, tranh khoá với pipeline). V6 đã đổi sang SSE + cache.
4. `GET /api/jobs/{id}` có side effect (ghép parent) — V6 đã bỏ.

## 6. Rủi ro còn lại / nợ

- Tiến trình con (render worker, engine ASR/TTS) ghi manifest trực tiếp → UI biết qua watcher (stat 1 s). F6 sửa: job được
  theo dõi ngay khi spawn (trước có thể trễ tới 30 s nếu job chưa «running» trong index).
- Desktop: nếu cổng instance đã mở, Tauri **dùng lại** tiến trình đang nghe (không kiểm là V6). Với v6 (:8806) vô hại;
  đừng chạy desktop với `PINELINE_INSTANCE=prod` khi V2 còn chạy.
- Backend chạy không console (Tauri): engine/worker con có thể bật cửa sổ console (cần `CREATE_NO_WINDOW` — review2 L4).
- Đường dẫn D:\PinelineV2_tmp / V4 / V5 còn trong `agents/hongguo_rpc.py`, `agents/hongguo_license_ensure.py` — chỉ là
  danh sách ứng viên để DÒ (đọc), sau env/TEMP của instance (test `test_no_v2_tree_paths_in_running_code` khoá danh sách).
- `agents/test_channel.py` trùng `tools/test_channel.py` (tree_audit dups=1).
- **Nợ đếm được**: `importlib.reload` — `http_routes/routes_hg.py` 43, `agents/agent_tele_cmd.py` 30, `server.py` 4;
  `js.persist` 93 (ratchet chỉ giảm); services (feeder/autopilot…) vẫn gọi HTTP về CHÍNH server thay vì gọi hàm;
  server.py 66 chỗ nuốt lỗi `except: pass`; Runtime/Stage-Gate (F3 D) chưa thay `_auto_gpu`.
- Second Brain V6 (`docs/brain/`) sinh mới bằng AST: phần MANUAL trong thẻ module trống (chưa chuyển ghi chú tay từ brain V2).

## 7. B11 (vệ sinh — review V2 2026-11-11) đã làm gì

- Code chết: `engines/mux.py` → `_to_delete/20261111_b11_dead_code/` (không ai import; render_job/render.py thay);
  `sync_from_v2.py` bỏ qua `mux`. Bỏ import `run_asr` không dùng trong server.py.
- Test cho module rủi ro chưa có test: `tests/test_b11_risky_modules.py` (agent_feeder, agent_tele_cmd, clone_tts — hàm thuần).
- Lỗi font docs: docs V6 không có mojibake; `DECISIONS.md`/`fixes/README.md` lỗi font ở **V2** — không sửa (không đụng V2).
- Không làm: dọn 66 `except: pass` của server.py, 43 reload routes_hg (cần chạy thật với Hongguo :8000) — rủi ro, để sau.
