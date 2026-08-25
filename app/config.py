from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    gitlab_token: str = ""
    gitlab_api_url: str = "https://gitlab.com/api/v4"
    default_projects: str = ""
    database_path: str = "./dashgit.db"
    max_concurrency: int = 8

    # Colunas que sao fila de espera, nao trabalho: entram no board mas ficam
    # fora de toda conta de tempo. Um card pode passar meses no Backlog sem
    # que ninguem tenha trabalhado nele.
    excluded_labels: str = "Backlog"

    # Coluna que representa "trabalho acontecendo". Vazio = detecta sozinho.
    focus_label: str = ""

    # Faixas do dia que nao sao tempo de trabalho (aula, almoco). Formato
    # HH:MM-HH:MM, separadas por virgula. Vazio = o dia inteiro conta.
    non_working_hours: str = ""

    # Feriados nacionais calculados automaticamente. "br" = Brasil, vazio =
    # nenhum. Os moveis (carnaval, sexta-feira santa, corpus christi) saem da
    # data da Pascoa, entao valem para qualquer ano sem tabela nova.
    holiday_calendar: str = "br"

    # Feriados extras (municipal, recesso, semana de prova), em ISO e
    # separados por virgula. Ex.: 2026-01-25,2026-07-09
    holidays: str = ""

    # Sabado e domingo nao contam como tempo de trabalho: um card que passa
    # a sexta-feira em Review nao ficou 3 dias esperando, ficou 1 dia util.
    # O fim de semana e avaliado no fuso da maquina que roda o dash.
    skip_weekends: bool = True

    # Commits de quem nao e do time (bot do template, professor, convidado)
    # ficam fora de toda metrica de commit por padrao: eles nao sao trabalho
    # do time e afundam a aderencia a convencao. O que sobrou de fora aparece
    # como nota, nunca some calado.
    count_non_members: bool = False

    # Coluna de revisao. Vazio = detecta sozinho (Review / Revisao / QA).
    # O tempo dela e medido a parte do SCOPE: revisar card dos outros e o
    # caso normal, e ficaria invisivel se seguisse a regra de atribuicao.
    review_label: str = ""

    # De quem e o tempo de cada coluna:
    #   mover    = quem moveu o card para la (o executor faz o Doing, o
    #              revisor faz o Review) — cada etapa vai para quem a fez
    #   assignee = tudo para o responsavel atual da issue
    attribution: str = "mover"

    # Que issues contam no tempo de uma pessoa:
    #   assigned = so as issues atribuidas a ela
    #   touched  = qualquer issue em que ela fez alguma etapa
    scope: str = "assigned"

    # Colunas de espera: o card fica la parado esperando alguem pegar. O tempo
    # conta na analise de gargalo (e onde o fluxo trava) mas nao entra no
    # tempo de ninguem — ninguem esta trabalhando enquanto o card espera.
    queue_labels: str = ""

    @property
    def holiday_list(self) -> list[str]:
        return [d.strip() for d in self.holidays.split(",") if d.strip()]

    @property
    def non_working_list(self) -> list[tuple[str, str]]:
        faixas = []
        for parte in self.non_working_hours.split(","):
            parte = parte.strip()
            if not parte:
                continue
            inicio, _, fim = parte.partition("-")
            if inicio.strip() and fim.strip():
                faixas.append((inicio.strip(), fim.strip()))
        return faixas

    @property
    def project_list(self) -> list[str]:
        return [p.strip() for p in self.default_projects.split(",") if p.strip()]

    @property
    def excluded_list(self) -> list[str]:
        return [x.strip() for x in self.excluded_labels.split(",") if x.strip()]

    @property
    def queue_list(self) -> list[str]:
        return [x.strip() for x in self.queue_labels.split(",") if x.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
