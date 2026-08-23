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


