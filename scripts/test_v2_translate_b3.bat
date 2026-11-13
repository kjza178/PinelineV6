@echo off
rem Test dot 3 translate (translate-b3) tren V2 truoc khi restart :8801. Khong bat server, khong goi LLM/GPU.
rem Gom: b3 moi + b2/b2_review/b1/b1_scenarios + nhom review/pronoun (so sanh voi so truoc b3 trong docs\fixes\2026-11-12-translate-b3.md).
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set AGENT_BRAIN=0
set B3_GOLDEN_DIR=Z:\PinelineV6\data\golden
cd /d Z:\PinelineV2
python -m pytest -q -p no:cacheprovider tests\test_translate_b3.py tests\test_translate_b2.py tests\test_translate_b2_review.py tests\test_translate_b1.py tests\test_translate_b1_scenarios.py tests\test_review_fail_not_rv.py tests\test_park_review_retry.py tests\test_review_parallel_workers.py tests\test_optimize_b5_b6.py tests\test_review_batches.py tests\test_pronoun_postpass.py tests\test_pronoun_consistency.py tests\test_translate_missing.py tests\test_review_pass1_upload.py tests\test_pronoun_qa_slim_plan.py > Z:\PinelineV6\scratch\v2_translate_b3_pytest.log 2>&1
findstr /R /C:" passed" /C:" failed" /C:"^FAILED" Z:\PinelineV6\scratch\v2_translate_b3_pytest.log
pause
