"""Testes do motor de metricas com um cache SQLite sintetico."""

import os
import tempfile
from datetime import datetime, timedelta, timezone

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


