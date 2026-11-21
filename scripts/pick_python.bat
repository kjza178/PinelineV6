@echo off
rem Chon python cho Pineline (goi bang: call scripts\pick_python.bat  -> bien PY). Thu tu:
rem   PINELINE_PYTHON > .pineline_env_path (install.py ghi) > ..\pineline_env > pineline_env > python he thong
rem Dung CHUNG cho run_v6 / run_sidecar_v6 / install_firered_asr de server va thu vien cung MOT python.
set "PY="
set "_ROOT=%~dp0.."
if defined PINELINE_PYTHON set "PY=%PINELINE_PYTHON%"
if not defined PY if exist "%_ROOT%\.pineline_env_path" set /p PY=<"%_ROOT%\.pineline_env_path"
if defined PY if not exist "%PY%" set "PY="
if not defined PY if exist "%_ROOT%\..\pineline_env\Scripts\python.exe" set "PY=%_ROOT%\..\pineline_env\Scripts\python.exe"
if not defined PY if exist "%_ROOT%\pineline_env\Scripts\python.exe" set "PY=%_ROOT%\pineline_env\Scripts\python.exe"
if not defined PY set "PY=python"
set "_ROOT="
echo [python] %PY%
