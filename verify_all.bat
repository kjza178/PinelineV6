@echo off
rem Kiem toan bo Pineline V6 MOT LAN (Windows). Khong chay job that, khong GPU/LLM, khong dung V2 (:8801/:8802).
rem   a) pytest test V6   b) pytest tests\v5   c) tree_audit   d) smoke backend instance test (:8899)   e) bang PASS/FAIL
rem Log: scratch\verify_all_<thoi gian>.log  -  Chon buoc: verify_all.bat --only a,d
cd /d "%~dp0"
chcp 65001 >nul
setlocal
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set AGENT_BRAIN=0
set AGENT_WATCH=0
set AGENT_TELEGRAM=0
if not exist scratch mkdir scratch
python -X utf8 scripts\verify_all.py %*
set RC=%ERRORLEVEL%
echo.
if "%RC%"=="0" (echo VERIFY_ALL: PASS) else (echo VERIFY_ALL: FAIL - xem scratch\verify_all_*.log)
pause
endlocal & exit /b %RC%
