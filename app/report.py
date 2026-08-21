"""Relatorio comparativo entre sprints.

Junta as tres dimensoes que o dash mede separado — tempo de board, pessoas e
commits — numa leitura unica por sprint, com a variacao contra a sprint
anterior. E composicao: nada aqui recalcula metrica, tudo vem de `metrics` e
de `commits`, para o relatorio nunca discordar da tela.
"""

from collections import defaultdict
from datetime import datetime, timezone
from html import escape
from typing import Any, Iterable

from app import commits as commit_metrics
from app import metrics
from app.config import get_settings
from app.db import session


def _pct_delta(now: float, before: float) -> float | None:
    """Variacao percentual. `None` quando nao ha base de comparacao."""
    if before <= 0:
        return None
    return round((now - before) / before * 100, 1)


def _issue_milestones(project_id: int) -> dict[int, str]:
    with session() as conn:
        rows = conn.execute(
            "SELECT iid, milestone FROM issues WHERE project_id = ?", (project_id,)
        ).fetchall()
    return {r["iid"]: (r["milestone"] or metrics.NO_MILESTONE) for r in rows}


def _commits_by_sprint(project_id: int) -> tuple[dict[str, dict[str, int]], int]:
    """Commits por sprint, pela issue que a mensagem cita.

    Data nao serve para isso: commit feito no primeiro dia da sprint 2 pode
    ser de uma issue arrastada da sprint 1. Quem nao cita issue nao entra em
    sprint nenhuma — e o numero de orfaos e devolvido junto, para o relatorio
    poder dizer de quantos commits ele nao sabe a sprint.
    """
    milestone_of = _issue_milestones(project_id)
    dentro, _ = commit_metrics.split_members(project_id, commit_metrics._rows(project_id))
    out: dict[str, dict[str, int]] = {}
    orphans = 0
    for row in dentro:
        iid = commit_metrics.issue_ref(row["title"])
        sprint = milestone_of.get(iid) if iid is not None else None
        if sprint is None:
            orphans += 1
            continue
        bucket = out.setdefault(sprint, {"commits": 0, "ok": 0})
        bucket["commits"] += 1
        if not commit_metrics.check_title(row["title"]):
            bucket["ok"] += 1
    return out, orphans


def sprint_report(
    project_id: int,
    labels: Iterable[str] | None = None,
    limit: int = 8,
    people_limit: int = 6,
) -> dict[str, Any]:
    """Uma linha por sprint, da mais recente para a mais antiga, com delta."""
    label_filter = list(labels) if labels else None
    base = metrics.milestone_report(project_id, label_filter, limit=limit)
    focus = metrics.focus_label(project_id, label_filter)
    review = metrics.review_label(project_id, label_filter)
    commits_by_sprint, orphan_commits = _commits_by_sprint(project_id)

    with session() as conn:
        project = conn.execute(
            "SELECT path, name, web_url, synced_at FROM projects WHERE id = ?", (project_id,)
        ).fetchone()

    # `(sem sprint)` e um balde, nao uma sprint: deixa-lo na fila faria a
    # sprint mais antiga ser comparada contra ele e inventar variacoes
    unscheduled = next(
        (r for r in base["milestones"] if r["milestone"] == metrics.NO_MILESTONE), None
    )

    sprints: list[dict[str, Any]] = []
    for row in base["milestones"]:
        title = row["milestone"]
        if title == metrics.NO_MILESTONE:
            continue
        people = metrics.contributor_report(project_id, label_filter, milestone=title)
        commits = commits_by_sprint.get(title, {"commits": 0, "ok": 0})
        pct = round(commits["ok"] / commits["commits"] * 100, 1) if commits["commits"] else None

        sprints.append({
            **row,
            "focus_hours": row["by_label"].get(focus, {}).get("hours", 0.0) if focus else 0.0,
            "review_hours": row["by_label"].get(review, {}).get("hours", 0.0) if review else 0.0,
            "commits": commits["commits"],
            "convention_ok": commits["ok"],
            "convention_pct": pct,
            "people": [
                {
                    "contributor": p["contributor"],
                    "hours": p["total_hours"],
                    "human": p["total_human"],
                    "review_hours": p["review_hours"],
                    "issues": p["issues"],
                    "closed_issues": p["closed_issues"],
                    "waiting_hours": p["waiting_hours"],
                }
                for p in people["contributors"][:people_limit]
            ],
        })

    # a comparacao e sempre com a sprint imediatamente anterior — as linhas ja
    # vem da mais recente para a mais antiga
    for i, sprint in enumerate(sprints):
        before = sprints[i + 1] if i + 1 < len(sprints) else None
        sprint["compared_to"] = before["milestone"] if before else None
        sprint["delta"] = {} if not before else {
            "total_hours": _pct_delta(sprint["total_hours"], before["total_hours"]),
            "focus_hours": _pct_delta(sprint["focus_hours"], before["focus_hours"]),
            "issues": _pct_delta(sprint["issues"], before["issues"]),
            "closed_issues": _pct_delta(sprint["closed_issues"], before["closed_issues"]),
            "avg_lead_hours": _pct_delta(sprint["avg_lead_hours"], before["avg_lead_hours"]),
            "commits": _pct_delta(sprint["commits"], before["commits"]),
            # taxa se compara em pontos percentuais, nao em variacao relativa
            "completion_pp": round(sprint["completion"] - before["completion"], 1),
            "convention_pp": round(sprint["convention_pct"] - before["convention_pct"], 1)
            if sprint["convention_pct"] is not None and before["convention_pct"] is not None
            else None,
        }

    return {
        "project_id": project_id,
        "project": dict(project) if project else {},
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "columns": base["columns"],
        "focus_label": focus,
        "review_label": review,
        "orphan_commits": orphan_commits,
        "outsiders": _outsiders(project_id),
        "unscheduled": {
            "issues": unscheduled["issues"],
            "closed_issues": unscheduled["closed_issues"],
            "human": unscheduled["total_human"],
        } if unscheduled else None,
        "sprints": sprints,
        "convention": commit_metrics.convention_report(project_id),
    }


def _outsiders(project_id: int) -> dict[str, Any]:
    """Quem commitou no repositorio sem ser do time, e quanto ficou de fora."""
    _, fora = commit_metrics.split_members(project_id, commit_metrics._rows(project_id))
    return commit_metrics.outsiders_note(fora)


def _sprint_commits(project_id: int, milestone: str) -> list[dict[str, Any]]:
    """Commits da sprint, pela issue citada, ja com o veredito da convencao.

    Commit de quem nao e do time nao entra: o relatorio mede o trabalho da
    equipe, e o bot do template nao faz parte dela.
    """
    milestone_of = _issue_milestones(project_id)
    dentro, _ = commit_metrics.split_members(project_id, commit_metrics._rows(project_id))
    out = []
    for row in dentro:
        iid = commit_metrics.issue_ref(row["title"])
        if iid is None or milestone_of.get(iid) != milestone:
            continue
        out.append({
            "short_id": row["short_id"],
            "title": row["title"],
            "author": row["author_name"],
            "committed_at": row["committed_at"],
            "web_url": row["web_url"],
            "issue": iid,
            "convention": commit_metrics.check_title(row["title"]),
        })
    return out


