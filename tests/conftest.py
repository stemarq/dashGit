"""Ajustes comuns aos testes.

Os seeds sinteticos contam horas corridas a partir de agora ("ha 30h"), entao
cairiam no fim de semana (ou na janela de aula) conforme o dia e a hora em que
a suite roda. Aqui as regras de calendario ficam desligadas por padrao — e o
`.env` da maquina nao muda o resultado dos testes. Quem as testa e o bloco de
testes delas, que as liga de volta.
"""

import pytest

from app import metrics


@pytest.fixture(autouse=True)
def sem_regras_de_calendario(monkeypatch):
    monkeypatch.setattr(metrics, "skip_weekends", lambda: False)
    monkeypatch.setattr(metrics, "non_working_windows", lambda: [])


@pytest.fixture
def com_fim_de_semana(monkeypatch):
    monkeypatch.setattr(metrics, "skip_weekends", lambda: True)
