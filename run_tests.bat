@echo off
rem Test V6.  run_tests.bat        = nhanh: test V6 (bo tests\v5), ~10 s
rem           run_tests.bat all    = ca test V5 (cham), ghi ten tung test + dump treo sau 120 s
cd /d %~dp0
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
set AGENT_BRAIN=0
if not exist scratch mkdir scratch
if /I "%1"=="all" (
  python -m pytest -v -rfE --durations=15 -o faulthandler_timeout=120 --basetemp=scratch\pytest_tmp tests > scratch\pytest_all.log 2>&1
  findstr /R /C:" passed" /C:" failed" /C:"Timeout" scratch\pytest_all.log
) else (
  python -m pytest -q -o faulthandler_timeout=60 --basetemp=scratch\pytest_tmp --ignore=tests\v5 tests > scratch\pytest.log 2>&1
  findstr /R /C:" passed" /C:" failed" scratch\pytest.log
)
pause
