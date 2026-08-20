"""Cache local em SQLite.

O GitLab e a fonte da verdade; aqui guardamos apenas o material bruto
(issues + eventos de label) para nao refazer N+1 chamadas a cada grafico.
"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app.config import get_settings

