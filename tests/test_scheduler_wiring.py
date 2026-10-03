"""Regression guard: importing the Celery *app* must, on its own, register the
tasks and load the beat schedule.

This is the bug that silently stopped every scheduled sync in production for ~13
days: `celery -A scheduler.celery_app worker --beat` loaded the app but not
scheduler/tasks.py, so beat ran with an empty schedule and no tasks registered.
We assert the app self-loads its tasks so it can't recur.
"""
from __future__ import annotations

import importlib


def test_app_import_registers_tasks_and_schedule():
    # Import ONLY the app module (as the celery CLI does) — do NOT import tasks.
    celery_app_mod = importlib.import_module("scheduler.celery_app")
    app = celery_app_mod.celery_app

    # Beat schedule must be populated (this is what beat reads on startup).
    schedule = app.conf.beat_schedule or {}
    for entry in ("sync-linkmydeals", "sync-cuelinks", "enqueue-revalidations"):
        assert entry in schedule, f"beat schedule missing {entry!r}"

    # The task functions must be registered with the app.
    for name in ("sync_linkmydeals", "sync_cuelinks", "sync_feedico", "validate_coupon"):
        assert name in app.tasks, f"task {name!r} not registered on the app"


def test_sync_schedule_targets_correct_tasks():
    from scheduler.celery_app import celery_app

    sched = celery_app.conf.beat_schedule
    assert sched["sync-linkmydeals"]["task"] == "sync_linkmydeals"
    assert sched["sync-cuelinks"]["task"] == "sync_cuelinks"


def test_validations_have_their_own_queue_and_worker_consumes_both():
    """Validation backlog must not starve feed syncs (LinkMyDeals went 12h+
    without a run while validations outranked it on one shared queue)."""
    from scheduler.celery_app import celery_app

    assert celery_app.amqp.router.route({}, "validate_coupon")["queue"].name == "validation"
    assert celery_app.amqp.router.route({}, "sync_linkmydeals")["queue"].name == "celery"
    assert {q.name for q in celery_app.conf.task_queues} >= {"celery", "validation"}
    vt = celery_app.tasks["validate_coupon"]
    assert vt.time_limit and vt.soft_time_limit  # a hung browser can't hold a slot


def test_enqueue_validation_dedupes_pending_coupon(monkeypatch):
    import scheduler.tasks as tasks

    class FakeRedis:
        def __init__(self):
            self.keys = {}

        def set(self, key, val, nx=False, ex=None):
            if nx and key in self.keys:
                return False
            self.keys[key] = val
            return True

        def delete(self, key):
            self.keys.pop(key, None)

    fake = FakeRedis()
    sent = []
    monkeypatch.setattr(tasks, "_redis", lambda: fake)
    monkeypatch.setattr(tasks.validate_coupon, "apply_async",
                        lambda args, priority: sent.append((args[0], priority)))

    assert tasks.enqueue_validation(7, priority=1) is True
    assert tasks.enqueue_validation(7, priority=1) is False     # already pending
    tasks._clear_pending(7)                                      # job finished
    assert tasks.enqueue_validation(7, priority=1) is True
    assert sent == [(7, 1), (7, 1)]


def test_purge_old_validations_keeps_other_jobs():
    import json

    from scheduler.queues import purge_old_validations

    def msg(task, n):
        return json.dumps({"headers": {"task": task, "id": str(n)}}).encode()

    class FakeRedis:
        def __init__(self):
            self.lists = {b"celery": [msg("validate_coupon", 1), msg("sync_linkmydeals", 2),
                                      msg("validate_coupon", 3)],
                          b"validation": [msg("validate_coupon", 4)]}

        def scan_iter(self, match=None):
            return iter(list(self.lists))

        def type(self, k):
            return b"list"

        def lrange(self, k, a, b):
            return list(self.lists[k])

        def lrem(self, k, n, v):
            self.lists[k].remove(v)
            return 1

    r = FakeRedis()
    assert purge_old_validations(r) == 2
    assert [json.loads(m)["headers"]["task"] for m in r.lists[b"celery"]] == ["sync_linkmydeals"]
    assert len(r.lists[b"validation"]) == 1          # new queue untouched


def test_feed_syncs_are_clock_pinned_not_countdowns():
    """A countdown (timedelta) restarts from zero on every worker restart, so
    frequent deploys meant the 30-min LinkMyDeals sync never came due."""
    from celery.schedules import crontab

    from scheduler.celery_app import celery_app
    from scheduler.tasks import _clock

    sched = celery_app.conf.beat_schedule
    for entry in ("sync-linkmydeals", "sync-cuelinks", "sync-involve-asia",
                  "sync-vcommission", "sync-admitad", "sync-feedico"):
        assert isinstance(sched[entry]["schedule"], crontab), entry

    # 30 min, offset 5 -> :05 and :35
    assert _clock(30, 5).minute == {5, 35}
    assert _clock(120, 40).hour == set(range(0, 24, 2)) and _clock(120, 40).minute == {40}
