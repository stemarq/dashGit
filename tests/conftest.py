"""Ajustes comuns aos testes.

Os seeds sinteticos contam horas corridas a partir de agora ("ha 30h"), entao
cairiam no fim de semana ou nao dependendo do dia em que a suite roda. Aqui a
regra de fim de semana fica desligada por padrao: quem a testa e o bloco de
testes dela, que a liga de volta.
"""

import pytest

from app import metrics


@pytest.fixture(autouse=True)
def sem_fim_de_semana(monkeypatch):
    monkeypatch.setattr(metrics, "skip_weekends", lambda: False)


@pytest.fixture
def com_fim_de_semana(monkeypatch):
    monkeypatch.setattr(metrics, "skip_weekends", lambda: True)
