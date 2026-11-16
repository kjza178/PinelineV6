# CHANGELOG — V6

## 2026-11-16
- **Giọng đọc trên máy mới:** Cài đặt → Giọng đọc có mục «Giọng clone trên máy này»: liệt kê giọng dùng được / **thiếu file
  mẫu** (git mang danh sách tên nhưng không mang wav), nghe thử, xoá mục hỏng; **tạo giọng mới** từ file ghi âm 3–8s
  (không cần mở một job) + ô «lời trong file mẫu» (máy chưa có model nhận dạng thì trước đây lời mẫu rỗng → clone kém;
  `/api/clone-voice` nhận thêm `text`). Hướng dẫn chép thư mục voices từ máy cũ bằng `scripts/import_voices.py`.
  `services/voices.py`, `/api/v6/voices`.
- **Kênh test trên máy mới (không có V2):** hộp «Test bản quyền» → «Thêm tài khoản»: chọn / dán file OAuth client JSON
  (Google Cloud Console; «web» hoặc «installed»), hoặc «Dùng file này» với client_secret*.json tìm thấy trong thư mục
  yt_uploader → ghi `data/test_channel/projects.json` (trước không code nào tạo file này) → «Đăng nhập». Hộp hiện đúng
  redirect URI cần khai báo. `services/test_channel_setup.py`, `/api/v6/test-channel/{candidates,projects}`; không trả
  client_secret ra ngoài.
- Sửa: films.db MỚI tạo thiếu cột `library_blocks.recheck_at` (migrate chạy trước khi tạo bảng) → lần ghi kết quả test
  bản quyền đầu tiên lỗi «no such column: recheck_at». Cột nay có sẵn trong SCHEMA.
- **Sửa: đăng nhập kênh test bấm không chạy trong app desktop.** WebView2 của app chặn link mở tab mới (`target=_blank`)
  → mọi link như vậy trong /v6/ nay mở bằng trình duyệt ngoài qua server (`POST /api/v6/open-external`, chỉ Google /
  YouTube / localhost). Hộp «Test bản quyền» tự đăng nhập: danh sách tài khoản (projects) + nút «Đăng nhập» → mở trang
  Google trong trình duyệt; xong callback báo app qua SSE `test_channel.changed` (hộp tự cập nhật). Không mở được trình
  duyệt → hiện link để chép.
- Cấu hình kênh test (`data/test_channel`: projects, token Google) nằm riêng từng cây — V6 chưa có thì luôn «chưa đăng
  nhập». `scripts\import_test_channel_v2.bat` chép từ V2 (không ghi đè, xem trước). `data/test_channel/` vào .gitignore
  (chứa client_secret + token).
- **Cài đặt AI trên máy** (Cài đặt → AI dịch → «AI trên máy»; `core/local_ai.py`, `/api/v6/ai/local`) — trước gắn cứng /
  chỉ đặt bằng biến môi trường: địa chỉ llama-server (LLAMA_BASE_URL), địa chỉ Ollama (OLLAMA_BASE_URL — trước chỉ env
  LOCAL_LLM_BASE_URL, vẫn tôn trọng), số phần dịch cùng lúc riêng cho local (LOCAL_CONCURRENCY — trước dùng chung
  SIMPLE_WORKERS với Aurora), trần token (LOCAL_MAX_TOKENS), bật/tắt «suy nghĩ» (LLM_ENABLE_THINKING), context Ollama
  (LLM_NUM_CTX); hiện llama-server có chạy không + model đang mở. Thẻ Aurora: ô địa chỉ Aurora (AURORA_BASE_URL).
  Mọi nơi đọc LÚC GỌI (không cần restart). Chưa đặt LOCAL_* → giữ hành vi cũ (SIMPLE_*).
- **Test bản quyền YouTube trong «Phim chờ làm»** (chuyển từ hộp «Kiểm tra Bản Quyền» của trang Phim cũ): nhãn kết quả
  trên từng phim (Chưa test / Đang test / Sạch / Chặn 1 phần / Bị chặn / Claim / Test lỗi), lọc theo bản quyền, nút
  «Test BQ» mở hộp: chọn tài khoản kênh test, cắt mẫu 90s → đăng Không công khai → Content ID; «Kiểm lại ngay»; tự đánh
  dấu kết quả. Chọn nhiều phim → «Test bản quyền N phim» (chạy nền). Kết quả recheck (~5 phút) về qua SSE `library.block`
  (core/film_db.upsert_library_block), không polling. Đăng nhập kênh test vẫn ở trang Đăng YouTube (cũ).
- Sửa: cắt mẫu test (tools/lib_check) đọc thư mục thư viện LÚC GỌI (`lib_root()`, theo thư mục Hongguo 2 / Cài đặt) —
  trước cố định lúc server khởi động. Nút dừng test hàng loạt gọi đúng module với nút bắt đầu (tools.test_channel).

## 2026-11-15
- Phim chờ làm: chọn **kiểu xem Lưới / Danh sách** (danh sách = bảng có ảnh nhỏ, ô chọn + «chọn tất cả»); app nhớ lựa chọn.
- Phim chờ làm: nút **Xoá** (từng phim hoặc chọn nhiều) — xoá HẲN thư mục phim khỏi ổ sau khi hỏi xác nhận (kèm dung
  lượng), như «Xoá» trang Phim cũ; chặn khi bộ đang gộp hoặc còn job chưa xong đang đọc file trong đó; xoá xong làm mới
  thư viện Hongguo 2. Nút **Dọn tập lẻ** cho bộ đã gộp (chỉ khi file gộp đủ nội dung, chuyển vào thùng rác thư viện).
- **«Phim chờ làm»** (đổi từ «Phim trên ổ»): nguồn chính = **thư viện Hongguo 2** (tên Trung, series_id, thư mục lưu,
  poster) như tab Library của Hongguo 2; máy chưa có / chưa mở Hongguo 2 → quét LIBRARY_DIR. Dạng lưới poster, tên
  tiếng Việt (dịch có cache), mới tải lên đầu, bấm poster để chọn nhiều phim → «Tạo N job».
- **«Phim trên ổ» (mới, giao diện chính)** — thay tab «Thư viện chưa làm» đã bỏ cùng Hongguo 1: quét THẲNG thư mục thư
  viện (Cài đặt → Thư mục, vd `D:\Lib`), không cần Hongguo trả lời. Mỗi bộ: số tập, dung lượng, trạng thái (sẵn sàng /
  cần gộp tập / đang gộp / đã làm). «Tạo job» (một hoặc nhiều bộ) → tạo job + xếp hàng theo cấu hình chung, gom nhóm
  theo bộ như tab cũ. «Gộp tập» chạy nền tuần tự (cùng độ phân giải: giữ nguyên chất lượng; lệch: chuẩn hoá 1080p),
  không xoá tập lẻ; báo xong / lỗi qua SSE. `services/disk_library.py`, `/api/v6/disk-library*`.
  «Thư viện phim» đổi tên **«Phim đã làm»**.
- **Thư viện phim gom về giao diện chính (`/v6/`)**: thêm thanh «Đăng YouTube» (tự đăng khi render xong — công tắc khoá
  và ghi rõ khi instance tắt `auto_publish`; chế độ; hẹn giờ; đăng tất cả phim chưa đăng; bỏ qua video đang đăng), cột +
  bộ lọc «Bản quyền», nút «Kiểm» từng video / «Kiểm bản quyền tất cả», «Dọn phim mất thư mục», «Ẩn phim mất thư mục»,
  «Tải CSV». Cài đặt có mục mới **«Thư mục»** (thư viện phim, yt_uploader, mẫu cắt thử — `/api/v6/paths`, core/machine).
- Trang Phim cũ (`/films/ui`): **gỡ hộp «Cài Đặt Hệ Thống V4»** (ghi đè khoá AI / model của /v6/; thư mục → /v6/ Cài
  đặt → Thư mục); nút Cài đặt dẫn về /v6/; sửa «Lỗi: Không đọc được dữ liệu» (`GET /films` thiếu `ok`). Trang này chỉ
  còn giữ công cụ chưa chuyển: gộp job, metadata / thumbnail, hàng chờ upload, kênh test.
- **Cài đặt → Âm thanh sửa sai khoá**: thanh «Âm lượng giọng Việt» trước gắn vào `voice_vol` — render dùng khoá này cho
  GIỌNG TRUNG nền (chỉ chế độ «Nhạc gốc + giọng Trung nhỏ»), kéo không đổi gì giọng Việt. Nay: «Giọng Việt» =
  `RENDER_DUB_VOL` (mặc định 125%), «Nhạc nền thư viện» = `RENDER_BGM_VOL`, «Nhạc kho» / «Tiếng động gốc» (sfx_lib),
  «Tiếng gốc nền» = `orig_vol`, «Giọng Trung nền» = `voice_vol`; chỉ hiện mức mà chế độ âm nền đang chọn dùng
  (`core/audio_levels.py`, `/api/v6/config` nhận `levels`). 3 chế độ giữ nhạc gốc gom vào mục gập «dễ dính bản quyền».
  Render `duck_bgm`: tiếng gốc > 28% trước bị hạ thẳng về 18% → nay giữ ở trần 28%.
- Cài đặt: bỏ «Kiểu dịch (luồng cũ)», «Soát dự phòng Aurora (luồng cũ)» khỏi giao diện (luồng V6 tự chọn cỡ lô).
  Giọng đọc: nút «▶ Nghe thử» cạnh giọng mặc định. Thư viện phim: nút «Xoá» dòng không còn là job (vd dòng test cũ).
- **Trang chủ `/` = giao diện mới (`/v6/`)**; Studio ở `/studio` (cũng `/ui3`) và có nút «← Giao diện mới»; trang Phim
  có «⬅ Giao diện chính». App desktop build trước 14/11 tối mở `/ui3` (Studio) — build lại (`desktop\build.bat`) để mở `/v6/`.
- **Hongguo 2 không còn giới hạn số phim** (cần restart :8806; trang /hongguo2 tải lại):
  - Sửa: Browse MẤT phim giữa các trang — engine trả ~20 thẻ/lần, code gom cho đủ 24 rồi CẮT phần thừa nhưng vị trí trang
    sau đã nằm sau phần bị cắt (mỗi trang mất ~15 phim). Nay trả hết thẻ đã gom.
  - Ranking: bỏ trần 100; phân trang bằng rank.more (bộ nhớ đệm theo bảng); nút «Xem thêm».
  - New: nút «Xem thêm» (trước chỉ 48 phim).
- **Bỏ hẳn Hongguo 1** (cần restart :8806) — `agents/hongguo_bridge.py` không còn nhánh HTTP :8000 (`_req`, token, `/dl/*`),
  không còn chọn backend (`HG_BACKEND`, `agents/hg_ui_ctx.py` xoá, khoá máy `HONGGUO_BASE_URL` xoá); mọi `/api/hg/*` và
  `/api/hg2/*` dùng Hongguo 2. Gộp tập: tại chỗ bằng ffmpeg `-c copy` (`merge_series_copy`) thay cho Hongguo 1 gộp hộ.
  Agent Hongguo bật engine Hongguo 2 thay vì mở Downloader 1.x. `check_env` / Second Brain dò Hongguo 2.
- **Trang Phim bỏ tab «Thư viện chưa làm» + «Mùa mới»** (phụ thuộc Hongguo 1; không quét thư viện trên ổ độc lập).
- **Nút trang Phim / Studio trước chỉ sidecar có → server V6 phục vụ**: Tra Baike, Tạo ảnh AI, Lưu / nạp cấu hình batch
  (`/config`), Dừng test kênh — `app/routes/ui_compat.py` (route mỏng) + `services/ui_tools.py` (sidecar dùng lại cùng
  hàm). Studio: Dừng → `/api/queue/jobs/stop`.
- **Sửa: mọi `/api/hg/*` bị ép sang Hongguo 1 (:8000, đã ngừng)** (cần restart :8806) — trang Phim «Thư viện chưa làm»,
  «Mùa mới», gửi tải; Studio thư viện / Merge; feeder tự thêm job đều 502. Nay theo `HG_BACKEND` (mặc định auto =
  Hongguo 2). Sơ đồ hoạt động toàn bộ UI: docs/design/2026-11-15-ui-map.md (`scripts/ui_map.py`).
- **Giao diện desktop làm lại** (cần restart :8806; app desktop chỉ cần mở lại — giao diện tải từ server):
  thanh bên trái (Công việc · Thư viện phim · Trợ lý AI · Cài đặt) thay cho hàng nút + hộp thoại; bảng job gọn (tên,
  thanh tiến độ 5 bước, trạng thái tiếng Việt, việc đang làm); **một màn hình Cài đặt** gộp «Cài đặt» + «Cài đặt AI»:
  AI dịch (chọn nhà cung cấp → dán key → Kiểm tra → Áp dụng; tốc độ Tiết kiệm/Cân bằng/Tối đa; danh sách «AI đang làm gì»),
  Giọng đọc, Âm thanh, Hình ảnh, Thương hiệu — mỗi tuỳ chọn có nhãn + một dòng giải thích, thanh trượt / lựa chọn có
  mô tả / công tắc, không hiện tên khoá; bảng model từng chức năng, địa chỉ API, tuỳ chọn luồng cũ dồn vào «Nâng cao».
  Trợ lý AI thành một màn hình chat có câu hỏi gợi ý. Thư viện phim thành một màn hình.
- **Cài đặt AI chỉ hiện chức năng luồng V6 dùng thật** — «Pass sửa tên», «Pass xưng hô» (cơ chế V2), «Bổ sung cặp xưng hô»,
  «Phân tích xưng hô cả phim» (thử nghiệm đang tắt), «Soát luồng cũ» gấp vào mục «Luồng cũ / thử nghiệm». Agent «rà soát»
  trên job V6 KHÔNG chạy pass tên / xưng hô cũ nữa (soát lại V6 tự xử lý theo plan + kiểm + vá).
- **Sửa: desktop dính giao diện cũ sau khi cập nhật** — file giao diện trả kèm `Cache-Control: no-cache` (WebView2 / trình
  duyệt hỏi lại server mỗi lần, có ETag nên rẻ).
- **Trang UI gọn lại** (cần restart :8806; desktop build lại để nav mới) — theo chủ dự án:
  Studio (UI3, `index3.html`) đổi tên thành `index.html` = trang chính `/`; UI0 cũ và UI4 bỏ; Hongguo 1 (`hongguo.html`)
  bỏ, dùng Hongguo 2. Đường cũ không gãy: `/ui3` `/index3.html` `/studio` = `/`; `/ui4` → `/`; `/hongguo` `/ui-hg` →
  `/hongguo2` (chuyển hướng, giữ query). Nút «Phim» trỏ `/films/ui` cùng máy chủ (trước trỏ sidecar :8802 → V6 tắt
  sidecar nên trang Phim không mở). Thanh nav Studio: UI3 · HG · Phim · Agent.
  Chưa: Hongguo 2 chưa đọc `?sid=&q=` (link «HG» từ trang Phim mở Hongguo 2 nhưng không tự tìm sẵn).
- **Rà model theo khuyến nghị** (cần restart :8806 + build lại app):
  - Sửa: Agent / Agent dự phòng chọn Aurora (`chatgpt/…`) hoặc Ollama (`local/<tên>`) bị gửi nhầm tới llama :8083 →
    không chạy. Nay qua `engines.llm.chat` bằng giao thức JSON {tool,args}|{final}.
  - Sửa: plan dự phòng (khi model plan lỗi) dùng `SIMPLE_MODEL` thay vì `TRANSLATE_MODEL` (máy đặt model local nhỏ
    cho dịch phụ → Aurora lỗi là cả bảng nhân vật do model 12B lập).
  - «Cài đặt AI» thêm bộ cài sẵn «Khuyến nghị»: DeepSeek cho soát/dịch, pass tên, pass xưng hô, Agent; Aurora cho plan
    (`PLAN_MODE=file`) + Agent dự phòng; local cho dịch chữ trên hình và sửa job treo.
- **A14 song song theo nhà cung cấp + cỡ lô tự tính** (cần restart :8806 + build lại app) — DeepSeek/Alibaba gửi
  32 lô cùng lúc (chỉnh «Song song» ở dòng nhà cung cấp trong «Cài đặt AI»; Aurora/local vẫn 8). Cỡ lô = số câu ÷ số
  request song song (40–200 câu, không vượt «Trần output») → cả phim xong trong ~1 đợt. Lô 200 câu cần trần output
  ~18 000 token: đặt «Trần output» DeepSeek ≥ 20 000 nếu model hỗ trợ (mặc định 8 192 → lô tối đa 84 câu).
- **A13 phim rất dài + giảm token** (cần restart :8806 + build lại app) — chế độ cả phim (DeepSeek/Alibaba):
  phần đầu gọn (bỏ cột giây); phim có phần đầu > `SIMPLE_FULL_MAX_TOKENS` (mặc định 40 000, ~1 500–2 000 câu) → chia
  CHƯƠNG, mỗi chương phần đầu riêng (plan lọc theo chương + câu của chương ± `SIMPLE_CHAPTER_MARGIN`=100 câu), lô không
  vắt chương; lô đầu mỗi chương chạy trước để làm nóng cache rồi mới gửi đồng loạt; lô mặc định 80 câu, tự thu nhỏ cho
  vừa trần output (~90 token/câu); trần output chỉnh theo nhà cung cấp trong «Cài đặt AI» (`DEEPSEEK_MAX_TOKENS`).
- **A12 bảng nhân vật của bộ không bị ghi đè âm thầm** (cần restart :8806 + build lại app) — docs/fixes/2026-11-15-series-glossary-conflicts.md.
  Trước: mỗi lần lập plan tập mới, bảng THÔ của tập đó đè lên bảng bộ (tên, giới tính, xưng hô, thuật ngữ) → một tập
  AI đoán sai tên là mọi tập sau sai theo. Nay: khác bản đã chốt → giữ bản cũ, ghi xung đột; desktop nút «Xung đột tên».
- **A11 luồng dịch V6 bỏ hẳn NMT** (cần restart :8806 + build lại app) — bước 1 chỉ lập plan; AI (DeepSeek, Aurora…)
  dịch thẳng từ câu Trung ở bước soát, có plan + ngữ cảnh. Không còn nạp model NMT lên GPU ở luồng V6 (kể cả khi
  `MT_ZH_VI=1`). Bản Việt có sẵn từ lần chạy trước vẫn giữ và được soát. Luồng cũ `TRANSLATE_PIPELINE=legacy` giữ NMT.
  Hệ quả: «nhận nháp chưa soát» chỉ còn tác dụng với câu đã có bản Việt từ trước — câu AI không trả được → job chờ thử lại.
- **A8 soát theo ngữ cảnh cả phim** (cần restart :8806) — model DeepSeek/Alibaba: mọi lô soát và vá gửi CÙNG một phần
  đầu (plan đầy đủ + toàn bộ câu Trung, không có VI) → nhà cung cấp cache được, model thấy cả phim; lô 60 câu (ít lần
  gọi hơn). Model khác giữ y cách cũ (lô 30, ±4 câu). Khoá `SIMPLE_CONTEXT`, `SIMPLE_FULL_BATCH`, `SIMPLE_FULL_MAX_TOKENS`.
- **A10 vì sao câu bị sửa** (cần restart :8806 + build lại app) — mỗi câu soát ghi giữ nháp / máy đã sửa / dịch mới
  kèm lý do model trả; tab Bản dịch: nhãn, lọc «Máy đã sửa», nút «Lịch sử» (nháp → soát → vá → hậu kỳ, cũ → mới).
- **Sửa:** Cài đặt AI khoá «Phân tích xưng hô» chỉ cho Aurora cả khi `PRONOUN_ANALYZE_MODE=full/fast` (không cần upload).
- **Không ghi cứng tên model DeepSeek/Alibaba** (cần restart :8806 + build lại app) — `deepseek-chat` /
  `deepseek-reasoner` đã bị DeepSeek khai tử (sau 24/07/2026) mà bộ cài sẵn đang dùng. Nay: nút «Lấy danh sách model»
  ở dòng nhà cung cấp (GET /models bằng key, nạp vào ô gợi ý); bộ cài sẵn DeepSeek/Alibaba hỏi danh sách thật rồi chọn
  bản «flash»; `deepseek/` thiếu tên bị từ chối. API `POST /api/v6/ai/models`. docs/design/2026-11-15-ai-providers.md.
- **Cài đặt AI trên desktop** (cần restart :8806 + build lại app) — nút «Cài đặt AI»: bảng từng chức năng (soát/dịch simple,
  plan nhân vật, tên, đại từ, ghép cặp, dịch V5, Agent + dự phòng, hang fix, phân tích đại từ, meta, thumbnail) → khoá cấu hình
  → model ĐANG DÙNG thật + nguồn; nút «Thử»; bộ cài sẵn DeepSeek/Alibaba/Aurora/Local; nhà cung cấp (URL, key chỉ ghi, hiện
  `****1234`). Một nguồn: `core/ai_models.py`, API `GET/POST /api/v6/ai`, `POST /api/v6/ai/test`. Từ chối model 9router và
  model không phải Aurora cho chức năng cần upload file.
- **Sửa:** plan nhân vật chế độ `sample` bỏ qua `PLAN_MODEL`; `PLAN_MODE=file` với model không phải Aurora → tự dùng sample;
  `core.config.save_config` không tồn tại (trang cài đặt cũ `/api/settings` lỗi khi lưu); OCR dịch chữ trên hình và Context
  Studio đọc model lúc import / chỉ env → đọc cấu hình lúc gọi; hang fix hết model cứng.
- **Nhà cung cấp AI qua API: DeepSeek + Alibaba (Qwen)** — `engines/providers/` (mỗi nhà một file, lõi chung `_openai.py`).
  Dùng bằng tiền tố model: `deepseek/<tên>`, `alibaba/<tên>`; key `DEEPSEEK_API_KEY`, `DASHSCOPE_API_KEY`.
  Áp cho mọi chức năng gọi `llm.chat` và cho Agent (có gọi công cụ). **Bỏ 9router khỏi mặc định**: `LLM_BASE_URL` mặc định
  llama local, model `ollama/…-cloud` chỉ chạy khi cấu hình rõ. Thiết kế + giới hạn: docs/design/2026-11-15-ai-providers.md.
  Cần restart :8806; chủ dự án tự đổi các khoá trong `pineline.config.json` đang trỏ 9router.

## 2026-11-14
- **Sửa theo review** (cần restart :8806) — docs/fixes/2026-11-14-review-fixes.md: films.db dùng chung không còn bị
  instance này đánh dấu / xoá job của instance khác (cột `work_root`); ghi nhật ký hàng chờ ra luồng riêng (không giữ
  khoá); Thư viện phim chỉ đồng bộ khi cũ > 2 phút + nút «Đồng bộ»; feeder tính đường dẫn trạng thái lúc gọi; bỏ nuốt lỗi
  im lặng; `/api/health` dùng helper dung lượng có sẵn. Test không còn ghi vào `data/films.db` thật.
- **Desktop đợt 2** (cần restart :8806 + build lại app): sửa câu Trung (✎ trong tab Bản dịch); tab Giọng đọc có «Nghe mẫu»
  và «Thêm giọng clone» (upload file mẫu); nút **Thư viện phim** (đồng bộ, tìm/lọc, mở job, «Đăng YouTube» có hỏi + chọn chế độ);
  nút **Agent** (chat, mặc định chỉ đọc); app Tauri hỏi xác nhận khi đóng lúc còn job chạy (hộp thoại Windows, crate `rfd`).
  `/api/health` thêm `running`, `routers`.
- **Sửa:** trang Phim cũ `/films` hỏng do route trùng (luôn trả HTML); film_db / auto_publisher / feeder / meta_studio /
  batch_config đọc work root của INSTANCE thay vì cứng `ROOT/work` — docs/fixes/2026-11-14-films-route-work-root.md.
- **Desktop: sửa bản dịch, giọng đọc, cài đặt** (cần restart :8806 + build lại app). Khung chi tiết job có 4 tab:
  Tổng quan · **Bản dịch** (lọc câu bị cờ / chưa soát / trống / sửa tay, tìm, sửa câu Việt, nghe và đọc lại từng câu) ·
  **Giọng đọc** (giọng / tốc độ / bộ đọc của job) · **Kết quả** (xem video, tải phụ đề). Nút **Cài đặt**: sửa cấu hình batch
  (API mới `GET/POST /api/v6/config`; trước chỉ sửa được qua sidecar). API mới `GET /api/v6/jobs/{id}/script` (chỉ đọc).
  Vẫn 0 request khi để yên. Sửa kèm: `POST /api/jobs/{id}/script-line` ghi atomic và trả 409 khi đang dịch (trước: câu sửa
  tay bị engine ghi đè lúc dịch xong).
- **Hàng chờ 24/7 R7.3–R7.5** (cần restart :8806) — docs/fixes/2026-11-14-queue-24x7.md: 3 job lỗi liên tiếp → hàng chờ
  TẠM DỪNG + báo (`QUEUE_MAX_CONSEC_FAIL`, 0 = tắt), gỡ bằng «Chạy tiếp hàng chờ»; nhật ký hàng chờ ra file + nút «Nhật ký»;
  tuỳ chọn `QUEUE_HOLD_YIELD_SEC` (mặc định 0 = như cũ) cho job HOLD cần người nhường ghế. Thay đổi từ sidecar/CLI lên UI
  trong ~1 s (trước tới 30 s). `/api/health` có hàng chờ, ổ đĩa, phiên bản, cảnh báo. Desktop: nút «Làm lại» một bước có xác nhận.
- **Hàng chờ R7.1–R7.2** (cần restart :8806) — docs/fixes/2026-11-14-queue-worker-robust.md: ghi `_job_queue.json`
  lỗi (đĩa đầy / file bị khoá) không còn giết luồng worker (trước: mọi job phía sau đứng im lặng) — thử lại, ghi log,
  `persist_ok` trong `/api/queue/jobs`, UI desktop cảnh báo; job đang giữ ghế chạy lại trước dù hàng chờ bị sắp lại.
- **Render R6.1** (cần restart :8806): ffmpeg 8 đã bỏ `-filter_complex_script` → mọi lần render sẽ lỗi «Unrecognized
  option» trên máy nâng ffmpeg. `engines/render.py` `filter_script_opt()` dò một lần và dùng `-/filter_complex` khi
  ffmpeg nhận (≥ 7), không thì giữ tên cũ. Lộ ra nhờ CI chạy trên Windows với ffmpeg mới nhất.
- **Kế hoạch + mô hình làm việc:** `docs/ROADMAP.md` (G0 chạy thật → G1 độ bền 24/7 → G2 desktop đủ chức năng → G3 cấu trúc),
  `docs/WORKING_MODEL.md` (vòng làm việc, 3 mức «đã kiểm», bảng chờ kiểm trên Windows, lịch rà từng khu vực);
  `CLAUDE.md` sửa cho đúng hiện trạng (lõi vẫn là server.py, chưa cutover, chưa chạy job thật).
- **CI** (`.github/workflows/ci.yml`): pytest Ubuntu + Windows, `cargo check` desktop. Bộ test 0 fail trên Linux
  (sửa 3 kiểm thử lệch nền tảng; khoá một-bản của sidecar nay có tác dụng cả ngoài Windows).
- **Dịch simple R1.1–R1.4** (cần restart :8806) — docs/fixes/2026-11-14-translate-simple-r1.md: «Soát lại» khi cloud
  chết không còn báo xong giả; plan hỏng không bị lưu/dùng lại, job park chờ lập lại (`SIMPLE_REQUIRE_PLAN`, mặc định 1);
  lỗi code → stage failed ngay + `translate_error.log` (không park 15 phút); giờ stage dịch ISO UTC.
- **Desktop R2.1–R2.2** (cần build lại app, **chưa kiểm trên Windows**) — docs/fixes/2026-11-14-desktop-kill-tree-early-exit.md:
  đóng app giết cả cây tiến trình (`taskkill /T /F`); backend chết lúc khởi động báo ngay kèm đường dẫn log.
- **UI desktop `/v6/` có chức năng** (`desktop/ui/` viết lại — trước chỉ là bảng xem): thêm job (link / đường dẫn máy),
  tick nhiều job → Xếp hàng chạy / Gỡ khỏi hàng chờ, Dừng hàng chờ; khung chi tiết job: Chạy tiếp (gỡ HOLD), Xếp hàng
  chạy, Dừng, chạy/chạy lại từng bước, Mở thư mục, Xoá job. Vẫn 1 kết nối SSE, **để yên = 0 request** (đo: 20 s không
  request nào; `/ui3` ~6 request/30 s). Chỉ gọi API khi bấm nút, mở chi tiết, hoặc job đang mở / hàng chờ đổi theo event.
  Chưa có: sửa lời thoại, chọn giọng, cấu hình render, Phim, Hongguo (link sang trang cũ).
  Tauri vẫn mở `/v6/` mặc định; đổi trang: env `PINELINE_DESKTOP_PATH` hoặc `"desktop_path"` trong instance JSON.
  `DELETE /api/jobs/{id}` báo index ngay (trước: job đã xoá còn trong danh sách tới 30 s). Màn chờ ghi đúng 180 s.
  Kiểm ở cloud bằng Chromium + backend instance test; **chưa chạy trong cửa sổ Tauri/WebView2 và chưa với job thật**.
  Cần restart :8806 (server.py) + build lại app (`desktop\dev.bat`).
- Fix UI cũ `/` (`index.html`): thiếu khai báo `selectedStep`, `panelJobId`, `lastPanelSig` → console «refresh: lỗi vẽ
  ReferenceError: selectedStep is not defined», khung chi tiết job không vẽ. Thêm `let … = null` (như `index3.html`).
  Lỗi có sẵn từ V4/V5. Không cần restart — hard refresh trang (Ctrl+F5).

## 2026-11-13
- Fix review 4 bug (cloud, **chờ restart :8806** + chạy `verify_all.bat` trên Windows) — docs/fixes/2026-11-13-review-sse-timestamp-testdata-bat.md:
  (1) SSE `/api/events` chuyển generator async (`events.bus.subscribe_async`/`sse_stream`) — bản đồng bộ giữ 1 thread
  threadpool/client, ~40 kết nối là server đứng; (2) `started_at`/`finished_at` thống nhất ISO UTC (`core/timeutil.py`;
  `state.job_state`, heal `server.py`, `engines/render_job.py`), `film_db`/feeder/ETA đọc được dữ liệu cũ lẫn epoch;
  (3) `agents/auto_publisher.py` tính file runtime theo `DATA_DIR` lúc gọi + test dùng films.db tạm → pytest không còn
  ghi `data/` thật; bỏ theo dõi git `data/auto_publish_queue.json` + `auto_publish_state.json` (chứa rác test `job2`);
  (4) `.gitattributes` ép `*.bat` CRLF. `.gitignore` thêm `pineline.config.json`/token/cookies (file CHƯA gỡ khỏi git).
- Port V2 → V6 (agent R6, từ V2 agent N): brain/heal/watch bỏ job ĐÃ XONG (`agents.agent_hold.job_finished` — đã đăng,
  parent đã ghép Full, part của parent đó, mux+output); 3 detector heal gốc; tin tạm dừng đúng loại (park tự thử lại ⏸ ≠
  HOLD cần người ⛔); «Chạy tiếp»/chạy tay/job xong gỡ khoá park. docs/fixes/2026-11-13-port-brain-finished-hold.md
- Port V2 → V6 (agent Q6): preflight + chuẩn hoá video nguồn (`engines/source_preflight.py`, mọi `_find_video` +
  `resolve_asr_source` + render worker qua `stage_source_video`; import gắn `preflight_pending`; GET chỉ đọc),
  `media_ts.extract_pcm_from_video` bền (K/K2 + chỗ gọi của chủ dự án), FireRed chia slice tại khoảng lặng + đệm chồng lấn,
  câu ASR rác `engines/text_junk.py` (FireRed lọc; simple tự soát «…»), simple: lượt review câu thiếu + nhận nháp
  ≤ `SIMPLE_UNREVIEWED_TOLERANCE`, parent auto-split giữ ghế khi part park/HOLD (`held_at=split_parts`), cur_text parent
  «đã ghép từ N parts». Ghi qua `state.job_state`; cờ instance tuỳ chọn `video_preflight`/`video_normalize`; tools
  `preflight_scan`, `asr_boundary_scan` (work root instance). docs/fixes/2026-11-13-port-preflight-slice-junk.md

## 2026-11-12
- Fix sau port (agent J): index job cách ly theo work root instance — `state/index.py` `load_all` GẮN + nạp lại
  (không cộng dồn), hook ghi manifest → `apply_dir` bỏ job ngoài work root, app shutdown `unbind`. Trước đó test
  brain/hold P6 (chạy trước test_app_integration) ghi thư mục tạm → /api/v6/jobs + snapshot SSE lẫn
  '2937ffd7989f','f0834acf0cc0','jB' (không phải job thật, không ghi đĩa thật). Ratchet bỏ qua `_scratch*`/`scratch*`/
  `_to_delete*`; `_scratch_agentB` + `scratch/agentP6/new` → `_to_delete/scratch_20261112/`. Test restart_server giải mã
  UTF-8 (Windows cp1252 → 0x8f). Test mới `tests/test_index_instance_isolation.py`.
- Port V2 → V6 (agent P6): Second Brain tin Telegram theo mẫu (`agents/agent_brain_notify.py`, mặc định `events`/600 s),
  gỡ HOLD cũ + gỡ HOLD khi chạy tay (`agents/agent_hold.py`, 5 route server, lệnh Telegram/chat Agent của người = tay — I2),
  hoãn heal 180 s + không heal job đang chạy, đuôi log engine/render worker khi lỗi, luồng dịch THEO JOB
  (`cfg.job_pipeline`, đóng dấu `manifest.translate_pipeline`; Dịch LẠI = mặc định instance), `js.try_mutate`.
  docs/fixes/2026-11-12-port-brain-hold-pipeline.md
- Port V2 → V6 (F6): luồng dịch simple (`engines/translate_simple/` + Stage `pipeline/stages/simple_translate.py` +
  Gate `pipeline/gates/simple_translate_gate.py`, ghi agent qua `state.job_state.apply_simple_gate`), Demucs trước ASR
  (`engines/separate.py` chia khúc khoảng lặng/đúng mẫu, `engines/asr/asr.py` fallback), `engines/cloud_retry.py`,
  `data/lexicon/`, `tools/{collect_manual_edits,simple_stats}.py`. Mặc định simple + Demucs=1; cờ instance
  `translate_pipeline` (legacy = rollback), `asr_use_demucs`. docs/fixes/2026-11-12-port-simple-demucs.md

## 2026-11-11
- F0: khung state/events/pipeline/runtime/workers, Tauri shell, UI SSE.
- F0 fix: backend không lên (import fastapi), encoding Windows, SSE đứt (generator đồng bộ).
- F1: nhập code V5; cô lập instance (WORK_DIR, sidecar, bỏ D:\PinelineV2_work cứng); một đường ghi manifest
  (hook + watcher); 110 `.save()` → state (11 ngữ nghĩa, 99 persist); ratchet test.
- F2: `core/job_opts.py` — một nguồn opts job (tường minh > ghim > batch > mặc định); UI/hàng chờ/agent cùng opts
  (test gate). Sửa bug route run-all gắn nhầm vào `_req_audio` (có cả ở V2/V4/V5 — chưa sửa ở đó).
- F3 tích hợp: `python -m app` phục vụ cả API V5 (server.py) lẫn V6 (/api/health, /api/events, /api/v6/jobs, UI /v6/)
  trên 1 cổng; cờ instance chặn brain/watch/telegram/auto-publish/hg-resume; UI cũ hết polling 2 s (SSE + live_sync.js);
  GET /api/jobs/{id} không ghi; cache script.json trong list_jobs; js.persist 99→98.
- Review F2/F3 (review1): cờ instance mới `title_translate`, `queue_autostart` (v6/test tắt); hàng gộp Hongguo theo
  WORK_DIR (trước dùng chung `<ổ>/work/logs` với V2) và GET merge-queue tôn trọng `hg_merge_resume`; khoá backend theo work
  root; live_sync.js: CLOSED → poll, gộp tiến độ 2 s, dự phòng 5 s khi job chạy, tab ẩn. docs/reviews/2026-11-11-review-f2-f3.md
- F5 (render tiến trình con): `engines/render_job.do_render` = một nguồn logic render (thân `_do_render` cũ);
  `workers/render_worker.py` chạy nó ở tiến trình con (không importlib.reload; sửa render có hiệu lực job kế; Dừng kill
  worker; lỗi → mark_failed). Cờ instance `render_subprocess` (v6/test bật, prod tắt tới khi so job thật). js.persist 98→93.
- F4 (services + sidecar thuần): `services/` (feeder, autopilot, film_db_sync, publisher, hongguo_flow, background);
  agent tools running/lib/feed gọi thẳng services (bỏ vòng server→sidecar /cmd); việc nền chạy trong server theo cờ `feeder`
  (v6/test tắt, prod bật); sidecar `--instance` (cổng instance, Telegram theo cờ, không vòng nền) + `run_sidecar_v6.bat`;
  bỏ cổng cứng 8800/8801/8802/8804/8805 khi thiếu env (core.instance.server_base/sidecar_url). tree_audit cổng 8→0.
  docs/fixes/2026-11-11-f4-f5.md
- Review F4/F5 (review2): UI cũ (index/index3/hongguo/films_ui) gọi cứng sidecar/server V2 :8802/:8801 → đổi lúc
  phục vụ sang instance (`core.instance.localize_html`); restart_server/migrate_work chỉ nhắm tiến trình của CÂY + instance
  (so token cổng/instance + cwd), bỏ env PORT lạc, từ chối cổng ngoài V6 (8801/8802/8000/8083) trừ `--allow-foreign-port`,
  bỏ run_v5/run_v2/run_tele_cmd.bat, Telegram theo cờ; Dừng render kill CẢ CÂY worker (ffmpeg/demucs cháu, hết kẹt pipe);
  sidecar chạy trần mặc định instance v6; work_dir tuyệt đối cho worker; run-watch nhận server `-m app`.
  docs/reviews/2026-11-11-review-f4-f5.md
- F6 lượt kiểm cuối: watcher theo dõi job ngay khi spawn render worker / engine (`state.watcher.watch_job/watching`) —
  tiến độ con lên SSE không chờ quét 30 s; uploader settings mặc định WORK_DIR/TEMP theo instance (bỏ cứng V5);
  B11: `engines/mux.py` (code chết) → _to_delete, bỏ import `run_asr` thừa, test hàm thuần agent_feeder/agent_tele_cmd/
  clone_tts; `verify_all.bat` + `scripts/verify_all.py` (pytest V6 + v5, tree_audit, smoke backend :8899, bảng PASS/FAIL);
  brain V6 (`docs/brain/`); bàn giao `docs/V6_HANDOFF.md`. docs/fixes/2026-11-11-f6-final.md
- machine-config: `core/machine.py` — cổng/thư mục phụ thuộc máy thành key config (mặc định = máy gốc, không đổi hành vi):
  LLAMA_BASE_URL (trống → LLM_BASE_URL nếu localhost → :8083), AUTO_SWITCH_PRESETS, MODEL_PRESETS_DIR, MODEL_START_SCRIPT,
  MODEL_START_CMD, CHATGPT/AURORA_BASE_URL, AURORA_FALLBACK_URLS, CLOUD_LLM_BASE_URL, HONGGUO_BASE_URL, MT_MODEL_DIR,
  LIBRARY_DIR, YT_UPLOADER_DIR, LIB_SAMPLE_DIR. preset_switcher bỏ qua (không lỗi) khi thiếu presets/AUTO_SWITCH_PRESETS=0;
  Aurora hỏng → lỗi rõ + gợi ý đổi model. Doctor `scripts/check_env.py`, `docs/SETUP_MAY_MOI.md`, 14 test. Port sang V5 + V4.
  docs/fixes/2026-11-11-machine-config.md
