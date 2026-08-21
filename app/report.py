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


def _convention_by_person(
    project_id: int, commits: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Aderencia a convencao de cada pessoa dentro deste recorte de commits.

    Agrupa por membro do GitLab, nao por assinatura de git: quem commita como
    `lucas.delmirio` e como "Lucas Delmirio da Silva" e uma pessoa so, e o
    resumo e por membro do time.
    """
    with session() as conn:
        members = [r[0] for r in conn.execute(
            "SELECT DISTINCT user_name FROM label_events"
            " WHERE project_id = ? AND user_name IS NOT NULL", (project_id,))]

    por_pessoa: dict[str, dict[str, Any]] = {}
    for c in commits:
        autor = (c["author"] or "?").strip()
        nome = commit_metrics.match_member(autor, members) or autor
        bucket = por_pessoa.setdefault(nome, {
            "person": nome, "member": nome in members,
            "commits": 0, "ok": 0, "reasons": defaultdict(int),
        })
        bucket["commits"] += 1
        if not c["convention"]:
            bucket["ok"] += 1
        for motivo in c["convention"]:
            bucket["reasons"][motivo] += 1

    linhas = [
        {
            **b,
            "off": b["commits"] - b["ok"],
            "pct": round(b["ok"] / b["commits"] * 100, 1) if b["commits"] else 0.0,
            "reasons": dict(sorted(b["reasons"].items(), key=lambda x: -x[1])),
        }
        for b in por_pessoa.values()
    ]
    linhas.sort(key=lambda r: (r["pct"], -r["commits"]))
    return linhas


def sprint_summary(
    project_id: int,
    milestone: str,
    labels: Iterable[str] | None = None,
    issue_limit: int = 10,
    offender_limit: int = 12,
) -> dict[str, Any]:
    """Uma sprint por inteiro: numeros, gargalo, pessoas, issues e commits.

    O comparativo responde "estamos melhorando?"; este responde "o que
    aconteceu nesta sprint?". Por isso aqui nada e cortado no topo — entram
    todas as pessoas, e nao so as seis primeiras.
    """
    label_filter = list(labels) if labels else None
    base = metrics.milestone_report(project_id, label_filter, limit=50)
    linhas = [r for r in base["milestones"] if r["milestone"] != metrics.NO_MILESTONE]
    atual = next((r for r in linhas if r["milestone"] == milestone), None)
    if atual is None:
        return {}

    # a sprint imediatamente anterior, para o resumo tambem situar o numero
    indice = linhas.index(atual)
    anterior = linhas[indice + 1] if indice + 1 < len(linhas) else None

    focus = metrics.focus_label(project_id, label_filter)
    review = metrics.review_label(project_id, label_filter)
    people = metrics.contributor_report(project_id, label_filter, milestone=milestone)
    colunas = metrics.column_report(project_id, label_filter, milestone=milestone)
    issues = metrics.issue_report(project_id, label_filter, milestone=milestone)
    commits = _sprint_commits(project_id, milestone)
    por_pessoa = _convention_by_person(project_id, commits)
    fora = [c for c in commits if c["convention"]]
    pct = round((len(commits) - len(fora)) / len(commits) * 100, 1) if commits else None

    anterior_commits = _sprint_commits(project_id, anterior["milestone"]) if anterior else []
    anterior_pct = (
        round(sum(1 for c in anterior_commits if not c["convention"])
              / len(anterior_commits) * 100, 1)
        if anterior_commits else None
    )

    def em_foco(linha: dict[str, Any]) -> float:
        return linha["by_label"].get(focus, {}).get("hours", 0.0) if focus else 0.0

    delta = {} if not anterior else {
        "total_hours": _pct_delta(atual["total_hours"], anterior["total_hours"]),
        "focus_hours": _pct_delta(em_foco(atual), em_foco(anterior)),
        "closed_issues": _pct_delta(atual["closed_issues"], anterior["closed_issues"]),
        "avg_lead_hours": _pct_delta(atual["avg_lead_hours"], anterior["avg_lead_hours"]),
        "commits": _pct_delta(len(commits), len(anterior_commits)),
        "completion_pp": round(atual["completion"] - anterior["completion"], 1),
        "convention_pp": round(pct - anterior_pct, 1)
        if pct is not None and anterior_pct is not None else None,
    }

    with session() as conn:
        project = conn.execute(
            "SELECT path, name, web_url, synced_at FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        meta = conn.execute(
            "SELECT web_url FROM milestones WHERE project_id = ? AND title = ?",
            (project_id, milestone),
        ).fetchone()

    return {
        "project_id": project_id,
        "project": dict(project) if project else {},
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "focus_label": focus,
        "review_label": review,
        "milestone": {**atual, "web_url": (meta["web_url"] if meta else None)},
        "compared_to": anterior["milestone"] if anterior else None,
        "delta": delta,
        "columns": colunas["columns"],
        # a ordem do board define a cor de cada coluna, igual a tela: quem ve
        # o PDF depois do dash reconhece a coluna pela cor
        "columns_order": base["columns"],
        "people": [
            {**p, "convention": next(
                (c for c in por_pessoa if c["person"] == p["contributor"]), None
            )}
            for p in people["contributors"]
        ],
        # quem commitou na sprint mas nao aparece na tabela de tempo (nao moveu
        # card nenhum) — some se a juncao for so pela esquerda
        "commit_only": [
            c for c in por_pessoa
            if not any(p["contributor"] == c["person"] for p in people["contributors"])
        ],
        "waiting_label": ", ".join(get_settings().queue_list),
        "issues": issues["issues"][:issue_limit],
        "issues_total": len(issues["issues"]),
        "commits": {
            "total": len(commits),
            "ok": len(commits) - len(fora),
            "off": len(fora),
            "pct": pct,
            "by_person": por_pessoa,
            "outsiders": _outsiders(project_id),
            "rule": commit_metrics.CONVENTION_RULE,
            "reason_labels": commit_metrics.REASON_LABELS,
            "offenders": fora[:offender_limit],
        },
    }


# ── exportacao ───────────────────────────────────────────────────────────
#
# HTML de arquivo unico, sem CSS externo e sem script: e feito para virar
# anexo de entrega e para imprimir em PDF pelo proprio navegador.

_CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { margin: 0; padding: 40px 32px 64px; background: #f4f4f5; color: #0f0f10;
  font: 14px/1.5 ui-sans-serif, system-ui, "Segoe UI", Roboto, sans-serif; }
main { max-width: 1000px; margin: 0 auto; }
h1 { font-size: 26px; letter-spacing: -.03em; margin: 0 0 6px; }
h2 { font-size: 17px; letter-spacing: -.02em; margin: 32px 0 10px; }
p.sub { color: #52525b; margin: 0 0 4px; font-size: 13px; }
section { background: #fff; border: 1px solid #ececef; border-radius: 14px;
  padding: 18px 20px; margin-top: 16px; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { text-align: left; padding: 9px 10px; border-bottom: 1px solid #ececef;
  vertical-align: top; }
th { color: #52525b; font-weight: 500; font-size: 12px; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
tbody tr:last-child td { border-bottom: 0; }
.delta { font-size: 11.5px; display: block; }
.up { color: #15803d; } .down { color: #dc2626; } .flat { color: #a1a1aa; }
.bar { height: 7px; border-radius: 99px; background: #f1f1f4; overflow: hidden;
  margin-top: 5px; }
.bar i { display: block; height: 100%; background: #7c3aed; border-radius: 99px; }
.bar i.warn { background: #b45309; } .bar i.good { background: #15803d; }
.pill { display: inline-block; padding: 2px 8px; border-radius: 99px;
  border: 1px solid #e0e0e4; font-size: 11.5px; color: #52525b; margin-right: 4px; }
.pill.off { color: #b45309; border-color: #e8c9a0; background: #fdf6ec; }
.dot { display: inline-block; width: 9px; height: 9px; border-radius: 50%;
  margin-right: 7px; vertical-align: -1px; }
.tag { display: inline-flex; align-items: center; padding: 2px 9px 2px 7px;
  border-radius: 99px; border: 1px solid #ececef; font-size: 11.5px; }
h2 { border-left: 3px solid #7c3aed; padding-left: 10px; }
.num b { font-weight: 600; }
.muted { color: #a1a1aa; }
code { background: #f4f4f5; padding: 1px 5px; border-radius: 5px; font-size: 12px; }
footer { color: #a1a1aa; font-size: 12px; margin-top: 28px; text-align: center; }
@media print {
  @page { size: A4 portrait; margin: 14mm 12mm; }
  /* sem isto o Chrome imprime as barras e os deltas em cinza */
  * { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
  body { background: #fff; padding: 0; font-size: 11.5px; }
  main { max-width: none; }
  h1 { font-size: 20px; }
  h2 { font-size: 14px; margin: 0 0 8px; }
  section { break-inside: auto; border-radius: 0; border: 0; padding: 0;
    margin-top: 18px; border-top: 1px solid #ddd; padding-top: 12px; }
  section:first-of-type { border-top: 0; padding-top: 0; }
  table { font-size: 10.5px; }
  th, td { padding: 5px 6px; }
  /* tabela que atravessa a quebra continua com cabecalho na pagina seguinte */
  thead { display: table-header-group; }
  tr { break-inside: avoid; }
  .no-print { display: none !important; }
}
"""


# As mesmas seis cores de serie da tela, na mesma ordem: quem olha o PDF
# depois de olhar o dash reconhece a coluna pela cor.
SERIES = ("#7c3aed", "#8cae00", "#0e9bd6", "#b45309", "#a78bfa", "#db2777")
NEUTRA = "#a1a1aa"


def _palette(columns: list[str]) -> dict[str, str]:
    return {name: SERIES[i] if i < len(SERIES) else NEUTRA for i, name in enumerate(columns)}


def _rate_color(pct: float | None) -> str:
    """Verde a partir de 80%, vermelho abaixo de 50%. Os cortes sao os mesmos
    da tela — servem para varrer com o olho, nao para dar nota."""
    if pct is None:
        return NEUTRA
    return "#15803d" if pct >= 80 else "#dc2626" if pct < 50 else "#b45309"


def _bar(fraction: float, color: str) -> str:
    largura = max(0.0, min(fraction, 1.0)) * 100
    return f'<div class="bar"><i style="width:{largura:.0f}%;background:{color}"></i></div>'


def _swatch(color: str) -> str:
    return f'<i class="dot" style="background:{color}"></i>'


def _fmt_h(hours: float | None) -> str:
    if not hours:
        return "0h"
    if hours < 1:
        return f"{round(hours * 60)}m"
    if hours < 48:
        return f"{hours:.1f}h" if hours < 10 else f"{hours:.0f}h"
    return f"{int(hours // 24)}d {round(hours % 24)}h"


def _periodo(sprint: dict[str, Any]) -> str:
    """Datas da sprint — nem todo time preenche as datas da milestone."""
    inicio, fim = sprint.get("start_date"), sprint.get("due_date")
    if inicio and fim:
        return f"{inicio} — {fim}"
    if fim:
        return f"entrega {fim}"
    if inicio:
        return f"inicio {inicio}"
    return "encerrada" if sprint.get("state") == "closed" else "em andamento"


def _delta_html(value: float | None, unit: str = "%", lower_is_better: bool = False) -> str:
    if value is None:
        return '<span class="delta flat">—</span>'
    if abs(value) < 0.05:
        return '<span class="delta flat">estavel</span>'
    good = (value < 0) if lower_is_better else (value > 0)
    sinal = "+" if value > 0 else ""
    return (f'<span class="delta {"up" if good else "down"}">'
            f'{sinal}{value:g}{unit}</span>')


_SUMMARY_CSS = """
.kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 10px; }
.kpi { border-radius: 10px; padding: 12px 14px; border: 1px solid #ececef;
  border-left-width: 3px; }
.kpi-label { color: #52525b; font-size: 12px; margin-bottom: 5px; }
.kpi-value { font-size: 22px; font-weight: 600; letter-spacing: -.03em; }
@media print { .kpis { grid-template-columns: repeat(6, 1fr); gap: 6px; }
  .kpi { padding: 8px 10px; }
  .kpi-value { font-size: 15px; } }
"""

_PRINT_JS = """
<script>
  // a caixa de impressao do navegador e o caminho para o PDF: nao exige
  // dependencia nenhuma no servidor e sai igual ao que se ve na tela
  addEventListener("load", () => setTimeout(() => window.print(), 250));
</script>"""

_PRINT_BAR = """
<p class="sub no-print" style="margin-bottom:14px">
  A caixa de impressao vai abrir sozinha — escolha <b>Salvar como PDF</b> em
  "Destino". Se ela nao abrir, use Ctrl+P.</p>"""


def _outsiders_html(nota: dict[str, Any] | None) -> str:
    """A frase que declara o que ficou de fora. Excluir calado seria pior que
    incluir: quem le tem de saber que 18 commits nao entraram na conta."""
    if not nota or not nota["commits"]:
        return ""
    quem = ", ".join(f"{escape(a['author'])} ({a['commits']})" for a in nota["authors"])
    return (f" Fora da conta: {nota['commits']} commits de quem nao e do time"
            f" — {quem}.")


