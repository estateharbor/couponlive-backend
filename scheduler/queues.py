"""Inspect / clean the Celery queues in Redis.

    docker compose exec -T worker python -m scheduler.queues            # show backlog
    docker compose exec -T worker python -m scheduler.queues --purge-old-validations

Before validations got their own queue they shared `celery` with the feed syncs,
and the 30-min sweep re-queued the same coupons every run — leaving a large
backlog of duplicate validate_coupon jobs ahead of the syncs. The purge removes
ONLY those validate_coupon messages from the `celery` queue (feed syncs and
other jobs stay); the sweep re-queues each due coupon once, on its own queue.
"""
from __future__ import annotations

import json
import sys
from collections import Counter

from scheduler.tasks import _redis


def _queue_lists(r) -> list[bytes]:
    # Celery's Redis transport keeps one list per priority step:
    # "celery", "celery\x06\x163", "celery\x06\x166", "celery\x06\x169".
    keys = [k for k in r.scan_iter(match=b"*") if r.type(k) == b"list"]
    return sorted(k for k in keys if k.split(b"\x06")[0] in (b"celery", b"validation"))


def _task_name(raw: bytes) -> str:
    try:
        return json.loads(raw).get("headers", {}).get("task") or "?"
    except Exception:  # noqa: BLE001
        return "?"


def show(r) -> None:
    for key in _queue_lists(r):
        msgs = r.lrange(key, 0, -1)
        counts = Counter(_task_name(m) for m in msgs)
        print(f"{key.decode(errors='replace')!r}: {len(msgs)} job(s) {dict(counts)}")
    if not _queue_lists(r):
        print("queues empty")


def purge_old_validations(r) -> int:
    removed = 0
    for key in _queue_lists(r):
        if key.split(b"\x06")[0] != b"celery":
            continue
        for raw in r.lrange(key, 0, -1):
            if _task_name(raw) == "validate_coupon":
                removed += r.lrem(key, 1, raw)
    return removed


if __name__ == "__main__":
    r = _redis()
    print("BEFORE:")
    show(r)
    if "--purge-old-validations" in sys.argv[1:]:
        print(f"removed {purge_old_validations(r)} queued validate_coupon job(s) from 'celery'")
        print("AFTER:")
        show(r)
