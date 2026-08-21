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


