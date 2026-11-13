@echo off
rem A/B luong dich SIMPLE (translate_simple) vs luong cu tren golden silver (Z:\PinelineV6\data\golden).
rem CANH BAO: dung GPU (NMT HachimiMT) + GPT/Aurora (REVIEW) = TON QUOTA. CHI chay khi V2 RANH
rem (hang doi :8801 trong, khong job dang dich/TTS). Khong doi preset llama, khong ghi vao D:\PinelineV2_work.
rem Job tam: D:\PinelineV6_work\golden_ab\<job>_<bien_the>\ (da co ket qua -> bo qua, khong chay lai).
rem Cach dung:
rem   golden_ab_simple.bat            = THU NHO: 1 job 32da3d8f225b x base,regex_mask_off,simple
rem   golden_ab_simple.bat full       = 4 job x base,regex_mask_off,nmt_hint_regex_mask_off,simple
rem   golden_ab_simple.bat dry        = dry-run thu nho (in ke hoach, khong chay gi)   / dry full
rem   golden_ab_simple.bat score      = cham lai job tam da chay (khong LLM)           / score full
rem   golden_ab_simple.bat heldout    = golden HELD-OUT (data\golden_heldout, 2 job) x base,nmt_hint_regex_mask_off,simple
rem                                     / dry heldout / score heldout. Ket qua: docs\reviews\2026-11-12-golden-ab-heldout.md
rem   golden_ab_simple.bat next       = 4 job x simple,simple_noleak,simple_rules,simple_rules_fill (co thu nghiem
rem                                     translate_simple next; noleak = bo muc lexicon golden_derived) / dry next / score next
rem                                     Ket qua: docs\reviews\2026-11-12-golden-ab-simple-next.md
rem Ket qua: docs\reviews\2026-11-12-golden-ab-simple-try.md (thu nho) / 2026-11-12-golden-ab-simple.md (full)
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set AGENT_BRAIN=0
set PINELINE_FLAG_PRESET_SWITCH=0
set TRANSLATE_PIPELINE=
cd /d Z:\PinelineV6
set PY=python
if exist Z:\PinelineV2\venv\Scripts\python.exe set PY=Z:\PinelineV2\venv\Scripts\python.exe
rem Danh sach bien the/job dat TRONG bat (dau phay khong di qua %%1 -> khong bi cmd tach)
set "VARS=base,regex_mask_off,simple"
set "JOBS=32da3d8f225b"
set "OUT=docs\reviews\2026-11-12-golden-ab-simple-try"
set "GOLD=data\golden"
set "MODE=%~1"
set "SCOPE=%~2"
if /I "%MODE%"=="full" set "SCOPE=full"
if /I "%MODE%"=="full" set "MODE=run"
if /I "%MODE%"=="heldout" set "SCOPE=heldout"
if /I "%MODE%"=="heldout" set "MODE=run"
if /I "%MODE%"=="next" set "SCOPE=next"
if /I "%MODE%"=="next" set "MODE=run"
if "%MODE%"=="" set "MODE=run"
if /I "%SCOPE%"=="heldout" goto scope_heldout
if /I "%SCOPE%"=="next" goto scope_next
if /I not "%SCOPE%"=="full" goto scope_done
set "VARS=base,regex_mask_off,nmt_hint_regex_mask_off,simple"
set "JOBS=32da3d8f225b,9dedde16e35b,d1e00b9e7393,f0834acf0cc0"
set "OUT=docs\reviews\2026-11-12-golden-ab-simple"
goto scope_done
:scope_next
set "VARS=simple,simple_noleak,simple_rules,simple_rules_fill"
set "JOBS=32da3d8f225b,9dedde16e35b,d1e00b9e7393,f0834acf0cc0"
set "OUT=docs\reviews\2026-11-12-golden-ab-simple-next"
goto scope_done
:scope_heldout
rem HELD-OUT: chi de DO (thanh ngu/xung ho khong hoc tu). KHONG dung de seed lexicon/prompt/quy tac.
set "VARS=base,nmt_hint_regex_mask_off,simple"
set "JOBS=2493d3c7cb29_p01,2d3316593ea9"
set "OUT=docs\reviews\2026-11-12-golden-ab-heldout"
set "GOLD=data\golden_heldout"
:scope_done
echo [golden_ab_simple] mode=%MODE%  jobs=%JOBS%  golden=%GOLD%
echo [golden_ab_simple] bien the=%VARS%
if /I "%MODE%"=="dry" goto dry
if /I "%MODE%"=="score" goto score
if /I "%MODE%"=="run" goto run
echo Tham so la: %MODE%  (dung: [trong] / full / heldout / next / dry [full/heldout/next] / score [full/heldout/next])
goto end
:dry
%PY% scripts\golden_ab.py --golden "%GOLD%" --dry-run --variants "%VARS%" --jobs "%JOBS%"
goto end
:score
%PY% scripts\golden_ab.py --golden "%GOLD%" --score-only --variants "%VARS%" --jobs "%JOBS%" --out-md "%OUT%.md" --out-json "%OUT%.json"
goto end
:run
echo.
echo ================================================================
echo  SAP CHAY THAT: GPU (NMT) + GPT/Aurora (REVIEW) - TON QUOTA.
echo  Kiem tra V2 :8801 dang RANH (khong job dich/TTS) truoc khi tiep tuc.
echo  Ctrl+C de HUY. Bam phim bat ky de CHAY (tu tra loi YES).
echo ================================================================
pause
%PY% scripts\golden_ab.py --golden "%GOLD%" --variants "%VARS%" --jobs "%JOBS%" --out-md "%OUT%.md" --out-json "%OUT%.json" --yes
goto end
:end
pause
