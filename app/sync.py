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


async def sync_project(project: str | int, full: bool = False) -> dict[str, Any]:
    """Busca projeto, boards, issues e eventos de label. Retorna um resumo."""
    async with GitLabClient() as gl:
        meta = await gl.project(project)
        project_id = meta["id"]

        with session() as conn:
            row = conn.execute(
                "SELECT last_synced_at, commits_synced_at FROM sync_state"
                " WHERE project_id = ?", (project_id,)
            ).fetchone()
        updated_after = None if full else (row["last_synced_at"] if row else None)
        commits_since = None if full else (row["commits_synced_at"] if row else None)

        started = _now()
        boards = await _sync_boards(gl, project_ref=project, project_id=project_id)
        # milestones sao poucas e mudam de data no meio da sprint: sempre completo
        milestones = await gl.milestones(project)
        issues = await gl.issues(project, updated_after=updated_after)
        log.info("projeto %s: %d issues para atualizar", meta["path_with_namespace"], len(issues))

        events = await _fetch_events(gl, project, issues)
        commits = await _sync_commits(gl, project, project_id, commits_since)

    _persist(meta, issues, events, milestones, started)
    return {
        "project_id": project_id,
        "project": meta["path_with_namespace"],
        "issues_synced": len(issues),
        "label_events": sum(len(v) for v in events.values()),
        "board_lists": boards,
        "milestones": len(milestones),
        "commits": commits,
        "incremental": updated_after is not None,
        "synced_at": started,
    }


