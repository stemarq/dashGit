"""Testes do relatorio comparativo entre sprints."""

import os
import tempfile
from datetime import datetime, timedelta, timezone

os.environ.setdefault("GITLAB_TOKEN", "test-token")
