# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import sys
import unittest

_TF_AGENT = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "TF-agent")
)
if _TF_AGENT not in sys.path:
    sys.path.insert(0, _TF_AGENT)

import globe_engine  # noqa: E402


class TestGlobeEngineUi(unittest.TestCase):
    def test_navigation_help_is_hidden_by_default(self):
        html = globe_engine.build_cesium_html({})
        self.assertIn("navigationHelpButton: false", html)
        self.assertIn(".cesium-navigation-help-button", html)

    def test_rectangle_aoi_requires_drag_and_has_live_preview(self):
        html = globe_engine.build_cesium_html({})
        self.assertIn("rectStartScreen", html)
        self.assertIn("Math.hypot(dx, dy) < 8", html)
        self.assertIn("Cesium.ScreenSpaceEventType.MOUSE_MOVE", html)
        self.assertIn("矩形模式：请按住鼠标拖拽框选", html)
        self.assertIn("矩形绘制中…松开鼠标完成", html)
        self.assertIn("screenSpaceCameraController.enableInputs = !mode", html)
        self.assertIn("showLocalAoiPreview(geometry)", html)
        self.assertIn('indexOf("aoi:") === 0', html)
        self.assertIn("AOI 已选定，已同步", html)
        self.assertIn("AOI 发送失败，请重试", html)

    def test_unsequenced_fly_is_rejected_after_a_sequenced_navigation(self):
        """A delayed legacy FLY must not cancel the current sequenced flight."""
        html = globe_engine.build_cesium_html({})
        guard = "if (!hasNavigationSeq && _lastFlyNavigationSeq > 0)"
        self.assertIn(guard, html)
        self.assertLess(html.index(guard), html.index("const navigationOptions ="))


if __name__ == "__main__":
    unittest.main()
