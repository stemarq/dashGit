"""Sincronizacao GitLab -> cache local.

Estrategia: incremental por `updated_after`. Uma issue so muda de estado
via evento, entao basta rebuscar as issues tocadas desde o ultimo sync e
substituir a linha do tempo de labels delas.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from app.db import session
from app.gitlab_client import GitLabClient

log = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_iso(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _user(payload: dict[str, Any] | None) -> tuple[int | None, str | None]:
    if not payload:
        return None, None
    return payload.get("id"), payload.get("name") or payload.get("username")


