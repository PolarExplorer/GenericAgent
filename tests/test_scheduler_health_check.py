import importlib.util
from pathlib import Path


def load_scheduler_module():
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("scheduler_under_test", root / "reflect" / "scheduler.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_health_check_reports_tasks_without_side_effects(tmp_path, monkeypatch):
    scheduler = load_scheduler_module()
    tasks = tmp_path / "sche_tasks"
    done = tasks / "done"
    tasks.mkdir()
    done.mkdir()
    log = tasks / "scheduler.log"
    log.write_text("before", encoding="utf-8")
    (tasks / "late.json").write_text(
        '{"enabled": true, "schedule": "09:00", "repeat": "daily", "max_delay_hours": 1, "prompt": "noop"}',
        encoding="utf-8",
    )
    (tasks / "future.json").write_text(
        '{"enabled": true, "schedule": "23:30", "repeat": "daily", "max_delay_hours": 6, "prompt": "noop"}',
        encoding="utf-8",
    )
    (tasks / "disabled.json").write_text(
        '{"enabled": false, "schedule": "09:00", "repeat": "daily", "max_delay_hours": 6, "prompt": "noop"}',
        encoding="utf-8",
    )

    monkeypatch.setattr(scheduler, "TASKS", str(tasks))
    monkeypatch.setattr(scheduler, "DONE", str(done))

    before_done = sorted(p.name for p in done.glob("*.md"))
    before_log = log.read_text(encoding="utf-8")
    rows = scheduler.health_check(now=scheduler.datetime(2026, 5, 25, 18, 0))
    after_done = sorted(p.name for p in done.glob("*.md"))
    after_log = log.read_text(encoding="utf-8")

    by_id = {row["id"]: row for row in rows}
    assert by_id["late"]["status"] == "OVERDUE"
    assert by_id["late"]["reason"] == "past_max_delay"
    assert by_id["late"]["max_delay_hours"] == 1
    assert by_id["future"]["status"] == "HEALTHY"
    assert by_id["future"]["reason"] == "not_due"
    assert by_id["disabled"]["status"] == "DISABLED"
    assert before_done == after_done
    assert before_log == after_log
