@echo off
rem Chay backend V6 (khong Tauri): UI cu http://127.0.0.1:8806/ (index3: /ui3) - UI moi http://127.0.0.1:8806/v6/
cd /d %~dp0
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
python -m app --instance v6
