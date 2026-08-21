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


def _resolve(project: str | None) -> int:
    settings = get_settings()
    candidate = project or (settings.project_list[0] if settings.project_list else None)
    project_id = metrics.resolve_project_id(candidate)
    if project_id is None:
        raise HTTPException(
            404,
            f"Projeto '{candidate or '(nenhum)'}' nao esta no cache. "
            "Rode POST /api/sync?project=grupo/projeto primeiro.",
        )
    return project_id


def _parse_since(since: str | None, days: int | None) -> datetime | None:
    if since:
        parsed = metrics.parse_ts(since)
        if parsed is None:
            raise HTTPException(400, f"Data invalida em 'since': {since}")
        return parsed
    if days:
        return datetime.now(timezone.utc) - timedelta(days=days)
    return None


@router.get("/health")
def health() -> dict[str, Any]:
    settings = get_settings()
    with session() as conn:
        projects = conn.execute("SELECT COUNT(*) AS n FROM projects").fetchone()["n"]
        issues = conn.execute("SELECT COUNT(*) AS n FROM issues").fetchone()["n"]
    return {
        "status": "ok",
        "gitlab_api": settings.gitlab_api_url,
        "token_configured": bool(settings.gitlab_token),
        "cached_projects": projects,
        "cached_issues": issues,
    }


@router.post("/sync")
async def sync(
    project: str | None = Query(None, description="grupo/projeto ou ID numerico"),
    full: bool = Query(False, description="Ignora o sync incremental e rebusca tudo"),
) -> dict[str, Any]:
    settings = get_settings()
    targets = [project] if project else settings.project_list
    if not targets:
        raise HTTPException(400, "Informe ?project= ou defina DEFAULT_PROJECTS no .env")
    results = []
    for target in targets:
        try:
            results.append(await sync_project(target, full=full))
        except GitLabError as exc:
            raise HTTPException(502, str(exc)) from exc
    return {"synced": results}


@router.get("/projects")
def projects() -> list[dict[str, Any]]:
    with session() as conn:
        rows = conn.execute(
            "SELECT p.id, p.path, p.name, p.web_url, p.synced_at,"
            " (SELECT COUNT(*) FROM issues i WHERE i.project_id = p.id) AS issues"
            " FROM projects p ORDER BY p.path"
        ).fetchall()
    return [dict(r) for r in rows]


