"""Testes do motor de metricas com um cache SQLite sintetico."""

import os
import tempfile
from datetime import datetime, timedelta, timezone

os.environ.setdefault("GITLAB_TOKEN", "test-token")
