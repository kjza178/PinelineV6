@echo off
rem So sanh ASR FireRed: audio goc (ASR_USE_DEMUCS=0) vs giong tach Demucs (=1) - V2 fix 2026-11-12.
rem CANH BAO: dung GPU (Demucs ~4-6GB + FireRed). CHI chay khi V2 RANH (hang doi :8801 trong).
rem Khong goi HTTP, khong kill/doi preset :8000 / :8083. D:\PinelineV2_work va D:\Lib CHI DOC.
rem Ban sao: D:\PinelineV6_work\asr_demucs_cmp\<job>\{src,off,on,clips}\
rem Bao cao: Z:\PinelineV6\docs\reviews\2026-11-12-asr-demucs-compare.md
rem Cach dung:
rem   asr_demucs_compare.bat                       = dry-run (in job se chon, khong chay)
rem   asr_demucs_compare.bat run                   = 3 job tu chon, 12 phut dau (hoi YES)
rem   asr_demucs_compare.bat run --jobs 32da3d8f225b,2493d3c7cb29_p01 --minutes 15 --ocr
rem   asr_demucs_compare.bat report                = cham lai tu ket qua da chay
rem   asr_demucs_compare.bat golden                = vocals cho 4 cua so golden -> scratch\voice_exp\data\*.vocals.wav
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set AGENT_BRAIN=0
set PINELINE_FLAG_PRESET_SWITCH=0
set "TEMP=D:\PinelineV6_work\tmp"
set "TMP=D:\PinelineV6_work\tmp"
if not exist "%TEMP%" mkdir "%TEMP%"
cd /d Z:\PinelineV6
set PY=python
if exist "Z:\PinelineV2\pineline_env\Scripts\python.exe" set "PY=Z:\PinelineV2\pineline_env\Scripts\python.exe"
if exist "C:\Python312\python.exe" set "PY=C:\Python312\python.exe"
echo [asr_demucs_compare] python = %PY%
"%PY%" scripts\asr_demucs_compare.py %*
