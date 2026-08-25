"""Rotas da API. Tudo que le metrica trabalha em cima do cache local;
o unico caminho que fala com o GitLab e o /sync.
"""

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse

from app import commits as commit_metrics
from app import metrics
from app import report as sprint_report_builder
from app.config import get_settings
from app.db import session
from app.gitlab_client import GitLabError
from app.sync import sync_project

router = APIRouter(prefix="/api", tags=["dashgit"])

MILESTONE_DESC = (
    "Titulo da milestone/sprint. Use '(sem sprint)' para as issues sem milestone."
)


def _resolve(project: str | None) -> int:
    settings = get_settings()
    candidate = project or (settings.project_list[0] if settings.project_list else None)
    project_id = metrics.resolve_project_id(candidate)
    if project_id is None:
        raise HTTPException(
            404,
            f"Projeto '{candidate or '(nenhum)'}' nao esta no cache. "
            "Rode POST /api/sync?project=grupo/projeto primeiro.",
        )
    return project_id


def _parse_since(since: str | None, days: int | None) -> datetime | None:
    if since:
        parsed = metrics.parse_ts(since)
        if parsed is None:
            raise HTTPException(400, f"Data invalida em 'since': {since}")
        return parsed
    if days:
        return datetime.now(timezone.utc) - timedelta(days=days)
    return None


@router.get("/health")
def health() -> dict[str, Any]:
    settings = get_settings()
    with session() as conn:
        projects = conn.execute("SELECT COUNT(*) AS n FROM projects").fetchone()["n"]
        issues = conn.execute("SELECT COUNT(*) AS n FROM issues").fetchone()["n"]
    return {
        "status": "ok",
        "gitlab_api": settings.gitlab_api_url,
        "token_configured": bool(settings.gitlab_token),
        "cached_projects": projects,
        "cached_issues": issues,
    }


@router.post("/sync")
async def sync(
    project: str | None = Query(None, description="grupo/projeto ou ID numerico"),
    full: bool = Query(False, description="Ignora o sync incremental e rebusca tudo"),
) -> dict[str, Any]:
    settings = get_settings()
    targets = [project] if project else settings.project_list
    if not targets:
        raise HTTPException(400, "Informe ?project= ou defina DEFAULT_PROJECTS no .env")
    results = []
    for target in targets:
        try:
            results.append(await sync_project(target, full=full))
        except GitLabError as exc:
            raise HTTPException(502, str(exc)) from exc
    return {"synced": results}


@router.get("/projects")
def projects() -> list[dict[str, Any]]:
    with session() as conn:
        rows = conn.execute(
            "SELECT p.id, p.path, p.name, p.web_url, p.synced_at,"
            " (SELECT COUNT(*) FROM issues i WHERE i.project_id = p.id) AS issues"
            " FROM projects p ORDER BY p.path"
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/boards")
def boards(project: str | None = None) -> dict[str, Any]:
    """Colunas do board. `excluded` sao as que ficam fora das contas de tempo."""
    project_id = _resolve(project)
    with session() as conn:
        rows = conn.execute(
            "SELECT board_id, board_name, list_id, position, label_name FROM board_lists"
            " WHERE project_id = ? ORDER BY board_id, position",
            (project_id,),
        ).fetchall()
    hidden = metrics.excluded_labels()
    grouped: dict[int, dict[str, Any]] = {}
    for row in rows:
        board = grouped.setdefault(
            row["board_id"],
            {"board_id": row["board_id"], "name": row["board_name"], "columns": []},
        )
        board["columns"].append({
            "list_id": row["list_id"],
            "label": row["label_name"],
            "excluded": row["label_name"].lower() in hidden,
        })
    return {
        "project_id": project_id,
        "excluded_labels": get_settings().excluded_list,
        "focus_label": metrics.focus_label(project_id),
        "review_label": metrics.review_label(project_id),
        # sem colunas conhecidas, toda label vira etapa e o total pode passar
        # do tempo de relogio — o cliente avisa em vez de mentir calado
        "columns_known": bool(rows),
        "attribution": metrics.attribution_mode(),
        "skip_weekends": metrics.skip_weekends(),
        "non_working_hours": get_settings().non_working_list,
        "queue_labels": get_settings().queue_list,
        "scope": metrics.scope_mode(),
        "boards": list(grouped.values()),
    }


@router.get("/milestones")
def milestones(project: str | None = None) -> dict[str, Any]:
    """Sprints disponiveis no cache, com contagem de issues."""
    project_id = _resolve(project)
    return {"project_id": project_id, "milestones": metrics.milestones(project_id)}


@router.get("/metrics/contributors")
def contributors(
    project: str | None = None,
    labels: str | None = Query(None, description="Colunas separadas por virgula, ex: Doing,Review"),
    milestone: str | None = Query(None, description=MILESTONE_DESC),
    since: str | None = Query(None, description="ISO 8601, ex: 2026-01-01"),
    days: int | None = Query(None, ge=1, le=3650, description="Atalho: ultimos N dias"),
    state: str | None = Query(None, pattern="^(opened|closed)$"),
) -> dict[str, Any]:
    """Tempo total por contribuidor em cada coluna do board."""
    project_id = _resolve(project)
    label_list = [x.strip() for x in labels.split(",")] if labels else None
    return metrics.contributor_report(
        project_id, label_list, since=_parse_since(since, days), state=state,
        milestone=milestone,
    )


@router.get("/metrics/contributor")
def contributor(
    name: str = Query(..., description="Nome exato do responsavel, como vem em /metrics/contributors"),
    project: str | None = None,
    labels: str | None = None,
    milestone: str | None = Query(None, description=MILESTONE_DESC),
    since: str | None = None,
    days: int | None = Query(None, ge=1, le=3650),
) -> dict[str, Any]:
    """Perfil de uma pessoa: tempo por coluna, por sprint e as issues dela."""
    project_id = _resolve(project)
    label_list = [x.strip() for x in labels.split(",")] if labels else None
    data = metrics.contributor_detail(
        project_id, name, label_list, since=_parse_since(since, days), milestone=milestone
    )
    if not data["issues_count"]:
        raise HTTPException(404, f"Sem issues atribuidas a '{name}' neste recorte.")
    return data


@router.get("/metrics/columns")
def columns(
    project: str | None = None,
    labels: str | None = None,
    milestone: str | None = Query(None, description=MILESTONE_DESC),
) -> dict[str, Any]:
    """Tempo medio/mediano de permanencia por coluna + WIP atual."""
    project_id = _resolve(project)
    label_list = [x.strip() for x in labels.split(",")] if labels else None
    return metrics.column_report(project_id, label_list, milestone=milestone)


@router.get("/metrics/milestones")
def milestone_metrics(
    project: str | None = None,
    labels: str | None = None,
    limit: int = Query(8, ge=1, le=50, description="Quantas sprints comparar"),
) -> dict[str, Any]:
    """Comparativo entre sprints: tempo por coluna, throughput e lead time.

    Ignora o filtro de periodo de proposito — cada sprint e comparada pela
    sua duracao inteira.
    """
    project_id = _resolve(project)
    label_list = [x.strip() for x in labels.split(",")] if labels else None
    return metrics.milestone_report(project_id, label_list, limit=limit)


@router.get("/metrics/commits")
def commits(
    project: str | None = None,
    since: str | None = None,
    days: int | None = Query(None, ge=1, le=3650),
    author: str | None = Query(None, description="Nome do autor no git, ou e-mail"),
    include_merges: bool = Query(False, description="Inclui merge commits (fora por padrao:"
                                " eles repetem as linhas dos commits que trazem)"),
    only_off: bool = Query(False, description="Lista so os commits fora da convencao."
                           " Recorta a listagem, nao os totais"),
) -> dict[str, Any]:
    """Volume, autores, ritmo diario e horario dos commits.

    `author` aceita o nome do GitLab ou a assinatura do git: as identidades da
    mesma pessoa entram juntas.
    """
    project_id = _resolve(project)
    return commit_metrics.commit_report(
        project_id, since=_parse_since(since, days), author=author,
        include_merges=include_merges, only_off=only_off,
    )


@router.get("/commit-authors")
def commit_authors(project: str | None = None) -> dict[str, Any]:
    """Autores de commit e os e-mails de cada um, com o nome do GitLab quando
    da para casar. Serve para achar quem ficou dividido em duas identidades."""
    project_id = _resolve(project)
    with session() as conn:
        members = [r[0] for r in conn.execute(
            "SELECT DISTINCT user_name FROM label_events"
            " WHERE project_id = ? AND user_name IS NOT NULL", (project_id,))]
    autores = commit_metrics.identities(project_id)
    for a in autores:
        a["gitlab_name"] = commit_metrics.match_member(
            a["author"], members, a["emails"][0] if a["emails"] else None
        )
    return {"project_id": project_id, "authors": autores}


@router.get("/metrics/issues")
def issues(
    project: str | None = None,
    labels: str | None = None,
    milestone: str | None = Query(None, description=MILESTONE_DESC),
    state: str | None = Query(None, pattern="^(opened|closed)$"),
    sort: str = Query(
        "focus",
        pattern="^(focus|working|lead_time)$",
        description="focus = tempo na coluna de trabalho (padrao);"
        " working = tempo somado em todas as colunas contadas;"
        " lead_time = criacao ate fechamento",
    ),
    limit: int = Query(200, ge=1, le=2000),
) -> dict[str, Any]:
    """Drill-down: linha do tempo de colunas de cada issue.

    O ranking padrao e por tempo na coluna de trabalho, nao por lead time:
    uma issue esquecida no Backlog nao e uma issue demorada.
    """
    project_id = _resolve(project)
    label_list = [x.strip() for x in labels.split(",")] if labels else None
    data = metrics.issue_report(project_id, label_list, state, milestone=milestone, sort=sort)
    return {
        "project_id": project_id,
        "focus_label": data["focus_label"],
        "sorted_by": data["sorted_by"],
        "count": len(data["issues"]),
        "issues": data["issues"][:limit],
    }


@router.get("/metrics/commit-convention")
def commit_convention(
    project: str | None = None,
    since: str | None = None,
    days: int | None = Query(None, ge=1, le=3650),
    author: str | None = Query(None, description="Nome do autor no git, ou e-mail"),
    include_merges: bool = Query(False, description="Merge commit tem mensagem gerada"
                                " pelo GitLab: reprovar o time por ela nao mede nada"),
) -> dict[str, Any]:
    """Aderencia de cada pessoa a `tipo(#issue): descricao`, e o que quebra."""
    project_id = _resolve(project)
    return commit_metrics.convention_report(
        project_id, since=_parse_since(since, days), author=author,
        include_merges=include_merges,
    )


@router.get("/report/sprints")
def report_sprints(
    project: str | None = None,
    labels: str | None = None,
    limit: int = Query(8, ge=1, le=50, description="Quantas sprints comparar"),
) -> dict[str, Any]:
    """Relatorio comparativo entre sprints: board, pessoas e commits juntos.

    Ignora o filtro de periodo de proposito — cada sprint e comparada pela sua
    duracao inteira, como no card de sprints da visao geral.
    """
    project_id = _resolve(project)
    label_list = [x.strip() for x in labels.split(",")] if labels else None
    return sprint_report_builder.sprint_report(project_id, label_list, limit=limit)


@router.get("/report/sprints.html", response_class=HTMLResponse)
def report_sprints_html(
    project: str | None = None,
    labels: str | None = None,
    limit: int = Query(8, ge=1, le=50),
    download: bool = Query(True, description="false abre no navegador em vez de baixar"),
    printable: bool = Query(False, alias="print", description="abre a caixa de impressao"
                            " assim que carrega — e por ela que sai o PDF"),
) -> HTMLResponse:
    """O mesmo relatorio como pagina autocontida, para anexar na entrega.

    O PDF sai por `?print=1`: a pagina se manda imprimir e o proprio navegador
    salva como PDF. Gerar o PDF no servidor exigiria um motor de renderizacao
    (WeasyPrint, wkhtmltopdf) so para repetir o que o navegador ja faz.
    """
    project_id = _resolve(project)
    label_list = [x.strip() for x in labels.split(",")] if labels else None
    data = sprint_report_builder.sprint_report(project_id, label_list, limit=limit)
    slug = (data["project"].get("path") or str(project_id)).replace("/", "-")
    stamp = data["generated_at"][:10]
    headers = {} if printable or not download else {
        "Content-Disposition": f'attachment; filename="relatorio-sprints-{slug}-{stamp}.html"'
    }
    return HTMLResponse(
        sprint_report_builder.render_html(data, autoprint=printable), headers=headers
    )


@router.get("/report/sprint")
def report_sprint(
    milestone: str = Query(..., description=MILESTONE_DESC),
    project: str | None = None,
    labels: str | None = None,
) -> dict[str, Any]:
    """Resumo de uma sprint: numeros, gargalo, pessoas, issues e commits.

    O comparativo responde "estamos melhorando?"; este responde "o que
    aconteceu nesta sprint?".
    """
    project_id = _resolve(project)
    label_list = [x.strip() for x in labels.split(",")] if labels else None
    data = sprint_report_builder.sprint_summary(project_id, milestone, label_list)
    if not data:
        raise HTTPException(404, f"Sprint '{milestone}' nao esta no cache deste projeto.")
    return data


@router.get("/report/sprint.html", response_class=HTMLResponse)
def report_sprint_html(
    milestone: str = Query(..., description=MILESTONE_DESC),
    project: str | None = None,
    labels: str | None = None,
    download: bool = Query(True, description="false abre no navegador em vez de baixar"),
    printable: bool = Query(False, alias="print", description="abre a caixa de impressao"),
) -> HTMLResponse:
    """O resumo da sprint como pagina autocontida."""
    project_id = _resolve(project)
    label_list = [x.strip() for x in labels.split(",")] if labels else None
    data = sprint_report_builder.sprint_summary(project_id, milestone, label_list)
    if not data:
        raise HTTPException(404, f"Sprint '{milestone}' nao esta no cache deste projeto.")
    slug = milestone.lower().replace(" ", "-").replace("/", "-")
    stamp = data["generated_at"][:10]
    headers = {} if printable or not download else {
        "Content-Disposition": f'attachment; filename="relatorio-{slug}-{stamp}.html"'
    }
    return HTMLResponse(
        sprint_report_builder.render_summary_html(data, autoprint=printable), headers=headers
    )
