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


def interval_owner(interval: "Interval", timeline: "IssueTimeline") -> str:
    """De quem e o tempo deste intervalo.

    No fluxo tipico o executor move o card para Doing e o revisor move para
    Review — entao quem aplicou a label da coluna e quem fez aquela etapa.
    Sem essa informacao (evento antigo, label posta por automacao), cai para
    o responsavel da issue.
    """
    if attribution_mode() == "mover" and interval.moved_by:
        return interval.moved_by
    return timeline.assignee


def queue_labels() -> set[str]:
    """Colunas de espera: contam no gargalo, mas nao no tempo de ninguem."""
    return {name.lower() for name in get_settings().queue_list}


QUEUE_UNCLAIMED = "(fila sem dono)"


def person_labels(project_id: int) -> dict[str, str]:
    """Labels que nomeiam alguem do time -> nome canonico da pessoa.

    Alguns times marcam o revisor com uma label de nome em vez do campo de
    responsavel. Detecta comparando as labels com os nomes que ja aparecem
    nos eventos e nos responsaveis.
    """
    with session() as conn:
        members = {r[0] for r in conn.execute(
            "SELECT DISTINCT user_name FROM label_events"
            " WHERE project_id = ? AND user_name IS NOT NULL", (project_id,))}
        members |= {r[0] for r in conn.execute(
            "SELECT DISTINCT assignee_name FROM issues"
            " WHERE project_id = ? AND assignee_name IS NOT NULL", (project_id,))}
        labels = {r[0] for r in conn.execute(
            "SELECT DISTINCT label_name FROM label_events"
            " WHERE project_id = ? AND label_name IS NOT NULL", (project_id,))}

    board = {name.lower() for name in board_labels(project_id, include_excluded=True)}
    full = {m.lower(): m for m in members}
    by_first: dict[str, str] = {}
    for member in members:
        parts = member.split()
        if parts:
            by_first.setdefault(parts[0].lower(), member)

    out: dict[str, str] = {}
    for label in labels:
        key = label.lower()
        if key in board:
            continue
        if key in full:
            out[key] = full[key]
        elif len(label.split()) <= 2 and label.split()[0].lower() in by_first:
            out[key] = by_first[label.split()[0].lower()]
    return out


def queue_debts(
    timeline: "IssueTimeline", queues: set[str], persons: dict[str, str]
) -> list[tuple[str, "Interval"]]:
    """De quem e a culpa por cada intervalo de fila.

    O card parado em "Waiting Review" esperava um revisor. Quem acabou
    pegando a revisao e quem o deixou esperando — a espera e demerito dele,
    nao de quem terminou o trabalho e colocou na fila. Card que ainda espera
    nao tem revisor conhecido: cai para a label de nome, se houver.
    """
    out: list[tuple[str, Interval]] = []
    for i, itv in enumerate(timeline.intervals):
        if itv.label.lower() not in queues:
            continue
        following = timeline.intervals[i + 1] if i + 1 < len(timeline.intervals) else None
        if following is not None:
            debtor = interval_owner(following, timeline)
        else:
            debtor = next(
                (persons[tag.lower()] for tag in timeline.tags if tag.lower() in persons),
                QUEUE_UNCLAIMED,
            )
        out.append((debtor, itv))
    return out


def scope_mode() -> str:
    mode = get_settings().scope.strip().lower()
    return mode if mode in ("assigned", "touched") else "assigned"


def counts_for_person(
    interval: "Interval", timeline: "IssueTimeline", review: str | None = None
) -> bool:
    """Se este intervalo entra no tempo de alguma pessoa.

    Em `assigned`, so conta o que a pessoa fez nas issues atribuidas a ela —
    etapa feita numa issue alheia fica de fora do tempo individual (continua
    no total do projeto e no gargalo, que sao contas por coluna).

    A revisao e a excecao: ela e *sempre* trabalho no card de outra pessoa,
    entao o escopo a apagaria por completo. Revisou, acumulou.
    """
    if scope_mode() != "assigned":
        return True
    if review is not None and interval.label == review:
        return True
    return interval_owner(interval, timeline) == timeline.assignee


def excluded_labels() -> set[str]:
    """Colunas fora de qualquer conta de tempo (ex.: Backlog)."""
    return {name.lower() for name in get_settings().excluded_list}


def focus_label(project_id: int, available: Iterable[str] | None = None) -> str | None:
    """A coluna que representa trabalho acontecendo.

    Prioridade: `FOCUS_LABEL` do .env -> heuristica pelo nome -> primeira
    coluna do board que nao esta excluida.
    """
    labels = list(available) if available is not None else board_labels(project_id)
    if not labels:
        return None
    configured = get_settings().focus_label.strip()
    if configured:
        for name in labels:
            if name.lower() == configured.lower():
                return name
    for name in labels:
        if any(token in name.lower() for token in FOCUS_PATTERN):
            return name
    return labels[0]


def review_label(project_id: int, available: Iterable[str] | None = None) -> str | None:
    """A coluna em que alguem revisa o trabalho de outra pessoa.

    Prioridade: `REVIEW_LABEL` do .env -> heuristica pelo nome. Nunca uma
    coluna de fila ("Waiting Review" e espera, nao revisao) nem uma excluida.
    """
    labels = list(available) if available is not None else board_labels(project_id)
    out = [name for name in labels
           if name.lower() not in queue_labels() and name.lower() not in excluded_labels()]
    if not out:
        return None
    configured = get_settings().review_label.strip()
    if configured:
        for name in out:
            if name.lower() == configured.lower():
                return name
        return None
    focus = focus_label(project_id, labels)
    for name in out:
        if name != focus and any(token in name.lower() for token in REVIEW_PATTERN):
            return name
    return None


def review_intervals(
    timeline: "IssueTimeline", name: str, label: str | None
) -> list["Interval"]:
    """Intervalos de revisao feitos por esta pessoa neste card.

    De proposito sem `counts_for_person`: revisar e trabalhar no card de
    outra pessoa, entao o SCOPE zeraria justamente o que se quer medir.
    """
    if not label:
        return []
    return [i for i in timeline.intervals
            if i.label == label and interval_owner(i, timeline) == name]


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@dataclass
class Interval:
    label: str
    start: datetime
    end: datetime | None      # None = ainda esta na coluna
    moved_by: str | None

    @property
    def closed(self) -> bool:
        return self.end is not None

    def seconds(self, now: datetime) -> float:
        return max(0.0, ((self.end or now) - self.start).total_seconds())


@dataclass
class IssueTimeline:
    project_id: int
    iid: int
    title: str
    state: str
    assignee: str
    author: str
    milestone: str
    web_url: str
    created_at: datetime | None
    closed_at: datetime | None
    intervals: list[Interval] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)   # labels que nao sao coluna


def resolve_project_id(project: str | int | None) -> int | None:
    """Aceita id numerico, `grupo/projeto` ou None (= primeiro projeto sincronizado)."""
    with session() as conn:
        if project is None:
            row = conn.execute("SELECT id FROM projects ORDER BY id LIMIT 1").fetchone()
        elif str(project).isdigit():
            row = conn.execute("SELECT id FROM projects WHERE id = ?", (int(project),)).fetchone()
        else:
            row = conn.execute("SELECT id FROM projects WHERE path = ?", (str(project),)).fetchone()
    return row["id"] if row else None


def board_labels(
    project_id: int, board_id: int | None = None, include_excluded: bool = False
) -> list[str]:
    """Colunas do board, na ordem em que aparecem, sem as excluidas."""
    query = "SELECT label_name, MIN(position) AS pos FROM board_lists WHERE project_id = ?"
    params: list[Any] = [project_id]
    if board_id is not None:
        query += " AND board_id = ?"
        params.append(board_id)
    query += " GROUP BY label_name ORDER BY pos"
    with session() as conn:
        names = [r["label_name"] for r in conn.execute(query, params).fetchall()]
    if include_excluded:
        return names
    hidden = excluded_labels()
    return [name for name in names if name.lower() not in hidden]


def board_positions(project_id: int) -> dict[str, int]:
    """Label da coluna (minuscula) -> posicao no board. Serve para saber o
    que e coluna e qual coluna e a mais avancada."""
    with session() as conn:
        rows = conn.execute(
            "SELECT label_name, MIN(position) AS pos FROM board_lists"
            " WHERE project_id = ? GROUP BY label_name",
            (project_id,),
        ).fetchall()
    return {r["label_name"].lower(): (r["pos"] if r["pos"] is not None else 0) for r in rows}


