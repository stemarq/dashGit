"""Cliente assincrono da API REST v4 do GitLab.

Cobre paginacao por header, backoff em 429/5xx e limite de concorrencia
(gitlab.com corta em ~2000 req/min por token).
"""

import asyncio
import logging
from typing import Any, AsyncIterator
from urllib.parse import quote

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)

RETRY_STATUS = {429, 500, 502, 503, 504}
MAX_RETRIES = 4


class GitLabError(RuntimeError):
    pass


def encode_project(project: str | int) -> str:
    """`grupo/sub/projeto` -> `grupo%2Fsub%2Fprojeto`; IDs passam direto."""
    text = str(project)
    return text if text.isdigit() else quote(text, safe="")


