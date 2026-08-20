"""Calculo das metricas a partir do cache.

Ideia central: as colunas de um board do GitLab sao labels. Cada par
`add`/`remove` de uma label no `resource_label_events` forma um intervalo
em que o card esteve naquela coluna. Somando os intervalos por issue e
agrupando por responsavel sai o "tempo total em Doing".

Limitacao conhecida: a API v4 nao expoe historico de assignee, entao o
tempo e atribuido ao responsavel *atual* da issue (fallback: autor).
O campo `moved_by` mostra quem de fato moveu o card, para conferencia.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from app.config import get_settings
from app.db import session

UNASSIGNED = "(sem responsavel)"
NO_MILESTONE = "(sem sprint)"

FOCUS_PATTERN = ("doing", "em andamento", "andamento", "in progress", "progress",
                 "desenvolvimento", "wip")

REVIEW_PATTERN = ("review", "revisao", "revisão", "qa", "homologacao", "homologação")


def attribution_mode() -> str:
    mode = get_settings().attribution.strip().lower()
    return mode if mode in ("mover", "assignee") else "mover"


