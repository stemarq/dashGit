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


def member_authors(project_id: int) -> set[str]:
    """Assinaturas de git (em minusculas) que pertencem a alguem do time.

    O bot do template e as contas de professor commitam no mesmo repositorio;
    contar o que eles fazem como trabalho do time distorce ritmo, ranking e
    aderencia — e a aderencia deles e sempre 0%, porque nem tentam seguir a
    convencao do time.
    """
    membros = member_names(project_id)
    if not membros:
        return set()          # sem eventos de board nao da para saber quem e do time
    with session() as conn:
        assinaturas = conn.execute(
            "SELECT DISTINCT author_name, author_email FROM commits WHERE project_id = ?",
            (project_id,),
        ).fetchall()
    return {
        r["author_name"].lower() for r in assinaturas
        if r["author_name"] and match_member(r["author_name"], membros, r["author_email"])
    }


