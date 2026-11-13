@echo off
REM Cai FunASR KHONG dung torch CUDA (cu128).
REM Loi hay gap: `pip install funasr` keo torch 2.13+cpu -> mat GPU.
setlocal
set PY=python
where python >nul 2>&1 || (echo [loi] khong thay python & exit /b 1)

echo [1/3] Cai funasr + deps (KHONG cai torch)...
"%PY%" -m pip install --user -U funasr modelscope modelscope-hub tiktoken ^
  jieba jamo jaconv kaldiio torch_complex tensorboardX umap-learn pynndescent ^
  oss2 aliyun-python-sdk-core aliyun-python-sdk-kms crcmod jmespath ^
  --no-deps 2>nul
"%PY%" -m pip install --user -U funasr modelscope tiktoken jieba jamo jaconv ^
  kaldiio torch_complex tensorboardX umap-learn pynndescent huggingface_hub ^
  hydra-core omegaconf librosa soundfile sentencepiece rapidfuzz scipy safetensors ^
  transformers websockets PyYAML regex requests tqdm

echo [2/3] Khoa lai torch CUDA 2.11+cu128 (neu bi keo)...
"%PY%" -m pip install --user --force-reinstall ^
  "torch==2.11.0+cu128" "torchvision==0.26.0+cu128" "torchaudio==2.11.0+cu128" ^
  --index-url https://download.pytorch.org/whl/cu128

echo [3/3] Kiem tra...
"%PY%" -c "import torch,funasr; assert torch.cuda.is_available(), 'CUDA mat'; print('OK torch', torch.__version__, 'cuda', torch.version.cuda, '| funasr', funasr.__version__)"
if errorlevel 1 exit /b 1
echo Xong.
endlocal
