# dashGit

API + dashboard de métricas de contribuidores a partir de **boards do GitLab**.

Responde perguntas do tipo: *quanto tempo cada pessoa acumulou em "Doing"?*,
*qual coluna é o gargalo?*, *quais issues estão paradas há mais tempo?*

## Como funciona

Colunas de board no GitLab são **labels** por baixo dos panos. A API v4 expõe
`resource_label_events` (quando cada label foi adicionada/removida de uma issue),
então cada par `add` → `remove` vira um intervalo de permanência na coluna:

```
issue #42   ──[ To Do 3h ]──[ Doing 14h ]──[ Review 2h ]──● fechada
                            └─ add em 10/03 09:00, remove em 10/03 23:00
```

Somando os intervalos e agrupando por responsável sai o tempo total por pessoa.
Labels ainda aplicadas contam até o fechamento da issue (ou até agora, se aberta).

**Nem toda coluna é trabalho.** O `Backlog` é planejamento: um card pode ficar
meses lá sem que ninguém tenha encostado nele. Colunas assim entram em
`EXCLUDED_LABELS` e ficam de fora de *toda* conta de tempo — não aparecem em
gráfico, legenda, soma nem contagem de WIP.

**E nem toda label é coluna.** Um card costuma carregar também etiquetas de
conteúdo (`DOCUMENTATION`, `ART 1`, `BACKEND`). Só label que é lista do board
vira tempo; as outras aparecem como etiqueta no drill-down da issue e não
entram em conta nenhuma. Sem esse corte, um card com três labels contaria o
mesmo período três vezes.

**Duas colunas ao mesmo tempo contam uma vez.** Nada impede um card de ter
`Doing` e `Review` simultaneamente. Cada instante fica com a coluna mais
avançada do board — a mesma que o GitLab mostra no card — então o total de
uma pessoa nunca passa do tempo de relógio.

O sync grava tudo num SQLite local; todas as rotas de métrica leem só do cache,
então o dashboard é instantâneo e não estoura o rate limit do gitlab.com.

## Setup

```bash
python -m venv .venv
.venv/Scripts/activate          # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env            # e preencha o GITLAB_TOKEN
```

O token é um [Personal Access Token](https://gitlab.com/-/user_settings/personal_access_tokens)
com escopo **`read_api`**.

```bash
uvicorn app.main:app --reload
```

- Dashboard: http://localhost:8000
- Docs interativas (Swagger): http://localhost:8000/docs

Primeiro uso: clique em **Sincronizar GitLab** no dashboard, ou

```bash
curl -X POST "http://localhost:8000/api/sync?project=meu-grupo/meu-projeto"
```

