# Hotspots — nơi nên soi bug/refactor trước

_Sinh tự động bởi `scripts/brain_build.py` lúc 2026-11-11 13:00. Đừng sửa tay — chạy lại script._


Điểm = LOC/500 + 2×fix log + 0.15×nhắc CHANGELOG + 1×hàm dài + 0.1×`except Exception` + 1×bare except + 0.5×nuốt lỗi (`except: pass`) + 0.5×Thread + 0.3×`global` + 3 nếu >800 dòng mà không test.
Đây là **heuristic để ưu tiên**, không phải kết luận có bug.

| # | Module | Điểm | Dòng | Fix | Hàm dài | broad/bare/swallow except | Thread | Test |
|---:|---|---:|---:|---:|---:|---|---:|---|
| 1 | [server](../modules/server.md) | 77.5 | 6626 | 3 | 3 | 205/0/66 | 2 | ✅ |
| 2 | [agents.hongguo_bridge](../modules/agents.hongguo_bridge.md) | 37.4 | 4222 | 0 | 5 | 89/0/25 | 1 | ✅ |
| 3 | [agents.agent_tele_cmd](../modules/agents.agent_tele_cmd.md) | 28.1 | 2302 | 1 | 4 | 53/0/11 | 11 | ✅ |
| 4 | [engines.render](../modules/engines.render.md) | 26.4 | 3067 | 1 | 7 | 33/0/15 | 1 | ✅ |
| 5 | [engines.translate](../modules/engines.translate.md) | 26.3 | 4131 | 0 | 6 | 60/0/12 | 0 | ✅ |
| 6 | [agents.agent_feeder](../modules/agents.agent_feeder.md) | 24.4 | 2072 | 1 | 4 | 40/0/20 | 0 | ✅ |
| 7 | [agents.meta_studio](../modules/agents.meta_studio.md) | 22.9 | 1919 | 1 | 5 | 36/0/17 | 0 | ✅ |
| 8 | [agents.auto_publisher](../modules/agents.auto_publisher.md) | 22.6 | 1568 | 0 | 1 | 44/0/25 | 2 | ✅ |
| 9 | [agents.agent_hongguo](../modules/agents.agent_hongguo.md) | 19.5 | 1572 | 1 | 1 | 33/0/12 | 1 | ❌ |
| 10 | [agents.test_channel](../modules/agents.test_channel.md) | 19.0 | 1435 | 0 | 0 | 34/0/15 | 2 | ❌ |
| 11 | [tools.test_channel](../modules/tools.test_channel.md) | 19.0 | 1435 | 0 | 0 | 34/0/15 | 2 | ❌ |
| 12 | [agents.agent_brain](../modules/agents.agent_brain.md) | 16.9 | 1233 | 0 | 3 | 36/0/8 | 1 | ❌ |
| 13 | [agents.hongguo_rpc](../modules/agents.hongguo_rpc.md) | 16.9 | 1449 | 0 | 0 | 22/0/9 | 5 | ❌ |
| 14 | [engines.clone_tts](../modules/engines.clone_tts.md) | 11.9 | 1116 | 0 | 2 | 19/0/10 | 1 | ✅ |
| 15 | [http_routes.routes_hg](../modules/http_routes.routes_hg.md) | 10.8 | 1018 | 1 | 0 | 53/0/3 | 0 | ✅ |
| 16 | [core.film_db](../modules/core.film_db.md) | 9.6 | 1091 | 0 | 0 | 19/0/11 | 0 | ✅ |
| 17 | [agents.agent_heal](../modules/agents.agent_heal.md) | 8.9 | 662 | 0 | 2 | 16/0/8 | 0 | ❌ |
| 18 | [agents.agent_watch](../modules/agents.agent_watch.md) | 8.8 | 375 | 2 | 0 | 12/0/4 | 1 | ❌ |
| 19 | [workers.render_worker](../modules/workers.render_worker.md) | 8.6 | 300 | 1 | 0 | 13/0/8 | 1 | ✅ |
| 20 | [agents.agent_core](../modules/agents.agent_core.md) | 8.5 | 2080 | 1 | 1 | 5/0/1 | 0 | ✅ |
| 21 | [scripts.brain_build](../modules/scripts.brain_build.md) | 8.1 | 857 | 0 | 2 | 4/0/2 | 0 | ❌ |
| 22 | [engines.llm](../modules/engines.llm.md) | 7.5 | 894 | 0 | 1 | 22/0/5 | 0 | ✅ |
| 23 | [http_routes.routes_uploader_settings](../modules/http_routes.routes_uploader_settings.md) | 7.1 | 659 | 0 | 0 | 18/0/8 | 0 | ❌ |
| 24 | [agents.uploader_settings_api](../modules/agents.uploader_settings_api.md) | 7.0 | 670 | 0 | 0 | 22/0/7 | 0 | ❌ |
| 25 | [core.job_queue](../modules/core.job_queue.md) | 6.9 | 669 | 0 | 0 | 11/0/2 | 1 | ✅ |
| 26 | [engines.subs](../modules/engines.subs.md) | 6.5 | 1106 | 0 | 1 | 3/0/0 | 0 | ❌ |
| 27 | [engines.render_job](../modules/engines.render_job.md) | 6.0 | 186 | 1 | 1 | 6/0/4 | 0 | ✅ |
| 28 | [agents.canva_client](../modules/agents.canva_client.md) | 5.7 | 394 | 1 | 0 | 9/0/4 | 0 | ❌ |
| 29 | [tools.migrate_work](../modules/tools.migrate_work.md) | 5.7 | 396 | 1 | 0 | 14/0/3 | 0 | ✅ |
| 30 | [http_routes.routes_films](../modules/http_routes.routes_films.md) | 5.5 | 381 | 1 | 0 | 17/0/2 | 0 | ✅ |
| 31 | [engines.pronoun_consistency](../modules/engines.pronoun_consistency.md) | 5.3 | 871 | 0 | 1 | 11/0/3 | 0 | ✅ |
| 32 | [engines.asr.fun_asr_nano](../modules/engines.asr.fun_asr_nano.md) | 4.9 | 603 | 0 | 1 | 12/0/3 | 0 | ❌ |
| 33 | [app.main](../modules/app.main.md) | 4.8 | 162 | 2 | 0 | 0/0/0 | 1 | ✅ |
| 34 | [core.instance](../modules/core.instance.md) | 4.6 | 222 | 2 | 0 | 2/0/0 | 0 | ✅ |
| 35 | [engines.split_parts](../modules/engines.split_parts.md) | 4.5 | 834 | 0 | 2 | 8/0/0 | 0 | ✅ |
| 36 | [engines.asr.asr](../modules/engines.asr.asr.md) | 4.2 | 448 | 0 | 1 | 8/0/2 | 1 | ✅ |
| 37 | [agents.agent_hang_fix](../modules/agents.agent_hang_fix.md) | 3.9 | 368 | 0 | 1 | 7/0/3 | 0 | ❌ |
| 38 | [engines.download](../modules/engines.download.md) | 3.9 | 698 | 0 | 1 | 10/0/0 | 1 | ✅ |
| 39 | [agents.agent_pronoun_pass](../modules/agents.agent_pronoun_pass.md) | 3.8 | 257 | 0 | 1 | 8/0/3 | 0 | ❌ |
| 40 | [agents.agent_autopilot](../modules/agents.agent_autopilot.md) | 3.6 | 332 | 1 | 0 | 6/0/0 | 0 | ❌ |
