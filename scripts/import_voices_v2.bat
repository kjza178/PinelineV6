@echo off
rem Chep giong clone tu V2 sang V6 (gop danh sach, khong ghi de giong da co). Xem truoc roi hoi.
cd /d %~dp0..
python scripts\import_voices.py --src Z:\PinelineV2
echo.
set /p OK=Chep that? (y/n): 
if /i "%OK%"=="y" python scripts\import_voices.py --src Z:\PinelineV2 --apply
pause
