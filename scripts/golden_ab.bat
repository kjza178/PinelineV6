@echo off
rem A/B co dich translate-b3 tren golden silver (Z:\PinelineV6\data\golden).
rem CANH BAO: dung GPU (NMT) + GPT/Aurora (ton quota). CHI chay khi V2 RANH (hang doi :8801 trong).
rem Khong doi preset llama (PINELINE_FLAG_PRESET_SWITCH=0), khong ghi vao D:\PinelineV2_work.
rem Job tam: D:\PinelineV6_work\golden_ab\<job>_<bien_the>\ . Ket qua: docs\reviews\2026-11-12-golden-ab.md
rem Cach dung:
rem   golden_ab.bat                 = dry-run (in ke hoach, khong chay gi)
rem   golden_ab.bat run             = chay tat ca moi job x moi bien the (so luot that in ra truoc khi chay; hoi YES)
rem   golden_ab.bat run --variants base,regex_off --jobs 9dedde16e35b
rem   golden_ab.bat score           = cham lai cac job tam da chay
rem   golden_ab.bat source          = khong LLM: cham script.json hien co cua V2 (kiem pipeline)
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set AGENT_BRAIN=0
set PINELINE_FLAG_PRESET_SWITCH=0
cd /d Z:\PinelineV6
rem %* giu nguyen dau phay (cmd tach %2 %3 theo dau phay: base,regex_off -> 2 tham so)
set "ALL=%*"
set "REST="
if not "%~1"=="" call set "REST=%%ALL:*%~1=%%"
set PY=python
if exist Z:\PinelineV2\venv\Scripts\python.exe set PY=Z:\PinelineV2\venv\Scripts\python.exe
if "%1"=="" goto dry
if /I "%1"=="run" goto run
if /I "%1"=="score" goto score
if /I "%1"=="source" goto source
goto dry
:dry
%PY% scripts\golden_ab.py --dry-run %REST%
goto end
:run
%PY% scripts\golden_ab.py %REST%
goto end
:score
%PY% scripts\golden_ab.py --score-only %REST%
goto end
:source
%PY% scripts\golden_ab.py --score-source --out-md scratch\golden_ab_source.md --out-json scratch\golden_ab_source.json %REST%
goto end
:end
pause
