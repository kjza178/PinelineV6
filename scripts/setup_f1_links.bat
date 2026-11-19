@echo off
rem V6 portable (2026-11-19): KHONG con junction sang Z:\PinelineV5 / D:\PinelineV6_work.
rem   work, scratch\tmp: tao trong cay (instances\v6.json: "./work", "./scratch/tmp").
rem   Muon de job o o khac: tao instances\v6.local.json (khong vao git), vd:
rem     { "work_root": "E:/PinelineV6_work", "temp": "E:/PinelineV6_tmp" }
rem   models\ (cdx23 tach nhac nen), cookies\: chep tay tu may cu neu co (khong co trong git).
rem   Tuy chon: setup_f1_links.bat <thu_muc_nguon>  -> chep models\ va cookies\ tu <thu_muc_nguon> (vd E:\PineLineV6).
cd /d %~dp0..
if not exist work mkdir work
if not exist scratch\tmp mkdir scratch\tmp
if not "%~1"=="" (
  if exist "%~1\models" if not exist models robocopy "%~1\models" models /E /NFL /NDL /NJH /NJS
  if exist "%~1\cookies" if not exist cookies robocopy "%~1\cookies" cookies /E /NFL /NDL /NJH /NJS
)
echo.
echo Xong. Kiem tra may: python scripts\check_env.py
pause
