---
description: Audit toàn bộ AI agent runtime (brain/core/heal/watch/qa/autopilot) + đội subagent dev
argument-hint: '[runtime | dev-team | cả hai]'
---
Phạm vi: $ARGUMENTS (để trống = cả hai)

1. Giao **agent-architect** audit phạm vi trên (đọc log runtime chỉ-đọc, fix logs agent, DECISIONS 11/2026).
2. Song song giao **bug-hunter** cho `agent_brain`, `agent_tool_loop`, `agent_heal` (tập trung: hành động tự động chồng chéo, loop, nuốt lỗi, mutate không cooldown).
3. Gộp: rủi ro an toàn trước, rồi cải tiến. Không sửa code; hỏi user mục nào làm.
