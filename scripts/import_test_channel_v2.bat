@echo off
rem Chep cau hinh kenh test YouTube (data\test_channel) tu V2 sang V6, khong ghi de file da co.
cd /d %~dp0..
python scripts\import_test_channel.py --src Z:\PinelineV2
echo.
set /p OK=Chep that? (y/n): 
if /i "%OK%"=="y" python scripts\import_test_channel.py --src Z:\PinelineV2 --apply
pause
