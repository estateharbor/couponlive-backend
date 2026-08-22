"""Shared FastAPI dependencies."""
from __future__ import annotations

from collections.abc import Generator

from sqlalchemy.orm import Session

from models.base import get_sessionmaker


def get_db() -> Generator[Session, None, None]:
    session = get_sessionmaker()()
    try:
        yield session
    finally:
        session.close()
