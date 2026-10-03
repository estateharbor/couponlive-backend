"""Feed syncs record their failures for /health — with credentials redacted."""
from __future__ import annotations

import pytest

import scheduler.tasks as tasks


def test_redacts_query_string_key_and_json_secret():
    url = ("400 Client Error for url: https://feed.linkmydeals.com/getOffers/"
           "?API_KEY=abc123&format=json")
    assert "abc123" not in tasks._redact(url)
    assert "format=json" in tasks._redact(url)
    body = 'token HTTP 401: {"client_secret": "zzz", "error": "invalid_client"}'
    out = tasks._redact(body)
    assert "zzz" not in out and "invalid_client" in out
    assert tasks._redact("monkey=ok") == "monkey=ok"


@pytest.fixture
def recorded(monkeypatch):
    calls: list[tuple[str, str | None]] = []
    monkeypatch.setattr(tasks, "_record_feed_outcome", lambda n, e: calls.append((n, e)))
    return calls


def test_exception_is_recorded_and_reraised(recorded):
    @tasks._tracked_feed("Feed")
    def boom():
        raise RuntimeError("HTTP 500")

    with pytest.raises(RuntimeError):
        boom()
    assert recorded == [("Feed", "RuntimeError: HTTP 500")]


def test_error_result_recorded_and_clean_run_clears(recorded):
    @tasks._tracked_feed("Feed")
    def empty():
        return {"source": "Feed", "error": "0 coupons"}

    @tasks._tracked_feed("Feed")
    def ok():
        return {"source": "Feed", "created": 1}

    empty()
    ok()
    assert recorded == [("Feed", "0 coupons"), ("Feed", None)]


def test_record_outcome_writes_and_clears_source(db_session, monkeypatch):
    from models.enums import IngestionMethod
    from models.models import Source

    db_session.add(Source(name="Feed", ingestion_method=IngestionMethod.affiliate_api))
    db_session.commit()
    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: (lambda: db_session))
    monkeypatch.setattr(db_session, "close", lambda: None)

    tasks._record_feed_outcome("Feed", "HTTPError: url ?API_KEY=secret123")
    src = db_session.query(Source).filter_by(name="Feed").one()
    assert src.last_error and "secret123" not in src.last_error and src.last_error_at

    tasks._record_feed_outcome("Feed", None)
    assert db_session.query(Source).filter_by(name="Feed").one().last_error is None
