import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "TF-agent"))


class Stop:
    def wait(self, seconds):
        return False


def test_cloud_running_then_complete_is_waiting_sync_not_failure():
    from gee_agent_loop import monitor_drive_export
    states = iter([{"state": "RUNNING"}, {"state": "COMPLETED"}])
    events = []
    result = {"success": True, "export_state": "READY",
              "outputs": {"gee_task_ids": ["fake-task"]}}
    outcome = monitor_drive_export(result, Stop(), events.append,
                                   poll_fn=lambda _: next(states), interval=0)
    assert outcome["status"] == "WAITING_SYNC"
    assert result["export_state"] == "COMPLETED"
    assert [e["status"] for e in events] == ["SUBMITTED", "RUNNING", "WAITING_SYNC"]


def test_cloud_failed_and_unknown_are_distinct():
    from gee_agent_loop import monitor_drive_export
    for remote, expected in [("FAILED", "FAILED"), ("CANCELLED", "CANCELLED"),
                              ("UNKNOWN", "UNKNOWN")]:
        result = {"success": True, "outputs": {"gee_task_ids": ["fake-task"]}}
        outcome = monitor_drive_export(result, Stop(), lambda _: None,
            poll_fn=lambda _: {"state": remote, "error_message": "test detail"}, interval=0)
        assert outcome["status"] == expected


def test_missing_task_id_is_unknown_not_success():
    from gee_agent_loop import monitor_drive_export
    outcome = monitor_drive_export({"outputs": {}}, Stop(), lambda _: None,
                                   poll_fn=lambda _: (_ for _ in ()).throw(AssertionError()))
    assert outcome["status"] == "UNKNOWN"


def test_drive_worker_does_not_verify_or_register_unsynced_files(monkeypatch):
    import ast
    import datetime
    import threading
    import gee_agent_loop as gal
    source = Path(__file__).resolve().parents[2] / "TF-agent" / "app.py"
    function = next(n for n in ast.parse(source.read_text()).body
                    if isinstance(n, ast.FunctionDef) and n.name == "_gee_worker_entry")
    namespace = {"datetime": datetime, "sanitize_external_text": str,
                 "_record_worker_exception": lambda *args: (_ for _ in ()).throw(AssertionError(args))}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), "exec"), namespace)
    monkeypatch.setattr(gal, "execute_gee_download", lambda *a, **k: {
        "success": True, "outputs": {"gee_task_ids": ["fake"], "local_tifs": []}})
    monkeypatch.setattr(gal, "_poll_gee_task_status", lambda _: {"state": "COMPLETED"})
    def unexpected(*args, **kwargs):
        raise AssertionError("unsynced files must not be verified or registered")
    monkeypatch.setattr(gal, "verify_gee_outputs", unexpected)
    monkeypatch.setattr(gal, "register_gee_dataset_asset", unexpected)
    shared = {"lock": threading.Lock()}
    namespace["_gee_worker_entry"]({"gee_plan": {"ready": True, "export_to": "drive"}},
                                    shared, threading.Event())
    assert shared["done"]
    assert shared["gee_cloud_outcome"]["status"] == "WAITING_SYNC"
    assert "dataset_id" not in shared
    assert shared["status"][0] != "error"
