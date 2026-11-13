@echo off
rem Test dot 2 translate (translate-b2) tren V2 truoc khi restart :8801. Khong bat server, khong goi LLM/GPU.
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set AGENT_BRAIN=0
cd /d Z:\PinelineV2
python -m pytest -q -p no:cacheprovider tests\test_translate_b2.py tests\test_translate_b2_review.py tests\test_translate_b1.py tests\test_translate_b1_scenarios.py tests\test_review_fail_not_rv.py tests\test_park_review_retry.py tests\test_review_parallel_workers.py tests\test_optimize_b5_b6.py tests\test_review_pass1_upload.py tests\test_translate_missing.py > Z:\PinelineV6\scratch\v2_translate_b2_pytest.log 2>&1
findstr /R /C:" passed" /C:" failed" Z:\PinelineV6\scratch\v2_translate_b2_pytest.log
pause
