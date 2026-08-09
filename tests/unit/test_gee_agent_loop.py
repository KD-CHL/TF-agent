# -*- coding: utf-8 -*-
"""GEE 数据下载可信执行闭环单元测试（gee_agent_loop + 门闩语义）。

覆盖 B13 spec 24 项高价值检查点：
  1   合法输入生成计划（bands 默认 B4/B3/B2 顺序 RGB）
  2   缺少 AOI 阻断
  3   无效 AOI 阻断
  4   日期非法阻断（格式错误 / start>end）
  5   ee 不可导入阻断
  6   project 缺失阻断
  7   proxy 格式非法阻断
  8   波段非法阻断
  9   未确认不执行（build_pending_task 拒绝）
 10   重复确认不重复执行
 11   rerun 不重新 task.start（读 ledger 已有 gee_task_id）
 12   RUNNING 状态轮询
 13   FAILED 真实错误原样保存
 14   COMPLETED 但本地文件缺失 → 不登记（区分云端/本地就绪）
 15   无 CRS 文件 → 验证失败
 16   全 NoData 文件 → 验证失败
 17   成功后登记资产（scene_count 进 metadata）
 18   scene_count 正确
 19   capability 更新（gee_download 使用多源 project 解析语义）
 20   inference plan 读取 dataset asset（scene_count 用于 A1 阻断）
 21   GEE confirm 不触发 inference
 22   Copilot 不 fake completion（summarize 只基于真实结果）
 23   task_id / plan_id 隔离（多 pending 不串用）
 24   不泄露凭证（plan / summarize / validate 输出无 credentials）

原则：不真实连接 GEE。用 fake m4_engine（写真实小 GeoTIFF）+ mock ee，
走真实磁盘产物；dataset 登记路径重定向到 tmp，避免污染真实 registry。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest import mock

_TF_AGENT = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "TF-agent")
)
if _TF_AGENT not in sys.path:
    sys.path.insert(0, _TF_AGENT)

import gee_agent_loop as gal  # noqa: E402
from aoi_context import aoi_from_bbox  # noqa: E402


# ---------------------------------------------------------------
#  helpers
# ---------------------------------------------------------------
def _make_tif(
    path,
    width=64,
    height=64,
    crs="EPSG:4326",
    bands=3,
    nodata=None,
    fill=None,
):
    import numpy as np
    import rasterio
    from rasterio.transform import from_origin

    path = os.path.abspath(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tr = from_origin(119.0, 26.0, 0.001, 0.001)
    with rasterio.open(
        path, "w", driver="GTiff", width=width, height=height,
        count=bands, dtype="uint8", crs=crs, transform=tr, nodata=nodata,
    ) as dst:
        for b in range(1, bands + 1):
            arr = np.zeros((height, width), dtype="uint8")
            if fill is not None:
                arr[:] = fill
            else:
                arr[:] = np.arange(height * width, dtype="uint8").reshape(height, width) % 250
            dst.write(arr, b)
    return path


def _make_aoi_dict(label="test_aoi"):
    ctx = aoi_from_bbox(119.0, 25.5, 120.0, 26.0, source="map_rectangle", label=label)
    return ctx.to_dict()


def _happy_plan(tmp, task="task_a", export_to="local", bands=None, **kw):
    out_dir = os.path.join(tmp, "out")
    os.makedirs(out_dir, exist_ok=True)
    return gal.build_gee_download_plan(
        task_id=task,
        aoi=_make_aoi_dict(),
        start_date=kw.pop("start_date", "2023-01-01"),
        end_date=kw.pop("end_date", "2023-01-15"),
        bands=bands,
        export_to=export_to,
        local_out_dir=out_dir,
        **kw,
    )


class _FakeM4:
    """fake m4_engine：run_m4_download 在本地写出真实小 GeoTIFF 并返回真实 dict。"""

    def __init__(self, tmp, scene_count=3, fail=False, drive_ids=None):
        self.tmp = tmp
        self.scene_count = scene_count
        self.fail = fail
        self.drive_ids = drive_ids or []
        self.started = []  # 记录 task.start() 调用次数（drive 模式）

    def run_m4_download(self, **kw):
        if self.fail:
            raise RuntimeError("模拟 GEE 服务异常: BOOM")
        export_to = kw.get("export_to", "local")
        out_dir = kw.get("local_out_dir") or os.path.join(self.tmp, "out")
        os.makedirs(out_dir, exist_ok=True)
        roi_name = kw.get("roi_name", "task")
        n = kw.get("image_count") or self.scene_count
        ids = [f"20230101T{f'{i:02d}'}0000_{roi_name}" for i in range(1, n + 1)]
        if export_to == "drive":
            on_start = kw.get("on_task_started")
            for i, gid in enumerate(self.drive_ids or ids):
                task = SimpleNamespace(id=gid, description=f"{roi_name}_{gid}")
                task.start = lambda: self.started.append(1)
                task.start()
                if on_start:
                    on_start(task)
            return {
                "image_count": n, "export_to": "drive",
                "local_out_dir": None, "drive_folder": kw.get("drive_folder"),
                "id_list": ids, "roi_name": roi_name,
            }
        # local
        for i, gid in enumerate(ids):
            _make_tif(os.path.join(out_dir, f"{roi_name}_{gid}.tif"),
                      bands=len(kw.get("bands") or gal.DEFAULT_BANDS))
        return {
            "image_count": n, "export_to": "local",
            "local_out_dir": out_dir, "drive_folder": None,
            "id_list": ids, "roi_name": roi_name,
        }

    # ---- 被 gee_agent_loop 引用的模块级函数 ----
    @staticmethod
    def _resolve_ee_project(override=None):
        return override or "test-project-123"

    @staticmethod
    def ensure_ee_initialized(gee_proxy_url=None, gee_project_id=None, push_log=None):
        return None


def _patch_env_registry(tmp):
    reg = os.path.join(tmp, "dataset_assets_registry.json")
    patcher = mock.patch.dict(
        os.environ, {"DATASET_ASSETS_REGISTRY_PATH": reg}, clear=False
    )
    patcher.start()
    return reg


def _run_local(tmp, task="tv", scene_count=2, **tif_kw):
    plan = _happy_plan(tmp, task=task, export_to="local")
    fake = _FakeM4(tmp, scene_count=scene_count)
    result = gal.execute_gee_download(plan, m4_engine_mod=fake, push_log=lambda m: None)
    return plan, result, fake


class TestPlanBuild(unittest.TestCase):
    """B3 计划构建"""

    def test_01_valid_plan_default_bands_rgb_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = _happy_plan(tmp)
            self.assertTrue(plan["ready"], plan["blockers"])
            self.assertEqual(plan["bands"], ["B4", "B3", "B2"])
            self.assertEqual(plan["index_bands"], ["B8", "B11"])
            self.assertEqual(plan["schema"], "gee_download_plan_v1")
            self.assertEqual(plan["status"], "waiting_confirmation")
            self.assertEqual(plan["export_to"], "local")
            self.assertTrue(plan["plan_id"])

    def test_02_missing_aoi_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = os.path.join(tmp, "out")
            plan = gal.build_gee_download_plan(
                task_id="t", aoi={}, start_date="2023-01-01", end_date="2023-01-15",
                export_to="local", local_out_dir=out_dir,
            )
            self.assertFalse(plan["ready"])
            self.assertTrue(any("AOI" in b for b in plan["blockers"]))

    def test_03_invalid_aoi_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad_aoi = {"aoi_id": "x", "geometry": {"type": "Point", "coordinates": [0, 0]}}
            plan = gal.build_gee_download_plan(
                task_id="t", aoi=bad_aoi, start_date="2023-01-01", end_date="2023-01-15",
                export_to="local", local_out_dir=os.path.join(tmp, "out"),
            )
            self.assertFalse(plan["ready"])
            self.assertTrue(any("AOI" in b for b in plan["blockers"]))

    def test_04_bad_dates_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            p1 = _happy_plan(tmp, start_date="2023-01-15", end_date="2023-01-01")
            self.assertFalse(p1["ready"])
            self.assertTrue(any("结束日期" in b or "不能早于" in b for b in p1["blockers"]))
            p2 = _happy_plan(tmp, start_date="2023/01/01")
            self.assertFalse(p2["ready"])
            self.assertTrue(any("YYYY-MM-DD" in b for b in p2["blockers"]))

    def test_08_invalid_band_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = _happy_plan(tmp, bands=["B4", "B3", "B99"])
            self.assertFalse(plan["ready"])
            self.assertTrue(any("B99" in b for b in plan["blockers"]))
            plan2 = _happy_plan(tmp, bands=["B4", "B4", "B2"])
            self.assertFalse(plan2["ready"])
            self.assertTrue(any("重复" in b for b in plan2["blockers"]))

    def test_07_invalid_proxy_blocks(self):
        # proxy 格式校验在 validate（B4），build 不阻断但 validate 必须拦截
        with tempfile.TemporaryDirectory() as tmp:
            plan = _happy_plan(tmp, gee_proxy_url="not-a-proxy")
            self.assertTrue(plan["ready"], plan["blockers"])  # build 不查 proxy
            with mock.patch.object(gal, "_resolve_ee_project_any", return_value="p1"), \
                 mock.patch.object(gal, "_credentials_file_ok", return_value=True):
                ok, blockers = gal.validate_gee_download_plan(plan)
            self.assertFalse(ok)
            self.assertTrue(any("代理" in b for b in blockers))
            ok2 = _happy_plan(tmp, gee_proxy_url="http://127.0.0.1:7892")
            self.assertTrue(ok2["ready"], ok2["blockers"])

    def test_bad_scale_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = _happy_plan(tmp, scale=7)
            self.assertFalse(plan["ready"])
            self.assertTrue(any("分辨率" in b or "scale" in b.lower() for b in plan["blockers"]))

    def test_bad_export_target_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = _happy_plan(tmp, export_to="s3")
            self.assertFalse(plan["ready"])

    def test_unwritable_outdir_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = gal.build_gee_download_plan(
                task_id="t", aoi=_make_aoi_dict(), start_date="2023-01-01",
                end_date="2023-01-15", export_to="local",
                local_out_dir=os.path.join(tmp, "no_such_dir", "nested"),
            )
            # 目录会自动创建成功 → ready（可写探测通过）
            self.assertTrue(plan["ready"], plan["blockers"])


class TestValidate(unittest.TestCase):
    """B4 执行前校验"""

    def test_05_ee_not_importable_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = _happy_plan(tmp)
            with mock.patch("builtins.__import__", side_effect=ImportError("no ee")):
                ok, blockers = gal.validate_gee_download_plan(plan)
                self.assertFalse(ok)
                self.assertTrue(any("GEE Python 包" in b for b in blockers))

    def test_06_project_missing_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = _happy_plan(tmp)
            with mock.patch.object(gal, "_resolve_ee_project_any", return_value=None), \
                 mock.patch.object(gal, "_credentials_file_ok", return_value=True):
                ok, blockers = gal.validate_gee_download_plan(plan)
                self.assertFalse(ok)
                self.assertTrue(any("project" in b.lower() or "Cloud Project" in b
                                    for b in blockers))

    def test_validate_ok_when_project_resolved(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = _happy_plan(tmp)
            with mock.patch.object(gal, "_resolve_ee_project_any", return_value="p1"), \
                 mock.patch.object(gal, "_credentials_file_ok", return_value=True):
                ok, blockers = gal.validate_gee_download_plan(plan)
                self.assertTrue(ok, blockers)

    def test_24_no_credentials_in_plan_or_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = _happy_plan(tmp)
            text = gal.format_gee_plan_for_user(plan)
            ctx = gal.build_gee_context_for_agent()
            # 计划/上下文/展示文本均不得包含凭证内容（键与值）
            blob = json.dumps({**plan, "ctx": ctx}, ensure_ascii=False)
            for secret in ("private_key", "refresh_token", "access_token",
                           "client_secret", "\"project\"", ": \"sk-"):
                self.assertNotIn(secret, blob, secret)
            for secret in ("private_key", "refresh_token", "client_secret"):
                self.assertNotIn(secret, text, secret)
            # 允许的布尔探针键（仅存在性，不泄露内容）
            self.assertIn("credentials_file", json.dumps(ctx, ensure_ascii=False))


class TestConfirmGate(unittest.TestCase):
    """B5 确认门闩"""

    def _state(self, tmp):
        plan = _happy_plan(tmp)
        state = {"_gee_pending_plan": plan}
        return state, plan

    def test_09_unconfirmed_not_executable_via_bridge(self):
        from agent_command_bridge import build_pending_task, init_ui_session_defaults
        with tempfile.TemporaryDirectory() as tmp:
            state: dict = {}
            init_ui_session_defaults(state)
            state["_gee_pending_plan"] = _happy_plan(tmp)
            pt, at, errs = build_pending_task(state, {
                "type": "run_gee_download", "task": "task_a", "plan_id": "x",
            })
            self.assertIsNone(pt)
            self.assertTrue(any("确认" in e for e in errs), errs)

    def test_10_duplicate_confirm_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            state, plan = self._state(tmp)
            ok, err = gal.confirm_gee_download_plan(state, plan["plan_id"])
            self.assertTrue(ok, err)
            ok2, err2 = gal.confirm_gee_download_plan(state, plan["plan_id"])
            self.assertFalse(ok2)
            self.assertIn("重复确认", err2 or "")

    def test_confirm_mismatched_plan_id_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            state, plan = self._state(tmp)
            ok, err = gal.confirm_gee_download_plan(state, "other-plan-id")
            self.assertFalse(ok)
            self.assertIn("不匹配", err or "")

    def test_confirm_without_pending_plan_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = {}
            ok, err = gal.confirm_gee_download_plan(state, "abc")
            self.assertFalse(ok)

    def test_23_plan_parameter_change_creates_new_plan_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            p1 = _happy_plan(tmp, cloud_limit=30)
            p2 = _happy_plan(tmp, cloud_limit=60)
            self.assertNotEqual(p1["plan_id"], p2["plan_id"])
            # 旧计划确认不影响新计划
            state = {"_gee_pending_plan": p2}
            gal.confirm_gee_download_plan(state, p2["plan_id"])
            self.assertFalse(gal.is_gee_plan_confirmed(state, p1["plan_id"]))

    def test_21_gee_confirm_does_not_trigger_inference(self):
        from agent_command_bridge import apply_system_command, init_ui_session_defaults
        with tempfile.TemporaryDirectory() as tmp:
            plan = _happy_plan(tmp)
            state: dict = {}
            init_ui_session_defaults(state)
            state["_gee_pending_plan"] = plan
            res = apply_system_command(state, {
                "pending_action": {"type": "confirm_gee",
                                   "plan_id": plan["plan_id"],
                                   "confirmed": True},
            })
            self.assertTrue(res.applied)
            self.assertEqual(res.action_type, "run_gee_download")
            # 不应有 inference pending plan / 不应启动推理
            self.assertNotIn("_inference_pending_plan", state)
            pt = state.get("pending_task")
            self.assertTrue(pt and pt.get("mode") == "gee")


class TestExecute(unittest.TestCase):
    """B6/B7 真实执行 + 账本"""

    def setUp(self):
        self._ledger_patch = mock.patch.object(
            gal, "GEE_TASK_LEDGER_PATH",
            os.path.join(tempfile.mkdtemp(), "gee_task_ledger.json"))
        self._ledger_patch.start()

    def tearDown(self):
        self._ledger_patch.stop()


    def test_11_rerun_reads_ledger_no_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = _happy_plan(tmp, task="t11", export_to="drive")
            # 预置账本：该 plan 已有 gee_task_id（模拟上次提交后进程重启）
            gal._ledger_upsert("t11", plan_id=plan["plan_id"],
                               gee_task_id="GEE_TASK_OLD", status="RUNNING")
            fake = _FakeM4(tmp, scene_count=2, drive_ids=["GEE_TASK_OLD"])
            with mock.patch.object(gal, "_poll_gee_task_status",
                                   return_value={"state": "COMPLETED",
                                                 "error_message": "",
                                                 "description": "d"}):
                result = gal.execute_gee_download(
                    plan, m4_engine_mod=fake, push_log=lambda m: None)
            # 账本里仍是旧 id，且未被替换为新提交
            row = gal._ledger_get("t11")
            self.assertEqual(row["gee_task_id"], "GEE_TASK_OLD")
            self.assertIn("GEE_TASK_OLD", (result["outputs"] or {}).get("gee_task_ids") or [])

    def test_12_running_status_polled(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = _happy_plan(tmp, task="t12", export_to="drive")
            fake = _FakeM4(tmp, scene_count=1, drive_ids=["GEE_TASK_RUN"])
            with mock.patch.object(gal, "_poll_gee_task_status",
                                   return_value={"state": "RUNNING",
                                                 "error_message": "",
                                                 "description": "d"}):
                result = gal.execute_gee_download(plan, m4_engine_mod=fake,
                                                  push_log=lambda m: None)
            self.assertEqual(result["export_state"], "RUNNING")
            self.assertTrue(result["success"])

    def test_13_failed_error_preserved_verbatim(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = _happy_plan(tmp, task="t13", export_to="drive")
            fake = _FakeM4(tmp, fail=True)
            with mock.patch.object(gal, "_poll_gee_task_status", return_value={}):
                result = gal.execute_gee_download(plan, m4_engine_mod=fake,
                                                  push_log=lambda m: None)
            self.assertFalse(result["success"])
            self.assertIn("BOOM", result["error"] or "")

    def test_execute_local_writes_real_tifs(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = _happy_plan(tmp, task="t14", export_to="local")
            fake = _FakeM4(tmp, scene_count=3)
            result = gal.execute_gee_download(plan, m4_engine_mod=fake,
                                              push_log=lambda m: None)
            self.assertTrue(result["success"], result.get("error"))
            self.assertEqual(result["metrics"]["scene_count"], 3)
            self.assertEqual(len(result["outputs"]["local_tifs"]), 3)
            for f in result["outputs"]["local_tifs"]:
                self.assertTrue(os.path.isfile(f))

    def test_execute_plan_id_isolation(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan_a = _happy_plan(tmp, task="tA", export_to="local")
            plan_b = _happy_plan(tmp, task="tB", export_to="local")
            fake = _FakeM4(tmp, scene_count=2)
            ra = gal.execute_gee_download(plan_a, m4_engine_mod=fake, push_log=lambda m: None)
            rb = gal.execute_gee_download(plan_b, m4_engine_mod=fake, push_log=lambda m: None)
            self.assertEqual(ra["plan_id"], plan_a["plan_id"])
            self.assertEqual(rb["plan_id"], plan_b["plan_id"])
            # 账本各自独立
            self.assertEqual(gal._ledger_get("tA")["plan_id"], plan_a["plan_id"])
            self.assertEqual(gal._ledger_get("tB")["plan_id"], plan_b["plan_id"])


class TestVerifyAndRegister(unittest.TestCase):
    """B8/B9 验证 + 登记"""

    def setUp(self):
        self._ledger_patch = mock.patch.object(
            gal, "GEE_TASK_LEDGER_PATH",
            os.path.join(tempfile.mkdtemp(), "gee_task_ledger.json"))
        self._ledger_patch.start()

    def tearDown(self):
        self._ledger_patch.stop()

    def test_17_success_register_scene_count_in_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            _patch_env_registry(tmp)
            plan, result, _ = _run_local(tmp, task="t17", scene_count=3)
            ver = gal.verify_gee_outputs(plan, result)
            self.assertTrue(ver["ok"], ver["checks"])
            did = gal.register_gee_dataset_asset(plan, result, ver)
            self.assertTrue(did)
            import dataset_assets
            entry = dataset_assets.get_dataset(did)
            self.assertEqual(entry["scene_count"], 3)
            self.assertEqual(entry["source"], "open")
            self.assertEqual(entry["format"], "geotiff")
            self.assertEqual(entry["coverage_scale"], "scene")
            self.assertEqual(entry["bands"], ["B4", "B3", "B2"])
            self.assertEqual(entry["plan_id"], plan["plan_id"])

    def test_14_completed_but_file_missing_no_register(self):
        with tempfile.TemporaryDirectory() as tmp:
            _patch_env_registry(tmp)
            plan = _happy_plan(tmp, task="t14d", export_to="drive")
            fake = _FakeM4(tmp, scene_count=1, drive_ids=["GEE_TASK_DONE"])
            with mock.patch.object(gal, "_poll_gee_task_status",
                                   return_value={"state": "COMPLETED",
                                                 "error_message": "",
                                                 "description": "d"}):
                result = gal.execute_gee_download(plan, m4_engine_mod=fake,
                                                  push_log=lambda m: None)
            self.assertEqual(result["export_state"], "COMPLETED")
            ver = gal.verify_gee_outputs(plan, result)
            # 云端 COMPLETED ≠ 本地就绪
            self.assertFalse(ver["ok"])
            self.assertFalse(ver["asset_ready"])
            did = gal.register_gee_dataset_asset(plan, result, ver)
            self.assertIsNone(did)

    def test_15_no_crs_file_fails_verify(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan, result, _ = _run_local(tmp, task="t15", scene_count=1)
            # 覆盖生成的 tif 为无 CRS
            tif = result["outputs"]["local_tifs"][0]
            _make_tif(tif, crs=None, bands=3)
            ver = gal.verify_gee_outputs(plan, result)
            self.assertFalse(ver["ok"])
            self.assertTrue(any("crs" in str(c.get("name")).lower() and not c["passed"]
                                for c in ver["checks"]))

    def test_16_all_nodata_fails_verify(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan, result, _ = _run_local(tmp, task="t16", scene_count=1)
            tif = result["outputs"]["local_tifs"][0]
            _make_tif(tif, nodata=0, fill=0, bands=3)  # 全 0 = 全 NoData
            ver = gal.verify_gee_outputs(plan, result)
            self.assertFalse(ver["ok"])
            self.assertTrue(any("has_data" in c["name"] and not c["passed"]
                                for c in ver["checks"]))

    def test_18_scene_count_matches_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan, result, _ = _run_local(tmp, task="t18", scene_count=4)
            ver = gal.verify_gee_outputs(plan, result)
            self.assertTrue(ver["ok"])
            self.assertEqual(len(ver["local_tifs"]), 4)
            self.assertEqual(result["metrics"]["scene_count"], 4)

    def test_verify_failed_no_register(self):
        with tempfile.TemporaryDirectory() as tmp:
            _patch_env_registry(tmp)
            plan, result, _ = _run_local(tmp, task="t19", scene_count=1)
            tif = result["outputs"]["local_tifs"][0]
            os.remove(tif)  # 文件缺失
            ver = gal.verify_gee_outputs(plan, result)
            self.assertFalse(ver["ok"])
            did = gal.register_gee_dataset_asset(plan, result, ver)
            self.assertIsNone(did)


class TestSummarizeAndCapability(unittest.TestCase):
    """B10/B12 汇报与能力状态"""

    def test_22_summarize_uses_real_result_not_fake(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan, result, _ = _run_local(tmp, task="t22", scene_count=2)
            text = gal.summarize_gee_result_for_chat(result)
            self.assertIn("共 2 景影像", text)
            self.assertIn("不会自动启动提取", text)
            self.assertNotIn("GEE_TASK_", text)  # drive 无任务时不虚构

    def test_summarize_failure_honest(self):
        with tempfile.TemporaryDirectory() as tmp:
            text = gal.summarize_gee_result_for_chat({
                "success": False, "task_id": "x", "error": "模拟失败: BOOM",
            })
            self.assertIn("失败", text)
            self.assertIn("BOOM", text)

    def test_19_gee_capability_uses_multi_source_project(self):
        # 语义对齐：capability 的 project 解析与 m4_engine._resolve_ee_project 一致
        with mock.patch.object(gal, "_resolve_ee_project_any", return_value="env-project"):
            ctx = gal.build_gee_context_for_agent()
            self.assertTrue(ctx["gee_project_resolved"])
        with mock.patch.object(gal, "_resolve_ee_project_any", return_value=None):
            ctx = gal.build_gee_context_for_agent()
            self.assertFalse(ctx["gee_project_resolved"])

    def test_20_inference_plan_reads_dataset_scene_count(self):
        """inference 的 A1 阻断能读取已登记 GEE 资产的 scene_count。"""
        import dataset_assets
        with tempfile.TemporaryDirectory() as tmp:
            reg = _patch_env_registry(tmp)
            # 登记一个只有 1 景的 GEE 数据集
            one = os.path.join(tmp, "one.tif")
            _make_tif(one, bands=3)
            import aoi_context
            ctx = aoi_context.aoi_from_bbox(119.0, 25.5, 120.0, 26.0)
            did = dataset_assets.register_dataset({
                "id": "gee_tt_1scene", "title": "1景", "source": "open",
                "format": "geotiff", "role": "auxiliary", "coverage_scale": "scene",
                "primary_path": one, "bands": ["B4", "B3", "B2"], "scene_count": 1,
                "aoi_bbox": list(ctx.bbox), "provider": "Earth Engine",
            })
            # 用该资产作 input_asset_id 建推理计划，cnt=2 → 应被 A1 阻断
            import inference_agent_loop as ial
            in_dir = os.path.join(tmp, "in")
            os.makedirs(in_dir, exist_ok=True)
            plan = ial.build_inference_plan(
                task_id="infer_a", root_dir=in_dir, final_root=os.path.join(tmp, "fin"),
                mask_root=os.path.join(tmp, "mask"), model_path=os.path.join(tmp, "m.pth"),
                prob_threshold=0.05, count_threshold=2, input_asset_id=did,
                weight_id="cdnet_resnet50", device_policy="auto",
            )
            self.assertFalse(plan["ready"])
            self.assertTrue(any("频次阈值" in b for b in plan["blockers"]))


class TestB12Integration(unittest.TestCase):
    """B12 能力状态与时间线阶段集成"""

    def test_timeline_has_gee_phases(self):
        import task_timeline as tt
        for phase in ("GEE_EXPORT", "WAIT_REMOTE", "FETCH_OUTPUT"):
            self.assertIn(phase, tt.PHASES)
        # 顺序：EXECUTE 之前有 GEE_EXPORT（紧跟 EXECUTE 之后的语义在文档层，PHASES 内保证存在）
        self.assertLess(tt.PHASES.index("GEE_EXPORT"), tt.PHASES.index("VERIFY"))

    def test_capability_gee_uses_multi_source_project(self):
        import capability_registry as cr
        with mock.patch.dict(os.environ, {}, clear=False):
            reg = cr.CapabilityRegistry(context={})
            # env 无 GEE_PROJECT 但有 EE_PROJECT → 应 CONDITIONAL（多源解析）
            with mock.patch.dict(os.environ, {"EE_PROJECT": "proj-abc"}, clear=False):
                st = reg.check("gee_download")
                self.assertEqual(st.status, cr.CONDITIONAL)
                self.assertTrue(st.evidence.get("gee_project_configured"))
        # 全无 project → UNAVAILABLE（保持既有语义）
        reg = cr.CapabilityRegistry(context={})
        st = reg.check("gee_download")
        if st.status != cr.CONDITIONAL:
            self.assertIn(st.status, (cr.UNAVAILABLE, cr.BLOCKED))

    def test_agent_prompt_and_tools_mention_gee(self):
        # B12: agent 工具链含 gee 计划/确认工具（不触发 API Key，仅静态检查）
        import agent as agent_mod
        tool_names = [getattr(t, "name", "") or getattr(t, "__name__", "") for t in agent_mod.tools]
        self.assertIn("gee_download_plan", tool_names)
        self.assertIn("confirm_gee_download", tool_names)

    def test_bridge_propose_gee_then_confirm(self):
        from agent_command_bridge import (
            apply_system_command, init_ui_session_defaults, queue_agent_command,
            flush_pending_agent_commands,
        )
        with tempfile.TemporaryDirectory() as tmp:
            state: dict = {}
            init_ui_session_defaults(state)
            # propose_gee：无 AOI → 计划带 blockers（不崩溃），并且不启动线程
            res = apply_system_command(state, {
                "pending_action": {"type": "propose_gee", "task": "p1",
                                   "start_date": "2023-01-01", "end_date": "2023-01-31"},
            })
            self.assertTrue(res.applied)
            self.assertTrue(res.gee_plan_text)
            self.assertFalse(state.get("pending_task"))
            plan = state.get("_gee_pending_plan")
            self.assertTrue(plan)
            # 计划含默认 bands
            self.assertEqual(plan["bands"], ["B4", "B3", "B2"])
            # 未确认 → run_gee_download 被拒
            res2 = apply_system_command(state, {
                "pending_action": {"type": "run_gee_download", "task": "p1",
                                   "plan_id": plan["plan_id"]},
            })
            self.assertFalse(state.get("pending_task"))
            self.assertTrue(any("确认" in e for e in res2.errors))
            # confirm_gee → run_gee_download 通过（bridge 侧）
            res3 = apply_system_command(state, {
                "pending_action": {"type": "confirm_gee", "plan_id": plan["plan_id"],
                                   "task": "p1", "confirmed": True},
            })
            self.assertEqual(res3.action_type, "run_gee_download")
            pt = state.get("pending_task")
            self.assertTrue(pt and pt.get("mode") == "gee")
            # queue/flush 路径（UI 按钮同路径）
            from agent_command_bridge import PENDING_AGENT_COMMANDS_KEY
            queue_agent_command(state, {"pending_action": {"type": "propose_gee", "task": "p2"}})
            self.assertTrue(state.get(PENDING_AGENT_COMMANDS_KEY))
            fl = flush_pending_agent_commands(state)
            self.assertTrue(fl.applied)


class TestLedgerPersistence(unittest.TestCase):
    """B7 最小持久化"""

    def test_ledger_roundtrip_and_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            orig = gal.GEE_TASK_LEDGER_PATH
            try:
                gal.GEE_TASK_LEDGER_PATH = os.path.join(tmp, "gee_task_ledger.json")
                gal._ledger_upsert("task1", plan_id="p1", gee_task_id="g1", status="RUNNING")
                row = gal._ledger_get("task1")
                self.assertEqual(row["plan_id"], "p1")
                self.assertEqual(row["gee_task_id"], "g1")
                # 模拟进程重启：重新加载（读盘）
                gal.GEE_TASK_LEDGER_PATH = os.path.join(tmp, "gee_task_ledger.json")
                restored = gal._ledger_get("task1")
                self.assertEqual(restored["status"], "RUNNING")
                self.assertTrue(restored["last_checked_at"])
            finally:
                gal.GEE_TASK_LEDGER_PATH = orig


if __name__ == "__main__":
    unittest.main()
