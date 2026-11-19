@echo off
REM Cai FireRedASR2S + tai model AED/VAD/LID/Punc ve vendor\FireRedASR2S\pretrained_models (portable, trong cay)
REM KHONG pip install requirements.txt full (torch cu118 pin) — dung torch Pineline hien co.
setlocal
cd /d "%~dp0.."
call scripts\pick_python.bat

set ROOT=%CD%\vendor\FireRedASR2S
set MODELS=%ROOT%\pretrained_models
if defined FIRERED_MODELS set "MODELS=%FIRERED_MODELS%"

if not exist "vendor" mkdir vendor
if not exist "%ROOT%" (
  echo [1/3] Clone FireRedASR2S...
  git clone --depth 1 https://github.com/FireRedTeam/FireRedASR2S.git "%ROOT%"
) else (
  echo [1/3] Da co %ROOT%
)

echo [2/3] pip deps (khong pin torch)...
"%PY%" -m pip install "cn2an" "kaldiio" "kaldi_native_fbank" "sentencepiece" "soundfile" "textgrid" "peft>=0.13.2"

if not exist "%MODELS%" mkdir "%MODELS%"
echo [3/3] Download models vao %MODELS% ...
set FIRERED_MODELS=%MODELS%
"%PY%" scripts\download_firered_models.py

echo [4/4] Ghi FIRERED_ROOT / FIRERED_MODELS vao pineline.config.json (trong = mac dinh trong cay, portable)...
set "_CFG_MODELS="
if /I not "%MODELS%"=="%ROOT%\pretrained_models" set "_CFG_MODELS=%MODELS%"
"%PY%" -c "from core.config import save_config; save_config({'FIRERED_ROOT': '', 'FIRERED_MODELS': r'%_CFG_MODELS%'})"
echo.
echo OK. FIRERED_ROOT   = %ROOT%
echo     FIRERED_MODELS = %MODELS%
echo Kiem tra: python scripts\check_env.py  ^|  Cai dat - Giong doc - Nhan dang tieng Trung: FireRed ASR
endlocal
