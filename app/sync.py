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


async def _sync_boards(gl: GitLabClient, project_ref: str | int, project_id: int) -> int:
    rows: list[tuple[Any, ...]] = []
    for board in await gl.boards(project_ref):
        lists = await gl.board_lists(project_ref, board["id"])
        for lst in lists:
            label = (lst.get("label") or {}).get("name")
            if not label:
                continue  # listas de assignee/milestone nao viram coluna de label
            rows.append(
                (project_id, board["id"], board.get("name"), lst["id"], lst.get("position"), label)
            )
    with session() as conn:
        conn.execute("DELETE FROM board_lists WHERE project_id = ?", (project_id,))
        conn.executemany(
            "INSERT INTO board_lists (project_id, board_id, board_name, list_id, position,"
            " label_name) VALUES (?,?,?,?,?,?)",
            rows,
        )
    return len(rows)


async def _sync_commits(
    gl: GitLabClient, project_ref: str | int, project_id: int, since: str | None
) -> int:
    """Commits de todos os branches.

    O `since` do GitLab e inclusivo e compara pela data do commit, que pode
    ser reescrita por rebase — entao o import volta um pouco no tempo e conta
    com o INSERT OR REPLACE para nao duplicar.
    """
    if since:
        overlap = parse_iso(since) - timedelta(days=1)
        since = overlap.isoformat()

    rows: list[tuple[Any, ...]] = []
    for c in await gl.commits(project_ref, since=since):
        stats = c.get("stats") or {}
        rows.append((
            project_id,
            c["id"],
            c.get("short_id"),
            c.get("title"),
            c.get("author_name"),
            (c.get("author_email") or "").lower(),
            c.get("committed_date") or c.get("created_at"),
            stats.get("additions") or 0,
            stats.get("deletions") or 0,
            1 if len(c.get("parent_ids") or []) > 1 else 0,
            c.get("web_url"),
        ))

    with session() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO commits (project_id, id, short_id, title, author_name,"
            " author_email, committed_at, additions, deletions, is_merge, web_url)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
    log.info("projeto %s: %d commits importados", project_id, len(rows))
    return len(rows)


async def _fetch_events(
    gl: GitLabClient, project: str | int, issues: list[dict[str, Any]]
) -> dict[int, list[dict[str, Any]]]:
    async def one(iid: int) -> tuple[int, list[dict[str, Any]]]:
        return iid, await gl.label_events(project, iid)

    results = await asyncio.gather(*(one(i["iid"]) for i in issues), return_exceptions=True)
    out: dict[int, list[dict[str, Any]]] = {}
    for res in results:
        if isinstance(res, BaseException):
            log.warning("falha ao buscar eventos de label: %s", res)
            continue
        iid, evts = res
        out[iid] = evts
    return out


def _persist(
    meta: dict[str, Any],
    issues: list[dict[str, Any]],
    events: dict[int, list[dict[str, Any]]],
    milestones: list[dict[str, Any]],
    synced_at: str,
) -> None:
    project_id = meta["id"]
    with session() as conn:
        conn.execute(
            "INSERT INTO projects (id, path, name, web_url, synced_at) VALUES (?,?,?,?,?)"
            " ON CONFLICT(id) DO UPDATE SET path=excluded.path, name=excluded.name,"
            " web_url=excluded.web_url, synced_at=excluded.synced_at",
            (
                project_id,
                meta["path_with_namespace"],
                meta.get("name"),
                meta.get("web_url"),
                synced_at,
            ),
        )

        for milestone in milestones:
            conn.execute(
                "INSERT INTO milestones (project_id, id, iid, title, state, start_date,"
                " due_date, web_url) VALUES (?,?,?,?,?,?,?,?)"
                " ON CONFLICT(project_id, id) DO UPDATE SET title=excluded.title,"
                " state=excluded.state, start_date=excluded.start_date,"
                " due_date=excluded.due_date, web_url=excluded.web_url",
                (
                    project_id,
                    milestone["id"],
                    milestone.get("iid"),
                    milestone.get("title"),
                    milestone.get("state"),
                    milestone.get("start_date"),
                    milestone.get("due_date"),
                    milestone.get("web_url"),
                ),
            )

        for issue in issues:
            author_id, author_name = _user(issue.get("author"))
            assignee_id, assignee_name = _user(issue.get("assignee"))
            issue_milestone = issue.get("milestone") or {}
            conn.execute(
                "INSERT INTO issues (project_id, iid, id, title, state, created_at, closed_at,"
                " updated_at, author_id, author_name, assignee_id, assignee_name, milestone,"
                " milestone_id, web_url) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(project_id, iid) DO UPDATE SET title=excluded.title,"
                " state=excluded.state, closed_at=excluded.closed_at,"
                " updated_at=excluded.updated_at, assignee_id=excluded.assignee_id,"
                " assignee_name=excluded.assignee_name, milestone=excluded.milestone,"
                " milestone_id=excluded.milestone_id",
                (
                    project_id,
                    issue["iid"],
                    issue.get("id"),
                    issue.get("title"),
                    issue.get("state"),
                    issue.get("created_at"),
                    issue.get("closed_at"),
                    issue.get("updated_at"),
                    author_id,
                    author_name,
                    assignee_id,
                    assignee_name,
                    issue_milestone.get("title"),
                    issue_milestone.get("id"),
                    issue.get("web_url"),
                ),
            )

        for iid, evts in events.items():
            conn.execute(
                "DELETE FROM label_events WHERE project_id = ? AND issue_iid = ?",
                (project_id, iid),
            )
            for ev in evts:
                label = (ev.get("label") or {}).get("name")
                user_id, user_name = _user(ev.get("user"))
                conn.execute(
                    "INSERT OR REPLACE INTO label_events (id, project_id, issue_iid, action,"
                    " label_name, user_id, user_name, created_at) VALUES (?,?,?,?,?,?,?,?)",
                    (
                        ev["id"],
                        project_id,
                        iid,
                        ev.get("action"),
                        label,
                        user_id,
                        user_name,
                        ev.get("created_at"),
                    ),
                )

        conn.execute(
            "INSERT INTO sync_state (project_id, last_synced_at, commits_synced_at)"
            " VALUES (?,?,?) ON CONFLICT(project_id) DO UPDATE SET"
            " last_synced_at=excluded.last_synced_at,"
            " commits_synced_at=excluded.commits_synced_at",
            (project_id, synced_at, synced_at),
        )
