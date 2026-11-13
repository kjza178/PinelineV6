@echo off
rem Sidecar V6 (HTTP mong, cong AUTOPILOT_PORT cua instance v6 = 8816). Telegram chi khi instance telegram=true
rem (v6: false -> khong poll/gui Telegram). Vong nen autopilot/feeder/film_db KHONG chay o sidecar (server so huu, co feeder).
cd /d %~dp0
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
python agents\agent_tele_cmd.py --instance v6
