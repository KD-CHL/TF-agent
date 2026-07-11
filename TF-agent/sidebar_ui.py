"""Streamlit 侧边栏 UI — 轻量样式，避免与 Streamlit 控件布局冲突。"""

from __future__ import annotations

import streamlit as st


SIDEBAR_CSS = """
[data-testid="stSidebar"] {
    background-color: #16181d !important;
    border-right: 1px solid #2a2d35 !important;
}
[data-testid="stSidebar"] > div:first-child {
    padding-top: 0.5rem;
}
[data-testid="stSidebar"] [data-testid="stVerticalBlock"] {
    gap: 0.6rem;
}
[data-testid="stSidebar"] [data-testid="stVerticalBlock"] > div {
    margin-bottom: 0.1rem;
}
[data-testid="stSidebar"] label[data-testid="stWidgetLabel"] p {
    font-size: 0.8125rem !important;
    color: #a8b0c0 !important;
}
[data-testid="stSidebar"] .stTextInput>div>div>input,
[data-testid="stSidebar"] .stSelectbox>div>div>div {
    background-color: #1e2128 !important;
    border: 1px solid #353945 !important;
    border-radius: 6px !important;
    color: #e8eaed !important;
}
[data-testid="stSidebar"] [data-testid="stExpander"] {
    border: 1px solid #2e323a !important;
    border-radius: 8px !important;
    background-color: #1a1d24 !important;
}
[data-testid="stSidebar"] div.stButton > button[kind="primary"] {
    background-color: #3a62d7 !important;
    border: none !important;
    border-radius: 6px !important;
}
[data-testid="stSidebar"] div.stButton > button[kind="secondary"] {
    background-color: #252830 !important;
    border: 1px solid #353945 !important;
    border-radius: 6px !important;
}
.sb-sec {
    font-size: 0.68rem;
    font-weight: 600;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: #6e7688 !important;
    margin: 0.5rem 0 0.15rem;
    padding: 0;
}
.sb-hint {
    font-size: 0.75rem;
    color: #7a8496 !important;
    margin: 0 0 0.35rem;
    line-height: 1.4;
}
.sb-hint-ok { color: #6dbf8a !important; }
.sb-hint-warn { color: #d4a855 !important; }
.sb-hint-run { color: #7aa2ff !important; }
"""


def inject_sidebar_css() -> None:
    st.markdown(f"<style>{SIDEBAR_CSS}</style>", unsafe_allow_html=True)


def section(label: str) -> None:
    st.markdown(f'<p class="sb-sec">{label}</p>', unsafe_allow_html=True)


def hint(text: str, variant: str = "") -> None:
    cls = f"sb-hint sb-hint-{variant}" if variant else "sb-hint"
    st.markdown(f'<p class="{cls}">{text}</p>', unsafe_allow_html=True)
