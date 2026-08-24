"""Testes das metricas de commits, com um cache SQLite sintetico."""

import os
import tempfile
from datetime import datetime, timedelta, timezone

os.environ.setdefault("GITLAB_TOKEN", "test-token")
_DB = os.path.join(tempfile.mkdtemp(), "commits.db")
os.environ["DATABASE_PATH"] = _DB

from app import commits as cm  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import init_db, session  # noqa: E402

get_settings.cache_clear()
get_settings()

NOW = datetime.now(timezone.utc)


def iso(offset_hours: float) -> str:
    return (NOW + timedelta(hours=offset_hours)).isoformat()


def seed(rows=None) -> None:
    init_db()
    with session() as conn:
        conn.execute("DELETE FROM commits")
        conn.execute("DELETE FROM projects")
        conn.execute(
            "INSERT INTO projects (id, path, name, web_url, synced_at) VALUES (1,'g/p','P','u',?)",
            (iso(0),),
        )
        conn.executemany(
            "INSERT INTO commits (project_id, id, short_id, title, author_name, author_email,"
            " committed_at, additions, deletions, is_merge, web_url) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            rows if rows is not None else [
                (1, "a1", "a1", "feat: x", "Ana", "ana@x.com", iso(-5), 30, 5, 0, "u1"),
                (1, "a2", "a2", "fix: y", "Ana", "ana@x.com", iso(-4), 10, 2, 0, "u2"),
                (1, "b1", "b1", "docs: z", "Bruno", "bruno@x.com", iso(-3), 4, 40, 0, "u3"),
                (1, "m1", "m1", "Merge branch", "Bruno", "bruno@x.com", iso(-2), 999, 999, 1, "u4"),
            ],
        )


def approx(value, expected, tol=0.05):
    return abs(value - expected) <= tol


def test_merge_commit_fica_de_fora_das_linhas():
    """Merge repete as linhas dos commits que traz; contar infla tudo."""
    seed()
    t = cm.commit_report(1)["totals"]
    assert t["commits"] == 3
    assert t["additions"] == 44 and t["deletions"] == 47

    com_merge = cm.commit_report(1, include_merges=True)["totals"]
    assert com_merge["commits"] == 4
    assert com_merge["additions"] == 1043


def test_totais_por_autor():
    seed()
    autores = {a["author"]: a for a in cm.commit_report(1)["authors"]}
    assert autores["Ana"]["commits"] == 2
    assert autores["Ana"]["additions"] == 40
    assert autores["Bruno"]["commits"] == 1
    assert autores["Bruno"]["net"] == 4 - 40      # removeu mais do que somou
    assert [a["author"] for a in cm.commit_report(1)["authors"]] == ["Ana", "Bruno"]


