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
from functools import lru_cache
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
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


def skip_weekends() -> bool:
    return get_settings().skip_weekends


def _off_day_seconds(start: datetime, end: datetime) -> float:
    """Quanto do intervalo caiu em dia que nao conta — fim de semana ou feriado.

    A avaliacao e na hora local porque e ela que diz quando o time nao estava
    trabalhando: em UTC, a sexta-feira brasileira vira sabado as 21h.
    """
    inicio, fim = start.astimezone(), end.astimezone()
    total = 0.0
    dia = inicio.date()
    while dia <= fim.date():
        if is_off_day(dia):
            abre = datetime.combine(dia, time.min, tzinfo=inicio.tzinfo)
            fecha = abre + timedelta(days=1)
            sobreposicao = min(fim, fecha) - max(inicio, abre)
            if sobreposicao.total_seconds() > 0:
                total += sobreposicao.total_seconds()
        dia += timedelta(days=1)
    return total


def _easter(year: int) -> date:
    """Domingo de Pascoa (algoritmo de Gauss/Meeus).

    Carnaval, sexta-feira santa e corpus christi saem dele, entao o dash
    acerta os feriados moveis de qualquer ano sem tabela para manter.
    """
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    ll = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ll) // 451
    mes = (h + ll - 7 * m + 114) // 31
    dia = ((h + ll - 7 * m + 114) % 31) + 1
    return date(year, mes, dia)


# Feriados nacionais fixos. O 20/11 so vale de 2024 em diante (Lei 14.759).
_FIXOS_BR = ((1, 1), (4, 21), (5, 1), (9, 7), (10, 12), (11, 2), (11, 15), (12, 25))


@lru_cache(maxsize=32)
def _national_holidays(year: int, calendario: str) -> frozenset[date]:
    if calendario != "br":
        return frozenset()
    pascoa = _easter(year)
    datas = {date(year, mes, dia) for mes, dia in _FIXOS_BR}
    if year >= 2024:
        datas.add(date(year, 11, 20))
    datas |= {
        pascoa - timedelta(days=47),   # carnaval (terca)
        pascoa - timedelta(days=2),    # sexta-feira santa
        pascoa + timedelta(days=60),   # corpus christi
    }
    return frozenset(datas)


def holidays(year: int) -> frozenset[date]:
    """Feriados do ano: os nacionais mais os extras do .env."""
    settings = get_settings()
    extras = set()
    for texto in settings.holiday_list:
        try:
            extras.add(date.fromisoformat(texto))
        except ValueError:
            continue          # data torta no .env nao derruba o dash
    calendario = settings.holiday_calendar.strip().lower()
    return frozenset(_national_holidays(year, calendario) | extras)


def is_off_day(dia: date) -> bool:
    """Dia que nao conta inteiro: fim de semana ou feriado."""
    if skip_weekends() and dia.weekday() >= 5:
        return True
    return dia in holidays(dia.year)


def non_working_windows() -> list[tuple[time, time]]:
    """Faixas do dia que nao sao trabalho (aula, almoco), ja convertidas."""
    saida = []
    for inicio, fim in get_settings().non_working_list:
        try:
            h1, h2 = time.fromisoformat(inicio), time.fromisoformat(fim)
        except ValueError:
            continue          # faixa mal escrita no .env nao derruba o dash
        if h2 > h1:
            saida.append((h1, h2))
    return saida


def _blocked_seconds(start: datetime, end: datetime) -> float:
    """Quanto do intervalo caiu nas faixas nao uteis de cada dia.

    So conta nos dias que ja contam: descontar a janela de um sabado seria
    tirar duas vezes o mesmo tempo, porque o fim de semana inteiro ja saiu.
    """
    janelas = non_working_windows()
    if not janelas:
        return 0.0
    inicio, fim = start.astimezone(), end.astimezone()
    total = 0.0
    dia = inicio.date()
    while dia <= fim.date():
        if is_off_day(dia):
            dia += timedelta(days=1)
            continue          # o dia ja saiu inteiro; descontar de novo dobraria
        for h1, h2 in janelas:
            janela_ini = datetime.combine(dia, h1, tzinfo=inicio.tzinfo)
            janela_fim = datetime.combine(dia, h2, tzinfo=inicio.tzinfo)
            sobreposicao = min(fim, janela_fim) - max(inicio, janela_ini)
            if sobreposicao.total_seconds() > 0:
                total += sobreposicao.total_seconds()
        dia += timedelta(days=1)
    return total


def elapsed(start: datetime | None, end: datetime | None) -> float:
    """Segundos uteis entre dois instantes — a unica conta de tempo do dash.

    Tudo passa por aqui (coluna, fila, lead time) para que ligar ou desligar
    o fim de semana mude todos os numeros de uma vez, e nao metade deles.
    """
    if start is None or end is None or end <= start:
        return 0.0
    bruto = (end - start).total_seconds() - _off_day_seconds(start, end)
    return max(0.0, bruto - _blocked_seconds(start, end))


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
        return elapsed(self.start, self.end or now)


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


def build_timelines(
    project_id: int,
    labels: Iterable[str] | None = None,
    state: str | None = None,
    milestone: str | None = None,
) -> list[IssueTimeline]:
    now = datetime.now(timezone.utc)
    wanted = {name.lower() for name in labels} if labels else None
    # pedir a coluna explicitamente ganha da exclusao global
    hidden = excluded_labels() - (wanted or set())
    positions = board_positions(project_id)

    # So label que e coluna do board vira tempo. Um card costuma carregar
    # tambem etiquetas de conteudo ("DOCUMENTATION", "ART 1"); contar cada
    # uma como etapa multiplicaria o mesmo periodo por quantas houvesse.
    tracked: set[str] | None = wanted if wanted is not None else set(positions) - hidden
    # projeto sem board sincronizado: nao da para distinguir coluna de
    # etiqueta, entao conta todas em vez de zerar o dashboard
    if tracked is not None and not tracked:
        tracked = None

    with session() as conn:
        issue_sql = "SELECT * FROM issues WHERE project_id = ?"
        params: list[Any] = [project_id]
        if state in ("opened", "closed"):
            issue_sql += " AND state = ?"
            params.append(state)
        if milestone == NO_MILESTONE:
            issue_sql += " AND (milestone IS NULL OR milestone = '')"
        elif milestone:
            issue_sql += " AND milestone = ?"
            params.append(milestone)
        issues = conn.execute(issue_sql, params).fetchall()

        events = conn.execute(
            "SELECT issue_iid, action, label_name, user_name, created_at FROM label_events"
            " WHERE project_id = ? ORDER BY created_at, id",
            (project_id,),
        ).fetchall()

    by_issue: dict[int, list[Any]] = defaultdict(list)
    for ev in events:
        by_issue[ev["issue_iid"]].append(ev)

    timelines: list[IssueTimeline] = []
    for issue in issues:
        tl = IssueTimeline(
            project_id=project_id,
            iid=issue["iid"],
            title=issue["title"] or "",
            state=issue["state"] or "",
            assignee=issue["assignee_name"] or UNASSIGNED,
            author=issue["author_name"] or UNASSIGNED,
            milestone=issue["milestone"] or NO_MILESTONE,
            web_url=issue["web_url"] or "",
            created_at=parse_ts(issue["created_at"]),
            closed_at=parse_ts(issue["closed_at"]),
        )
        issue_events = by_issue.get(issue["iid"], [])
        tl.intervals = _intervals(issue_events, tracked, hidden, tl.closed_at)
        if positions:
            # so da para arbitrar sobreposicao quem conhece a ordem do board;
            # sem board sincronizado o certo e nao inventar um vencedor
            tl.intervals = _resolve_overlaps(tl.intervals, positions, now)
        tl.tags = _open_tags(issue_events, tracked)
        timelines.append(tl)
    return timelines


def _open_tags(events: list[Any], tracked: set[str] | None) -> list[str]:
    """Etiquetas que nao sao coluna e continuam aplicadas na issue."""
    if tracked is None:
        return []
    applied: dict[str, bool] = {}
    for ev in events:
        label = ev["label_name"]
        if not label or label.lower() in tracked:
            continue
        applied[label] = ev["action"] == "add"
    return sorted(name for name, on in applied.items() if on)


def _resolve_overlaps(
    intervals: list[Interval], positions: dict[str, int], now: datetime
) -> list[Interval]:
    """Da a cada instante uma coluna so.

    Nada impede um card de carregar 'Doing' e 'Review' ao mesmo tempo. Somar
    os dois periodos contaria a mesma hora duas vezes, e o total da pessoa
    passaria do tempo de relogio. Aqui cada instante fica com a coluna mais
    avancada do board — a mesma que o GitLab mostra no card.
    """
    if len(intervals) < 2:
        return intervals

    edges = sorted({t for i in intervals for t in (i.start, i.end or now)})
    pieces: list[tuple[Interval, datetime, datetime]] = []
    for left, right in zip(edges, edges[1:]):
        active = [i for i in intervals if i.start <= left and (i.end or now) >= right]
        if not active:
            continue
        winner = max(active, key=lambda i: (positions.get(i.label.lower(), -1), i.start))
        pieces.append((winner, left, right))

    out: list[Interval] = []
    for winner, left, right in pieces:
        if out and out[-1].label == winner.label and out[-1].end == left:
            out[-1].end = right
        else:
            out.append(Interval(winner.label, left, right, winner.moved_by))
    # o ultimo pedaco herda a "abertura" de quem ainda esta na coluna
    if out and out[-1].end == now:
        still_open = any(i.end is None and i.label == out[-1].label for i in intervals)
        if still_open:
            out[-1].end = None
    return out


def _intervals(
    events: list[Any],
    tracked: set[str] | None,
    hidden: set[str],
    closed_at: datetime | None,
) -> list[Interval]:
    """Casa cada `add` com o `remove` seguinte da mesma label."""
    open_by_label: dict[str, tuple[datetime, str | None]] = {}
    out: list[Interval] = []

    for ev in events:
        label = ev["label_name"]
        if not label or label.lower() in hidden:
            continue
        if tracked is not None and label.lower() not in tracked:
            continue
        ts = parse_ts(ev["created_at"])
        if ts is None:
            continue
        if ev["action"] == "add":
            # add duplicado sem remove: mantem o primeiro, que e o inicio real
            open_by_label.setdefault(label, (ts, ev["user_name"]))
        elif ev["action"] == "remove":
            start = open_by_label.pop(label, None)
            if start is None:
                continue  # remove sem add correspondente (label anterior ao rastreio)
            out.append(Interval(label, start[0], ts, start[1]))

    # labels ainda aplicadas: contam ate o fechamento da issue ou ate agora
    for label, (start, user) in open_by_label.items():
        out.append(Interval(label, start, closed_at, user))

    out.sort(key=lambda i: i.start)
    return out


def format_duration(seconds: float) -> str:
    total = int(seconds)
    days, rest = divmod(total, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def issue_participants(
    timeline: "IssueTimeline",
    queues: set[str],
    persons: dict[str, str],
    now: datetime,
) -> list[dict[str, Any]]:
    """Quem passou quanto tempo nesta issue, coluna por coluna.

    E o unico lugar onde o SCOPE nao se aplica: numa issue interessa quem fez
    cada etapa, inclusive o revisor de fora — cujo tempo, no modo `assigned`,
    nao entra no total individual dele em lugar nenhum. Aqui ele aparece.
    """
    people: dict[str, dict[str, Any]] = {}

    def bucket(name: str) -> dict[str, Any]:
        return people.setdefault(name, {
            "person": name,
            "seconds": 0.0,
            "by_column": defaultdict(lambda: {"seconds": 0.0, "stints": 0, "open": False}),
            "waiting_seconds": 0.0,
            "last_touch": None,
        })

    for itv in timeline.intervals:
        if itv.label.lower() in queues:
            continue          # fila: o card espera, ninguem trabalha
        person = bucket(interval_owner(itv, timeline))
        seconds = itv.seconds(now)
        column = person["by_column"][itv.label]
        column["seconds"] += seconds
        column["stints"] += 1
        column["open"] = column["open"] or not itv.closed
        person["seconds"] += seconds
        end = itv.end or now
        if person["last_touch"] is None or end > person["last_touch"]:
            person["last_touch"] = end

    # a espera na fila nao e tempo de trabalho, mas e desta pessoa
    for debtor, itv in queue_debts(timeline, queues, persons):
        bucket(debtor)["waiting_seconds"] += itv.seconds(now)

    total = sum(p["seconds"] for p in people.values())
    rows = []
    for person in people.values():
        if not person["seconds"] and not person["waiting_seconds"]:
            continue
        rows.append({
            "person": person["person"],
            "hours": round(person["seconds"] / 3600, 2),
            "human": format_duration(person["seconds"]),
            "share": round(person["seconds"] / total * 100, 1) if total else 0.0,
            "waiting_hours": round(person["waiting_seconds"] / 3600, 2),
            "waiting_human": format_duration(person["waiting_seconds"]),
            "last_touch": person["last_touch"].isoformat() if person["last_touch"] else None,
            "by_column": {
                label: {
                    "hours": round(data["seconds"] / 3600, 2),
                    "human": format_duration(data["seconds"]),
                    "stints": data["stints"],
                    "still_in_column": data["open"],
                }
                for label, data in sorted(
                    person["by_column"].items(), key=lambda x: -x[1]["seconds"]
                )
            },
        })
    rows.sort(key=lambda r: (r["hours"], r["waiting_hours"]), reverse=True)
    return rows


def contributor_report(
    project_id: int,
    labels: Iterable[str] | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    state: str | None = None,
    milestone: str | None = None,
) -> dict[str, Any]:
    """Tempo por contribuidor x coluna, com totais e medias."""
    now = datetime.now(timezone.utc)
    until = until or now
    label_filter = list(labels) if labels else None
    timelines = build_timelines(project_id, label_filter, state, milestone)
    label_order = label_filter or board_labels(project_id)

    per_person: dict[str, dict[str, Any]] = {}
    per_label_totals: dict[str, float] = defaultdict(float)

    queues = queue_labels()
    persons = person_labels(project_id) if queues else {}
    review = review_label(project_id, label_filter)

    def blank(owner: str) -> dict[str, Any]:
        return {
            "contributor": owner,
            "issues": set(),
            "closed_issues": set(),
            "by_label": defaultdict(lambda: {"seconds": 0.0, "issues": set(), "open": 0}),
            "total_seconds": 0.0,
            "waiting_seconds": 0.0,
            "waiting_issues": set(),
            "review_seconds": 0.0,
            "review_issues": set(),
        }

    for tl in timelines:
        touched: set[str] = set()

        # fila: a espera e demerito de quem deveria ter pego o card
        for debtor, itv in queue_debts(tl, queues, persons):
            start = max(itv.start, since) if since else itv.start
            end = min(itv.end or now, until)
            if end <= start:
                continue
            person = per_person.setdefault(debtor, blank(debtor))
            person["waiting_seconds"] += elapsed(start, end)
            person["waiting_issues"].add(tl.iid)

        for itv in tl.intervals:
            if itv.label.lower() in queues:
                continue          # fila: o card espera, ninguem trabalha
            start = max(itv.start, since) if since else itv.start
            end = min(itv.end or now, until)
            if end <= start:
                continue
            seconds = elapsed(start, end)

            # cada etapa vai para quem a fez, nao para o dono da issue
            # o total por coluna e do projeto e nao depende do escopo
            per_label_totals[itv.label] += seconds

            # revisao e contabilizada a parte, fora do SCOPE: quem revisa
            # trabalha no card de outra pessoa, e some se seguir a regra
            if review and itv.label == review:
                reviewer = interval_owner(itv, tl)
                person = per_person.setdefault(reviewer, blank(reviewer))
                person["review_seconds"] += seconds
                person["review_issues"].add(tl.iid)
                touched.add(reviewer)

            if not counts_for_person(itv, tl, review):
                continue

            owner = interval_owner(itv, tl)
            person = per_person.setdefault(owner, blank(owner))
            bucket = person["by_label"][itv.label]
            bucket["seconds"] += seconds
            bucket["issues"].add(tl.iid)
            if not itv.closed:
                bucket["open"] += 1
            person["total_seconds"] += seconds
            touched.add(owner)

        for owner in touched:
            per_person[owner]["issues"].add(tl.iid)
            if tl.state == "closed":
                per_person[owner]["closed_issues"].add(tl.iid)

    rows = []
    for person in per_person.values():
        by_label = {}
        for label, data in person["by_label"].items():
            count = len(data["issues"])
            by_label[label] = {
                "seconds": round(data["seconds"], 1),
                "hours": round(data["seconds"] / 3600, 2),
                "human": format_duration(data["seconds"]),
                "issues": count,
                "still_in_column": data["open"],
                "avg_hours_per_issue": round(data["seconds"] / 3600 / count, 2) if count else 0,
            }
        if not by_label and not person["waiting_seconds"] and not person["review_seconds"]:
            continue
        rows.append(
            {
                "contributor": person["contributor"],
                "review_seconds": round(person["review_seconds"], 1),
                "review_hours": round(person["review_seconds"] / 3600, 2),
                "review_human": format_duration(person["review_seconds"]),
                "review_issues": len(person["review_issues"]),
                "waiting_seconds": round(person["waiting_seconds"], 1),
                "waiting_hours": round(person["waiting_seconds"] / 3600, 2),
                "waiting_human": format_duration(person["waiting_seconds"]),
                "waiting_issues": len(person["waiting_issues"]),
                "total_seconds": round(person["total_seconds"], 1),
                "total_hours": round(person["total_seconds"] / 3600, 2),
                "total_human": format_duration(person["total_seconds"]),
                "issues": len(person["issues"]),
                "closed_issues": len(person["closed_issues"]),
                "by_label": by_label,
            }
        )
    rows.sort(key=lambda r: r["total_seconds"], reverse=True)

    columns = [name for name in label_order if name in per_label_totals]
    columns += [name for name in per_label_totals if name not in columns]

    return {
        "project_id": project_id,
        "columns": columns,
        "review_label": review,
        "milestone": milestone,
        "window": {"since": since.isoformat() if since else None, "until": until.isoformat()},
        "totals": {
            label: {"hours": round(sec / 3600, 2), "human": format_duration(sec)}
            for label, sec in sorted(per_label_totals.items(), key=lambda x: -x[1])
        },
        "contributors": rows,
    }


def column_report(
    project_id: int,
    labels: Iterable[str] | None = None,
    milestone: str | None = None,
) -> dict[str, Any]:
    """Media/mediana de permanencia por coluna - util para achar gargalo."""
    now = datetime.now(timezone.utc)
    timelines = build_timelines(project_id, list(labels) if labels else None, None, milestone)
    samples: dict[str, list[float]] = defaultdict(list)
    wip: dict[str, int] = defaultdict(int)

    for tl in timelines:
        for itv in tl.intervals:
            if itv.closed:
                samples[itv.label].append(itv.seconds(now))
            else:
                wip[itv.label] += 1

    def median(values: list[float]) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        mid = len(ordered) // 2
        return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2

    keys = [name for name in board_labels(project_id) if name in samples or name in wip]
    keys += [name for name in list(samples) + list(wip) if name not in keys]

    columns = []
    for label in keys:
        values = samples[label]
        avg = sum(values) / len(values) if values else 0.0
        columns.append(
            {
                "label": label,
                "completed_passes": len(values),
                "wip": wip[label],
                "avg_hours": round(avg / 3600, 2),
                "median_hours": round(median(values) / 3600, 2),
                "max_hours": round(max(values) / 3600, 2) if values else 0,
                "avg_human": format_duration(avg) if values else "-",
            }
        )
    return {"project_id": project_id, "milestone": milestone, "columns": columns}


def issue_report(
    project_id: int,
    labels: Iterable[str] | None = None,
    state: str | None = None,
    milestone: str | None = None,
    sort: str = "focus",
) -> dict[str, Any]:
    """Detalhe por issue - o drill-down de qualquer numero agregado acima.

    Por padrao ranqueia pelo tempo na coluna de trabalho (`focus`), nao pelo
    lead time: uma issue parada meses no Backlog nao e uma issue "demorada",
    e o Backlog nem entra na conta.
    """
    now = datetime.now(timezone.utc)
    focus = focus_label(project_id, labels)
    queues = queue_labels()
    persons = person_labels(project_id) if queues else {}
    out = []
    for tl in build_timelines(project_id, list(labels) if labels else None, state, milestone):
        if not tl.intervals:
            continue
        by_label: dict[str, float] = defaultdict(float)
        for itv in tl.intervals:
            by_label[itv.label] += itv.seconds(now)
        lead = elapsed(tl.created_at, tl.closed_at or now)
        focus_seconds = by_label.get(focus, 0.0) if focus else 0.0
        out.append(
            {
                "iid": tl.iid,
                "title": tl.title,
                "state": tl.state,
                "assignee": tl.assignee,
                "author": tl.author,
                "milestone": tl.milestone,
                "tags": tl.tags,
                "web_url": tl.web_url,
                "lead_time_hours": round(lead / 3600, 2),
                "focus_hours": round(focus_seconds / 3600, 2),
                "working_hours": round(sum(by_label.values()) / 3600, 2),
                "current_column": next(
                    (i.label for i in reversed(tl.intervals) if not i.closed), None
                ),
                "time_by_column": {
                    label: {"hours": round(sec / 3600, 2), "human": format_duration(sec)}
                    for label, sec in sorted(by_label.items(), key=lambda x: -x[1])
                },
                "participants": issue_participants(tl, queues, persons, now),
                "transitions": [
                    {
                        "label": i.label,
                        "start": i.start.isoformat(),
                        "end": i.end.isoformat() if i.end else None,
                        "hours": round(i.seconds(now) / 3600, 2),
                        "moved_by": i.moved_by,
                        "owner": interval_owner(i, tl),
                        "queue": i.label.lower() in queues,
                    }
                    for i in tl.intervals
                ],
            }
        )
    key = "lead_time_hours" if sort == "lead_time" else (
        "working_hours" if sort == "working" or not focus else "focus_hours"
    )
    out.sort(key=lambda i: i[key], reverse=True)
    return {"focus_label": focus, "sorted_by": key, "issues": out}


def milestones(project_id: int) -> list[dict[str, Any]]:
    """Sprints do cache, com contagem de issues. Ordena pela data de termino
    (mais recente primeiro); milestones sem data caem no fim."""
    with session() as conn:
        rows = conn.execute(
            "SELECT m.title, m.state, m.start_date, m.due_date, m.web_url,"
            " (SELECT COUNT(*) FROM issues i"
            "    WHERE i.project_id = m.project_id AND i.milestone = m.title) AS issues"
            " FROM milestones m WHERE m.project_id = ?",
            (project_id,),
        ).fetchall()
        orphans = conn.execute(
            "SELECT COUNT(*) AS n FROM issues"
            " WHERE project_id = ? AND (milestone IS NULL OR milestone = '')",
            (project_id,),
        ).fetchone()["n"]
        # issues podem apontar para uma milestone que nao veio no sync
        # (deletada, ou de um grupo fora do alcance do token)
        unknown = conn.execute(
            "SELECT i.milestone AS title, COUNT(*) AS issues FROM issues i"
            " WHERE i.project_id = ? AND i.milestone IS NOT NULL AND i.milestone <> ''"
            "   AND i.milestone NOT IN (SELECT title FROM milestones WHERE project_id = ?)"
            " GROUP BY i.milestone",
            (project_id, project_id),
        ).fetchall()

    out = [dict(r) for r in rows]
    out += [
        {"title": r["title"], "state": None, "start_date": None, "due_date": None,
         "web_url": None, "issues": r["issues"]}
        for r in unknown
    ]
    out.sort(key=lambda m: (m["due_date"] or "", m["title"]), reverse=True)
    if orphans:
        out.append({"title": NO_MILESTONE, "state": None, "start_date": None,
                    "due_date": None, "web_url": None, "issues": orphans})
    return out


def milestone_report(
    project_id: int,
    labels: Iterable[str] | None = None,
    limit: int = 8,
) -> dict[str, Any]:
    """Uma linha por sprint: tempo por coluna, throughput e lead time.

    A janela de tempo nao se aplica aqui de proposito — comparar sprints so
    faz sentido sobre a duracao inteira de cada uma.
    """
    now = datetime.now(timezone.utc)
    label_filter = list(labels) if labels else None
    timelines = build_timelines(project_id, label_filter)
    label_order = label_filter or board_labels(project_id)

    known = {m["title"]: m for m in milestones(project_id)}
    queues = queue_labels()
    buckets: dict[str, dict[str, Any]] = {}
    per_label_totals: dict[str, float] = defaultdict(float)

    for tl in timelines:
        bucket = buckets.setdefault(tl.milestone, {
            "milestone": tl.milestone,
            "by_label": defaultdict(float),
            "total_seconds": 0.0,
            "issues": 0,
            "closed": 0,
            "lead_seconds": [],
            "contributors": set(),
        })
        bucket["issues"] += 1
        if tl.state == "closed":
            bucket["closed"] += 1
        for itv in tl.intervals:
            if itv.label.lower() in queues or not counts_for_person(itv, tl):
                continue
            owner = interval_owner(itv, tl)
            if owner != UNASSIGNED:
                bucket["contributors"].add(owner)
        if tl.created_at:
            bucket["lead_seconds"].append(elapsed(tl.created_at, tl.closed_at or now))
        for itv in tl.intervals:
            seconds = itv.seconds(now)
            bucket["by_label"][itv.label] += seconds
            bucket["total_seconds"] += seconds
            per_label_totals[itv.label] += seconds

    rows = []
    for bucket in buckets.values():
        meta = known.get(bucket["milestone"], {})
        leads = bucket["lead_seconds"]
        rows.append({
            "milestone": bucket["milestone"],
            "state": meta.get("state"),
            "start_date": meta.get("start_date"),
            "due_date": meta.get("due_date"),
            "web_url": meta.get("web_url"),
            "issues": bucket["issues"],
            "closed_issues": bucket["closed"],
            "completion": round(bucket["closed"] / bucket["issues"] * 100, 1)
            if bucket["issues"] else 0.0,
            "contributors": len(bucket["contributors"]),
            "total_hours": round(bucket["total_seconds"] / 3600, 2),
            "total_human": format_duration(bucket["total_seconds"]),
            "avg_lead_hours": round(sum(leads) / len(leads) / 3600, 2) if leads else 0.0,
            "by_label": {
                label: {
                    "hours": round(sec / 3600, 2),
                    "human": format_duration(sec),
                }
                for label, sec in sorted(bucket["by_label"].items(), key=lambda x: -x[1])
            },
        })

    # sprint sem data vai para o fim; entre as datadas, a mais recente primeiro
    rows.sort(key=lambda r: (r["due_date"] or "", r["milestone"]), reverse=True)
    rows.sort(key=lambda r: r["milestone"] == NO_MILESTONE)

    columns = [name for name in label_order if name in per_label_totals]
    columns += [name for name in per_label_totals if name not in columns]

    return {"project_id": project_id, "columns": columns, "milestones": rows[:limit]}


def contributor_detail(
    project_id: int,
    name: str,
    labels: Iterable[str] | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    milestone: str | None = None,
) -> dict[str, Any]:
    """Perfil de uma pessoa: tempo por coluna, por sprint e as issues dela.

    A janela (`since`/`until`) recorta os intervalos como no relatorio geral,
    mas a lista de issues e o lead time olham o historico inteiro — senao uma
    issue longa desapareceria do perfil de quem a tocou.
    """
    now = datetime.now(timezone.utc)
    until = until or now
    label_filter = list(labels) if labels else None
    focus = focus_label(project_id, label_filter)
    # a pessoa entra no perfil por ter feito alguma etapa, nao por ser a
    # responsavel: quem so revisou tambem tem tempo aqui
    queues = queue_labels()
    persons = person_labels(project_id) if queues else {}
    review = review_label(project_id, label_filter)
    timelines = build_timelines(project_id, label_filter, None, milestone)
    # entra no perfil quem fez alguma etapa, quem revisou, ou quem deixou
    # algum card esperando
    mine = [t for t in timelines
            if any(interval_owner(i, t) == name and i.label.lower() not in queues
                   and counts_for_person(i, t, review) for i in t.intervals)
            or any(d == name for d, _ in queue_debts(t, queues, persons))]
    waiting_total = 0.0
    waiting_issues: set[int] = set()
    review_total = 0.0
    review_issues: set[int] = set()

    by_label: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"seconds": 0.0, "issues": set(), "open": 0}
    )
    by_sprint: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"seconds": 0.0, "issues": 0, "closed": 0}
    )
    total = 0.0
    leads: list[float] = []
    issues: list[dict[str, Any]] = []
    wip = 0

    for tl in mine:
        owned = [i for i in tl.intervals
                 if interval_owner(i, tl) == name and i.label.lower() not in queues
                 and counts_for_person(i, tl, review)]
        review_seconds = sum(i.seconds(now) for i in owned if i.label == review)
        if review_seconds:
            review_total += review_seconds
            review_issues.add(tl.iid)
        debt = sum(itv.seconds(now) for debtor, itv in queue_debts(tl, queues, persons)
                   if debtor == name)
        waiting_total += debt
        if debt:
            waiting_issues.add(tl.iid)
        windowed = 0.0
        for itv in owned:
            start = max(itv.start, since) if since else itv.start
            end = min(itv.end or now, until)
            if end <= start:
                continue
            seconds = elapsed(start, end)
            bucket = by_label[itv.label]
            bucket["seconds"] += seconds
            bucket["issues"].add(tl.iid)
            if not itv.closed:
                bucket["open"] += 1
            windowed += seconds
        total += windowed

        sprint = by_sprint[tl.milestone]
        sprint["seconds"] += windowed
        sprint["issues"] += 1
        if tl.state == "closed":
            sprint["closed"] += 1

        # o detalhe da issue mostra so o que e desta pessoa
        full: dict[str, float] = defaultdict(float)
        for itv in owned:
            full[itv.label] += itv.seconds(now)
        stints = sorted(owned, key=lambda i: i.start)
        current = next((i.label for i in reversed(stints) if not i.closed), None)
        if current:
            wip += 1
        if tl.created_at:
            leads.append(elapsed(tl.created_at, tl.closed_at or now))

        issues.append({
            "iid": tl.iid,
            "title": tl.title,
            "state": tl.state,
            "milestone": tl.milestone,
            "tags": tl.tags,
            "web_url": tl.web_url,
            "current_column": current,
            "focus_hours": round(full.get(focus, 0.0) / 3600, 2) if focus else 0.0,
            "working_hours": round(sum(full.values()) / 3600, 2),
            "lead_time_hours": round(elapsed(tl.created_at, tl.closed_at or now) / 3600, 2),
            "time_by_column": {
                label: {"hours": round(sec / 3600, 2), "human": format_duration(sec)}
                for label, sec in sorted(full.items(), key=lambda x: -x[1])
            },
            "role": sorted({i.label for i in stints}),
            "review_hours": round(review_seconds / 3600, 2),
            "review_human": format_duration(review_seconds),
            "waiting_hours": round(debt / 3600, 2),
            "transitions": [
                {
                    "label": i.label,
                    "start": i.start.isoformat(),
                    "end": i.end.isoformat() if i.end else None,
                    "hours": round(i.seconds(now) / 3600, 2),
                    "moved_by": i.moved_by,
                }
                for i in stints
            ],
        })

    # ranqueia pelo que foi mais longo para esta pessoa: a revisao nao pode
    # ficar sempre no fim so por nao ser a coluna de foco
    issues.sort(
        key=lambda i: (max(i["focus_hours"], i["review_hours"]), i["working_hours"]),
        reverse=True,
    )
    closed = sum(1 for t in mine if t.state == "closed")
    order = board_labels(project_id)
    columns = [c for c in order if c in by_label] + [c for c in by_label if c not in order]

    sprints = [
        {
            "milestone": title,
            "hours": round(data["seconds"] / 3600, 2),
            "human": format_duration(data["seconds"]),
            "issues": data["issues"],
            "closed_issues": data["closed"],
            "completion": round(data["closed"] / data["issues"] * 100, 1) if data["issues"] else 0.0,
        }
        for title, data in by_sprint.items()
    ]
    known = {m["title"]: (m["due_date"] or "") for m in milestones(project_id)}
    sprints.sort(key=lambda s: (known.get(s["milestone"], ""), s["milestone"]), reverse=True)
    sprints.sort(key=lambda s: s["milestone"] == NO_MILESTONE)

    return {
        "project_id": project_id,
        "contributor": name,
        "focus_label": focus,
        "review_label": review,
        "review_hours": round(review_total / 3600, 2),
        "review_human": format_duration(review_total),
        "review_issues": len(review_issues),
        "columns": columns,
        "total_hours": round(total / 3600, 2),
        "total_human": format_duration(total),
        "issues_count": len(mine),
        "closed_issues": closed,
        "open_issues": len(mine) - closed,
        "wip": wip,
        "avg_lead_hours": round(sum(leads) / len(leads) / 3600, 2) if leads else 0.0,
        "waiting_hours": round(waiting_total / 3600, 2),
        "waiting_human": format_duration(waiting_total),
        "waiting_issues": len(waiting_issues),
        "focus_hours": round(by_label.get(focus, {}).get("seconds", 0.0) / 3600, 2)
        if focus else 0.0,
        "by_label": {
            label: {
                "hours": round(data["seconds"] / 3600, 2),
                "human": format_duration(data["seconds"]),
                "issues": len(data["issues"]),
                "still_in_column": data["open"],
            }
            for label, data in by_label.items()
        },
        "by_milestone": sprints,
        "issues": issues,
    }
