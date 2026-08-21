"""Metricas de commits.

Vive separado de `metrics.py` de proposito: commit e uma dimensao diferente
de issue. O autor de um commit vem do `git config` da maquina, nao do usuario
do GitLab — "Tiago" e "Tiago Brun de Arruda" sao a mesma pessoa para o time e
pessoas diferentes para os dados. A juncao e feita por e-mail e, quando isso
falha, por nome; o resultado fica explicito em `identities`.
"""

import re
import unicodedata
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

from app.config import get_settings
from app.db import session
from app.metrics import format_duration, parse_ts  # noqa: F401  (reuso do formatador)

WEEKDAYS = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sab", "Dom"]


def member_names(project_id: int) -> list[str]:
    """Quem e do time, pelos usuarios que aparecem nos eventos do board."""
    with session() as conn:
        return [r[0] for r in conn.execute(
            "SELECT DISTINCT user_name FROM label_events"
            " WHERE project_id = ? AND user_name IS NOT NULL", (project_id,))]


