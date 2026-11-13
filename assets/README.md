# Pineline assets

Thư mục asset cho render **copyright-safe** (bỏ nhạc/gốc có bản quyền, thêm lớp uniqueness).

| Thư mục | Vai trò |
|---------|---------|
| `luts/` | File `.cube` (LUT). Mặc định: `soft_contrast.cube` |
| `overlays/` | `grain.png` (film grain loop) |
| `bgm/` | Nhạc nền **thư viện của bạn** (mp3/wav/m4a/flac) — **không** dùng BGM gốc Bilibili |

## Tạo sample

```bash
python scripts/gen_sample_assets.py
```

## Bản quyền (quan trọng)

- Zoom / LUT / grain / đổi BGM **không** tự thành “fair use” pháp lý.
- Pipeline mặc định: **không lấy nhạc gốc**; chỉ giọng Việt + (tuỳ chọn) BGM trong `bgm/` mà **bạn có quyền dùng**.
- `sample_ambient.mp3` chỉ để thử kỹ thuật — thay bằng track royalty-free / tự làm / đã mua license trước khi upload YouTube.

Env (tuỳ chọn):

- `RENDER_LUT_FILE` — đường dẫn `.cube`
- `RENDER_GRAIN_FILE` — đường dẫn grain PNG
- `RENDER_BGM_FILE` — cố định 1 file BGM (không random)
- `RENDER_BGM_VOL` — âm lượng BGM thư viện (mặc định `0.14`)
