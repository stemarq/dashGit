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
from app.metrics import NO_MILESTONE, format_duration, parse_ts  # noqa: F401

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


def issue_milestones(project_id: int) -> dict[int, str]:
    """iid da issue -> sprint dela (ou o balde de quem nao tem sprint)."""
    with session() as conn:
        rows = conn.execute(
            "SELECT iid, milestone FROM issues WHERE project_id = ?", (project_id,)
        ).fetchall()
    return {r["iid"]: (r["milestone"] or NO_MILESTONE) for r in rows}


def in_milestone(
    project_id: int, rows: list[Any], milestone: str
) -> tuple[list[Any], int]:
    """Commits de uma sprint, pela issue que a mensagem cita.

    Data nao serve para isso: commit do primeiro dia da sprint 2 pode ser de
    uma issue arrastada da sprint 1. Quem nao cita issue nao entra em sprint
    nenhuma — e o numero de orfaos volta junto, para a tela poder dizer de
    quantos commits ela nao sabe a sprint.
    """
    milestone_of = issue_milestones(project_id)
    dentro, orfaos = [], 0
    for r in rows:
        iid = issue_ref(r["title"])
        if iid is None or iid not in milestone_of:
            orfaos += 1
            continue
        if milestone_of[iid] == milestone:
            dentro.append(r)
    return dentro, orfaos


def _rows(
    project_id: int,
    since: datetime | None = None,
    until: datetime | None = None,
    author: str | None = None,
    include_merges: bool = False,
    milestone: str | None = None,
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
    if milestone:
        rows, _ = in_milestone(project_id, rows, milestone)
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


def _series(by_day: dict[str, dict[str, int]]) -> tuple[list[dict[str, Any]], str]:
    """Serie temporal com o balde certo para o intervalo.

    Repositorio criado a partir de template carrega commits de anos atras.
    Forcar tudo em barras diarias renderiza centenas de colunas vazias e
    esconde a atividade real, entao o balde cresce com o intervalo.
    """
    if not by_day:
        return [], "day"

    start = date.fromisoformat(min(by_day))
    end = date.fromisoformat(max(by_day))
    span = (end - start).days

    if span <= 70:
        gran, passo = "day", timedelta(days=1)
        chave = lambda d: d                                   # noqa: E731
    elif span <= 400:
        gran, passo = "week", timedelta(days=7)
        chave = lambda d: d - timedelta(days=d.weekday())      # noqa: E731
        start = chave(start)
    else:
        gran, passo = "month", None
        chave = lambda d: d.replace(day=1)                     # noqa: E731
        start = chave(start)

    baldes: dict[date, dict[str, int]] = {}
    for key, valores in by_day.items():
        b = baldes.setdefault(chave(date.fromisoformat(key)),
                              {"commits": 0, "additions": 0, "deletions": 0})
        for campo in b:
            b[campo] += valores[campo]

    # preenche os vazios para o grafico nao mentir sobre ritmo
    out, cursor = [], start
    while cursor <= end:
        valores = baldes.get(cursor, {"commits": 0, "additions": 0, "deletions": 0})
        out.append({"date": cursor.isoformat(), **valores})
        if passo:
            cursor += passo
        else:
            cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
    return out, gran


# ── convencao de mensagem ────────────────────────────────────────────────
#
# O padrao combinado pelo time: `tipo(#issue): descricao`, sem acentuacao.
# Maiuscula e permitida (24/08/2026) — sigla tecnica no meio da descricao
# ("deriva NPS_MEDIO por rota") era o motivo mais comum de reprovacao e nao
# atrapalhava ninguem. Cada regra quebrada vira um motivo, para a analise
# dizer *o que* corrigir em vez de so reprovar.

CONVENTION_RULE = "tipo(#issue): descricao — sem acentuacao"

CONVENTION_TYPES = ("feat", "fix", "docs", "chore", "refactor", "test", "style",
                    "perf", "build", "ci", "revert")

CONVENTION_PATTERN = re.compile(r"^(?P<type>[a-z]+)\((?P<ref>#\d+)\): (?P<desc>\S.*)$")

# a forma certa, ignorando so o espacamento — separa "escreveu errado" de
# "esqueceu um espaco", que sao problemas de tamanho bem diferente
_LOOSE = re.compile(r"^[a-z]+\s*\(\s*#\d+\s*\)\s*:\s*\S")

REASON_LABELS = {
    "vazio": "mensagem vazia",
    "acento": "tem acentuacao",
    "espaco": "espacamento fora do padrao",
    "tipo": "tipo fora da lista",
    "sem_issue": "nao referencia a issue",
    "formato": "fora do formato tipo(#issue): descricao",
}


def check_title(title: str | None) -> list[str]:
    """Motivos pelos quais esta mensagem foge da convencao. Vazio = segue."""
    text = (title or "").strip()
    if not text:
        return ["vazio"]

    reasons: list[str] = []
    if _fold(text) != text.lower():
        reasons.append("acento")

    # a forma e conferida no texto normalizado, entao a caixa nao interfere:
    # `Fix(#1): Corrige` tem a forma certa, e maiuscula agora e permitida
    low = _fold(text)
    match = CONVENTION_PATTERN.match(low)
    if match:
        if match.group("type") not in CONVENTION_TYPES:
            reasons.append("tipo")
        return reasons

    if _LOOSE.match(low):
        # tem tipo, issue e descricao — o que quebrou foi o espacamento
        reasons.append("espaco")
        tipo = low.split("(")[0].strip()
        if tipo not in CONVENTION_TYPES:
            reasons.append("tipo")
    elif re.search(r"#\d+", low):
        reasons.append("formato")
    else:
        reasons.append("sem_issue")
    return reasons


def issue_ref(title: str | None) -> int | None:
    """A issue citada na mensagem, mesmo que o resto esteja fora do padrao."""
    match = re.search(r"#(\d+)", title or "")
    return int(match.group(1)) if match else None


def convention_report(
    project_id: int,
    since: datetime | None = None,
    until: datetime | None = None,
    author: str | None = None,
    include_merges: bool = False,
    milestone: str | None = None,
) -> dict[str, Any]:
    """Quanto de cada pessoa segue a convencao de mensagem de commit.

    Merge commit fica de fora por padrao: a mensagem e gerada pelo GitLab e
    reprovar o time por ela nao mede nada.
    """
    todos = _rows(project_id, since, until, author, include_merges)
    sem_issue = 0
    if milestone:
        todos, sem_issue = in_milestone(project_id, todos, milestone)
    rows, fora_do_time = split_members(project_id, todos)

    by_author: dict[str, dict[str, Any]] = {}
    totals_reasons: dict[str, int] = defaultdict(int)
    ok_total = 0

    for r in rows:
        name = (r["author_name"] or "?").strip()
        person = by_author.setdefault(name, {
            "author": name, "email": r["author_email"], "commits": 0, "ok": 0,
            "reasons": defaultdict(int), "offenders": [],
        })
        person["commits"] += 1
        reasons = check_title(r["title"])
        if not reasons:
            person["ok"] += 1
            ok_total += 1
            continue
        for reason in reasons:
            person["reasons"][reason] += 1
            totals_reasons[reason] += 1
        if len(person["offenders"]) < 8:
            person["offenders"].append({
                "short_id": r["short_id"],
                "title": r["title"],
                "web_url": r["web_url"],
                "committed_at": r["committed_at"],
                "reasons": reasons,
            })

    # nome do git != usuario do GitLab: "lucas.delmirio" e a mesma pessoa que
    # "Lucas Delmirio da Silva", e o relatorio e por membro do time
    members = member_names(project_id)

    autores = []
    for person in by_author.values():
        total = person["commits"]
        autores.append({
            "author": person["author"],
            "member": match_member(person["author"], members, person.get("email")),
            "commits": total,
            "ok": person["ok"],
            "off": total - person["ok"],
            "pct": round(person["ok"] / total * 100, 1) if total else 0.0,
            "reasons": dict(sorted(person["reasons"].items(), key=lambda x: -x[1])),
            "offenders": person["offenders"],
        })
    # pior aderencia primeiro entre quem tem volume parecido: o relatorio
    # existe para achar quem precisa ajustar, nao para premiar
    autores.sort(key=lambda a: (a["pct"], -a["commits"]))

    return {
        "project_id": project_id,
        "rule": CONVENTION_RULE,
        "types": list(CONVENTION_TYPES),
        "reason_labels": REASON_LABELS,
        "totals": {
            "commits": len(rows),
            "ok": ok_total,
            "off": len(rows) - ok_total,
            "pct": round(ok_total / len(rows) * 100, 1) if rows else 0.0,
        },
        "by_reason": dict(sorted(totals_reasons.items(), key=lambda x: -x[1])),
        "authors": autores,
        "outsiders": outsiders_note(fora_do_time),
        "milestone": milestone,
        "unlinked_commits": sem_issue,
    }


def convention_by_issue(project_id: int, include_merges: bool = False) -> dict[int, list[bool]]:
    """iid da issue citada -> lista de "segue a convencao?" de cada commit.

    Serve para levar a aderencia para o recorte de sprint: a sprint de um
    commit e a da issue que ele cita, nao a data em que foi feito.
    """
    out: dict[int, list[bool]] = defaultdict(list)
    dentro, _ = split_members(project_id, _rows(project_id, include_merges=include_merges))
    for r in dentro:
        iid = issue_ref(r["title"])
        if iid is not None:
            out[iid].append(not check_title(r["title"]))
    return out


def commit_report(
    project_id: int,
    since: datetime | None = None,
    until: datetime | None = None,
    author: str | None = None,
    include_merges: bool = False,
    only_off: bool = False,
    milestone: str | None = None,
) -> dict[str, Any]:
    """Volume, autores, ritmo diario e horario dos commits.

    `only_off` recorta *apenas a listagem* de commits recentes para os que
    fogem da convencao. As somas continuam sobre todos: filtrar a lista e uma
    lente de leitura; encolher os totais junto seria mentir sobre o volume.
    """
    now = datetime.now(timezone.utc)
    todos = _rows(project_id, since, until, author, include_merges)
    sem_issue = 0
    if milestone:
        todos, sem_issue = in_milestone(project_id, todos, milestone)
    rows, fora_do_time = split_members(project_id, todos)

    by_author: dict[str, dict[str, Any]] = {}
    by_day: dict[str, dict[str, int]] = defaultdict(
        lambda: {"commits": 0, "additions": 0, "deletions": 0}
    )
    heat = [[0] * 24 for _ in range(7)]
    additions = deletions = 0

    for r in rows:
        when = parse_ts(r["committed_at"])
        name = (r["author_name"] or "?").strip()
        person = by_author.setdefault(name, {
            "author": name, "email": r["author_email"], "commits": 0,
            "additions": 0, "deletions": 0, "days": set(),
            "first_at": r["committed_at"], "last_at": r["committed_at"],
        })
        person["commits"] += 1
        person["additions"] += r["additions"] or 0
        person["deletions"] += r["deletions"] or 0
        additions += r["additions"] or 0
        deletions += r["deletions"] or 0

        if when:
            local = when.astimezone()          # ritmo e horario sao locais
            key = local.date().isoformat()
            person["days"].add(key)
            day = by_day[key]
            day["commits"] += 1
            day["additions"] += r["additions"] or 0
            day["deletions"] += r["deletions"] or 0
            heat[local.weekday()][local.hour] += 1
            person["first_at"] = min(person["first_at"], r["committed_at"])
            person["last_at"] = max(person["last_at"], r["committed_at"])

    autores = []
    for person in by_author.values():
        dias = len(person["days"]) or 1
        autores.append({
            "author": person["author"],
            "email": person["email"],
            "commits": person["commits"],
            "additions": person["additions"],
            "deletions": person["deletions"],
            "net": person["additions"] - person["deletions"],
            "active_days": len(person["days"]),
            "commits_per_active_day": round(person["commits"] / dias, 2),
            "avg_size": round((person["additions"] + person["deletions"]) / person["commits"], 1)
            if person["commits"] else 0,
            "first_at": person["first_at"],
            "last_at": person["last_at"],
        })
    autores.sort(key=lambda a: -a["commits"])

    series, granularity = _series(by_day)

    # cada commit da listagem carrega o veredito da convencao, para a tabela
    # poder sinalizar sem uma segunda chamada
    marcados = [
        {
            "short_id": r["short_id"],
            "title": r["title"],
            "author": r["author_name"],
            "committed_at": r["committed_at"],
            "additions": r["additions"],
            "deletions": r["deletions"],
            "web_url": r["web_url"],
            "issue": issue_ref(r["title"]),
            "convention": check_title(r["title"]),
        }
        for r in rows
    ]
    fora = [c for c in marcados if c["convention"]]
    recent = (fora if only_off else marcados)[:30]

    dias_ativos = len(by_day)
    return {
        "project_id": project_id,
        "window": {
            "since": since.isoformat() if since else None,
            "until": (until or now).isoformat(),
        },
        "totals": {
            "commits": len(rows),
            "authors": len(by_author),
            "additions": additions,
            "deletions": deletions,
            "net": additions - deletions,
            "active_days": dias_ativos,
            "commits_per_active_day": round(len(rows) / dias_ativos, 2) if dias_ativos else 0,
            "avg_size": round((additions + deletions) / len(rows), 1) if rows else 0,
        },
        "authors": autores,
        "granularity": granularity,
        "series": series,
        "heatmap": {"weekdays": WEEKDAYS, "counts": heat},
        "recent": recent,
        "off_convention": len(fora),
        "reason_labels": REASON_LABELS,
        "outsiders": outsiders_note(fora_do_time),
        "milestone": milestone,
        "unlinked_commits": sem_issue,
    }
