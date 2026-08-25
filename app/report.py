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
        "skip_weekends": metrics.skip_weekends(),
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
        "skip_weekends": metrics.skip_weekends(),
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


# O icone vai embutido: o relatorio exportado tem de abrir igual fora do
# servidor, e um <link> para /static quebraria assim que o arquivo saisse
# da maquina.
_FAVICON = "data:image/svg+xml,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%20viewBox%3D%220%200%2032%2032%22%3E%3Cdefs%3E%3ClinearGradient%20id%3D%22g%22%20x1%3D%220%22%20y1%3D%220%22%20x2%3D%221%22%20y2%3D%221%22%3E%3Cstop%20offset%3D%220%22%20stop-color%3D%22%23a78bfa%22%2F%3E%3Cstop%20offset%3D%221%22%20stop-color%3D%22%236d28d9%22%2F%3E%3C%2FlinearGradient%3E%3C%2Fdefs%3E%3Crect%20width%3D%2232%22%20height%3D%2232%22%20rx%3D%227%22%20fill%3D%22url%28%23g%29%22%2F%3E%3Cg%20stroke-linecap%3D%22round%22%20stroke-width%3D%224%22%3E%3Cpath%20d%3D%22M9%2023v-5%22%20stroke%3D%22%23fff%22%20opacity%3D%22.85%22%2F%3E%3Cpath%20d%3D%22M16%2023v-9%22%20stroke%3D%22%23fff%22%2F%3E%3Cpath%20d%3D%22M23%2023V9%22%20stroke%3D%22%23c4f82a%22%2F%3E%3C%2Fg%3E%3C%2Fsvg%3E"


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


def render_html(data: dict[str, Any], autoprint: bool = False) -> str:
    """Monta o relatorio como uma pagina autocontida.

    `autoprint` abre a caixa de impressao assim que a pagina carrega — e por
    ela que sai o PDF, usando o "Salvar como PDF" do proprio navegador. O
    arquivo baixado como HTML continua sem script nenhum.
    """
    e = escape
    project = data["project"].get("path") or f"projeto {data['project_id']}"
    gerado = datetime.fromisoformat(data["generated_at"]).astimezone()
    sprints = data["sprints"]
    focus = data["focus_label"]

    linhas = []
    for s in sprints:
        d = s["delta"]
        linhas.append(f"""<tr>
          <td><b>{e(s['milestone'])}</b>
            <div class="muted">{e(_periodo(s))}</div>
            {f'<div class="muted">vs {e(s["compared_to"])}</div>' if s['compared_to'] else ''}</td>
          <td class="num">{s['closed_issues']}/{s['issues']}
            {_delta_html(d.get('completion_pp'), 'pp')}</td>
          <td class="num">{e(s['by_label'].get(focus, {}).get('human', '0m'))}
            {_delta_html(d.get('focus_hours'))}</td>
          <td class="num">{e(_fmt_h(s['total_hours']))}
            {_delta_html(d.get('total_hours'))}</td>
          <td class="num">{e(_fmt_h(s['avg_lead_hours']))}
            {_delta_html(d.get('avg_lead_hours'), lower_is_better=True)}</td>
          <td class="num">{s['commits']}{_delta_html(d.get('commits'))}</td>
          <td class="num">{'—' if s['convention_pct'] is None else f"{s['convention_pct']:g}%"}
            {_delta_html(d.get('convention_pp'), 'pp')}</td>
        </tr>""")

    colunas = data["columns"]
    cores = _palette(colunas)
    # cada coluna na sua propria escala: comparar Doing com Backlog na mesma
    # regua esconderia a variacao das colunas curtas
    teto = {c: max([s["by_label"].get(c, {}).get("hours", 0.0) for s in sprints] + [1.0])
            for c in colunas}
    por_coluna = []
    for s in sprints:
        celulas = ""
        for c in colunas:
            valor = s["by_label"].get(c)
            celulas += (f'<td class="num">{e(valor["human"]) if valor else "—"}'
                        + _bar((valor["hours"] if valor else 0) / teto[c], cores[c])
                        + "</td>")
        por_coluna.append(f"<tr><td><b>{e(s['milestone'])}</b></td>{celulas}</tr>")

    pessoas = []
    for s in sprints:
        if not s["people"]:
            continue
        topo = max((p["hours"] for p in s["people"]), default=1) or 1
        itens = "".join(f"""<tr>
            <td>{e(p['contributor'])}</td>
            <td class="num">{e(p['human'])}{_bar(p['hours'] / topo, SERIES[0])}</td>
            <td class="num">{e(_fmt_h(p['review_hours']))}</td>
            <td class="num">{p['closed_issues']}/{p['issues']}</td>
          </tr>""" for p in s["people"])
        pessoas.append(f"""<section>
          <h2 style="margin-top:0">{e(s['milestone'])} — quem fez o que</h2>
          <table><thead><tr><th>Pessoa</th><th class="num">Acumulado</th>
            <th class="num">Revisando</th><th class="num">Fechadas</th></tr></thead>
          <tbody>{itens}</tbody></table></section>""")

    conv = data["convention"]
    rotulos = conv["reason_labels"]
    autores = "".join(f"""<tr>
        <td>{e(a['member'] or a['author'])}
          {'' if a['member'] else '<span class="pill">fora do time</span>'}
          <div class="muted">{e(a['author'])}</div></td>
        <td class="num"><b style="color:{_rate_color(a['pct'])}">{a['pct']:g}%</b>
          {_bar(a['pct'] / 100, _rate_color(a['pct']))}</td>
        <td class="num">{a['ok']}/{a['commits']}</td>
        <td>{''.join(f'<span class="pill off">{e(rotulos.get(k, k))}: {v}</span>'
                     for k, v in a['reasons'].items())}</td>
      </tr>""" for a in conv["authors"])

    return f"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" href="{_FAVICON}">
<title>dashGit — relatorio de sprints — {e(project)}</title>
<style>{_CSS}</style>{_PRINT_JS if autoprint else ''}</head><body><main>
{_PRINT_BAR if autoprint else ''}
<h1>Relatorio comparativo de sprints</h1>
<p class="sub">{e(project)} · gerado em {gerado.strftime('%d/%m/%Y %H:%M')}</p>
<p class="sub">Cada sprint e medida pela sua duracao inteira. A variacao e contra a
sprint imediatamente anterior; taxas comparam em pontos percentuais (pp).{
  ' Sabado e domingo nao entram em nenhuma conta de tempo.' if data['skip_weekends'] else ''}</p>

<section>
<h2 style="margin-top:0">Visao por sprint</h2>
<table>
<thead><tr><th>Sprint</th><th class="num">Fechadas</th>
  <th class="num">Tempo em {e(focus or 'coluna de trabalho')}</th>
  <th class="num">Acumulado</th><th class="num">Lead time medio</th>
  <th class="num">Commits</th><th class="num">Convencao</th></tr></thead>
<tbody>{''.join(linhas) or '<tr><td colspan="7" class="muted">Sem sprints.</td></tr>'}</tbody>
</table>
</section>

<section>
<h2 style="margin-top:0">Tempo por coluna</h2>
<table><thead><tr><th>Sprint</th>{''.join(
  f'<th class="num"><span class="tag">{_swatch(cores[c])}{e(c)}</span></th>' for c in colunas)}</tr></thead>
<tbody>{''.join(por_coluna)}</tbody></table>
</section>

{''.join(pessoas)}

<section>
<h2 style="margin-top:0">Conventional commits</h2>
<p class="sub">Regra: <code>{e(conv['rule'])}</code>. Tipos aceitos:
  {', '.join(f'<code>{e(t)}</code>' for t in conv['types'])}.
  Merge commits ficam de fora. No total do projeto,
  <b>{conv['totals']['pct']:g}%</b> dos {conv['totals']['commits']} commits do time
  seguem a convencao.{_outsiders_html(data.get('outsiders'))}{f" {data['orphan_commits']} commits nao citam issue e por isso nao"
  " entram em nenhuma sprint." if data['orphan_commits'] else ""}</p>
<table><thead><tr><th>Pessoa</th><th class="num">Aderencia</th>
  <th class="num">Ok</th><th>O que quebra</th></tr></thead>
<tbody>{autores or '<tr><td colspan="4" class="muted">Sem commits.</td></tr>'}</tbody></table>
</section>

{f'<p class="sub">Fora das sprints: {data["unscheduled"]["issues"]} issues sem'
  f' milestone ({data["unscheduled"]["closed_issues"]} fechadas,'
  f' {escape(data["unscheduled"]["human"])} acumuladas). Elas nao entram na comparacao.</p>'
  if data.get('unscheduled') else ''}

<footer>dashGit · dados do cache local sincronizado em
{e((data['project'].get('synced_at') or '')[:16].replace('T', ' '))} UTC</footer>
</main></body></html>"""


def render_summary_html(data: dict[str, Any], autoprint: bool = False) -> str:
    """O resumo de uma sprint como pagina autocontida."""
    e = escape
    m = data["milestone"]
    d = data["delta"]
    focus = data["focus_label"]
    project = data["project"].get("path") or f"projeto {data['project_id']}"
    gerado = datetime.fromisoformat(data["generated_at"]).astimezone()
    commits = data["commits"]
    rotulos = commits["reason_labels"]

    cores = _palette(data["columns_order"])
    cor_foco = cores.get(focus, SERIES[0])
    numeros = [
        ("Issues fechadas", f"{m['closed_issues']}/{m['issues']}",
         _delta_html(d.get("completion_pp"), "pp"), "#15803d"),
        (f"Tempo em {focus or 'coluna de trabalho'}",
         m["by_label"].get(focus, {}).get("human", "0m") if focus else "—",
         _delta_html(d.get("focus_hours")), cor_foco),
        ("Acumulado", m["total_human"], _delta_html(d.get("total_hours")), SERIES[4]),
        ("Lead time medio", _fmt_h(m["avg_lead_hours"]),
         _delta_html(d.get("avg_lead_hours"), lower_is_better=True), SERIES[3]),
        ("Commits", str(commits["total"]), _delta_html(d.get("commits")), SERIES[2]),
        ("Convencao", "—" if commits["pct"] is None else f"{commits['pct']:g}%",
         _delta_html(d.get("convention_pp"), "pp"), _rate_color(commits["pct"])),
    ]
    cartoes = "".join(f"""<div class="kpi" style="border-left-color:{cor}">
        <div class="kpi-label">{e(rotulo)}</div>
        <div class="kpi-value" style="color:{cor}">{e(valor)}</div>{variacao}</div>"""
        for rotulo, valor, variacao, cor in numeros)

    topo_pessoa = max([p["total_hours"] for p in data["people"]] + [1.0])
    pessoas = ""
    for p in data["people"]:
        conv = p.get("convention")
        pessoas += f"""<tr>
        <td>{e(p['contributor'])}</td>
        <td class="num">{e(p['total_human'])}{_bar(p['total_hours'] / topo_pessoa, SERIES[0])}</td>
        <td class="num" style="color:{SERIES[2] if p['review_hours'] else NEUTRA}">
          {e(_fmt_h(p['review_hours']))}</td>
        <td class="num" style="color:{'#b45309' if p['waiting_hours'] else NEUTRA}">
          {e(_fmt_h(p['waiting_hours']))}</td>
        <td class="num">{p['closed_issues']}/{p['issues']}</td>
        <td class="num">{f'<b style="color:{_rate_color(conv["pct"])}">{conv["pct"]:g}%</b>'
                          + _bar(conv['pct'] / 100, _rate_color(conv['pct']))
                          + f'<span class="delta flat">{conv["ok"]}/{conv["commits"]}</span>'
                        if conv else '<span class="flat">—</span>'}</td>
      </tr>"""

    teto_coluna = max([c["avg_hours"] for c in data["columns"]] + [1.0])
    colunas = "".join(f"""<tr>
        <td><span class="tag">{_swatch(cores.get(c['label'], NEUTRA))}{e(c['label'])}</span></td>
        <td class="num">{e(c['avg_human'])}
          {_bar(c['avg_hours'] / teto_coluna, cores.get(c['label'], NEUTRA))}</td>
        <td class="num">{e(_fmt_h(c['median_hours']))}</td>
        <td class="num">{e(_fmt_h(c['max_hours']))}</td>
        <td class="num">{c['completed_passes']}</td>
        <td class="num">{c['wip']}</td>
      </tr>""" for c in data["columns"])

    teto_issue = max([i["focus_hours"] for i in data["issues"]] + [1.0])
    issues = "".join(f"""<tr>
        <td>#{i['iid']} {e(i['title'])}</td>
        <td>{e(i['assignee'])}</td>
        <td>{f'<span class="tag">{_swatch(cores.get(i["current_column"], NEUTRA))}'
              f'{e(i["current_column"])}</span>' if i['current_column']
             else '<span class="flat">fechada</span>'}</td>
        <td class="num">{e(_fmt_h(i['focus_hours']))}
          {_bar(i['focus_hours'] / teto_issue, cor_foco)}</td>
        <td class="num">{e(_fmt_h(i['lead_time_hours']))}</td>
      </tr>""" for i in data["issues"])

    infratores = "".join(f"""<tr>
        <td><code>{e(c['short_id'])}</code></td>
        <td>{e(c['title'])}</td>
        <td>{e(c['author'] or '?')}</td>
        <td>{''.join(f'<span class="pill off">{e(rotulos.get(r, r))}</span>'
                     for r in c['convention'])}</td>
      </tr>""" for c in commits["offenders"])

    return f"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" href="{_FAVICON}">
<title>dashGit — {e(m['milestone'])} — {e(project)}</title>
<style>{_CSS}{_SUMMARY_CSS}</style>{_PRINT_JS if autoprint else ''}</head><body><main>
{_PRINT_BAR if autoprint else ''}
<h1>{e(m['milestone'])}</h1>
<p class="sub">{e(project)} · {e(_periodo(m))} · gerado em {gerado.strftime('%d/%m/%Y %H:%M')}</p>
<p class="sub">{'Variacao contra ' + e(data['compared_to']) + '.'
  if data['compared_to'] else 'Primeira sprint com dados: nao ha com o que comparar.'}
  Taxas comparam em pontos percentuais (pp).{
  ' Sabado e domingo nao entram em nenhuma conta de tempo.' if data['skip_weekends'] else ''}</p>

<section><div class="kpis">{cartoes}</div></section>

<section>
<h2 style="margin-top:0">Quem fez o que</h2>
<table><thead><tr><th>Pessoa</th><th class="num">Acumulado</th>
  <th class="num">Revisando</th><th class="num">Espera causada</th>
  <th class="num">Fechadas</th><th class="num">Convencao</th></tr></thead>
<tbody>{pessoas or '<tr><td colspan="6" class="muted">Sem tempo registrado.</td></tr>'}</tbody></table>
</section>

<section>
<h2 style="margin-top:0">Onde o fluxo travou</h2>
<p class="sub">Media e mediana de permanencia por passagem na coluna, dentro desta sprint.</p>
<table><thead><tr><th>Coluna</th><th class="num">Media</th><th class="num">Mediana</th>
  <th class="num">Maximo</th><th class="num">Passagens</th><th class="num">Agora</th></tr></thead>
<tbody>{colunas or '<tr><td colspan="6" class="muted">Sem passagens.</td></tr>'}</tbody></table>
</section>

<section>
<h2 style="margin-top:0">Issues mais demoradas</h2>
<p class="sub">As {len(data['issues'])} mais longas de {data['issues_total']} issues com
  tempo registrado, ranqueadas pelo tempo em {e(focus or 'coluna de trabalho')}.</p>
<table><thead><tr><th>Issue</th><th>Responsavel</th><th>Coluna</th>
  <th class="num">{e(focus or 'Trabalho')}</th><th class="num">Lead time</th></tr></thead>
<tbody>{issues or '<tr><td colspan="5" class="muted">Sem issues.</td></tr>'}</tbody></table>
</section>

<section>
<h2 style="margin-top:0">Commits fora da convencao</h2>
<p class="sub">Regra: <code>{e(commits['rule'])}</code>.
  <b style="color:{_rate_color(commits['pct'])}">{commits['off']}</b> de
  {commits['total']} commits desta sprint fogem dela.
  A sprint de um commit e a da issue que ele cita.{_outsiders_html(commits.get('outsiders'))}</p>
<table><thead><tr><th>Commit</th><th>Mensagem</th><th>Autor</th><th>O que quebra</th></tr></thead>
<tbody>{infratores or '<tr><td colspan="4" class="muted">Nenhum commit fora do padrao.</td></tr>'}</tbody></table>
</section>

<footer>dashGit · dados do cache local sincronizado em
{e((data['project'].get('synced_at') or '')[:16].replace('T', ' '))} UTC</footer>
</main></body></html>"""
