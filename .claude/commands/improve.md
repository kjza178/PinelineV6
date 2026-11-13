---
description: Đề xuất cải tiến code + workflow (improvement-advisor) và AI agent (agent-architect)
argument-hint: '[khu vực: server | queue | translate | render | workflow | agents | toàn bộ]'
---
Khu vực: $ARGUMENTS (để trống = toàn bộ)

1. `python scripts/brain_build.py` để số liệu generated mới nhất.
2. Giao song song:
   - **improvement-advisor**: code + hiệu năng + độ tin cậy + workflow dev/vận hành cho khu vực trên.
   - **agent-architect**: nếu khu vực là "agents", "toàn bộ", hoặc đụng agent_*/llm/brain.
3. Gộp thành một bảng ưu tiên duy nhất (Giá trị/Công sức/Rủi ro), loại trùng, đánh dấu quick win. Không sửa code.
4. Hỏi user chọn mục nào để làm; mục được chọn → ghi `docs/DECISIONS.md`.
