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


def split_members(project_id: int, rows: list[Any]) -> tuple[list[Any], list[Any]]:
    """Separa os commits do time dos de fora. Nada e descartado calado."""
    if get_settings().count_non_members:
        return list(rows), []
    do_time = member_authors(project_id)
    # projeto sem board sincronizado: nao ha lista de membros para comparar, e
    # excluir todo mundo zeraria a tela em vez de protege-la do ruido
    if not do_time:
        return list(rows), []
    dentro = [r for r in rows if (r["author_name"] or "").lower() in do_time]
    fora = [r for r in rows if (r["author_name"] or "").lower() not in do_time]
    return dentro, fora


def outsiders_note(rows: list[Any]) -> dict[str, Any]:
    """Resumo do que ficou de fora, para a tela poder dizer o que ignorou."""
    por_autor: dict[str, int] = defaultdict(int)
    for r in rows:
        por_autor[(r["author_name"] or "?").strip()] += 1
    return {
        "commits": len(rows),
        "authors": [{"author": nome, "commits": n}
                    for nome, n in sorted(por_autor.items(), key=lambda x: -x[1])],
    }


def author_aliases(project_id: int, author: str) -> set[str]:
    """Todos os nomes de git da mesma pessoa, em minusculas.

    Filtrar por "Lucas Delmirio da Silva" tem de trazer os commits assinados
    como `lucas.delmirio` — sao a mesma pessoa, e o filtro e por contribuinte,
    nao por identidade de git. Quando o nome pedido nao casa com ninguem do
    time, vale so a correspondencia exata (nome ou e-mail).
    """
    alvo = author.strip().lower()
    members = member_names(project_id)
    with session() as conn:
        assinaturas = conn.execute(
            "SELECT DISTINCT author_name, author_email FROM commits WHERE project_id = ?",
            (project_id,),
        ).fetchall()

    exatos = {r["author_name"].lower() for r in assinaturas
              if (r["author_name"] or "").lower() == alvo
              or (r["author_email"] or "").lower() == alvo}

    # a pessoa pedida pode vir como nome do GitLab ou como assinatura do git
    membro = next((m for m in members if m.lower() == alvo), None) or match_member(
        author, members
    )
    if not membro:
        # pedido por e-mail: quem responde e a assinatura que aquele e-mail usa
        for r in assinaturas:
            if r["author_name"] and r["author_name"].lower() in exatos:
                membro = match_member(r["author_name"], members, r["author_email"])
                if membro:
                    break
    if not membro:
        return exatos or {alvo}

    return exatos | {
        r["author_name"].lower() for r in assinaturas
        if r["author_name"]
        and match_member(r["author_name"], members, r["author_email"]) == membro
    }


def _rows(
    project_id: int,
    since: datetime | None = None,
    until: datetime | None = None,
    author: str | None = None,
    include_merges: bool = False,
) -> list[Any]:
    sql = "SELECT * FROM commits WHERE project_id = ?"
    params: list[Any] = [project_id]
    if not include_merges:
        sql += " AND is_merge = 0"
    if since:
        sql += " AND committed_at >= ?"
        params.append(since.isoformat())
    if until:
        sql += " AND committed_at <= ?"
        params.append(until.isoformat())
    sql += " ORDER BY committed_at DESC"
    with session() as conn:
        rows = conn.execute(sql, params).fetchall()
    if author:
        aliases = author_aliases(project_id, author)
        rows = [r for r in rows if (r["author_name"] or "").lower() in aliases
                or (r["author_email"] or "").lower() in aliases]
    return rows


def identities(project_id: int) -> list[dict[str, Any]]:
    """Autores de commit agrupados por pessoa.

    Um mesmo autor costuma aparecer com varios e-mails (maquina pessoal, web
    IDE, GitHub noreply). Agrupa pelo nome normalizado e lista os e-mails para
    o time conferir se alguem ficou dividido em dois.
    """
    with session() as conn:
        rows = conn.execute(
            "SELECT author_name, author_email, COUNT(*) AS commits,"
            " MIN(committed_at) AS first_at, MAX(committed_at) AS last_at"
            " FROM commits WHERE project_id = ? AND is_merge = 0"
            " GROUP BY author_name, author_email",
            (project_id,),
        ).fetchall()

    grouped: dict[str, dict[str, Any]] = {}
    for r in rows:
        name = (r["author_name"] or "?").strip()
        bucket = grouped.setdefault(name.lower(), {
            "author": name, "commits": 0, "emails": set(),
            "first_at": r["first_at"], "last_at": r["last_at"],
        })
        bucket["commits"] += r["commits"]
        if r["author_email"]:
            bucket["emails"].add(r["author_email"])
        bucket["first_at"] = min(bucket["first_at"], r["first_at"] or bucket["first_at"])
        bucket["last_at"] = max(bucket["last_at"], r["last_at"] or bucket["last_at"])

    out = [{**b, "emails": sorted(b["emails"])} for b in grouped.values()]
    out.sort(key=lambda a: -a["commits"])
    return out


# particulas de nome nao identificam ninguem
STOPWORDS = {"dos", "das", "der", "van", "von"}


def _fold(text: str) -> str:
    """Sem acento e em minusculas: 'Brandao' e 'Brandão' sao a mesma pessoa."""
    nfkd = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _tokens(text: str) -> set[str]:
    """Pedacos uteis de um nome ou de um login de e-mail."""
    parts = re.split(r"[.\s_+-]+", _fold(text).strip())
    return {p for p in parts if len(p) > 2 and p not in STOPWORDS}


def match_member(author: str, members: Iterable[str], email: str | None = None) -> str | None:
    """Liga o nome do git ao usuario do GitLab.

    Nome de commit vem em todo formato: "Tiago", "lucas.delmirio",
    "Guilherme Maia" para um "Jose Guilherme Goncalves Maia". Compara os
    pedacos do nome (e do login do e-mail) e exige que a melhor combinacao
    seja unica, para nao chutar entre dois homonimos.
    """
    nome = _tokens(author)
    alvo = set(nome)
    if email and "@" in email:
        alvo |= _tokens(email.split("@")[0])
    if not alvo:
        return None

    pontos = [(len(alvo & _tokens(m)), m) for m in members]
    if not pontos:
        return None
    melhor = max(p for p, _ in pontos)
    if melhor == 0:
        return None
    empatados = [m for p, m in pontos if p == melhor]
    if len(empatados) > 1:
        return None                  # empate: melhor nao identificar que chutar
    # Um pedaco so de nome vale quando o autor assina com um nome unico
    # ("Tiago"). Se ele assina com nome completo, um unico pedaco em comum
    # costuma ser sobrenome corriqueiro — "Rodrigues" nao faz de duas pessoas
    # a mesma.
    return empatados[0] if melhor >= 2 or len(nome) <= 1 else None


