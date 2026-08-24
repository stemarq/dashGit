"""Testes do relatorio comparativo entre sprints."""

import os
import tempfile
from datetime import datetime, timedelta, timezone

os.environ.setdefault("GITLAB_TOKEN", "test-token")
_DB = os.path.join(tempfile.mkdtemp(), "report.db")
os.environ["DATABASE_PATH"] = _DB

from app import report  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import init_db, session  # noqa: E402

get_settings.cache_clear()
get_settings()

NOW = datetime.now(timezone.utc)


def iso(offset_hours: float) -> str:
    return (NOW + timedelta(hours=offset_hours)).isoformat()


def seed() -> None:
    """Duas sprints + uma issue orfa, com commits citando as issues.

    Sprint 1: issue 1, fechada, 10h em Doing (Ana) e 5h em Review (Bruno).
    Sprint 2: issue 2, aberta, 4h em Doing (Ana).
    Sem sprint: issue 3, sem tempo — existe so para provar que nao entra na
    comparacao.
    """
    init_db()
    with session() as conn:
        for table in ("commits", "label_events", "issues", "board_lists",
                      "milestones", "projects"):
            conn.execute(f"DELETE FROM {table}")
        conn.execute(
            "INSERT INTO projects (id, path, name, web_url, synced_at)"
            " VALUES (1,'g/p','P','u',?)", (iso(0),)
        )
        for pos, label in enumerate(["Doing", "Review"]):
            conn.execute("INSERT INTO board_lists VALUES (1, 10, 'Dev', ?, ?, ?)",
                         (pos + 1, pos, label))
        conn.execute("INSERT INTO milestones VALUES (1, 90, 1, 'Sprint 1', 'closed',"
                     " '2026-01-01', '2026-01-14', 'm1')")
        conn.execute("INSERT INTO milestones VALUES (1, 91, 2, 'Sprint 2', 'active',"
                     " '2026-01-15', '2026-01-28', 'm2')")

        issues = [
            (1, 1, 101, "Login", "closed", iso(-30), iso(-10), iso(-10), 1, "Bruno",
             2, "Ana", "Sprint 1", "u1"),
            (1, 2, 102, "Cache", "opened", iso(-8), None, iso(-4), 1, "Bruno",
             2, "Ana", "Sprint 2", "u2"),
            (1, 3, 103, "Solta", "opened", iso(-6), None, iso(-6), 1, "Bruno",
             2, "Ana", None, "u3"),
        ]
        conn.executemany(
            "INSERT INTO issues (project_id, iid, id, title, state, created_at, closed_at,"
            " updated_at, author_id, author_name, assignee_id, assignee_name, milestone,"
            " web_url) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", issues,
        )
        conn.executemany(
            "INSERT INTO label_events VALUES (?,?,?,?,?,?,?,?)",
            [
                (1, 1, 1, "add", "Doing", 2, "Ana", iso(-30)),
                (2, 1, 1, "remove", "Doing", 2, "Ana", iso(-20)),
                (3, 1, 1, "add", "Review", 1, "Bruno", iso(-20)),
                (4, 1, 1, "remove", "Review", 1, "Bruno", iso(-15)),
                (5, 1, 2, "add", "Doing", 2, "Ana", iso(-4)),
            ],
        )
        conn.executemany(
            "INSERT INTO commits (project_id, id, short_id, title, author_name, author_email,"
            " committed_at, additions, deletions, is_merge, web_url)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [
                # a issue 1 e da Sprint 1, mesmo com o commit feito agora
                (1, "c1", "c1", "feat(#1): adiciona o login", "Ana", "a@x", iso(-1), 1, 0, 0, "u"),
                # fora do padrao pelo espacamento — maiuscula e permitida
                (1, "c2", "c2", "fix (#1): corrige o login", "Ana", "a@x", iso(-1), 1, 0, 0, "u"),
                (1, "c3", "c3", "docs(#2): documenta o cache", "Ana", "a@x", iso(-2), 1, 0, 0, "u"),
                (1, "c4", "c4", "chore: sem issue nenhuma", "Bruno", "b@x", iso(-2), 1, 0, 0, "u"),
            ],
        )


def approx(value, expected, tol=0.05):
    return abs(value - expected) <= tol


def test_sprints_da_mais_recente_para_a_mais_antiga():
    seed()
    d = report.sprint_report(1)
    assert [s["milestone"] for s in d["sprints"]] == ["Sprint 2", "Sprint 1"]
    assert d["sprints"][0]["compared_to"] == "Sprint 1"
    assert d["sprints"][-1]["compared_to"] is None


def test_issues_sem_sprint_ficam_fora_da_comparacao():
    """`(sem sprint)` e um balde: se virasse linha, a sprint mais antiga
    seria comparada contra ele e inventaria variacao."""
    seed()
    d = report.sprint_report(1)
    assert "(sem sprint)" not in [s["milestone"] for s in d["sprints"]]
    assert d["unscheduled"]["issues"] == 1
    assert d["sprints"][-1]["delta"] == {}


def test_commit_vai_para_a_sprint_da_issue_e_nao_pela_data():
    """Os commits da issue 1 foram feitos ontem, mas a issue e da Sprint 1."""
    seed()
    por_sprint = {s["milestone"]: s for s in report.sprint_report(1)["sprints"]}
    assert por_sprint["Sprint 1"]["commits"] == 2
    assert por_sprint["Sprint 2"]["commits"] == 1


def test_commit_sem_issue_nao_entra_em_sprint_nenhuma():
    seed()
    d = report.sprint_report(1)
    assert d["orphan_commits"] == 1
    assert sum(s["commits"] for s in d["sprints"]) == 3


def test_aderencia_por_sprint_e_a_variacao_em_pontos():
    seed()
    por_sprint = {s["milestone"]: s for s in report.sprint_report(1)["sprints"]}
    assert approx(por_sprint["Sprint 1"]["convention_pct"], 50.0)   # 1 de 2
    assert approx(por_sprint["Sprint 2"]["convention_pct"], 100.0)  # 1 de 1
    assert approx(por_sprint["Sprint 2"]["delta"]["convention_pp"], 50.0)


def test_sprint_sem_commit_nao_finge_zero_por_cento():
    """Sem commit nao ha aderencia a medir — 0% diria que o time errou."""
    seed()
    with session() as conn:
        conn.execute("DELETE FROM commits WHERE id = 'c3'")
    sprint2 = report.sprint_report(1)["sprints"][0]
    assert sprint2["commits"] == 0
    assert sprint2["convention_pct"] is None
    assert sprint2["delta"]["convention_pp"] is None


def test_tempo_e_pessoas_batem_com_as_metricas():
    seed()
    por_sprint = {s["milestone"]: s for s in report.sprint_report(1)["sprints"]}
    assert approx(por_sprint["Sprint 1"]["by_label"]["Doing"]["hours"], 10)
    assert approx(por_sprint["Sprint 1"]["by_label"]["Review"]["hours"], 5)
    pessoas = {p["contributor"]: p for p in por_sprint["Sprint 1"]["people"]}
    assert approx(pessoas["Ana"]["hours"], 10)
    assert approx(pessoas["Bruno"]["review_hours"], 5)   # revisou a issue da Ana


def test_html_e_autocontido_e_cita_a_regra():
    seed()
    html = report.render_html(report.sprint_report(1))
    assert html.startswith("<!doctype html>")
    assert "tipo(#issue): descricao" in html
    assert "Sprint 1" in html and "Sprint 2" in html
    # nada de CSS/JS externo: o arquivo e para virar anexo de entrega
    assert "<script" not in html and "http://" not in html


def test_html_para_impressao_tem_o_disparo_e_o_normal_nao():
    """O PDF sai pela caixa de impressao do navegador; o arquivo baixado como
    HTML continua sem script, para poder ser anexado em qualquer lugar."""
    seed()
    d = report.sprint_report(1)
    assert "<script" not in report.render_html(d)
    printable = report.render_html(d, autoprint=True)
    assert "window.print()" in printable
    assert "Salvar como PDF" in printable
    assert "@page" in printable and "table-header-group" in printable


# ── resumo de uma sprint ─────────────────────────────────────────────────

def test_resumo_traz_a_sprint_inteira():
    seed()
    d = report.sprint_summary(1, "Sprint 1")
    assert d["milestone"]["closed_issues"] == 1 and d["milestone"]["issues"] == 1
    assert {p["contributor"] for p in d["people"]} == {"Ana", "Bruno"}
    assert [c["label"] for c in d["columns"]] == ["Doing", "Review"]
    assert [i["iid"] for i in d["issues"]] == [1]
    assert d["commits"]["total"] == 2 and d["commits"]["off"] == 1


def test_resumo_compara_com_a_sprint_anterior():
    seed()
    d = report.sprint_summary(1, "Sprint 2")
    assert d["compared_to"] == "Sprint 1"
    assert d["delta"]["convention_pp"] == 50.0     # 100% contra 50%
    # a issue 2 continua em Doing, entao o tempo dela cresce com o relogio:
    # a comparacao e da ordem de grandeza (4h contra 10h), nao do centesimo
    assert approx(d["delta"]["focus_hours"], -60.0, tol=1.5)


def test_resumo_da_primeira_sprint_nao_inventa_comparacao():
    seed()
    d = report.sprint_summary(1, "Sprint 1")
    assert d["compared_to"] is None and d["delta"] == {}


def test_resumo_de_sprint_inexistente_volta_vazio():
    seed()
    assert report.sprint_summary(1, "Sprint 9") == {}
    assert report.sprint_summary(1, "(sem sprint)") == {}


def test_resumo_lista_os_commits_fora_do_padrao_com_o_motivo():
    seed()
    fora = report.sprint_summary(1, "Sprint 1")["commits"]["offenders"]
    assert [c["short_id"] for c in fora] == ["c2"]
    assert fora[0]["convention"] == ["espaco"]
    assert fora[0]["issue"] == 1


def test_html_do_resumo_e_autocontido():
    seed()
    d = report.sprint_summary(1, "Sprint 1")
    html = report.render_summary_html(d)
    assert html.startswith("<!doctype html>") and "Sprint 1" in html
    assert "<script" not in html and "http://" not in html
    assert "window.print()" in report.render_summary_html(d, autoprint=True)
