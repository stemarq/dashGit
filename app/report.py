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


