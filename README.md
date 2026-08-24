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

## Commits

O sync traz os commits de **todos os branches** com `with_stats=true`, entao as
linhas adicionadas/removidas vêm na propria listagem — sem um request por
commit, diferente dos eventos de label. E incremental por data, com um dia de
sobreposicao porque rebase reescreve a data do commit.

Tres decisoes que mudam os numeros:

- **Merge commits ficam de fora** por padrao. Eles repetem as linhas dos
  commits que trazem; num board real isso somava +999/-999 numa tacada.
  `?include_merges=true` traz de volta.
- **O balde da serie cresce com o intervalo** (dia até 70 dias, semana até
  ~13 meses, mes acima disso). Repositorio criado a partir de template carrega
  commits de anos atras: sem isso o grafico vira centenas de colunas vazias com
  a atividade real espremida na borda.
- **Sprint e coluna nao filtram commits** — commit nao passa por board. So o
  periodo e o projeto valem nessa aba.

### Conventional commits

A regra medida é a combinada pelo time:

```
tipo(#issue): descricao      # sem acentuação
```

Tipos aceitos: `feat`, `fix`, `docs`, `chore`, `refactor`, `test`, `style`,
`perf`, `build`, `ci`, `revert`.

**Maiúscula é permitida** (decisão do time em 24/08/2026). Era o motivo mais
comum de reprovação — quase sempre sigla técnica no meio da descrição
(`deriva NPS_MEDIO por rota`) — e não atrapalhava a leitura de ninguém.

Cada commit fora do padrão vira um ou mais **motivos**, e é isso que a tela
mostra — reprovar sem dizer o quê não ajuda ninguém a corrigir:

| motivo | exemplo |
|---|---|
| `acento` | `docs(#7): corrige a duplicação` |
| `espaco` | `feat (#145): carrega os dados`, `feat(#145):carrega` |
| `tipo` | `wip(#3): mexe em tudo` |
| `sem_issue` | `docs: escreve a secao` |
| `formato` | tem `#7` em algum lugar, mas não no formato |

Três decisões que mudam o número:

- **A forma é conferida no texto normalizado**, então a caixa não interfere no
  veredito: `Fix(#1): Corrige` tem a forma certa e passa.
- **Merge commits ficam de fora.** A mensagem é gerada pelo GitLab; reprovar
  o time por ela não mede nada.
- **Quem não é do time fica de fora** (veja abaixo).

O nome do git é casado com o usuário do GitLab (`lucas.delmirio` →
`Lucas Delmirio da Silva`); quem não casa aparece marcado como *fora do time*
— normalmente é o template do repositório ou alguém de fora.

Na tela, cada commit fora do padrão vem com uma **barra lateral âmbar, um
triângulo e as etiquetas do que quebrou**, e dois controles no cabeçalho da
listagem:

- **filtro por pessoa** — recorta a tela inteira (ritmo, ranking, heatmap e
  aderência passam a ser dela). Filtra por *contribuinte*, não por assinatura:
  as identidades de git da mesma pessoa entram juntas, inclusive quando o
  filtro é pelo e-mail;
- **"só fora do padrão"** — recorta apenas a listagem. Os totais continuam
  sobre todos os commits: encolher os dois juntos mentiria sobre o volume.

### Quem é do time

```bash
COUNT_NON_MEMBERS=false   # padrão: só o time entra nas métricas de commit
```

O bot do template (`Inteli Hub`), contas de professor e convidados commitam no
mesmo repositório. Contar isso como trabalho do time distorce **ritmo, ranking,
linhas e aderência** — e a aderência deles é sempre 0%, porque nem tentam
seguir a convenção do time. Num board real, os 18 commits de fora derrubavam a
aderência de **72,9% para 64,6%**.

É do time quem aparece como usuário nos eventos do board (`label_events`). O
casamento é o mesmo das identidades, por nome e e-mail.

O que ficou de fora **nunca some calado**: cada tela e cada relatório declaram
quantos commits foram ignorados e de quem (`outsiders` na API). E se o projeto
não tiver board sincronizado, não há lista de membros para comparar — nesse
caso ninguém é excluído, porque zerar a tela seria pior que o ruído.

### Identidades

O autor de um commit vem do `git config` da maquina, nao do usuario do GitLab:
a mesma pessoa aparece como `Tiago`, `lucas.delmirio` ou `Guilherme Maia`. O
dash liga as duas pontas comparando os pedacos do nome e do login do e-mail,
ignorando acentos.

A regra e conservadora de proposito: dois pedacos em comum bastam, mas **um so
pedaco** vale apenas quando o autor assina com um nome unico (`Tiago`). Senao,
um sobrenome corriqueiro faria de duas pessoas a mesma — num board real,
"Thais Rodrigues Neubauer" casaria com "Joao Paulo ... Rodrigues de Paula".
Empate tambem nao identifica.

O card **Identidades de commit** lista quem nao bateu com ninguem do board.
Normalmente sao bots, ex-integrantes, ou alguem cujos commits estao divididos
em duas assinaturas.

## Sprints (milestones)

Sprint no GitLab é **milestone**, e o dash trata isso como um eixo de
agrupamento de primeira classe:

- o seletor de sprint na barra de filtros recorta **todo** o dashboard;
- o card **Comparativo de sprints** põe uma sprint por linha — tempo por
  coluna, `% concluído`, lead time médio e nº de pessoas — e clicar numa
  linha aplica o recorte (clicar de novo volta para "todas");
- issues sem milestone caem numa pseudo-sprint `(sem sprint)`, para não
  sumirem da conta.

Duas decisões que valem saber:

- **Sprint desliga o filtro de período.** Uma sprint já é uma janela de
  tempo; aplicar "últimos 7 dias" por cima esconderia trabalho feito dentro
  da própria sprint. Com uma sprint escolhida, o pill de período fica inerte.
- **O comparativo ignora o período de propósito.** Cada sprint é somada pela
  sua duração inteira — é a única forma de comparar sprints entre si.
- Com uma sprint selecionada, o delta dos indicadores passa a ser
  *sprint contra sprint anterior*, não "período anterior".

O sync busca as milestones do projeto **e as herdadas do grupo**
(`include_parent_milestones`), porque na maioria dos times a sprint mora no
grupo, não no projeto. Se uma issue apontar para uma milestone que o token
não enxerga, ela ainda aparece na lista, só sem datas.

## Relatórios

A tela **Relatorio** tem dois relatórios, escolhidos pelo seletor no cabeçalho
do card (que é o mesmo **filtro de sprint do topo**, escrito na linguagem da
tela) — são perguntas diferentes, não dois recortes da mesma:

| filtro | relatório | pergunta |
|---|---|---|
| `Todas as sprints` | comparativo | estamos melhorando? |
| uma sprint | resumo daquela sprint | o que aconteceu nesta sprint? |

### Comparativo entre sprints

Junta numa leitura só as três dimensões que o dash mede separado — board,
pessoas e commits — com a variação contra a sprint anterior:

- fechadas, tempo na coluna de trabalho, acumulado, lead time, commits e
  aderência à convenção, sprint a sprint;
- tempo por coluna em cada sprint, cada coluna na sua própria escala;
- quem fez o quê dentro da sprint, com quanto disso foi revisando.

Três decisões:

- **Taxas comparam em pontos percentuais.** Ir de 44% para 68% é +24pp, não
  +54% — variação relativa de porcentagem é armadilha de leitura.
- **A sprint de um commit é a da issue que ele cita**, não a data em que foi
  feito: commit do primeiro dia da sprint 2 pode ser de issue arrastada da 1.
  Commit que não cita issue não entra em sprint nenhuma, e o total de órfãos
  aparece no cabeçalho.
- **`(sem sprint)` fica fora da comparação.** É um balde, não uma sprint; se
  virasse linha, a sprint mais antiga seria comparada contra ele.

### Resumo de uma sprint

O mesmo material, mas fundo em vez de largo — e sem cortar no topo: entram
**todas** as pessoas, não as seis primeiras.

- os números da sprint (fechadas, tempo na coluna de trabalho, acumulado,
  lead time, commits, convenção), cada um com a variação contra a anterior;
- **quem fez o quê**: acumulado, tempo revisando, espera causada, issues
  fechadas e a **aderência à convenção de commit de cada pessoa** naquela
  sprint;
- **onde o fluxo travou**: média, mediana, máximo e WIP por coluna *dentro
  daquela sprint*;
- as issues mais demoradas e os **commits fora da convenção**, com o motivo
  de cada um.

### Exportação

Os dois relatórios exportam do mesmo jeito, com dois botões:

- **Exportar PDF** abre o relatório numa aba com `?print=1`: a página se manda
  imprimir e o *Salvar como PDF* do navegador faz o resto. Gerar o PDF no
  servidor exigiria um motor de renderização (WeasyPrint, wkhtmltopdf) só para
  repetir o que o navegador já faz — e ele viria com dependência de sistema no
  Windows. O CSS de impressão fixa A4, repete o cabeçalho das tabelas nas
  páginas seguintes (`table-header-group`), evita quebrar linha no meio e
  preserva as cores das barras.
- **HTML** baixa o arquivo único, autocontido e **sem script nenhum** — o
  disparo de impressão só existe na versão `?print=1`.

As duas páginas usam a **mesma paleta da tela** — a cor de cada coluna do board
é a mesma no dash e no PDF, e as taxas seguem a faixa verde ≥ 80% / âmbar /
vermelho < 50%. O CSS de impressão força `print-color-adjust: exact`, senão o
Chrome imprimiria as barras em cinza.

## Sincronização

O sync é incremental: guarda o timestamp do último import e só rebusca issues
com `updated_after` posterior, substituindo a linha do tempo de labels delas.
Para manter o dashboard atualizado sozinho, agende um `POST /api/sync` no cron
(a cada 15–30 min é suficiente para a maioria dos times).

Custo de chamadas: 1 request por página de issues + **1 request por issue tocada**
(os eventos de label não têm endpoint em lote no REST). Por isso o primeiro sync
de um projeto grande demora; os seguintes são rápidos. A concorrência é limitada
por `MAX_CONCURRENCY` e o cliente faz backoff automático em 429.

## De quem é o tempo

Cada etapa vai para **quem a fez**, não para o responsável da issue. O sinal é
`moved_by`: quem aplicou a label da coluna. No fluxo típico, quem executa move
o card para `Doing`, e quem revisa move para `Review` — então cada um leva a
sua parte.

```bash
ATTRIBUTION=mover      # padrão
ATTRIBUTION=assignee   # tudo para o responsável atual da issue
```

### Escopo do tempo individual

```bash
SCOPE=assigned   # padrão: só o que a pessoa fez nas issues atribuídas a ela
SCOPE=touched    # qualquer issue em que ela fez alguma etapa
```

Com `assigned`, uma etapa feita numa issue de outra pessoa **não entra no
tempo individual de ninguém** — com uma exceção: a **revisão**, que é sempre
trabalho no card dos outros e por isso acumula para quem revisou. O tempo que
fica de fora não some do dashboard: continua no peso das colunas (rosca,
treemap) e na análise de gargalo, que são contas por coluna, não por pessoa.
A consequência é que a soma dos contribuidores é menor que o total do projeto,
e a diferença é justamente o trabalho feito fora da própria fila.

Quem revisa não fica invisível: além da **espera causada** (abaixo), o
**tempo revisando** é medido à parte e não obedece ao `SCOPE` — revisar é,
por definição, trabalhar no card de outra pessoa, e seguir a regra de
atribuição apagaria justamente o que se quer medir.

### Tempo revisando

```bash
REVIEW_LABEL=          # vazio = detecta sozinho (Review / Revisao / QA)
REVIEW_LABEL=Review    # ou aponte a coluna na mão
```

A coluna de revisão nunca é uma de `QUEUE_LABELS` — `Waiting Review` é espera,
não revisão. O tempo aparece em três lugares: no card da pessoa em
**Contribuidores** (`3h 6m revisando · 8 cards`), como estatística própria no
perfil dela, e na coluna **Revisando** da tabela de issues do perfil, que
ranqueia pelo maior tempo dela no card — `Doing` ou `Review`.

`review_hours` é um recorte do acumulado, não uma soma nova: o tempo de
revisão já está dentro de `total_hours` e da coluna `Review` em `by_label`.
Quem só revisou tem perfil, acumulado e rosca — antes ficava invisível.

Vale conferir no seu projeto se `moved_by` é confiável. Num board real:
quem move para `Doing` era o assignee em **95/95** issues, e quem move para
`Review` era o assignee em apenas **1/86** — ou seja, o modo `assignee` estava
dando 100% do tempo de revisão para a pessoa errada.

### Colunas de fila

```bash
QUEUE_LABELS=Waiting Review
```

Uma coluna de fila é onde o card fica **parado esperando alguém pegar**. Esse
tempo não é trabalho de ninguém, então:

- não entra no tempo de nenhuma pessoa;
- continua aparecendo na análise de gargalo (é lá que o fluxo trava);
- vira **espera causada** por quem acabou pegando o card — a demora foi dele.

Card que ainda espera não tem "quem pegou": aí o dash procura uma label com o
nome de alguém do time; sem isso, a espera fica em `(fila sem dono)`.

No mesmo board real, `Waiting Review` era **94% de todo o tempo contabilizado**.
Sem esse tratamento, os números por pessoa eram basicamente fila de espera.

## Limitação: quem move nem sempre é quem faz

O modelo assume que quem arrasta o card no board é quem fez a etapa. Se no seu
time alguém move os cards dos outros (um scrum master, uma automação), a
atribuição sai errada — nesse caso use `ATTRIBUTION=assignee`.

Outras ressalvas:
- Issues anteriores ao GitLab 12.3 podem não ter eventos de label. Um `remove`
  sem `add` correspondente é descartado em vez de gerar tempo falso.
- Listas de board por *assignee* ou *milestone* são ignoradas — só colunas
  baseadas em label viram métrica.
- Todos os cálculos são em tempo corrido (24/7), não em horário comercial.
- Se o projeto não tiver board sincronizado, não há como distinguir coluna de
  etiqueta: o dash volta a contar todas as labels, avisa no topo da página, e
  aí sim os totais podem passar do tempo de relógio.

## Testes

```bash
.venv/Scripts/python.exe -m pytest tests -q
```

Os testes montam um cache SQLite sintético, sem tocar no GitLab.

