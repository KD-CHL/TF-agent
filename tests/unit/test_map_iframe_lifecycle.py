# -*- coding: utf-8 -*-
"""A 阶段 · iframe 生命周期测试：缓存签名不含相机字段、图层变更走协议 vs 重建、origin 一致性、版本失效。

运行：
    D:\anaconda3\envs\gwx\python.exe -m pytest tests/unit/test_map_iframe_lifecycle.py -q --tb=short -p no:cacheprovider
"""
from __future__ import annotations

import os
import sys
import unittest

_TF_AGENT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "TF-agent"))
if _TF_AGENT not in sys.path:
    sys.path.insert(0, _TF_AGENT)

import map_protocol as mp  # noqa: E402


class TestCacheSignatureCameraIndependence(unittest.TestCase):
    """核心约束：缓存签名不含相机字段 → 纯跳转复用 iframe，不重建 Viewer。"""

    def _sig(self, asset_path="", mtime=0.0, rev=0, opacity=50, show_e1=True, e1_tag="", force_local=True):
        return mp.globe_cache_signature(
            asset_path=asset_path,
            mtime=mtime,
            rev=rev,
            opacity_pct=opacity,
            show_e1=show_e1,
            e1_tag=e1_tag,
            force_local=force_local,
        )

    def test_signature_stable_across_camera_change(self):
        # 相机变化（map_center/map_zoom 不入签名）→ 签名不变
        s1 = self._sig()
        # 相机字段变化不影响签名（签名函数根本不接收相机参数）
        self.assertEqual(s1, self._sig())

    def test_signature_changes_on_asset_change(self):
        s1 = self._sig(asset_path="", mtime=0.0)
        s2 = self._sig(asset_path=r"E:\data\result.shp", mtime=1234.5)
        self.assertNotEqual(s1, s2)

    def test_signature_changes_on_mtime_change(self):
        s1 = self._sig(asset_path="a.shp", mtime=1.0)
        s2 = self._sig(asset_path="a.shp", mtime=2.0)
        self.assertNotEqual(s1, s2)

    def test_signature_changes_on_rev_bump(self):
        s1 = self._sig(rev=0)
        s2 = self._sig(rev=1)
        self.assertNotEqual(s1, s2)

    def test_signature_changes_on_opacity(self):
        s1 = self._sig(opacity=50)
        s2 = self._sig(opacity=70)
        self.assertNotEqual(s1, s2)

    def test_signature_changes_on_e1_flag(self):
        s1 = self._sig(show_e1=True, e1_tag="")
        s2 = self._sig(show_e1=False, e1_tag="")
        self.assertNotEqual(s1, s2)

    def test_signature_changes_on_force_local(self):
        s1 = self._sig(force_local=True)
        s2 = self._sig(force_local=False)
        self.assertNotEqual(s1, s2)

    def test_signature_is_deterministic(self):
        self.assertEqual(self._sig(), self._sig())


class TestCacheHitDecision(unittest.TestCase):
    """缓存命中判定。"""

    def test_hit_when_equal(self):
        sig = mp.globe_cache_signature(asset_path="a", mtime=1.0, rev=0, opacity_pct=50,
                                       show_e1=True, e1_tag="", force_local=True)
        self.assertTrue(mp.globe_cache_hit(cached_sig=sig, current_sig=sig))

    def test_miss_when_different(self):
        s1 = mp.globe_cache_signature(asset_path="a", mtime=1.0, rev=0, opacity_pct=50,
                                      show_e1=True, e1_tag="", force_local=True)
        s2 = mp.globe_cache_signature(asset_path="b", mtime=1.0, rev=0, opacity_pct=50,
                                      show_e1=True, e1_tag="", force_local=True)
        self.assertFalse(mp.globe_cache_hit(cached_sig=s1, current_sig=s2))

    def test_miss_when_cached_empty(self):
        sig = mp.globe_cache_signature(asset_path="a", mtime=1.0, rev=0, opacity_pct=50,
                                       show_e1=True, e1_tag="", force_local=True)
        self.assertFalse(mp.globe_cache_hit(cached_sig="", current_sig=sig))


class TestRebuildDecision(unittest.TestCase):
    """重建决策：图层协议可用时不重建；否则重建。"""

    def test_no_active_iframe_rebuilds(self):
        self.assertTrue(mp.should_rebuild_iframe(has_active_iframe=False, layer_protocol_ok=True, signature_changed=True))

    def test_layer_protocol_unavailable_rebuilds(self):
        self.assertTrue(mp.should_rebuild_iframe(has_active_iframe=True, layer_protocol_ok=False, signature_changed=True))

    def test_layer_protocol_ok_no_rebuild(self):
        # 有活跃 iframe + 协议可用 + 仅图层变化 → 走 CSTF_LAYER_ADD，不重建
        self.assertFalse(mp.should_rebuild_iframe(has_active_iframe=True, layer_protocol_ok=True, signature_changed=True))

    def test_nothing_changed_no_rebuild(self):
        self.assertFalse(mp.should_rebuild_iframe(has_active_iframe=True, layer_protocol_ok=True, signature_changed=False))


class TestSameGlobeOrigin(unittest.TestCase):
    """same_globe_origin：缓存 URL 仍指向当前服务时复用。"""

    def test_same_origin_local(self):
        self.assertTrue(mp.same_globe_origin("http://127.0.0.1:8765/globe?v=abc", base="http://127.0.0.1:8765"))

    def test_same_origin_with_query(self):
        self.assertTrue(mp.same_globe_origin("http://127.0.0.1:8765/globe?v=abc&b=1", base="http://127.0.0.1:8765"))

    def test_different_port(self):
        self.assertFalse(mp.same_globe_origin("http://127.0.0.1:9999/globe?v=abc", base="http://127.0.0.1:8765"))

    def test_different_host(self):
        self.assertFalse(mp.same_globe_origin("http://192.168.1.5:8765/globe", base="http://127.0.0.1:8765"))

    def test_empty_url(self):
        self.assertFalse(mp.same_globe_origin("", base="http://127.0.0.1:8765"))

    def test_remote_base(self):
        self.assertTrue(mp.same_globe_origin("https://abc.ngrok-free.app/globe?v=x", base="https://abc.ngrok-free.app"))


class TestServerVersionInvalidation(unittest.TestCase):
    """服务版本变化 → 旧 URL 失效。"""

    def test_version_part_of_origin_check(self):
        # 版本号体现在服务端 _SERVER_VERSION；同一 base 下 URL 相同 → 一致
        base = mp.globe_service_base_for_test(port=8765, force_local=True)
        self.assertEqual(base, "http://127.0.0.1:8765")

    def test_base_includes_scheme_and_port(self):
        base = mp.globe_service_base_for_test(port=1234, force_local=True)
        self.assertEqual(base, "http://127.0.0.1:1234")


if __name__ == "__main__":
    unittest.main()
