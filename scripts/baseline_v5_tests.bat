@echo off
rem Chay bo test CUA V5 ngay tren cay V5 de lay moc: test nao vo tu truoc (khong do V6).
rem Ket qua: Z:\PinelineV6\scratch\baseline_v5.log . Khong dung V2, khong bat server.
cd /d Z:\PinelineV5
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
set AGENT_BRAIN=0
python -m pytest -v -rfE -o faulthandler_timeout=120 --basetemp=D:\PinelineV5_tmp\pytest_baseline tests > Z:\PinelineV6\scratch\baseline_v5.log 2>&1
findstr /R /C:" passed" /C:" failed" Z:\PinelineV6\scripts\..\scratch\baseline_v5.log
pause
