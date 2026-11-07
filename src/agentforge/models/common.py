"""Shared model utilities and cross-cutting constants."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

# Reserved participant identifiers.
HUMAN_SENDER = "human"
SYSTEM_SENDER = "system"


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    """Create a short, unique, human-readable identifier."""
    return f"{prefix}_{uuid4().hex}"


def ensure_utc(value: datetime) -> datetime:
    """Normalize naive datetimes (e.g. loaded from storage) to UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
