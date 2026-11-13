# Cài Pineline trên máy mới

Bộ cài **tự nhận diện** OS + GPU, dựng môi trường, cài đúng thư viện. Chỉ cần Python và
mạng (model tự tải lần đầu).

## Yêu cầu tối thiểu
- **Python ≥ 3.10** (khuyên 3.12) — https://python.org
- **ffmpeg** (bắt buộc, xử lý audio/video) — thêm vào PATH
- **Ollama** (cho bước DỊCH) — https://ollama.com , rồi `ollama pull gemma4`
- **GPU NVIDIA + CUDA** (khuyên, nhanh hơn nhiều). Không có GPU vẫn chạy được (CPU).

## Cài (1 lệnh)
Từ repo root (`PinelineV5`):
```bash
python scripts/install/install.py
```
Tự detect GPU/CPU. Ép tay nếu muốn:
```bash
python scripts/install/install.py --cpu               # không dùng GPU
python scripts/install/install.py --gpu --cuda cu121  # GPU, chọn build CUDA
python scripts/install/install.py --skip-vieneu       # bỏ qua env TTS
```
Nó tạo `pineline_env` (app) + `vieneu_env` (TTS) **cạnh thư mục Pineline**, cài
torch/torchaudio + requirements, và ghi `pineline.config.json` ở **root** repo.

## Chạy app
```bash
# Windows (từ repo root)
..\pineline_env\Scripts\python.exe server.py
# Linux/macOS
../pineline_env/bin/python server.py
```
Mở trình duyệt: http://127.0.0.1:8805

## Model tải lần đầu (tự động, cần mạng)
- **VieNeu-TTS v3-Turbo** (lồng tiếng) — từ HuggingFace
- **faster-whisper** (nhận diện lời) · **RapidOCR** (chữ trên hình) · **Demucs** (tách nền)
Lưu ở `HF_HOME` (mặc định `./.cache/hf`). Đổi chỗ: sửa `HF_HOME` trong config.

## OmniVoice (TÙY CHỌN — engine lồng tiếng phụ)
VieNeu là engine chính, đủ dùng. Muốn thêm OmniVoice:
1. Clone repo OmniVoiceStudio, tạo venv, cài omnivoice.
2. Điền vào `pineline.config.json`:
   ```json
   "OMNI_PY": "<đường dẫn>/.venv/Scripts/python.exe",
   "OMNI_CWD": "<đường dẫn repo OmniVoiceStudio>"
   ```

## pineline.config.json — chỉnh tay khi cần
| Khóa | Ý nghĩa |
|---|---|
| `VIENEU_PY` | python của venv VieNeu |
| `OMNI_PY` / `OMNI_CWD` | python + thư mục OmniVoice (bỏ trống nếu không dùng) |
| `HF_HOME` | nơi lưu model tải về |
| `VIENEU_DEVICE` / `OMNI_DEVICE` | `cuda` hoặc `cpu` |

Mọi khóa cũng đọc được từ **biến môi trường** (ưu tiên cao hơn config).

## Biến môi trường tinh chỉnh (tùy chọn)
- `OMNI_NUM_STEP` (mặc định 48) — nhiều bước hơn = OmniVoice ổn định hơn, chậm hơn.
- `CLONE_CLEANUP` (1) — fade 2 đầu clip. `CLONE_HISHELF` (0) — hạ tần cao (giảm gắt).
- `HARDSUB_BLUR_W`/`HARDSUB_BLUR_H` — kích thước blur hardsub (khi không dò được).

## Lỗi thường gặp
- **`No space left on device` (hết ổ khi cài torch)**: torch CUDA ~2.5GB/env.
  install.py đã **tắt cache pip + đẩy TEMP về cùng ổ** để đỡ đầy ổ C:. Nếu vẫn hết:
  - Dọn ổ, hoặc đặt venv sang ổ nhiều chỗ: `python install.py --envs-dir E:/envs`
  - Hoặc **`python install.py --cpu`** (torch CPU chỉ ~200MB thay vì ~2.5GB).
  install.py giờ **báo trước dung lượng trống** và cảnh báo nếu thiếu.
- **`torch ... DLL / CUDA`**: sai build CUDA. Chạy lại `install.py --gpu --cuda cuXXX`
  đúng phiên bản CUDA máy (xem `nvidia-smi`), hoặc `--cpu`.
- **`ffmpeg not found`**: chưa thêm ffmpeg vào PATH.
- **Dịch không chạy**: chưa bật Ollama / chưa `ollama pull` model.
