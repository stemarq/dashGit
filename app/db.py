"""Cache local em SQLite.

O GitLab e a fonte da verdade; aqui guardamos apenas o material bruto
(issues + eventos de label) para nao refazer N+1 chamadas a cada grafico.
"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app.config import get_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id           INTEGER PRIMARY KEY,
    path         TEXT NOT NULL,
    name         TEXT,
    web_url      TEXT,
    synced_at    TEXT
);

CREATE TABLE IF NOT EXISTS board_lists (
    project_id   INTEGER NOT NULL,
    board_id     INTEGER NOT NULL,
    board_name   TEXT,
    list_id      INTEGER NOT NULL,
    position     INTEGER,
    label_name   TEXT NOT NULL,
    PRIMARY KEY (project_id, board_id, list_id)
);

CREATE TABLE IF NOT EXISTS issues (
    project_id    INTEGER NOT NULL,
    iid           INTEGER NOT NULL,
    id            INTEGER,
    title         TEXT,
    state         TEXT,
    created_at    TEXT,
    closed_at     TEXT,
    updated_at    TEXT,
    author_id     INTEGER,
    author_name   TEXT,
    assignee_id   INTEGER,
    assignee_name TEXT,
    milestone     TEXT,
    web_url       TEXT,
    PRIMARY KEY (project_id, iid)
);

CREATE TABLE IF NOT EXISTS milestones (
    project_id  INTEGER NOT NULL,
    id          INTEGER NOT NULL,
    iid         INTEGER,
    title       TEXT NOT NULL,
    state       TEXT,
    start_date  TEXT,
    due_date    TEXT,
    web_url     TEXT,
    PRIMARY KEY (project_id, id)
);

CREATE TABLE IF NOT EXISTS label_events (
    id          INTEGER PRIMARY KEY,
    project_id  INTEGER NOT NULL,
    issue_iid   INTEGER NOT NULL,
    action      TEXT NOT NULL,
    label_name  TEXT,
    user_id     INTEGER,
    user_name   TEXT,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_issue
    ON label_events (project_id, issue_iid, created_at);
CREATE INDEX IF NOT EXISTS idx_issues_assignee
    ON issues (project_id, assignee_id);
CREATE INDEX IF NOT EXISTS idx_issues_milestone
    ON issues (project_id, milestone);

CREATE TABLE IF NOT EXISTS commits (
    project_id    INTEGER NOT NULL,
    id            TEXT NOT NULL,
    short_id      TEXT,
    title         TEXT,
    author_name   TEXT,
    author_email  TEXT,
    committed_at  TEXT,
    additions     INTEGER,
    deletions     INTEGER,
    is_merge      INTEGER DEFAULT 0,
    web_url       TEXT,
    PRIMARY KEY (project_id, id)
);

CREATE INDEX IF NOT EXISTS idx_commits_when
    ON commits (project_id, committed_at);
CREATE INDEX IF NOT EXISTS idx_commits_author
    ON commits (project_id, author_email);

CREATE TABLE IF NOT EXISTS sync_state (
    project_id      INTEGER PRIMARY KEY,
    last_synced_at  TEXT
);
"""


def _path() -> str:
    p = Path(get_settings().database_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    return str(p)


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_path(), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


