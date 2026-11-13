@echo off
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
python "%~dp0fix_runall_decorator_v2v4v5.py"
pause
