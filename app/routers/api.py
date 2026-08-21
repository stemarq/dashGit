"""Rotas da API. Tudo que le metrica trabalha em cima do cache local;
o unico caminho que fala com o GitLab e o /sync.
"""

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse

from app import commits as commit_metrics
from app import metrics
from app import report as sprint_report_builder
from app.config import get_settings
from app.db import session
from app.gitlab_client import GitLabError
from app.sync import sync_project

router = APIRouter(prefix="/api", tags=["dashgit"])

MILESTONE_DESC = (
    "Titulo da milestone/sprint. Use '(sem sprint)' para as issues sem milestone."
)


