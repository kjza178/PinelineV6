@echo off
rem Test luong dich simple (translate_simple) tren V2 truoc khi restart :8801. Khong bat server, khong goi LLM/GPU/HTTP.
rem Gom: test moi + nhom dich/review cu (so sanh voi so truoc/sau trong docs\fixes\2026-11-12-translate-simple.md).
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set AGENT_BRAIN=0
set B3_GOLDEN_DIR=Z:\PinelineV6\data\golden
set TS_GOLDEN_DIR=Z:\PinelineV6\data\golden
set TS_GOLDEN_WORK=D:\PinelineV2_work
set TS_EVAL_SCRIPT=Z:\PinelineV6\scripts\eval_translation.py
rem F2 2026-11-12: TRANSLATE_PIPELINE trong = simple (mac dinh); test luong cu tu dat legacy
set TRANSLATE_PIPELINE=
cd /d Z:\PinelineV2
python -m pytest -q -p no:cacheprovider -s tests\test_translate_simple.py tests\test_translate_simple_scenarios.py tests\test_translate_simple_next.py tests\test_translate_simple_next_review.py tests\test_simple_junk_park.py > Z:\PinelineV6\scratch\v2_translate_simple_new.log 2>&1
python -m pytest -q -p no:cacheprovider tests\test_translate_b3.py tests\test_translate_b2.py tests\test_translate_b2_review.py tests\test_translate_b1.py tests\test_translate_b1_scenarios.py tests\test_review_fail_not_rv.py tests\test_park_review_retry.py tests\test_review_parallel_workers.py tests\test_optimize_b5_b6.py tests\test_review_batches.py tests\test_pronoun_postpass.py tests\test_pronoun_consistency.py tests\test_translate_missing.py tests\test_review_pass1_upload.py tests\test_pronoun_qa_slim_plan.py > Z:\PinelineV6\scratch\v2_translate_simple_group.log 2>&1
echo === test_translate_simple + scenarios + next (ky vong: 131 pass, 0 fail - F2: simple MAC DINH, +6 test_f2_*; +43 test_simple_junk_park (agentM 2026-11-13); next + next_review can backup Z:\PinelineV2\.backup_edit_20261112_agentB) ===
findstr /R /C:" passed" /C:" failed" /C:"^FAILED" /C:"e2e-simple" Z:\PinelineV6\scratch\v2_translate_simple_new.log
echo === nhom dich cu (ky vong: 7 fail co san (check_batch2-5, test_modernish_mixed, test_detox_nguoi_ta, test_filter_review_fixes_when_locked)) ===
findstr /R /C:" passed" /C:" failed" /C:"^FAILED" Z:\PinelineV6\scratch\v2_translate_simple_group.log
pause
