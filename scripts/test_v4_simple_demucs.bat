@echo off
rem test_v4_simple_demucs.bat - agent F4 2026-11-12: kiem port translate_simple + Demucs-truoc-ASR sang PinelineV4.
rem agent P4 2026-11-12: + dong dau luong dich theo job (F2) + Second Brain/HOLD (G+H) -> nhom (1b).
rem agent P4b 2026-11-12: + I2 lenh Telegram nguoi dung = chay tay (test_agent_hold_telegram) -> nhom (1b).
rem agent R4 2026-11-13: + brain job_finished / park vs HOLD (agent N V2: test_brain_finished_hold) -> nhom (1b).
rem agent Q4 2026-11-13: + port preflight/chuan hoa nguon (K/K2), FireRed slice khoang lang (L), cau rac + split_wait (M) -> nhom (1c).
rem CHI CHAY PYTEST (mock): khong HTTP :8000/:8083/:8801/:8802/:8804, khong LLM/GPU that, khong kill/restart.
rem Cach dung:
rem   test_v4_simple_demucs.bat          = (1) test moi simple+demucs  (2) nhom dich/review cu cua V4
rem   test_v4_simple_demucs.bat full     = them (3) toan bo tests V4
rem Cay V4: bien V4_ROOT, mac dinh thu muc anh em ..\..\PinelineV4 (canh PinelineV6).
rem File ASCII + CRLF.
setlocal
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set AGENT_BRAIN=0
if not defined V4_ROOT set "V4_ROOT=%~dp0..\..\PinelineV4"
if not exist "%V4_ROOT%\server.py" (
  echo [LOI] Khong thay "%V4_ROOT%\server.py" - dat V4_ROOT=duong_dan_PinelineV4
  exit /b 2
)
cd /d "%V4_ROOT%"

rem Python: giong run_v4.bat - .pineline_env_path > pineline_env > C:\Python312 > .venv > PATH
set "PY="
if exist ".pineline_env_path" set /p PY=<".pineline_env_path"
if defined PY if not exist "%PY%" set "PY="
if not defined PY if exist "pineline_env\Scripts\python.exe" set "PY=%CD%\pineline_env\Scripts\python.exe"
if not defined PY if exist "C:\Python312\python.exe" set "PY=C:\Python312\python.exe"
if not defined PY if exist ".venv\Scripts\python.exe" set "PY=%CD%\.venv\Scripts\python.exe"
if not defined PY set "PY=python"
echo [py] %PY%
echo [v4] %CD%

rem Test so sanh ban goc (agent B) - backup nam o V2; golden (tuy chon) o V6 + work V2
if not defined TS_NEXT_BACKUP if exist "%V4_ROOT%\..\PinelineV2\.backup_edit_20261112_agentB\translate_simple\pipeline.py" set "TS_NEXT_BACKUP=%V4_ROOT%\..\PinelineV2\.backup_edit_20261112_agentB"
if not defined TS_GOLDEN_DIR if exist "%~dp0..\data\golden" set "TS_GOLDEN_DIR=%~dp0..\data\golden"
if not defined TS_EVAL_SCRIPT if exist "%~dp0eval_translation.py" set "TS_EVAL_SCRIPT=%~dp0eval_translation.py"
if not defined TS_GOLDEN_WORK if exist "D:\PinelineV2_work\" set "TS_GOLDEN_WORK=D:\PinelineV2_work"

set RC=0
echo.
echo ===== (1) TEST MOI: translate_simple + Demucs truoc ASR =====
echo   Ky vong: 112 test, 0 failed (111 passed + 1 skipped neu thieu golden; 112 passed neu co golden)
echo   (agent P4: +6 test_f2_* dong dau luong dich theo job)
"%PY%" -m pytest -q tests\test_translate_simple.py tests\test_translate_simple_next.py tests\test_translate_simple_next_review.py tests\test_translate_simple_scenarios.py tests\test_separate_demucs_chunks.py tests\test_asr_demucs.py
if errorlevel 1 set RC=1

echo.
echo ===== (1b) TEST MOI: Second Brain events/notify + go HOLD chay tay + job xong/park (agent P4, P4b, R4) =====
echo   Ky vong: 76 passed (11 events + 28 notify + 14 hold_telegram + 23 brain_finished_hold), 0 failed
"%PY%" -m pytest -q tests\test_agent_brain_events.py tests\test_agent_brain_notify.py tests\test_agent_hold_telegram.py tests\test_brain_finished_hold.py
if errorlevel 1 set RC=1

echo.
echo ===== (1c) TEST MOI: preflight video + extract_pcm + FireRed slice + cau rac/split_wait (agent Q4) =====
echo   Ky vong: 88 passed, 0 failed (13 video_preflight + 2 extract_pcm_robust + 21 extract_pcm_timing
echo    + 9 firered_slice_silence + 43 simple_junk_park). Can ffmpeg/ffprobe trong PATH (may user: 8.1.2 gyan.dev).
"%PY%" -m pytest -q tests\test_video_preflight.py tests\test_extract_pcm_robust.py tests\test_extract_pcm_timing.py tests\test_firered_slice_silence.py tests\test_simple_junk_park.py
if errorlevel 1 set RC=1

echo.
echo ===== (2) NHOM DICH / REVIEW / QUEUE CU CUA V4 =====
echo   Ky vong (do tren ban sao Linux, TRUOC va SAU port nhu nhau): 74 test = 71 passed + 3 failed CO SAN
echo   (test_pronoun_consistency::test_filter_review_fixes_when_locked, test_pronoun_postpass::test_modernish_mixed,
echo    test_pronoun_postpass::test_detox_nguoi_ta - fail ca truoc port, khong lien quan). Fail khac = bao agent.
"%PY%" -m pytest -q tests\test_park_review_retry.py tests\test_review_fail_not_rv.py tests\test_review_parallel_workers.py tests\test_review_pass1_upload.py tests\test_pronoun_qa_slim_plan.py tests\test_pronoun_consistency.py tests\test_pronoun_postpass.py tests\test_optimize_b5_b6.py tests\test_queue_run_one.py tests\test_setup_machine.py

if /I not "%~1"=="full" goto done
echo.
echo ===== (3) TOAN BO tests V4 =====
echo   Ky vong: so failed KHONG tang so voi truoc port (R4 ban sao Linux, KHONG stub fastapi: truoc 324 passed/34 failed/10 error,
echo    sau 347 passed/34 failed/10 error cung tap; +23 test_brain_finished_hold)
echo   Ky vong: so failed KHONG tang so voi truoc port (Q4 ban sao Linux, stub fastapi: truoc 289 passed/10 failed/6 error,
echo    sau 377 passed/10 failed/6 error cung tap; +88 test nhom 1c)
echo   Ky vong: so failed KHONG tang so voi truoc port (P4 ban sao Linux: truoc 265 passed/33 failed, sau 310 passed/33 failed cung tap)
echo   Ky vong cu: so failed KHONG tang so voi truoc port (ban sao Linux: truoc 23 failed / sau 22 failed, cung tap
echo   + test_asr_demucs het fail; cac fail con lai do moi truong: fastapi stub, D:\PinelineV2_tmp, ffmpeg...).
"%PY%" -m pytest -q tests

:done
echo.
if "%RC%"=="0" (echo [OK] Test moi simple+demucs+preflight PASS) else (echo [FAIL] Test moi simple+demucs co loi - xem tren)
endlocal & exit /b %RC%
