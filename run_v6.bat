@echo off
rem Chay backend V6 (khong Tauri): Studio http://127.0.0.1:8806/ (index.html, truoc la index3) - UI moi http://127.0.0.1:8806/v6/
cd /d %~dp0
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
call scripts\pick_python.bat
"%PY%" -m app --instance v6
