"""Run the real workbench with inert log data for drawer layout acceptance."""
from pathlib import Path
import runpy
import sys

import streamlit as st


APP = Path(__file__).resolve().parents[2] / "TF-agent" / "app.py"
sys.path.insert(0, str(APP.parent))
if "pipeline_log_snapshot" not in st.session_state:
    st.session_state.pipeline_log_snapshot = [
        f"[08:16:{index:02d}] status-layout log line {index:02d}"
        for index in range(30)
    ]
    st.session_state.pipeline_log_snapshot[15] += " very-long-log-segment" * 60
    st.session_state.pipeline_progress_value = 65

runpy.run_path(str(APP), run_name="__main__")
