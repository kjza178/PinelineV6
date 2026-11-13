@echo off
rem Soi A/B muc cau: script.json bien the SIMPLE vs BASE (D:\PinelineV6_work\golden_ab\<job>_<bien_the>\) so voi gold.
rem CHI DOC: khong LLM/GPU/HTTP, khong ghi vao job tam. Can da chay golden_ab_simple.bat (full / heldout) truoc.
rem Cach dung:
rem   ab_inspect.bat            = 4 job golden (data\golden), simple vs base -> docs\reviews\2026-11-12-ab-inspect.md
rem   ab_inspect.bat hint       = nhu tren nhung simple vs nmt_hint_regex_mask_off -> ...-ab-inspect-vs-hint.md
rem   ab_inspect.bat heldout    = golden HELD-OUT (data\golden_heldout) simple vs base -> ...-ab-inspect-heldout.md
rem   ab_inspect.bat custom --a base --b simple --top 50 --jobs f0834acf0cc0   (tham so tu do cho ab_inspect.py)
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set AGENT_BRAIN=0
cd /d Z:\PinelineV6
set PY=python
if exist Z:\PinelineV2\venv\Scripts\python.exe set PY=Z:\PinelineV2\venv\Scripts\python.exe
if /I "%~1"=="heldout" goto heldout
if /I "%~1"=="hint" goto hint
if /I "%~1"=="custom" goto custom
if not "%~1"=="" goto usage
%PY% scripts\ab_inspect.py --golden data\golden --a base --b simple --out docs\reviews\2026-11-12-ab-inspect.md --out-json docs\reviews\2026-11-12-ab-inspect.json
goto end
:hint
%PY% scripts\ab_inspect.py --golden data\golden --a nmt_hint_regex_mask_off --b simple --out docs\reviews\2026-11-12-ab-inspect-vs-hint.md
goto end
:heldout
%PY% scripts\ab_inspect.py --golden data\golden_heldout --a base --b simple --out docs\reviews\2026-11-12-ab-inspect-heldout.md --out-json docs\reviews\2026-11-12-ab-inspect-heldout.json
goto end
:custom
shift
%PY% scripts\ab_inspect.py %1 %2 %3 %4 %5 %6 %7 %8 %9
goto end
:usage
echo Tham so la: %~1  (dung: [trong] / hint / heldout / custom ...)
:end
pause
