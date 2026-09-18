"""Celery application (tasks + beat schedule land in Phases 2/4/6).

Phase 1: just the app object wired to Redis, so the worker service has an
entrypoint to deploy. Tasks are registered here as they are built.

Tradeoff note (Celery vs APScheduler): the prompt allows APScheduler for the
MVP. Recommendation: Celery + Redis, because (a) scraping and checkout-flow
validation are I/O-bound jobs we want to run concurrently across worker
processes with isolation per job, (b) we need priority queues (new codes and
top-merchant re-validation jump ahead), and (c) Railway/Render model this
cleanly as a separate worker service. APScheduler is in-process and would
couple scheduling to the API service — fine for a toy, awkward for this.
"""
from __future__ import annotations

from celery import Celery

from core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "couponlive",
    broker=settings.redis_url,
    backend=settings.redis_url,
    # Import the tasks module on startup. Without this, `celery -A
    # scheduler.celery_app worker --beat` loads only the app object — NOT
    # scheduler/tasks.py — so NO tasks register and beat starts with an EMPTY
    # schedule. That silently stopped every scheduled sync/validation; the only
    # runs were manual `python -c "from scheduler.tasks import ..."` calls.
    include=["scheduler.tasks"],
)

celery_app.conf.update(
    task_acks_late=True,
    worker_prefetch_multiplier=1,   # fair dispatch for long-running validation jobs
    task_default_priority=5,
    task_track_started=True,
    timezone="UTC",
)

# The beat_schedule + @task registrations live in scheduler/tasks.py. Import it
# here for its side effects so the schedule is loaded whenever this app module is
# loaded (worker, beat, or a plain import) — not only when tasks is imported by
# name. Placed at the bottom so `celery_app` is already defined (no import cycle).
from scheduler import tasks as _tasks  # noqa: E402,F401
