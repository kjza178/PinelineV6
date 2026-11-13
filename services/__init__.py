"""services/ — việc nền trước đây sidecar (agents/agent_tele_cmd.py) tự làm — V6 F4.

Lớp MỎNG tái dùng code agents/* (không viết lại logic). Server gọi trực tiếp hàm service (không còn
server → POST sidecar /cmd → sidecar gọi ngược API server). Sidecar chỉ còn Telegram + HTTP mỏng và
cũng gọi cùng các hàm này cho lệnh /running /lib /feed.

Module chỉ import agents/requests BÊN TRONG hàm → `import services.*` không cần fastapi/requests.
"""
