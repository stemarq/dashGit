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


