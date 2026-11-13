"""Parser tool call dạng chữ (model in «Action: tool(...)» / JSON fence thay vì gọi tool native)."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import agents.agent_core as a  # noqa: E402

CASES = [
    ('**Action:** `hg_auto_flow(query_or_id="末世降临，我是末日生存方案供应商", auto_run=True)` $\\rightarrow$ Tự tạo job',
     ["hg_auto_flow"]),
    ("Chạy `hg_auto_flow` để tạo job từ thư viện", []),
    ("`hg_library` chỉ là danh sách file. `queue_add` cần `job_id`", []),
    ('queue_add(job_ids=["abc"], voice="v2")', ["pineline_queue_add"]),
    ('`queue_add(job_ids=["abc"])`', ["pineline_queue_add"]),
    ('gọi queue_add(job_ids=["abc"], voice="v2")', []),
    ('Đã gọi hg_auto_flow(query_or_id="X") xong, job đang chạy.', []),
    ('Nếu muốn dừng hãy dùng stop_job(job_id="abc").', []),
    ("Action: hg_auto_flow(query_or_id=..., voice=...)", []),
    ('```json\n{"name":"hg_status","arguments":{}}\n```', ["hg_status"]),
    ('[{"name": "status", "arguments": {}}]', ["pineline_status"]),
    ('```json\n{"name":"rm_rf","arguments":{}}\n```', []),
    ("Không có gì (ghi chú) cả", []),
    ("", []),
]


@pytest.mark.parametrize("text,want", CASES)
def test_parse_text_tool_calls(text, want):
    assert [t["function"]["name"] for t in a._parse_text_tool_calls(text)] == want


def test_pycall_arguments_are_literal_json():
    calls = a._parse_text_tool_calls('Action: queue_add(job_ids=["a", "b"], voice="v2")')
    assert json.loads(calls[0]["function"]["arguments"]) == {"job_ids": ["a", "b"], "voice": "v2"}


def test_pycall_rejects_positional_and_expressions():
    assert a._parse_text_tool_calls('Action: stop_job("abc")') == []
    assert a._parse_text_tool_calls("Action: stop_job(job_id=__import__('os'))") == []
