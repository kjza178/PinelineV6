@echo off
rem Diag hanh vi ffmpeg tren may nay (vd 8.1.2 Windows) voi AAC hong giua file - agentK3 2026-11-13.
rem Tao video bip + lam hong nhu tests\test_extract_pcm_timing.py, chay tung bien the lenh ffmpeg
rem (input seek / output seek / khong discardcorrupt / err_detect / file chi-tieng m4a) cho cua so
rem 25-35 s, 30-35 s, 30-60 s va ca file; in loi giai ma + vi tri bip + doan 0 -> JSON + tom tat.
rem Them: chay media_ts.extract_pcm_from_video cua Z:\PinelineV2 tren file hong.
rem CHI DOC moi cay Pineline. File tam: %TEMP%\diag_extract_pcm\ . Khong GPU/HTTP/LLM.
rem Cach dung:  diag_extract_pcm.bat            (V2)
rem             diag_extract_pcm.bat Z:\PinelineV4
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set AGENT_BRAIN=0
set TREE=%~1
if "%TREE%"=="" set TREE=Z:\PinelineV2
set PY=python
if exist Z:\PinelineV2\venv\Scripts\python.exe set PY=Z:\PinelineV2\venv\Scripts\python.exe
%PY% "%~dp0diag_extract_pcm.py" --out "%TEMP%\diag_extract_pcm" --v2 "%TREE%"
echo.
echo Gui lai file JSON o tren (result.json) cho agent.
pause
