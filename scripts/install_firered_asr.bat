@echo off
REM Cai FireRedASR2S + tai model AED/VAD/LID/Punc ve D:\AI_CACHE\firered
REM KHONG pip install requirements.txt full (torch cu118 pin) — dung torch Pineline hien co.
setlocal
cd /d "%~dp0.."

set ROOT=%CD%\vendor\FireRedASR2S
set MODELS=D:\AI_CACHE\firered

if not exist "vendor" mkdir vendor
if not exist "%ROOT%" (
  echo [1/3] Clone FireRedASR2S...
  git clone --depth 1 https://github.com/FireRedTeam/FireRedASR2S.git "%ROOT%"
) else (
  echo [1/3] Da co %ROOT%
)

echo [2/3] pip deps (khong pin torch)...
python -m pip install "cn2an" "kaldiio" "kaldi_native_fbank" "sentencepiece" "soundfile" "textgrid" "peft>=0.13.2"

if not exist "%MODELS%" mkdir "%MODELS%"
echo [3/3] Download models vao %MODELS% ...
set FIRERED_MODELS=%MODELS%
python scripts\download_firered_models.py

echo.
echo OK. Pineline config:
echo   FIRERED_ROOT   = Z:/Pineline/vendor/FireRedASR2S
echo   FIRERED_MODELS = %MODELS:\=/%
echo Chon engine firered_asr trong UI de thu.
endlocal
