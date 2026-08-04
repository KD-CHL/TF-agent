# -*- coding: utf-8 -*-
"""A 阶段 · 地图命令协议测试：CSTF_MAP_V1 信封、command_id 幂等、非法载荷阻断、origin 收紧、READY 等待。

运行：
    D:\anaconda3\envs\gwx\python.exe -m pytest tests/unit/test_map_command_protocol.py -q --tb=short -p no:cacheprovider
"""
from __future__ import annotations

import math
import os
import sys
import unittest

_TF_AGENT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "TF-agent"))
if _TF_AGENT not in sys.path:
    sys.path.insert(0, _TF_AGENT)

import map_protocol as mp  # noqa: E402


class TestFlyMessageEnvelope(unittest.TestCase):
    """CSTF_FLY 信封构造与解析。"""

    def test_make_fly_message_valid_envelope(self):
        msg, errs = mp.make_fly_message(120.8, 30.5, zoom=9)
        self.assertFalse(errs)
        self.assertEqual(msg["type"], mp.MSG_FLY)
        self.assertEqual(msg["version"], mp.MAP_PROTOCOL_VERSION)
        self.assertIn("command_id", msg)
        self.assertIn("ts", msg)
        self.assertEqual(msg["lat"], 30.5)
        self.assertEqual(msg["lon"], 120.8)

    def test_make_fly_message_command_ids_unique(self):
        m1, _ = mp.make_fly_message(120.8, 30.5)
        m2, _ = mp.make_fly_message(120.8, 30.5)
        self.assertNotEqual(m1["command_id"], m2["command_id"])

    def test_make_fly_message_explicit_command_id_kept(self):
        msg, _ = mp.make_fly_message(120.8, 30.5, command_id="cid-abc")
        self.assertEqual(msg["command_id"], "cid-abc")

    def test_make_fly_message_default_label(self):
        msg, _ = mp.make_fly_message(120.8, 30.5)
        self.assertIn("30.50", msg.get("label", ""))
        self.assertIn("120.80", msg.get("label", ""))

    def test_make_fly_message_custom_label(self):
        msg, _ = mp.make_fly_message(120.8, 30.5, label="杭州湾")
        self.assertEqual(msg["label"], "杭州湾")

    def test_parse_map_message_valid_envelope(self):
        msg, _ = mp.make_fly_message(120.8, 30.5)
        ok, errors = mp.parse_map_message(msg)
        self.assertTrue(ok)
        self.assertEqual(errors, [])

    def test_parse_map_message_unknown_type_rejected(self):
        ok, errors = mp.parse_map_message({"type": "CSTF_UNKNOWN", "version": 1})
        self.assertFalse(ok)
        self.assertTrue(any("type" in e for e in errors))

    def test_parse_map_message_missing_command_id_auto_filled(self):
        ok, errors = mp.parse_map_message({"type": mp.MSG_FLY, "version": 1, "lat": 30.0, "lon": 120.0})
        self.assertTrue(ok)
        self.assertEqual(errors, [])


class TestFlyPayloadValidation(unittest.TestCase):
    """非法坐标必须阻断：NaN / Inf / 越界 → 不产生消息。"""

    def test_invalid_lat_high_rejected(self):
        msg, errs = mp.make_fly_message(120.0, 91.0)
        self.assertIsNone(msg)
        self.assertTrue(any("lat" in e for e in errs))

    def test_invalid_lon_high_rejected(self):
        msg, errs = mp.make_fly_message(181.0, 30.0)
        self.assertIsNone(msg)
        self.assertTrue(any("lon" in e for e in errs))

    def test_invalid_lat_low_rejected(self):
        msg, errs = mp.make_fly_message(120.0, -91.0)
        self.assertIsNone(msg)

    def test_nan_coords_rejected(self):
        msg, errs = mp.make_fly_message(float("nan"), 30.0)
        self.assertIsNone(msg)
        self.assertTrue(any("非有限" in e or "finite" in e.lower() or "无效" in e for e in errs))

    def test_inf_coords_rejected(self):
        msg, errs = mp.make_fly_message(120.0, float("inf"))
        self.assertIsNone(msg)

    def test_none_coords_rejected(self):
        msg, errs = mp.make_fly_message(None, 30.0)
        self.assertIsNone(msg)

    def test_zoom_out_of_range_clamped(self):
        msg, _ = mp.make_fly_message(120.0, 30.0, zoom=99)
        self.assertIsNotNone(msg)
        # zoom 只影响高度；此处确认高度是合法有限值
        self.assertTrue(math.isfinite(msg["height"]))

    def test_fly_height_from_zoom_matches_engine_mapping(self):
        msg, _ = mp.make_fly_message(120.0, 30.0, zoom=9)
        self.assertAlmostEqual(msg["height"], 280_000.0, delta=1.0)


class TestCommandIdempotency(unittest.TestCase):
    """command_id 幂等：重复命令直接判定重复，容量有界。"""

    def test_duplicate_command_id_detected(self):
        seen = mp.CommandIdWindow(capacity=200)
        cid = "cid-1"
        self.assertFalse(seen.is_duplicate(cid))  # 首次不重复
        self.assertTrue(seen.is_duplicate(cid))   # 再次重复

    def test_capacity_evicts_oldest(self):
        seen = mp.CommandIdWindow(capacity=5)
        for i in range(5):
            seen.is_duplicate(f"cid-{i}")
        # 前 5 个仍在窗口内 → 重复
        self.assertTrue(seen.is_duplicate("cid-0"))
        # 加入第 6 个 → 挤出最早
        seen.is_duplicate("cid-5")
        self.assertFalse(seen.is_duplicate("cid-0"))  # 已淘汰
        self.assertTrue(seen.is_duplicate("cid-1"))   # 仍在


class TestOriginTightening(unittest.TestCase):
    """targetOrigin 收紧：默认 127.0.0.1 精确源；远程演示可放宽。"""

    def test_local_origin_default(self):
        self.assertEqual(mp.target_origin(8765, force_local=True), "http://127.0.0.1:8765")

    def test_local_origin_custom_port(self):
        self.assertEqual(mp.target_origin(9900, force_local=True), "http://127.0.0.1:9900")

    def test_remote_origin_uses_public_base(self):
        # 非本地（远程演示）→ 使用公网根 URL 的 origin
        origin = mp.target_origin(8765, force_local=False, public_base="https://abc.ngrok-free.app")
        self.assertEqual(origin, "https://abc.ngrok-free.app")

    def test_remote_origin_without_public_base_falls_back_local(self):
        origin = mp.target_origin(8765, force_local=False, public_base=None)
        self.assertEqual(origin, "http://127.0.0.1:8765")

    def test_wildcard_explicitly_allowed_only_when_requested(self):
        origin = mp.target_origin(8765, force_local=False, public_base=None, allow_wildcard=True)
        self.assertEqual(origin, "*")


class TestReadyHandshakeWindow(unittest.TestCase):
    """READY 握手等待：窗口判定与超时。"""

    def test_within_window_not_expired(self):
        self.assertFalse(mp.ready_window_expired(ready_ts=None, now=1000, timeout_s=3.0))
        self.assertFalse(mp.ready_window_expired(ready_ts=999.9, now=1000.0, timeout_s=3.0))

    def test_window_expired_after_timeout(self):
        self.assertTrue(mp.ready_window_expired(ready_ts=990.0, now=1000.0, timeout_s=3.0))

    def test_wait_policy_decision(self):
        # 已就绪 → 直接发
        self.assertEqual(
            mp.ready_policy(ready_ts=999.0, now=1000.0, timeout_s=3.0),
            "send",
        )
        # 未就绪且在窗口内 → 等待
        self.assertEqual(
            mp.ready_policy(ready_ts=None, now=1000.0, timeout_s=3.0),
            "wait",
        )
        # 未就绪且超时 → 发送但标记 warning
        self.assertEqual(
            mp.ready_policy(ready_ts=None, now=1000.0, timeout_s=-1.0),
            "send_warn",
        )


if __name__ == "__main__":
    unittest.main()
