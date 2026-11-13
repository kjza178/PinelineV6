@echo off
REM Cai onnxruntime-gpu cho vieneu_env (speaker/denoiser ONNX dung CUDA neu co).
REM G2P van chay CPU — khong the dua 100%% sang GPU.
cd /d "%~dp0"
set VPY=D:\vieneu_env\Scripts\python.exe
if not exist "%VPY%" set VPY=..\..\..\vieneu_env\Scripts\python.exe
if not exist "%VPY%" (
  echo Khong thay vieneu_env python.
  pause
  exit /b 1
)
echo [1/2] Go onnxruntime CPU...
"%VPY%" -m pip uninstall -y onnxruntime onnxruntime-gpu
echo [2/2] Cai onnxruntime-gpu...
"%VPY%" -m pip install --no-cache-dir onnxruntime-gpu
"%VPY%" -c "import onnxruntime as o; print('providers', o.get_available_providers())"
echo.
echo Xong. Restart Pineline + chay lai TTS.
echo Goi y: set VIENEU_BATCH=16  va  VIENEU_CPU_THREADS=2
pause
