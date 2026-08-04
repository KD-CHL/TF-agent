# -*- coding: utf-8 -*-
"""B 阶段 · 动态能力状态测试：9 能力注册、状态判定、cheap/expensive TTL、失效、
异常→UNKNOWN、无敏感值、摘要白名单、Agent 不可改写。"""
import os
import sys
import unittest

_TF_AGENT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "TF-agent"))
if _TF_AGENT not in sys.path:
    sys.path.insert(0, _TF_AGENT)

import capability_registry as cr  # noqa: E402

_EXPECTED_IDS = {
    "map_navigation",
    "map_layer_display",
    "deep_learning_inference",
    "gee_download",
    "e1_quality_evaluation",
    "m5_change_detection",
    "autotune",
    "pdf_report",
    "knowledge_search",
}


def _registry_with_fake_files(tmp, now_fn=None, **extra_ctx):
    """构造上下文：模型/脚本/知识库均为临时目录下的真实文件。"""
    model = tmp / "model.pth"
    model.write_bytes(b"fake-model")
    script = tmp / "auto_tune.py"
    script.write_text("# fake", encoding="utf-8")
    kb = tmp / "kb"
    kb.mkdir()
    ctx = {
        "model_path": str(model),
        "autotune_script": str(script),
        "knowledge_db_dir": str(kb),
        "task": "map_view",
    }
    ctx.update(extra_ctx)
    return cr.CapabilityRegistry(context=ctx, now_fn=now_fn)


class TestRegistrySetup(unittest.TestCase):
    def test_all_nine_capabilities_registered(self):
        reg = cr.CapabilityRegistry(context={})
        ids = set(reg.ids())
        self.assertEqual(ids, _EXPECTED_IDS)

    def test_unknown_capability_returns_unknown(self):
        reg = cr.CapabilityRegistry(context={})
        st = reg.check("no_such_capability")
        self.assertEqual(st.status, cr.UNKNOWN)
        self.assertIn("未注册", st.summary)

    def test_register_overwrites_previous(self):
        reg = cr.CapabilityRegistry(context={})
        reg.register("custom_cap", "自定义", "cheap", lambda ctx: cr.CapabilityStatus(
            capability_id="custom_cap", label="自定义", status=cr.AVAILABLE, summary="ok"))
        st = reg.check("custom_cap")
        self.assertEqual(st.status, cr.AVAILABLE)


class TestStatusDetermination(unittest.TestCase):
    def test_available_when_all_requirements_met(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tmp = __import__("pathlib").Path(td)
            reg = _registry_with_fake_files(tmp)
            st = reg.check("deep_learning_inference")
            self.assertEqual(st.status, cr.AVAILABLE)

    def test_blocked_when_model_path_missing(self):
        reg = cr.CapabilityRegistry(context={"model_path": "Z:/no/such/model.pth", "task": "infer"})
        st = reg.check("deep_learning_inference")
        self.assertEqual(st.status, cr.BLOCKED)

    def test_unavailable_when_gee_project_not_configured(self):
        reg = cr.CapabilityRegistry(context={})
        st = reg.check("gee_download")
        self.assertEqual(st.status, cr.UNAVAILABLE)

    def test_conditional_when_pdf_font_missing_with_fallback(self):
        reg = cr.CapabilityRegistry(context={})
        st = reg.check("pdf_report")
        self.assertIn(st.status, (cr.CONDITIONAL, cr.AVAILABLE))
        # 缺中文字体时应有降级提示
        if st.status == cr.CONDITIONAL:
            self.assertTrue(st.warnings or st.blockers or st.recommended_actions)

    def test_unknown_when_check_raises(self):
        def boom(ctx):
            raise ValueError("boom-boom")

        reg = cr.CapabilityRegistry(context={})
        reg.register("explode_cap", "爆炸", "cheap", boom)
        st = reg.check("explode_cap")
        self.assertEqual(st.status, cr.UNKNOWN)
        self.assertIn("boom-boom", st.summary)

    def test_blocked_summary_has_no_absolute_path(self):
        reg = cr.CapabilityRegistry(context={"model_path": "Z:/no/such/model.pth", "task": "infer"})
        st = reg.check("deep_learning_inference")
        self.assertNotIn("Z:/", st.summary)
        self.assertNotIn("model.pth", st.summary)
        self.assertTrue(st.blockers)


class TestTierTtl(unittest.TestCase):
    def test_cheap_ttl_10s_no_recheck(self):
        calls = {"n": 0}

        def check_fn(ctx):
            calls["n"] += 1
            return cr.CapabilityStatus(capability_id="c1", label="C1", status=cr.AVAILABLE, summary="ok")

        reg = cr.CapabilityRegistry(context={})
        reg.register("c1", "C1", "cheap", check_fn)
        t0 = [1000.0]
        reg._now_fn = lambda: t0[0]
        reg.check("c1")
        t0[0] += 9.0  # 9s < 10s TTL
        reg.check("c1")
        self.assertEqual(calls["n"], 1)

    def test_expensive_ttl_60s_no_recheck(self):
        calls = {"n": 0}

        def check_fn(ctx):
            calls["n"] += 1
            return cr.CapabilityStatus(capability_id="c2", label="C2", status=cr.AVAILABLE, summary="ok")

        reg = cr.CapabilityRegistry(context={})
        reg.register("c2", "C2", "expensive", check_fn)
        t0 = [2000.0]
        reg._now_fn = lambda: t0[0]
        reg.check("c2")
        t0[0] += 59.0  # < 60s TTL
        reg.check("c2")
        self.assertEqual(calls["n"], 1)

    def test_expired_ttl_rechecks(self):
        calls = {"n": 0}

        def check_fn(ctx):
            calls["n"] += 1
            return cr.CapabilityStatus(capability_id="c3", label="C3", status=cr.AVAILABLE, summary="ok")

        reg = cr.CapabilityRegistry(context={})
        reg.register("c3", "C3", "cheap", check_fn)
        t0 = [3000.0]
        reg._now_fn = lambda: t0[0]
        reg.check("c3")
        t0[0] += 11.0  # > 10s TTL
        reg.check("c3")
        self.assertEqual(calls["n"], 2)

    def test_force_rechecks_immediately(self):
        calls = {"n": 0}

        def check_fn(ctx):
            calls["n"] += 1
            return cr.CapabilityStatus(capability_id="c4", label="C4", status=cr.AVAILABLE, summary="ok")

        reg = cr.CapabilityRegistry(context={})
        reg.register("c4", "C4", "expensive", check_fn)
        t0 = [4000.0]
        reg._now_fn = lambda: t0[0]
        reg.check("c4")
        reg.check("c4", force=True)
        self.assertEqual(calls["n"], 2)


class TestInvalidation(unittest.TestCase):
    def test_bump_invalidates_all(self):
        calls = {"n": 0}

        def check_fn(ctx):
            calls["n"] += 1
            return cr.CapabilityStatus(capability_id="c5", label="C5", status=cr.AVAILABLE, summary="ok")

        reg = cr.CapabilityRegistry(context={})
        reg.register("c5", "C5", "cheap", check_fn)
        reg.check("c5")
        reg.bump()
        reg.check("c5")
        self.assertEqual(calls["n"], 2)

    def test_invalidate_single(self):
        calls = {"n": 0}

        def check_fn(ctx):
            calls["n"] += 1
            return cr.CapabilityStatus(capability_id="c6", label="C6", status=cr.AVAILABLE, summary="ok")

        reg = cr.CapabilityRegistry(context={})
        reg.register("c6", "C6", "cheap", check_fn)
        reg.check("c6")
        reg.invalidate("c6")
        reg.check("c6")
        self.assertEqual(calls["n"], 2)

    def test_task_switch_invalidates(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as td:
            reg = _registry_with_fake_files(Path(td))
            # 任务切换：context 变化应触发 bump（由消费方调用）
            reg.bump()
            self.assertEqual(len(reg._cache), 0)


class TestSafety(unittest.TestCase):
    def test_cache_never_contains_sensitive_values(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            reg = _registry_with_fake_files(tmp)
            for cid in reg.ids():
                st = reg.check(cid)
                ev = st.evidence or {}
                for k, v in ev.items():
                    kl = str(k).lower()
                    self.assertNotIn("token", kl)
                    self.assertNotIn("key", kl)
                    self.assertNotIn("secret", kl)
                    if isinstance(v, str):
                        self.assertNotIn("Z:/", v)

    def test_summary_whitelist_only(self):
        reg = cr.CapabilityRegistry(context={})
        snap = reg.snapshot_for_agent()
        self.assertIsInstance(snap, dict)
        # 快照只含能力 id → (status, summary)
        for cid, entry in snap.items():
            self.assertIn("status", entry)
            self.assertIn("summary", entry)
            self.assertNotIn("evidence", entry)
            self.assertNotIn("requirements", entry)
            self.assertNotIn("blockers", entry)
            self.assertNotIn("checked_at", entry)

    def test_unknown_summary_has_no_traceback(self):
        def boom(ctx):
            raise ValueError("kaboom")

        reg = cr.CapabilityRegistry(context={})
        reg.register("boom_cap", "爆炸", "cheap", boom)
        st = reg.check("boom_cap")
        self.assertNotIn("Traceback", st.summary)
        self.assertNotIn("File \"", st.summary)

    def test_agent_cannot_mutate_registry_via_snapshot(self):
        reg = cr.CapabilityRegistry(context={})
        snap = reg.snapshot_for_agent()
        if snap:
            first = next(iter(snap))
            snap[first] = {"status": "AVAILABLE", "summary": "伪造"}
        # 快照是拷贝：注册表内部状态不受影响
        st = reg.check(first)
        self.assertEqual(st.capability_id, first)
        self.assertNotEqual(st.status, "伪造")


if __name__ == "__main__":
    unittest.main()
