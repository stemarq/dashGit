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

## Endpoints

| Método | Rota | O que faz |
|---|---|---|
| `POST` | `/api/sync?project=&full=` | Puxa issues, boards e eventos de label do GitLab. Incremental por padrão (`full=true` refaz tudo). |
| `GET` | `/api/projects` | Projetos no cache e quando foram sincronizados. |
| `GET` | `/api/boards?project=` | Boards e suas colunas (labels). |
| `GET` | `/api/milestones?project=` | Sprints no cache, com contagem de issues, estado e datas. |
| `GET` | `/api/metrics/milestones` | **Comparativo entre sprints**: tempo por coluna, throughput, lead time. |
| `GET` | `/api/metrics/contributors` | **Tempo por contribuidor × coluna**, com totais, médias, tempo revisando e issues em aberto. |
| `GET` | `/api/metrics/contributor?name=` | Perfil de uma pessoa: tempo por coluna, por sprint, tempo revisando, espera causada e as issues dela. |
| `GET` | `/api/metrics/columns` | Média, mediana, máximo e WIP por coluna — para achar o gargalo. |
| `GET` | `/api/metrics/issues` | Drill-down: linha do tempo completa de cada issue, com quem moveu o card e `participants` — o tempo de cada pessoa naquele card, por coluna. Ranqueia por tempo na coluna de trabalho (`sort=focus\|working\|lead_time`). |
| `GET` | `/api/metrics/commits` | **Commits**: volume, autores, ritmo por dia/semana/mes e heatmap dia × hora. `author=` filtra por pessoa (nome do GitLab ou assinatura do git); `only_off=true` lista só os commits fora da convenção. |
| `GET` | `/api/commit-authors` | Autores de commit, e-mails de cada um e o usuário do GitLab correspondente. |
| `GET` | `/api/metrics/commit-convention` | **Conventional commits**: aderência de cada pessoa e o que quebra em cada commit fora do padrão. |
| `GET` | `/api/report/sprints` | **Relatório comparativo**: board, pessoas e commits por sprint, com a variação contra a anterior. |
| `GET` | `/api/report/sprints.html` | O mesmo relatório como página autocontida (`?download=false` abre no navegador, `?print=1` abre a caixa de impressão). |
| `GET` | `/api/report/sprint?milestone=` | **Resumo de uma sprint**: números, gargalo, pessoas, issues e commits. |
| `GET` | `/api/report/sprint.html?milestone=` | O resumo como página autocontida, com os mesmos `download`/`print`. |
| `GET` | `/api/health` | Status e tamanho do cache. |

Filtros comuns em todas as rotas de métrica:
`project` (`grupo/projeto` ou ID), `labels` (`Doing,Review`),
`milestone` (título da sprint), `days` ou `since` (ISO 8601),
`state` (`opened`/`closed`).

```bash
# tempo em Doing nos últimos 30 dias
curl "http://localhost:8000/api/metrics/contributors?labels=Doing&days=30"

# a mesma métrica, recortada por sprint
curl "http://localhost:8000/api/metrics/contributors?labels=Doing&milestone=Sprint%2014"

# onde o fluxo trava
curl "http://localhost:8000/api/metrics/columns"
```

## O que conta como tempo de trabalho

Duas configurações no `.env` mandam nisso:

```bash
EXCLUDED_LABELS=Backlog   # colunas de planejamento, fora de toda conta
FOCUS_LABEL=Doing         # a coluna que representa trabalho acontecendo
```

**`EXCLUDED_LABELS`** remove a coluna de tudo: séries do gráfico, totais por
pessoa, treemap, gargalo, WIP e slots de cor. Pedir a coluna explicitamente
(`?labels=Backlog`) ainda funciona — a exclusão vale como padrão, não como
censura.

**`FOCUS_LABEL`** define o que é uma issue "demorada". A tabela de issues é
ordenada por tempo nessa coluna, não por lead time: uma issue criada há três
meses e trabalhada em dois dias tem lead time enorme e trabalho pequeno — pelo
lead time ela lideraria a lista sem merecer. O lead time continua visível como
coluna secundária, e `?sort=lead_time` volta ao ranking antigo se você quiser
os dois olhares.

Deixando `FOCUS_LABEL` vazio, o dash detecta sozinho (procura `Doing`,
`Em andamento`, `In progress`, `WIP`…); sem achar, usa a primeira coluna não
excluída do board.

