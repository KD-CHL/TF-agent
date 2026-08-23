# -*- coding: utf-8 -*-
"""聊天区布局契约：会话可见、授权控件不侵入输入区、宽度可调。"""
from __future__ import annotations

from pathlib import Path
import unittest


class TestChatUiContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (Path(__file__).parents[2] / "TF-agent" / "app.py").read_text(encoding="utf-8")

    def test_chat_panel_has_resizable_width_and_session_list(self):
        self.assertIn("agent_chat_width_pct", self.source)
        self.assertIn("对话区宽度", self.source)
        self.assertIn("list_threads", self.source)
        self.assertIn("conversation_switch", self.source)

    def test_agent_dock_separates_chat_history_and_resizes_at_edge(self):
        """Agent Dock 保持常驻，宽度通过边缘分隔线调整。"""
        self.assertIn("agent_dock_view", self.source)
        self.assertIn("历史", self.source)
        self.assertIn("cstf-dock-resize-handle", self.source)
        self.assertNotIn('key="agent_dock_collapse"', self.source)

    def test_history_view_hides_chat_stream_and_composer(self):
        """历史页只做会话导航，不展示聊天流或发送控件。"""
        # Streamlit 会按浏览器语言翻译 aria-label，不能用英文 widget key 定位表单。
        self.assertIn(
            'div[data-testid="stColumn"]:has(.cstf-agent-view-history) [data-testid="stForm"],',
            self.source,
        )
        self.assertIn(
            'div[data-testid="stColumn"]:has(.cstf-agent-view-history) [data-testid="stForm"]:has(input[aria-label="chat_input"])',
            self.source,
        )
        self.assertIn(
            'div[data-testid="stColumn"]:has(.cstf-agent-view-history) [data-testid="stChatMessage"]',
            self.source,
        )
        self.assertIn(
            'div[data-testid="stColumn"]:has(.cstf-agent-view-history) > div[data-testid="stVerticalBlock"] > [data-testid="stLayoutWrapper"]:has([data-testid="stChatMessage"])',
            self.source,
        )
        self.assertIn("cstf-chat-stream-marker", self.source)
        self.assertIn('[data-testid="stVerticalBlockBorderWrapper"]:has(.cstf-chat-stream-marker)', self.source)

    def test_history_session_list_uses_remaining_dock_height(self):
        """会话列表填充剩余空间；空状态仅显示提示文本。"""
        self.assertIn('with st.container(height="stretch", border=True):', self.source)
        self.assertIn('if not _conversation_threads:', self.source)
        self.assertIn('[data-testid="stLayoutWrapper"]:has(.cstf-chat-stream-marker)', self.source)
        self.assertIn('st.caption("暂无历史会话")', self.source)
        self.assertIn('_conv_c1, _conv_c2 = st.columns(2)', self.source)

    def test_status_log_panel_is_below_map_and_adjustable(self):
        """状态/日志移到地图下方，并提供可收起与高度调节。"""
        self.assertIn('cstf-map-status-zone', self.source)
        self.assertIn('agent_status_panel_height', self.source)
        self.assertIn('agent_status_panel_collapsed', self.source)
        self.assertIn('cstf-status-edge-handle', self.source)

    def test_status_panel_preserves_fragment_refresh_fallback(self):
        """日志保持局部刷新，并为不支持 fragment 的环境保留整页刷新兜底。"""
        self.assertIn('st.fragment(run_every=2.5)(_pipeline_monitor_inner)', self.source)
        self.assertIn('not _PIPELINE_USE_FRAGMENT\n    and st.session_state.is_running', self.source)
        self.assertIn('cstf-status-edge-toggle', self.source)
        self.assertIn('cstf-dock-resize-handle', self.source)
        self.assertNotIn('cstf-map-status-title', self.source)
        self.assertIn('if _log_panel_slot is not None:', self.source)
        self.assertNotIn('_log_panel_slot = st.container()\n    st.markdown(\'<div class="cstf-log-panel-host-marker', self.source)
        self.assertIn('--cstf-status-panel-reserve', self.source)
        self.assertIn('mapPx', self.source)

    def test_resize_controls_do_not_render_sliders(self):
        """尺寸控制使用边缘拖拽，不再渲染两个可见滑块。"""
        self.assertNotIn('st.slider(\n            "状态区高度"', self.source)
        self.assertNotIn('st.slider(\n            "对话区宽度"', self.source)

    def test_chat_attachment_is_a_plus_button_in_compose_row(self):
        """附件选择器隐藏，+ 入口移动到消息输入行左侧。"""
        self.assertIn("inputRow.insertBefore(bar, inputRow.firstChild)", self.source)
        self.assertIn('input[aria-label="聊天输入"]', self.source)
        self.assertIn("cstf-chat-compose", self.source)
        self.assertIn("cstf-chat-input-row", self.source)
        self.assertIn("cstf-chat-send-column", self.source)
        self.assertIn('class="cstf-plus-btn"', self.source)
        self.assertIn("每个文件≤200MB", self.source)
        self.assertIn('data-tooltip="每个文件≤200MB · PNG / JPG / WebP / TIFF"', self.source)
        self.assertIn("content: attr(data-tooltip);", self.source)
        self.assertNotIn("附件仅用于本机会话预览", self.source)
        self.assertIn('data-testid="stFileUploader"] {', self.source)
        self.assertIn("flex-direction: row !important;", self.source)
        self.assertIn("order: 2 !important;", self.source)
        self.assertIn('data-media-mode="local"', self.source)
        self.assertIn('data-media-mode="external"', self.source)
        self.assertIn("fileInput.value = '';", self.source)
        self.assertIn("win.setTimeout(clearSelectedFileUi, 60);", self.source)
        self.assertIn("__cstfAttachmentLastEpoch", self.source)
        self.assertIn("attachmentEpochChanged", self.source)
        self.assertIn("__cstfAttachmentReconciler", self.source)
        self.assertIn("setInterval(reconcileAttachment", self.source)

    def test_resize_handles_show_only_the_blue_drag_bar(self):
        """拖动命中区不能带浏览器焦点外框或额外边框。"""
        self.assertIn(".cstf-dock-resize-handle:focus-visible", self.source)
        self.assertIn(".cstf-status-edge-handle:focus-visible", self.source)
        self.assertIn(
            "outline: none !important;\n        border: 0 !important;\n        box-shadow: none !important;",
            self.source,
        )

    def test_status_drawer_has_no_streamlit_gap_row(self):
        """地图列的默认子项间距不能把状态抽屉推离地图边界。"""
        self.assertIn("gap: 0 !important;", self.source)
        self.assertIn("max-height: 0 !important;", self.source)
        self.assertIn("(mapRect.left + mapRect.right) / 2", self.source)
        self.assertIn("collapsed ? mapRect.bottom - toggleHeight : mapRect.bottom", self.source)

    def test_status_toggle_targets_real_button_not_help_tooltip_button(self):
        """状态三角桥接必须避开 Streamlit help 生成的提示按钮。"""
        self.assertIn("div.st-key-agent_status_panel_toggle button", self.source)

    def test_status_bridge_button_does_not_create_nested_help_control(self):
        """隐藏桥接按钮不应再创建会拦截点击的 Streamlit help 子按钮。"""
        self.assertNotIn(
            'key="agent_status_panel_toggle",\n            help="收起或展开状态区",',
            self.source,
        )

    def test_status_bridge_row_remains_rendered_without_reserving_layout_space(self):
        """桥接行不能 display:none，否则第二次点击无法触发 Streamlit 事件。"""
        self.assertIn("position: absolute !important;", self.source)
        self.assertIn("pointer-events: none !important;", self.source)
        self.assertIn(
            'div.st-key-agent_status_panel_toggle button {',
            self.source,
        )

    def test_status_toggle_rebinds_after_streamlit_rerender(self):
        """Streamlit 重绘后必须重新确保三角按钮的 click 监听器存在。"""
        self.assertIn("toggle.dataset.cstfStatusClickBound", self.source)
        self.assertIn('toggle.setAttribute(\n            "onclick"', self.source)

    def test_resize_handles_expose_accessible_size_values(self):
        self.assertIn('handle.setAttribute("aria-valuemin", "24")', self.source)
        self.assertIn('handle.setAttribute("aria-valuemax", "48")', self.source)
        self.assertIn('handle.setAttribute("aria-valuemin", "192")', self.source)
        self.assertIn('handle.setAttribute("aria-valuemax", "392")', self.source)
        self.assertIn('handle.setAttribute("aria-valuenow"', self.source)

    def test_resize_edge_hit_areas_stay_above_embedded_content(self):
        """地图 iframe 与聊天内容不能遮挡整条拖拽边缘。"""
        self.assertIn("z-index: 1350;", self.source)
        self.assertIn("z-index: 1600;", self.source)

    def test_resize_drag_lifecycle_runs_in_parent_document_realm(self):
        """拖拽不能依赖会被 Streamlit 重绘回收的组件 iframe 监听器。"""
        self.assertIn("const parentResizePointerDown = String.raw`", self.source)
        self.assertIn("const parentResizeKeyDown = String.raw`", self.source)
        self.assertGreaterEqual(
            self.source.count('handle.setAttribute("onpointerdown", parentResizePointerDown);'),
            2,
        )
        self.assertIn('win.addEventListener("pointermove", move, true);', self.source)
        self.assertIn('win.addEventListener("pointerup", stop, true);', self.source)
        self.assertIn('overlay.className = "cstf-resize-capture";', self.source)
        self.assertIn('overlay.addEventListener("pointermove", move, true);', self.source)
        self.assertIn('overlay.addEventListener("mouseup", stop, true);', self.source)
        self.assertIn("overlay.remove();", self.source)
        self.assertNotIn('handle.addEventListener("pointermove"', self.source)

    def test_agent_width_resize_resynchronizes_status_boundary(self):
        """Agent 宽度变化后，状态区边缘必须立即跟随新的地图宽度。"""
        self.assertIn('statusHandle.style.width = mapRect.width + "px";', self.source)
        self.assertIn('statusHandle.style.left = mapRect.left + "px";', self.source)
        self.assertIn("applyDockWidth(pct);", self.source)
        self.assertIn("syncResizeGeometry();", self.source)

    def test_map_height_excludes_zero_height_command_iframes(self):
        """定位 postMessage 的辅助 iframe 不得被扩成地图高度。"""
        self.assertIn("const getPrimaryMapFrame = (mapCol) =>", self.source)
        self.assertIn('iframe[src*="/globe"]', self.source)
        self.assertIn('iframe[title*="streamlit_folium"]', self.source)
        self.assertIn("const mapFrame = getPrimaryMapFrame(mapCol);", self.source)

    def test_alerts_are_dismissible_and_do_not_reflow_workbench(self):
        """错误/警告通知浮动显示并支持关闭，避免挤压地图与 Agent。"""
        self.assertIn("cstf-dismissible-alert", self.source)
        self.assertIn("cstf-alert-close", self.source)
        self.assertIn('aria-label", "关闭通知"', self.source)
        self.assertIn("sessionStorage.getItem(noticeKey)", self.source)
        self.assertIn("sessionStorage.setItem(noticeKey, \"1\")", self.source)

    def test_history_view_prioritizes_conversation_space_over_monitor_log(self):
        """状态/日志在地图下方，与 Agent 历史页相互独立。"""
        self.assertIn('cstf-map-status-zone', self.source)
        self.assertIn('if _log_panel_slot is not None:', self.source)

    def test_chat_failure_is_persisted_as_an_assistant_reply(self):
        """连接失败也要留在会话流中，避免历史发送看起来像没有响应。"""
        self.assertIn('st.session_state.messages.append({"role": "assistant", "content": _error_reply})', self.source)

    def test_chat_messages_have_distinct_left_right_alignment(self):
        """用户消息右对齐、助手消息左对齐，且卡片宽度受控。"""
        self.assertIn('[data-testid="stChatMessage"]:has(.msg-role-user)', self.source)
        self.assertIn('flex-direction: row-reverse', self.source)
        self.assertIn('margin-left: auto', self.source)
        self.assertIn('[data-testid="stChatMessage"]:has(.msg-role-assistant)', self.source)
        self.assertIn('margin-right: auto', self.source)
        self.assertIn('max-width: 86%', self.source)

    def test_chat_stream_and_composer_have_explicit_size_contract(self):
        """消息滚动区占剩余高度，输入区固定收缩，避免再次出现空白或溢出。"""
        self.assertIn('min-height: 0 !important', self.source)
        self.assertIn('flex: 0 0 auto !important', self.source)
        self.assertIn('max-height: 250px !important', self.source)

    def test_message_cards_shrink_to_content_before_max_width(self):
        """短消息不应继承整列宽度，长消息仍受最大宽度约束。"""
        self.assertIn('width: fit-content !important', self.source)
        self.assertIn('min-width: 7rem !important', self.source)
        self.assertIn('max-width: 86% !important', self.source)

    def test_history_view_is_navigation_only_and_session_switch_opens_chat(self):
        """历史页仅展示记录，选中会话后自动返回对话视图。"""
        self.assertIn(
            'div[data-testid="stColumn"]:has(.cstf-agent-view-history) [data-testid="stForm"]:has(input[aria-label="chat_input"])',
            self.source,
        )
        self.assertIn(
            'div[data-testid="stColumn"]:has(.cstf-agent-view-history) [data-testid="stChatMessage"]',
            self.source,
        )
        self.assertIn(
            'div[data-testid="stColumn"]:has(.cstf-agent-view-history) > div[data-testid="stVerticalBlock"] > [data-testid="stLayoutWrapper"]:has([data-testid="stChatMessage"])',
            self.source,
        )
        self.assertIn('st.session_state.agent_dock_view = "对话"', self.source)

    def test_clear_session_selects_next_without_opening_chat(self):
        """清空当前会话后，应在历史页选中下一条会话。"""
        self.assertIn("next_thread_id_after_delete", self.source)
        self.assertIn("_next_thread_id = next_thread_id_after_delete", self.source)
        self.assertIn("load_messages(_next_thread_id)", self.source)
        self.assertIn('_conv_c1, _conv_c2 = st.columns(2)', self.source)
        self.assertNotIn("_conversation_stay_in_history", self.source)

    def test_attachment_observer_uses_parent_document_realm(self):
        self.assertIn("win.MutationObserver", self.source)

    def test_chat_input_does_not_render_unrequested_consent_checkboxes(self):
        self.assertNotIn("允许将上传影像发送给外部模型", self.source)
        self.assertNotIn("允许发送精确空间元数据", self.source)
        self.assertNotIn('st.checkbox(\n            "允许将上传影像', self.source)
        self.assertNotIn('st.checkbox(\n            "允许发送精确空间', self.source)


if __name__ == "__main__":
    unittest.main()
