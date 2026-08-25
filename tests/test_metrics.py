"""Testes do motor de metricas com um cache SQLite sintetico."""

import os
import tempfile
from datetime import date, datetime, time, timedelta, timezone

os.environ.setdefault("GITLAB_TOKEN", "test-token")
_DB = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ["DATABASE_PATH"] = _DB

from app import metrics  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import init_db, session  # noqa: E402

get_settings.cache_clear()
get_settings()

NOW = datetime.now(timezone.utc)


def iso(offset_hours: float) -> str:
    return (NOW + timedelta(hours=offset_hours)).isoformat()


def seed() -> None:
    init_db()
    with session() as conn:
        conn.execute("DELETE FROM label_events")
        conn.execute("DELETE FROM issues")
        conn.execute("DELETE FROM board_lists")
        conn.execute("DELETE FROM milestones")
        conn.execute("DELETE FROM projects")
        conn.execute(
            "INSERT INTO projects (id, path, name, web_url, synced_at) VALUES (1,'g/p','P','u',?)",
            (iso(0),),
        )
        for pos, label in enumerate(["To Do", "Doing", "Review"]):
            conn.execute(
                "INSERT INTO board_lists VALUES (1, 10, 'Dev', ?, ?, ?)", (pos + 1, pos, label)
            )
        conn.execute(
            "INSERT INTO milestones VALUES (1, 90, 1, 'Sprint 1', 'closed',"
            " '2026-01-01', '2026-01-14', 'm1')"
        )
        conn.execute(
            "INSERT INTO milestones VALUES (1, 91, 2, 'Sprint 2', 'active',"
            " '2026-01-15', '2026-01-28', 'm2')"
        )

        # issue 1: fechada, passou 10h em Doing e 5h em Review (Ana)
        conn.execute(
            "INSERT INTO issues (project_id, iid, id, title, state, created_at, closed_at,"
            " updated_at, author_id, author_name, assignee_id, assignee_name, milestone, web_url)"
            " VALUES (1,1,101,'Login','closed',?,?,?,1,'Bruno',2,'Ana','Sprint 1','u1')",
            (iso(-30), iso(-10), iso(-10)),
        )
        events = [
            (1, 1, 1, "add", "Doing", 2, "Ana", iso(-30)),
            (2, 1, 1, "remove", "Doing", 2, "Ana", iso(-20)),
            (3, 1, 1, "add", "Review", 1, "Bruno", iso(-20)),
            (4, 1, 1, "remove", "Review", 1, "Bruno", iso(-15)),
        ]
        # issue 2: aberta, esta em Doing ha 4h (Ana)
        conn.execute(
            "INSERT INTO issues (project_id, iid, id, title, state, created_at, closed_at,"
            " updated_at, author_id, author_name, assignee_id, assignee_name, milestone, web_url)"
            " VALUES (1,2,102,'Cache','opened',?,NULL,?,1,'Bruno',2,'Ana','Sprint 2','u2')",
            (iso(-8), iso(-4)),
        )
        events += [(5, 1, 2, "add", "Doing", 2, "Ana", iso(-4))]

        # issue 3: sem responsavel, 2h em Doing
        conn.execute(
            "INSERT INTO issues (project_id, iid, id, title, state, created_at, closed_at,"
            " updated_at, author_id, author_name, assignee_id, assignee_name, milestone, web_url)"
            " VALUES (1,3,103,'Docs','opened',?,NULL,?,1,'Bruno',NULL,NULL,NULL,'u3')",
            (iso(-6), iso(-2)),
        )
        events += [
            (6, 1, 3, "add", "Doing", 1, "Bruno", iso(-6)),
            (7, 1, 3, "remove", "Doing", 1, "Bruno", iso(-4)),
        ]
        conn.executemany("INSERT INTO label_events VALUES (?,?,?,?,?,?,?,?)", events)


def approx(value: float, expected: float, tol: float = 0.05) -> bool:
    return abs(value - expected) <= tol


def escopo_amplo(monkeypatch):
    """SCOPE=touched: conta o que a pessoa fez em qualquer issue, nao so nas dela."""
    monkeypatch.setattr(metrics, "scope_mode", lambda: "touched")


def test_intervals_pareados():
    seed()
    timelines = {t.iid: t for t in metrics.build_timelines(1)}
    doing = [i for i in timelines[1].intervals if i.label == "Doing"]
    assert len(doing) == 1 and approx(doing[0].seconds(NOW) / 3600, 10)
    assert doing[0].moved_by == "Ana"


def test_label_ainda_aberta_conta_ate_agora():
    seed()
    timelines = {t.iid: t for t in metrics.build_timelines(1)}
    itv = timelines[2].intervals[0]
    assert itv.end is None
    assert approx(itv.seconds(NOW) / 3600, 4)


def test_contributor_report_soma_por_coluna(monkeypatch):
    escopo_amplo(monkeypatch)
    seed()
    report = metrics.contributor_report(1, labels=["Doing"])
    ana = next(c for c in report["contributors"] if c["contributor"] == "Ana")
    assert approx(ana["by_label"]["Doing"]["hours"], 14)  # 10h fechada + 4h em aberto
    assert ana["by_label"]["Doing"]["issues"] == 2
    assert ana["by_label"]["Doing"]["still_in_column"] == 1
    assert ana["closed_issues"] == 1

    # a issue 3 nao tem responsavel, mas quem a moveu para Doing foi o Bruno
    bruno = next(c for c in report["contributors"] if c["contributor"] == "Bruno")
    assert approx(bruno["by_label"]["Doing"]["hours"], 2)


def test_janela_since_recorta_intervalo():
    seed()
    report = metrics.contributor_report(1, labels=["Doing"], since=NOW - timedelta(hours=5))
    ana = next(c for c in report["contributors"] if c["contributor"] == "Ana")
    # so as 4h da issue 2 caem na janela; a issue 1 saiu de Doing ha 20h
    assert approx(ana["by_label"]["Doing"]["hours"], 4)


def test_column_report_ordena_pelo_board():
    seed()
    report = metrics.column_report(1)
    labels = [c["label"] for c in report["columns"]]
    assert labels == ["Doing", "Review"]  # "To Do" nunca foi usada
    doing = report["columns"][0]
    assert doing["completed_passes"] == 2  # issues 1 e 3
    assert doing["wip"] == 1               # issue 2
    assert approx(doing["avg_hours"], 6)   # (10h + 2h) / 2


def test_issue_report_traz_coluna_atual():
    seed()
    report = metrics.issue_report(1)
    issues = {i["iid"]: i for i in report["issues"]}
    assert issues[2]["current_column"] == "Doing"
    assert issues[1]["current_column"] is None
    assert approx(issues[1]["lead_time_hours"], 20)


def test_resolve_project_por_path_e_id():
    seed()
    assert metrics.resolve_project_id("g/p") == 1
    assert metrics.resolve_project_id(1) == 1
    assert metrics.resolve_project_id("nao/existe") is None


def test_filtro_por_milestone_recorta_issues():
    seed()
    report = metrics.contributor_report(1, labels=["Doing"], milestone="Sprint 1")
    ana = next(c for c in report["contributors"] if c["contributor"] == "Ana")
    assert approx(ana["by_label"]["Doing"]["hours"], 10)   # so a issue 1
    assert ana["by_label"]["Doing"]["issues"] == 1
    assert report["milestone"] == "Sprint 1"


def test_filtro_sem_sprint_pega_issues_orfas(monkeypatch):
    escopo_amplo(monkeypatch)
    seed()
    report = metrics.contributor_report(1, milestone=metrics.NO_MILESTONE)
    assert [c["contributor"] for c in report["contributors"]] == ["Bruno"]
    assert approx(report["contributors"][0]["by_label"]["Doing"]["hours"], 2)


def test_lista_de_milestones_inclui_orfas_e_contagem():
    seed()
    rows = {m["title"]: m for m in metrics.milestones(1)}
    assert rows["Sprint 1"]["issues"] == 1
    assert rows["Sprint 2"]["state"] == "active"
    assert rows["Sprint 2"]["due_date"] == "2026-01-28"
    assert rows[metrics.NO_MILESTONE]["issues"] == 1     # issue 3
    # a pseudo-sprint fica sempre no fim da lista
    assert list(rows)[-1] == metrics.NO_MILESTONE


def test_milestone_report_compara_sprints():
    seed()
    report = metrics.milestone_report(1)
    rows = {m["milestone"]: m for m in report["milestones"]}

    s1 = rows["Sprint 1"]
    assert s1["issues"] == 1 and s1["closed_issues"] == 1
    assert s1["completion"] == 100.0
    assert approx(s1["by_label"]["Doing"]["hours"], 10)
    assert approx(s1["by_label"]["Review"]["hours"], 5)
    assert approx(s1["avg_lead_hours"], 20)
    assert s1["due_date"] == "2026-01-14"

    s2 = rows["Sprint 2"]
    assert s2["closed_issues"] == 0 and s2["completion"] == 0.0
    assert approx(s2["by_label"]["Doing"]["hours"], 4)

    # ordem: sprint mais recente primeiro, "(sem sprint)" por ultimo
    ordered = [m["milestone"] for m in report["milestones"]]
    assert ordered == ["Sprint 2", "Sprint 1", metrics.NO_MILESTONE]


def test_milestone_report_ignora_janela_de_periodo():
    seed()
    # milestone_report nao aceita `since` de proposito: a soma e da sprint toda
    total = metrics.milestone_report(1)["milestones"]
    doing = sum(m["by_label"].get("Doing", {}).get("hours", 0) for m in total)
    assert approx(doing, 16)   # 10h + 4h + 2h


def test_backlog_fica_fora_de_toda_conta_de_tempo():
    seed()
    with session() as conn:
        # a issue 2 passou 50h no Backlog antes de entrar em Doing
        conn.executemany("INSERT INTO label_events VALUES (?,?,?,?,?,?,?,?)", [
            (8, 1, 2, "add", "Backlog", 2, "Ana", iso(-54)),
            (9, 1, 2, "remove", "Backlog", 2, "Ana", iso(-4)),
        ])
        conn.execute("INSERT INTO board_lists VALUES (1, 10, 'Dev', 99, -1, 'Backlog')")

    report = metrics.contributor_report(1)
    ana = next(c for c in report["contributors"] if c["contributor"] == "Ana")
    assert "Backlog" not in ana["by_label"]
    assert "Backlog" not in report["totals"]
    assert "Backlog" not in metrics.board_labels(1)
    # e continua visivel para quem quiser olhar de proposito
    assert "Backlog" in metrics.board_labels(1, include_excluded=True)


def test_pedir_a_coluna_excluida_explicitamente_ganha():
    seed()
    with session() as conn:
        conn.executemany("INSERT INTO label_events VALUES (?,?,?,?,?,?,?,?)", [
            (8, 1, 2, "add", "Backlog", 2, "Ana", iso(-54)),
            (9, 1, 2, "remove", "Backlog", 2, "Ana", iso(-4)),
        ])
    report = metrics.contributor_report(1, labels=["Backlog"])
    ana = next(c for c in report["contributors"] if c["contributor"] == "Ana")
    assert approx(ana["by_label"]["Backlog"]["hours"], 50)


def test_issues_ranqueadas_por_tempo_em_doing():
    seed()
    with session() as conn:
        # issue 3 fica 300h paradas no Backlog: lead time enorme, trabalho zero
        conn.execute(
            "UPDATE issues SET created_at = ? WHERE project_id = 1 AND iid = 3",
            (iso(-300),),
        )
    report = metrics.issue_report(1)
    assert report["focus_label"] == "Doing"
    assert report["sorted_by"] == "focus_hours"

    ordem = [i["iid"] for i in report["issues"]]
    assert ordem[0] == 1                     # 10h em Doing, a que mais trabalhou
    assert ordem.index(2) < ordem.index(3)   # 4h em Doing vence 2h em Doing

    por_lead = metrics.issue_report(1, sort="lead_time")
    assert por_lead["issues"][0]["iid"] == 3  # lead time coroaria a issue parada
    assert por_lead["sorted_by"] == "lead_time_hours"


def test_label_que_nao_e_coluna_nao_vira_tempo():
    """Um card com Doing + DOCUMENTATION + ART 1 nao pode contar 3x o mesmo periodo."""
    seed()
    with session() as conn:
        conn.executemany("INSERT INTO label_events VALUES (?,?,?,?,?,?,?,?)", [
            (10, 1, 2, "add", "DOCUMENTATION", 2, "Ana", iso(-4)),
            (11, 1, 2, "add", "ART 1", 2, "Ana", iso(-4)),
        ])

    report = metrics.contributor_report(1)
    ana = next(c for c in report["contributors"] if c["contributor"] == "Ana")
    assert set(ana["by_label"]) == {"Doing"}   # o Review foi movido pelo Bruno
    assert "DOCUMENTATION" not in report["totals"]
    # 10h + 4h em Doing — nada multiplicado pelas etiquetas
    assert approx(ana["total_hours"], 14)

    # e as etiquetas continuam visiveis no drill-down
    issues = {i["iid"]: i for i in metrics.issue_report(1)["issues"]}
    assert issues[2]["tags"] == ["ART 1", "DOCUMENTATION"]


def test_colunas_simultaneas_nao_contam_o_mesmo_periodo_duas_vezes():
    seed()
    with session() as conn:
        # a issue 2 ganha Review as -3h enquanto ainda esta em Doing (desde -4h)
        conn.execute(
            "INSERT INTO label_events VALUES (12,1,2,'add','Review',2,'Ana',?)", (iso(-3),)
        )
    timelines = {t.iid: t for t in metrics.build_timelines(1)}
    total = sum(i.seconds(NOW) for i in timelines[2].intervals) / 3600
    assert approx(total, 4)     # 4h de relogio, nao 4h + 3h

    por_coluna = {i.label: i.seconds(NOW) / 3600 for i in timelines[2].intervals}
    # a sobreposicao fica com a coluna mais avancada do board, como o GitLab mostra
    assert approx(por_coluna["Doing"], 1)
    assert approx(por_coluna["Review"], 3)


def test_total_nunca_passa_do_tempo_de_relogio():
    seed()
    with session() as conn:
        conn.executemany("INSERT INTO label_events VALUES (?,?,?,?,?,?,?,?)", [
            (13, 1, 1, "add", "To Do", 2, "Ana", iso(-30)),      # sobrepoe Doing inteiro
            (14, 1, 1, "remove", "To Do", 2, "Ana", iso(-20)),
        ])
    timelines = {t.iid: t for t in metrics.build_timelines(1)}
    tl = timelines[1]
    wall = (tl.closed_at - tl.created_at).total_seconds() / 3600
    counted = sum(i.seconds(NOW) for i in tl.intervals) / 3600
    assert counted <= wall + 0.05


def test_projeto_sem_board_conta_todas_as_labels():
    """Sem board sincronizado nao da para saber o que e coluna — melhor contar
    tudo do que devolver um dashboard vazio."""
    seed()
    with session() as conn:
        conn.execute("DELETE FROM board_lists WHERE project_id = 1")
        conn.execute(
            "INSERT INTO label_events VALUES (15,1,2,'add','DOCUMENTATION',2,'Ana',?)", (iso(-4),)
        )
    report = metrics.contributor_report(1)
    ana = next(c for c in report["contributors"] if c["contributor"] == "Ana")
    assert "DOCUMENTATION" in ana["by_label"]


def test_contributor_detail_monta_o_perfil():
    seed()
    perfil = metrics.contributor_detail(1, "Ana")
    assert perfil["issues_count"] == 2 and perfil["closed_issues"] == 1
    assert perfil["focus_label"] == "Doing"
    assert approx(perfil["by_label"]["Doing"]["hours"], 14)
    assert "Review" not in perfil["by_label"]        # a revisao foi do Bruno
    assert perfil["wip"] == 1                        # a issue 2 segue em Doing
    assert perfil["columns"] == ["Doing"]

    # as issues vem ranqueadas pelo tempo na coluna de trabalho
    assert [i["iid"] for i in perfil["issues"]] == [1, 2]
    assert approx(perfil["issues"][0]["focus_hours"], 10)


def test_contributor_detail_agrupa_por_sprint():
    seed()
    perfil = metrics.contributor_detail(1, "Ana")
    sprints = {s["milestone"]: s for s in perfil["by_milestone"]}
    assert sprints["Sprint 1"]["issues"] == 1
    assert sprints["Sprint 1"]["completion"] == 100.0
    assert sprints["Sprint 2"]["completion"] == 0.0
    assert [s["milestone"] for s in perfil["by_milestone"]] == ["Sprint 2", "Sprint 1"]


def test_contributor_detail_respeita_janela_mas_mantem_a_issue():
    seed()
    perfil = metrics.contributor_detail(1, "Ana", since=NOW - timedelta(hours=5))
    # so as 4h recentes entram na soma...
    assert approx(perfil["by_label"]["Doing"]["hours"], 4)
    # ...mas a issue antiga continua listada, com o historico completo
    assert perfil["issues_count"] == 2
    assert approx(next(i for i in perfil["issues"] if i["iid"] == 1)["focus_hours"], 10)


def test_tempo_de_revisao_vai_para_quem_revisou(monkeypatch):
    escopo_amplo(monkeypatch)
    """Fluxo real: X faz e move para Waiting Review; Y move para Review e fecha.
    O tempo de Review e metrica de Y, mesmo com a issue atribuida a X."""
    seed()
    report = metrics.contributor_report(1)
    por_pessoa = {c["contributor"]: c for c in report["contributors"]}

    # a issue 1 e da Ana, mas quem moveu para Review foi o Bruno
    assert "Review" not in por_pessoa["Ana"]["by_label"]
    assert approx(por_pessoa["Bruno"]["by_label"]["Review"]["hours"], 5)
    assert approx(por_pessoa["Ana"]["by_label"]["Doing"]["hours"], 14)


def test_modo_assignee_devolve_o_comportamento_antigo(monkeypatch):
    seed()
    monkeypatch.setattr(metrics, "attribution_mode", lambda: "assignee")
    report = metrics.contributor_report(1)
    ana = next(c for c in report["contributors"] if c["contributor"] == "Ana")
    # com atribuicao por responsavel, o Review da Ana volta a contar para ela
    assert approx(ana["by_label"]["Review"]["hours"], 5)
    assert approx(ana["total_hours"], 19)


def test_perfil_inclui_quem_so_revisou(monkeypatch):
    escopo_amplo(monkeypatch)
    seed()
    perfil = metrics.contributor_detail(1, "Bruno")
    # o Bruno nao e responsavel por nenhuma issue, mas revisou a 1 e tocou a 3
    assert {i["iid"] for i in perfil["issues"]} == {1, 3}
    assert approx(perfil["by_label"]["Review"]["hours"], 5)
    assert approx(perfil["by_label"]["Doing"]["hours"], 2)

    # e a issue 1 aparece no perfil dele so com a parte que foi dele
    issue1 = next(i for i in perfil["issues"] if i["iid"] == 1)
    assert issue1["role"] == ["Review"]
    assert list(issue1["time_by_column"]) == ["Review"]
    assert approx(issue1["working_hours"], 5)      # nao as 15h da issue inteira


def test_perfil_de_quem_executou_nao_leva_a_revisao():
    seed()
    perfil = metrics.contributor_detail(1, "Ana")
    assert "Review" not in perfil["by_label"]
    issue1 = next(i for i in perfil["issues"] if i["iid"] == 1)
    assert issue1["role"] == ["Doing"]
    assert approx(issue1["focus_hours"], 10)


def test_coluna_de_fila_nao_entra_no_tempo_de_ninguem(monkeypatch):
    """Espera nao e trabalho: o card parado na fila nao vira metrica de pessoa,
    mas continua contando na analise de gargalo."""
    seed()
    monkeypatch.setattr(metrics, "queue_labels", lambda: {"review"})
    escopo_amplo(monkeypatch)

    report = metrics.contributor_report(1)
    por_pessoa = {c["contributor"]: c for c in report["contributors"]}
    assert "Review" not in report["totals"]
    assert "Review" not in por_pessoa["Bruno"]["by_label"]
    assert approx(por_pessoa["Bruno"]["total_hours"], 2)   # so o Doing da issue 3

    # o gargalo continua enxergando a fila
    colunas = {c["label"]: c for c in metrics.column_report(1)["columns"]}
    assert approx(colunas["Review"]["avg_hours"], 5)


def test_fila_some_do_perfil_mas_a_issue_permanece(monkeypatch):
    seed()
    monkeypatch.setattr(metrics, "queue_labels", lambda: {"review"})
    escopo_amplo(monkeypatch)
    perfil = metrics.contributor_detail(1, "Bruno")
    assert "Review" not in perfil["by_label"]
    # o Bruno so revisou a issue 1, entao ela sai do perfil dele; a 3 fica
    assert {i["iid"] for i in perfil["issues"]} == {3}


def _com_fila(monkeypatch):
    """Deixa 'Review' como fila para exercitar o debito de espera."""
    monkeypatch.setattr(metrics, "queue_labels", lambda: {"review"})
    escopo_amplo(monkeypatch)


def test_espera_e_demerito_de_quem_pegou_a_revisao(monkeypatch):
    """A issue 1 espera em Review das -20h as -15h e o Bruno e quem a pega.
    A espera e demerito dele, nao da Ana que terminou e colocou na fila."""
    seed()
    with session() as conn:
        # depois do Review, a issue volta para Doing pelas maos do Bruno
        conn.execute(
            "INSERT INTO label_events VALUES (20,1,1,'add','Doing',3,'Bruno',?)", (iso(-15),)
        )
    _com_fila(monkeypatch)

    report = metrics.contributor_report(1)
    por_pessoa = {c["contributor"]: c for c in report["contributors"]}
    assert approx(por_pessoa["Bruno"]["waiting_hours"], 5)
    assert por_pessoa["Bruno"]["waiting_issues"] == 1
    assert por_pessoa["Ana"]["waiting_hours"] == 0     # ela so colocou na fila


def test_fila_sem_revisor_cai_para_a_label_de_nome(monkeypatch):
    seed()
    with session() as conn:
        # a issue 2 entra em Review as -3h e fica esperando, marcada 'Bruno'
        conn.executemany("INSERT INTO label_events VALUES (?,?,?,?,?,?,?,?)", [
            (21, 1, 2, "add", "Review", 2, "Ana", iso(-3)),
            (22, 1, 2, "add", "Bruno", 2, "Ana", iso(-3)),
        ])
    _com_fila(monkeypatch)

    assert metrics.person_labels(1).get("bruno") == "Bruno"
    report = metrics.contributor_report(1)
    bruno = next(c for c in report["contributors"] if c["contributor"] == "Bruno")
    assert approx(bruno["waiting_hours"], 3)


def test_fila_sem_dono_quando_nao_da_para_saber(monkeypatch):
    seed()
    with session() as conn:
        conn.execute(
            "INSERT INTO label_events VALUES (23,1,2,'add','Review',2,'Ana',?)", (iso(-3),)
        )
    _com_fila(monkeypatch)
    report = metrics.contributor_report(1)
    orfa = next(c for c in report["contributors"]
                if c["contributor"] == metrics.QUEUE_UNCLAIMED)
    # 3h da issue 2 (ainda esperando) + 5h da issue 1, que nunca saiu do Review
    assert approx(orfa["waiting_hours"], 8)


def test_perfil_traz_a_espera_causada(monkeypatch):
    seed()
    with session() as conn:
        conn.execute(
            "INSERT INTO label_events VALUES (24,1,1,'add','Doing',3,'Bruno',?)", (iso(-15),)
        )
    _com_fila(monkeypatch)
    perfil = metrics.contributor_detail(1, "Bruno")
    assert approx(perfil["waiting_hours"], 5)
    assert perfil["waiting_issues"] == 1
    issue1 = next(i for i in perfil["issues"] if i["iid"] == 1)
    assert approx(issue1["waiting_hours"], 5)


def test_escopo_assigned_ignora_etapa_feita_em_issue_alheia():
    """SCOPE=assigned: etapa feita numa issue de outra pessoa nao entra no
    tempo individual — o Doing que o Bruno fez na issue 3 fica de fora."""
    seed()
    report = metrics.contributor_report(1)
    por_pessoa = {c["contributor"]: c for c in report["contributors"]}

    assert approx(por_pessoa["Ana"]["by_label"]["Doing"]["hours"], 14)
    assert "Review" not in por_pessoa["Ana"]["by_label"]

    bruno = por_pessoa["Bruno"]
    assert "Doing" not in bruno["by_label"]    # 2h na issue 3, que nao e dele


def test_revisao_entra_no_acumulado_mesmo_em_assigned():
    """A revisao e a excecao do escopo: revisar e sempre trabalhar no card de
    outra pessoa, entao o `assigned` a apagaria por completo."""
    seed()
    report = metrics.contributor_report(1)
    bruno = next(c for c in report["contributors"] if c["contributor"] == "Bruno")
    assert approx(bruno["by_label"]["Review"]["hours"], 5)
    assert approx(bruno["total_hours"], 5)     # so a revisao; o Doing alheio fica fora
    assert approx(bruno["review_hours"], 5) and bruno["review_issues"] == 1


def test_escopo_assigned_preserva_o_total_do_projeto():
    """O recorte e da atribuicao individual. O peso de cada coluna e conta de
    projeto e nao pode encolher junto — senao a rosca e o treemap mentem."""
    seed()
    report = metrics.contributor_report(1)
    assert approx(report["totals"]["Review"]["hours"], 5)   # a revisao existiu
    assert approx(report["totals"]["Doing"]["hours"], 16)   # 10 + 4 + 2

    soma_pessoas = sum(c["total_hours"] for c in report["contributors"])
    soma_colunas = sum(v["hours"] for v in report["totals"].values())
    assert soma_pessoas < soma_colunas   # a diferenca e o que ficou fora do escopo


def test_escopo_assigned_no_perfil():
    seed()
    perfil = metrics.contributor_detail(1, "Ana")
    assert "Review" not in perfil["by_label"]
    issue1 = next(i for i in perfil["issues"] if i["iid"] == 1)
    assert issue1["role"] == ["Doing"]


def test_gargalo_por_coluna_nao_depende_do_escopo():
    seed()
    colunas = {c["label"]: c for c in metrics.column_report(1)["columns"]}
    assert approx(colunas["Review"]["avg_hours"], 5)
    assert colunas["Doing"]["completed_passes"] == 2


def test_issue_traz_o_tempo_de_cada_pessoa():
    """A pergunta 'quanto o fulano ficou revisando esta issue' tem resposta
    na propria issue: a issue 1 e da Ana (10h em Doing), mas quem revisou
    foram as 5h do Bruno."""
    seed()
    issue = next(i for i in metrics.issue_report(1)["issues"] if i["iid"] == 1)
    por_pessoa = {p["person"]: p for p in issue["participants"]}
    assert approx(por_pessoa["Bruno"]["by_column"]["Review"]["hours"], 5)
    assert approx(por_pessoa["Ana"]["by_column"]["Doing"]["hours"], 10)
    assert approx(por_pessoa["Bruno"]["share"], 33.3, tol=0.2)
    assert por_pessoa["Bruno"]["by_column"]["Review"]["stints"] == 1


def test_perfil_de_quem_so_revisou_em_assigned():
    """Mesmo com SCOPE=assigned, quem so revisou tem perfil: a issue alheia
    entra com a parte que foi dele, e o acumulado inclui a revisao."""
    seed()
    assert metrics.scope_mode() == "assigned"
    perfil = metrics.contributor_detail(1, "Bruno")
    issue1 = next(i for i in perfil["issues"] if i["iid"] == 1)
    assert approx(issue1["review_hours"], 5)
    assert approx(issue1["working_hours"], 5)   # na issue 1 ele so revisou
    assert issue1["role"] == ["Review"]
    assert approx(perfil["by_label"]["Review"]["hours"], 5)
    assert approx(perfil["total_hours"], 5)
    assert approx(perfil["review_hours"], 5) and perfil["review_issues"] == 1

    issue = next(i for i in metrics.issue_report(1)["issues"] if i["iid"] == 1)
    assert "Bruno" in {p["person"] for p in issue["participants"]}


def test_participante_marca_coluna_ainda_aberta():
    seed()
    issue = next(i for i in metrics.issue_report(1)["issues"] if i["iid"] == 2)
    ana = next(p for p in issue["participants"] if p["person"] == "Ana")
    assert ana["by_column"]["Doing"]["still_in_column"] is True
    assert approx(ana["hours"], 4)


def test_participante_nao_ganha_o_tempo_de_fila(monkeypatch):
    """Com Review como fila, as 5h nao viram trabalho de ninguem: aparecem
    como espera causada por quem pegou o card."""
    seed()
    with session() as conn:
        conn.execute(
            "INSERT INTO label_events VALUES (21,1,1,'add','Doing',3,'Bruno',?)", (iso(-15),)
        )
    monkeypatch.setattr(metrics, "queue_labels", lambda: {"review"})

    issue = next(i for i in metrics.issue_report(1)["issues"] if i["iid"] == 1)
    bruno = next(p for p in issue["participants"] if p["person"] == "Bruno")
    assert "Review" not in bruno["by_column"]
    assert approx(bruno["waiting_hours"], 5)


def test_coluna_de_revisao_detectada_pelo_board():
    seed()
    assert metrics.review_label(1) == "Review"
    assert metrics.focus_label(1) == "Doing"


def test_fila_nao_e_coluna_de_revisao(monkeypatch):
    """'Waiting Review' e espera, nao revisao: se a unica coluna com cara de
    review for fila, o dash prefere nao ter metrica a ter uma errada."""
    seed()
    monkeypatch.setattr(metrics, "queue_labels", lambda: {"review"})
    assert metrics.review_label(1) is None


def test_tempo_revisando_nao_duplica_o_total(monkeypatch):
    """Em SCOPE=touched a revisao ja esta no total; a conta de revisao e um
    recorte dele, nao uma soma nova."""
    escopo_amplo(monkeypatch)
    seed()
    report = metrics.contributor_report(1)
    bruno = next(c for c in report["contributors"] if c["contributor"] == "Bruno")
    assert report["review_label"] == "Review"
    assert approx(bruno["review_hours"], 5)
    assert approx(bruno["total_hours"], 7)           # 5h de review + 2h de Doing
    assert approx(bruno["by_label"]["Review"]["hours"], 5)


# ── fim de semana ────────────────────────────────────────────────────────

def local(texto: str) -> datetime:
    """Horario local, que e o fuso em que o fim de semana e avaliado."""
    return datetime.fromisoformat(texto).astimezone()


def test_fim_de_semana_nao_conta_como_tempo(com_fim_de_semana):
    """Um card que entrou na sexta as 16h e saiu na segunda as 10h nao ficou
    66h na coluna: ficou 18h uteis."""
    assert approx(metrics.elapsed(local("2026-08-21T16:00"),
                                 local("2026-08-24T10:00")) / 3600, 18)


def test_intervalo_inteiro_no_fim_de_semana_e_zero(com_fim_de_semana):
    assert metrics.elapsed(local("2026-08-22T09:00"), local("2026-08-23T18:00")) == 0.0


def test_semana_cheia_conta_cinco_dias(com_fim_de_semana):
    assert approx(metrics.elapsed(local("2026-08-19T09:00"),
                                 local("2026-08-26T09:00")) / 3600, 120)


def test_virada_da_sexta_para_o_sabado(com_fim_de_semana):
    """A conta e no fuso local: em UTC a sexta brasileira ja seria sabado."""
    assert approx(metrics.elapsed(local("2026-08-21T23:00"),
                                 local("2026-08-22T01:00")) / 3600, 1)


def test_desligar_a_regra_devolve_o_tempo_de_relogio():
    """Com a regra desligada a conta volta a ser o tempo de relogio."""
    assert approx(metrics.elapsed(local("2026-08-21T16:00"),
                                 local("2026-08-24T10:00")) / 3600, 66)


def test_a_regra_vale_para_coluna_fila_e_lead_time(monkeypatch):
    """A conta e uma so: ligar ou desligar o fim de semana move todos os
    numeros juntos, nunca metade deles."""
    seed()
    sem_regra = metrics.contributor_report(1)
    monkeypatch.setattr(metrics, "skip_weekends", lambda: True)
    com_fds = metrics.contributor_report(1)
    # o seed usa horas relativas a agora, entao o total so pode encolher
    assert sum(c["total_hours"] for c in com_fds["contributors"]) <= \
        sum(c["total_hours"] for c in sem_regra["contributors"])


# ── faixas nao uteis do dia ──────────────────────────────────────────────

def com_janela(monkeypatch, faixa="10:00-14:00"):
    """Liga a faixa nao util sem mexer no .env de quem roda os testes."""
    monkeypatch.setattr(metrics, "non_working_windows",
                        lambda: [(time.fromisoformat(faixa.split("-")[0]),
                                  time.fromisoformat(faixa.split("-")[1]))])


def test_janela_de_aula_nao_conta(monkeypatch):
    """Das 10h as 14h o time esta em aula: um card que passa das 9h as 15h
    ficou 2h de trabalho, nao 6h."""
    com_janela(monkeypatch)
    assert approx(metrics.elapsed(local("2026-08-18T09:00"),
                                 local("2026-08-18T15:00")) / 3600, 2)


def test_intervalo_inteiro_dentro_da_janela_e_zero(monkeypatch):
    com_janela(monkeypatch)
    assert metrics.elapsed(local("2026-08-18T11:00"), local("2026-08-18T13:00")) == 0.0


def test_fora_da_janela_o_tempo_e_inteiro(monkeypatch):
    com_janela(monkeypatch)
    assert approx(metrics.elapsed(local("2026-08-18T18:00"),
                                 local("2026-08-19T09:00")) / 3600, 15)


def test_janela_e_fim_de_semana_nao_descontam_duas_vezes(monkeypatch, com_fim_de_semana):
    """O sabado ja saiu inteiro; descontar a janela dele tiraria o mesmo
    tempo duas vezes."""
    com_janela(monkeypatch)
    # sexta 9h -> segunda 11h: 11h uteis na sexta + 10h na segunda
    assert approx(metrics.elapsed(local("2026-08-21T09:00"),
                                 local("2026-08-24T11:00")) / 3600, 21)


def test_dia_util_tem_vinte_horas(monkeypatch, com_fim_de_semana):
    com_janela(monkeypatch)
    assert approx(metrics.elapsed(local("2026-08-17T00:00"),
                                 local("2026-08-24T00:00")) / 3600, 100)


def test_faixa_mal_escrita_e_ignorada(monkeypatch):
    """`.env` com a faixa torta nao pode derrubar o dash nem zerar o tempo."""
    settings = get_settings()
    monkeypatch.setattr(settings, "non_working_hours", "dez as duas, 14:00-10:00")
    assert metrics.non_working_windows() == []
    assert approx(metrics.elapsed(local("2026-08-18T09:00"),
                                  local("2026-08-18T15:00")) / 3600, 6)


# ── feriados ─────────────────────────────────────────────────────────────

# capturada na importacao, antes de o conftest desligar o calendario
_HOLIDAYS_REAL = metrics.holidays


def com_feriados(monkeypatch, extras=""):
    """Liga o calendario brasileiro sem depender do .env da maquina."""
    settings = get_settings()
    monkeypatch.setattr(settings, "holiday_calendar", "br")
    monkeypatch.setattr(settings, "holidays", extras)
    monkeypatch.setattr(metrics, "holidays", _HOLIDAYS_REAL)
    metrics._national_holidays.cache_clear()


def test_feriado_fixo_nao_conta(monkeypatch):
    """7 de setembro de 2026 cai numa segunda-feira."""
    com_feriados(monkeypatch)
    assert metrics.elapsed(local("2026-09-07T08:00"), local("2026-09-07T20:00")) == 0.0


def test_feriado_movel_sai_da_pascoa(monkeypatch):
    """Carnaval, sexta-feira santa e corpus christi mudam de data todo ano."""
    com_feriados(monkeypatch)
    assert metrics._easter(2026) == date(2026, 4, 5)   # pascoa
    assert date(2026, 2, 17) in metrics.holidays(2026)   # carnaval
    assert date(2026, 4, 3) in metrics.holidays(2026)    # sexta-feira santa
    assert date(2026, 6, 4) in metrics.holidays(2026)    # corpus christi
    assert date(2025, 3, 4) in metrics.holidays(2025)    # carnaval do ano anterior


def test_intervalo_que_atravessa_o_feriado(monkeypatch):
    """Sexta 21/04/2026 (Tiradentes) cai numa terca: de segunda a quarta o
    card so acumula os dois dias uteis."""
    com_feriados(monkeypatch)
    assert approx(metrics.elapsed(local("2026-04-20T08:00"),
                                 local("2026-04-22T08:00")) / 3600, 24)


def test_feriado_extra_do_calendario_da_turma(monkeypatch):
    com_feriados(monkeypatch, extras="2026-08-18")
    assert metrics.elapsed(local("2026-08-18T09:00"), local("2026-08-18T18:00")) == 0.0
    assert date(2026, 8, 18) in metrics.holidays(2026)


def test_data_torta_em_holidays_e_ignorada(monkeypatch):
    com_feriados(monkeypatch, extras="18/08/2026, 2026-08-19")
    assert date(2026, 8, 19) in metrics.holidays(2026)
    assert metrics.elapsed(local("2026-08-18T09:00"), local("2026-08-18T18:00")) > 0


def test_feriado_e_janela_nao_descontam_duas_vezes(monkeypatch):
    """O feriado ja saiu inteiro; descontar a janela dele dobraria a conta."""
    com_feriados(monkeypatch)
    com_janela(monkeypatch)
    # 20/04 (segunda) e 22/04 (quarta) contam 20h cada; 21/04 e feriado
    assert approx(metrics.elapsed(local("2026-04-20T00:00"),
                                 local("2026-04-23T00:00")) / 3600, 40)


def test_sem_calendario_o_feriado_conta(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "holiday_calendar", "")
    monkeypatch.setattr(settings, "holidays", "")
    monkeypatch.setattr(metrics, "holidays", _HOLIDAYS_REAL)
    metrics._national_holidays.cache_clear()
    assert approx(metrics.elapsed(local("2026-09-07T08:00"),
                                 local("2026-09-07T20:00")) / 3600, 12)
