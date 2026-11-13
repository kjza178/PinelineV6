# Context Studio (scaffold)

PRE-SCAN **trước** bước dịch Pineline.

```
SRT Trung / asr.json
        ↓
 context_studio (analyze)
        ↓
 glossary.json + style_lock.json + qa_flags.json
        ↓
 Pineline translate (bám pack — không tự đổi setting)
```

**Không dịch.** Chỉ quyết định: genre, style lock, nhân vật, thuật ngữ, cờ số.

## Chạy nhanh (heuristic, không API)

```bat
cd /d Z:\Pineline
python -m tools.context_studio analyze tools\context_studio\samples\demo_xianxia_game.srt -o tools\context_studio\_out --print-summary
```

## LLM (dùng stack Pineline)

```bat
python -m tools.context_studio analyze path\to\sub.zh.srt -o work\<job_id> --llm
```

Cần `llm.py` + `TRANSLATE_MODEL` / `LLM_*` như Pineline.

## Streamlit UI

```bat
pip install streamlit
streamlit run context_studio\app_streamlit.py
```

## Output → Pineline

| File | Vai trò |
|------|---------|
| `glossary.json` | Schema gần `build_plan` + `style_lock` + `pronouns` quan hệ |
| `style_lock.json` | mode + `address_bank` (nhóm xưng hô) |
| `relation_matrix.json` | who → to → self/call |
| `qa_flags.json` | Số nghi vấn — **không** sửa transcript |
| `context_pack.json` | `skip_plan: true` → Pineline **không** gọi lại `build_plan` |

Copy cả thư mục vào `work/<job_id>/` (hoặc chỉ các file trên).

> Pineline (`translate.py`): nếu có `context_pack.json` với `skip_plan` + `glossary.json` → giữ pack Context Studio kể cả khi force dịch lại.

## Modules

| File | Spec |
|------|------|
| `srt_io.py` | Parse SRT / asr.json |
| `analyzer.py` | 01 context analyzer |
| `character_mapper.py` | 02 character map enrich |
| `schema.py` | style_lock + number scan |
| `exporter.py` | 05 ghi pack |
| `__main__.py` | CLI |
| `app_streamlit.py` | UI stub |

03 translator / 04 QA full checklist — **cố ý chưa làm** (Pineline giữ dịch).
