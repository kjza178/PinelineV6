@echo off
REM Cai FunASR KHONG dung torch CUDA (cu128).
REM Loi hay gap: `pip install funasr` keo torch 2.13+cpu -> mat GPU.
setlocal
rem 2026-11-16: cung python voi server (scripts\pick_python.bat); venv -> bo co --user (pip tu choi trong venv)
call "%~dp0..\pick_python.bat"
set "USERFLAG=--user"
"%PY%" -c "import sys; sys.exit(0 if sys.prefix != sys.base_prefix else 1)" && set "USERFLAG="

echo [1/3] Cai funasr + deps (KHONG cai torch)...
"%PY%" -m pip install %USERFLAG% -U funasr modelscope modelscope-hub tiktoken ^
  jieba jamo jaconv kaldiio torch_complex tensorboardX umap-learn pynndescent ^
  oss2 aliyun-python-sdk-core aliyun-python-sdk-kms crcmod jmespath ^
  --no-deps 2>nul
"%PY%" -m pip install %USERFLAG% -U funasr modelscope tiktoken jieba jamo jaconv ^
  kaldiio torch_complex tensorboardX umap-learn pynndescent huggingface_hub ^
  hydra-core omegaconf librosa soundfile sentencepiece rapidfuzz scipy safetensors ^
  transformers websockets PyYAML regex requests tqdm

echo [2/3] Kiem torch CUDA (funasr co the keo torch CPU)...
"%PY%" -c "import torch, sys; sys.exit(0 if torch.cuda.is_available() else 1)"
if errorlevel 1 (
  echo     torch mat CUDA -^> cai lai torch + torchaudio ban cu128
  "%PY%" -m pip install %USERFLAG% --force-reinstall --no-deps torch torchaudio --index-url https://download.pytorch.org/whl/cu128
)

echo [3/3] Kiem tra...
"%PY%" -c "import torch,funasr; assert torch.cuda.is_available(), 'CUDA mat'; print('OK torch', torch.__version__, 'cuda', torch.version.cuda, '| funasr', funasr.__version__)"
if errorlevel 1 exit /b 1
echo Xong.
endlocal
