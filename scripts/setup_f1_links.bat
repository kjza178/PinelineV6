@echo off
rem V6 F1: work -> D:\PinelineV6_work (junction, giong V2); models/assets/voices dung chung V5 (junction, chi doc);
rem cookies copy rieng. Khong dung toi V2, khong xoa gi.
cd /d %~dp0..
if not exist D:\PinelineV6_work mkdir D:\PinelineV6_work
if not exist D:\PinelineV6_tmp mkdir D:\PinelineV6_tmp
if not exist work mklink /J work D:\PinelineV6_work
for %%D in (models assets voices voice_samples) do (
  if not exist %%D if exist Z:\PinelineV5\%%D mklink /J %%D Z:\PinelineV5\%%D
)
if not exist cookies robocopy Z:\PinelineV5\cookies cookies /E /NFL /NDL /NJH /NJS
echo.
dir /AL | findstr /I "JUNCTION"
echo Xong. Chay run_tests.bat de kiem tra.
pause
