"""Streamlit UI stub — Context Detection trước khi dịch Pineline.

Chạy:
  pip install streamlit
  streamlit run context_studio/app_streamlit.py
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.context_studio.analyzer import analyze, summarize
from tools.context_studio.character_mapper import enrich_characters, validate_pronouns
from tools.context_studio.exporter import write_pack
from tools.context_studio.schema import scan_number_flags
from tools.context_studio.srt_io import load_segments

try:
    import streamlit as st
except ImportError:
    print("Cài: pip install streamlit")
    raise


st.set_page_config(page_title="Context Studio · Pineline", layout="wide")
st.title("Chinese → Context Pack (trước dịch Pineline)")
st.caption("PRE-SCAN only — không dịch. Output: glossary.json + style_lock + qa_flags")

up = st.file_uploader("Upload SRT / asr.json", type=["srt", "json"])
use_llm = st.checkbox("Dùng LLM (Pineline llm.py)", value=False)
out_dir = st.text_input("Output dir", value=str(ROOT / "context_studio" / "_out"))

col1, col2 = st.columns(2)
with col1:
    run = st.button("Analyze Context", type="primary")
with col2:
    st.write("")

if run and up is not None:
    suffix = Path(up.name).suffix or ".srt"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(up.read())
        tmp_path = tmp.name
    segs = load_segments(tmp_path)
    st.info(f"Loaded **{len(segs)}** lines")
    with st.spinner("Analyzing…"):
        glossary = analyze(segs, use_llm=use_llm)
        glossary = enrich_characters(glossary, segments=segs)
        flags = scan_number_flags(segs)
        paths = write_pack(out_dir, glossary, flags)
    sm = summarize(glossary)
    st.subheader("CONTEXT DETECTION")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Genre", sm["genre"] or "—")
    m2.metric("Style", sm["style"])
    m3.metric("Characters", sm["characters"])
    m4.metric("Pronouns", sm["pronouns"])
    st.write(f"Confidence: **{sm['confidence']}** · Terms: {sm['terminology']}")
    st.write(sm.get("tone") or "")
    warns = validate_pronouns(glossary)
    if warns:
        st.warning("Pronoun validation:\n- " + "\n- ".join(warns[:10]))
    if glossary.get("relation_matrix"):
        st.subheader("RELATION MAP (who → to)")
        st.dataframe(glossary["relation_matrix"], use_container_width=True)
    n_sus = sum(1 for f in flags if f.get("suspicious"))
    st.subheader("QA FLAGS (numbers)")
    st.write(f"Total flags: {len(flags)} · Suspicious: **{n_sus}**")
    if flags:
        st.json(flags[:15])
    with st.expander("style_lock / characters"):
        st.json({
            "style_lock": {
                k: glossary.get("style_lock", {}).get(k)
                for k in ("mode", "pronoun_policy", "forbid", "prefer")
            },
            "characters": glossary.get("characters"),
            "pronouns": glossary.get("pronouns"),
        })
    st.subheader("Wrote")
    st.code("\n".join(f"{k}: {v}" for k, v in paths.items()))
    st.download_button(
        "Download glossary.json",
        data=json.dumps(glossary, ensure_ascii=False, indent=2),
        file_name="glossary.json",
        mime="application/json",
    )
elif run:
    st.error("Upload file trước.")
else:
    st.markdown(
        """
### Flow
1. Upload `sub.zh.srt` hoặc `asr.json`
2. **Analyze Context** → xem genre / style lock / characters
3. Copy output vào `work/<job_id>/`
4. Pineline dịch (bám glossary — skip plan khi đã wire)

Scaffold heuristic chạy **không cần API**. Tick LLM khi sẵn sàng.
"""
    )
