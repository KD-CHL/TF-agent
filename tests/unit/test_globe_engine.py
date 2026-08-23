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


if __name__ == "__main__":
    unittest.main()
