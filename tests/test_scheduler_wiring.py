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
