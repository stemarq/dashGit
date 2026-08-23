/* Tela de relatorio: comparativo entre sprints. Depende de app.js e do
   roteador de contributors.js, por isso carrega depois dos dois.

   O filtro de periodo nao vale aqui de proposito — cada sprint e comparada
   pela sua duracao inteira, como no card de sprints da visao geral. */

VIEWS.report = { title: "Relatorio de sprints", node: "view-report" };
SUBTITLES.report = {
  plain: "Board, pessoas e commits lado a lado, sprint a sprint,"
    + " com a variacao contra a sprint anterior.",
  scoped: (m) => `Resumo de ${m}: numeros, gargalo, pessoas, issues e commits.`
    + " Escolha 'Todas as sprints' para voltar ao comparativo.",
};

/** Variacao formatada. `lower` marca as metricas em que cair e bom
 *  (lead time). Taxas vem em pontos percentuais e nao em variacao relativa:
 *  ir de 44% para 68% e +24pp, nao +54%. */
function delta(value, { unit = "%", lower = false } = {}) {
  if (value === null || value === undefined) return `<span class="r-delta flat">—</span>`;
  if (Math.abs(value) < 0.05) return `<span class="r-delta flat">estavel</span>`;
  const bom = lower ? value < 0 : value > 0;
  const sinal = value > 0 ? "+" : "";
  return `<span class="r-delta ${bom ? "up" : "down"}">${sinal}${value}${unit}</span>`;
}

/* O filtro de sprint do topo escolhe o relatorio: sem sprint, o comparativo
   ("estamos melhorando?"); com uma sprint, o resumo dela ("o que aconteceu
   nesta sprint?"). Sao perguntas diferentes, nao dois recortes da mesma. */
async function loadReport() {
  const sprint = $("milestone").value;
  $("r-compare").hidden = !!sprint;
  $("r-summary").hidden = !sprint;
  renderScopePicker(sprint);
  return sprint ? loadSprintSummary(sprint) : loadComparison();
}

/* O relatorio e escolhido pelo filtro de sprint do topo, que fica longe e nao
   se parece com um seletor de relatorio. Este picker no cabecalho do card e o
   mesmo filtro, escrito na linguagem da tela. */
function renderScopePicker(sprint) {
  // sprint sem issue nao tem resumo para gerar, e `(sem sprint)` e um balde
  const sprints = (state.milestones || [])
    .filter((m) => m.issues > 0 && m.title !== "(sem sprint)")
    .map((m) => m.title);
  const opcoes = `<option value="">Comparativo entre sprints</option>`
    + sprints.map((nome) =>
        `<option value="${esc(nome)}">Resumo de ${esc(nome)}</option>`).join("");
  for (const id of ["r-scope", "s-scope"]) {
    const el = $(id);
    if (el.dataset.filled !== sprints.join("|")) {
      el.innerHTML = opcoes;
      el.dataset.filled = sprints.join("|");
    }
    el.value = sprint;
  }
}

for (const id of ["r-scope", "s-scope"]) {
  $(id).addEventListener("change", (ev) => {
    // o filtro do topo continua sendo a fonte da verdade do recorte
    $("milestone").value = ev.target.value;
    $("milestone").dispatchEvent(new Event("change"));
  });
}

async function loadComparison() {
  try {
    const data = await api("/report/sprints", reportParams());
    state.report = data;
    renderReport(data);
  } catch (e) {
    $("r-sub").textContent = `Nao foi possivel montar o relatorio: ${e.message}`;
  }
}

async function loadSprintSummary(sprint) {
  try {
    const data = await api("/report/sprint", reportParams({ milestone: sprint }));
    state.summary = data;
    renderSummary(data);
  } catch (e) {
    $("s-sub").textContent = `Nao foi possivel montar o resumo: ${e.message}`;
  }
}

function renderReport(d) {
  const focus = d.focus_label;
  const sprints = d.sprints;

  $("r-sub").innerHTML = sprints.length
    ? `${sprints.length} sprints, da mais recente para a mais antiga. Cada uma e medida`
      + ` pela sua duracao inteira — o filtro de periodo do topo nao se aplica aqui.`
      + (d.orphan_commits
          ? ` ${d.orphan_commits} commits nao citam issue e por isso nao entram em`
            + ` sprint nenhuma.`
          : "")
      + outsidersNote(d.outsiders)
    : "Nenhuma sprint no cache.";

  $("r-table").innerHTML =
    `<thead><tr><th>Sprint</th><th class="num">Fechadas</th>
      <th class="num">${esc(focus ? `Tempo em ${focus}` : "Tempo de trabalho")}</th>
      <th class="num">Acumulado</th><th class="num">Lead time</th>
      <th class="num">Commits</th><th class="num">Convencao</th></tr></thead><tbody>`
    + (sprints.length ? sprints.map((s) => `<tr>
        <td>
          <div class="row-name"><b>${esc(s.milestone)}</b></div>
          <div class="sprint-when">${esc(s.start_date || s.due_date
              ? fmtRange(s.start_date, s.due_date)
              : s.state === "closed" ? "encerrada" : "em andamento")}${
            s.compared_to ? ` · vs ${esc(s.compared_to)}` : ""}</div>
        </td>
        <td class="num"><b>${s.closed_issues}/${s.issues}</b>
          ${delta(s.delta.completion_pp, { unit: "pp" })}</td>
        <td class="num">${esc(focus ? (s.by_label[focus]?.human || "0m") : fmtH(s.focus_hours))}${
          delta(s.delta.focus_hours)}</td>
        <td class="num">${esc(s.total_human)}${delta(s.delta.total_hours)}</td>
        <td class="num">${esc(fmtH(s.avg_lead_hours))}
          ${delta(s.delta.avg_lead_hours, { lower: true })}</td>
        <td class="num">${s.commits}${delta(s.delta.commits)}</td>
        <td class="num">${s.convention_pct === null ? "—" : `<b>${s.convention_pct}%</b>`}
          ${delta(s.delta.convention_pp, { unit: "pp" })}</td>
      </tr>`).join("")
      : `<tr><td colspan="7" class="muted">Sem sprints no cache.</td></tr>`)
    + `</tbody>`;

  renderReportColumns(d);
  renderReportPeople(d);
}

function renderReportColumns(d) {
  const cols = d.columns;
  // cada coluna tem sua propria escala: comparar Doing com Backlog na mesma
  // regua esconderia a variacao das colunas curtas
  const teto = {};
  for (const c of cols) {
    teto[c] = Math.max(1, ...d.sprints.map((s) => s.by_label[c]?.hours || 0));
  }
  $("r-columns").innerHTML =
    `<thead><tr><th>Sprint</th>${cols.map((c) =>
      `<th class="num"><span class="tag-pill"><i class="swatch round" style="background:${colorOf(c)}"></i>${esc(c)}</span></th>`).join("")}</tr></thead><tbody>`
    + d.sprints.map((s) => `<tr>
        <td><b>${esc(s.milestone)}</b></td>
        ${cols.map((c) => {
          const v = s.by_label[c];
          return `<td class="num">${v ? esc(v.human) : `<span class="muted">—</span>`}
            <div class="mini-bar"><i style="width:${((v?.hours || 0) / teto[c]) * 100}%;background:${colorOf(c)}"></i></div>
          </td>`;
        }).join("")}
      </tr>`).join("")
    + `</tbody>`;
}

function renderReportPeople(d) {
  $("r-people-sub").innerHTML = `Acumulado de cada pessoa dentro da sprint`
    + (d.review_label ? `, com o quanto disso foi revisando em <b>${esc(d.review_label)}</b>` : "")
    + `. So as sprints com tempo registrado aparecem.`;

  const blocos = d.sprints.filter((s) => s.people.length);
  $("r-people").innerHTML = blocos.length ? blocos.map((s) => {
    const topo = Math.max(...s.people.map((p) => p.hours)) || 1;
    return `<div class="r-block">
      <div class="r-block-head"><b>${esc(s.milestone)}</b>
        <span class="muted">${s.closed_issues}/${s.issues} fechadas · ${esc(s.total_human)}
          no total</span></div>
      ${s.people.map((p) => `<div class="load-row">
        <div>
          <div class="row-name">
            <span class="avatar">${esc(initials(p.contributor))}</span>
            <span title="${esc(p.contributor)}">${esc(p.contributor)}</span>
            ${p.review_hours ? `<span class="tag-plain">${esc(fmtH(p.review_hours))} revisando</span>` : ""}
          </div>
          <div class="progress"><i style="width:${(p.hours / topo) * 100}%;background:${colorOf(state.focus)}"></i></div>
        </div>
        <div class="row-total">${esc(p.human)}
          <div class="sprint-when" style="text-align:right">${p.closed_issues}/${p.issues} fechadas</div>
        </div>
      </div>`).join("")}
    </div>`;
  }).join("") : `<div class="empty">Nenhuma sprint com tempo registrado.</div>`;
}

function reportParams(extra = {}) {
  const p = new URLSearchParams();
  if ($("project").value) p.set("project", $("project").value);
  const labels = $("labels").value.trim();
  if (labels) p.set("labels", labels);
  for (const [k, v] of Object.entries(extra)) p.set(k, v);
  return p;
}

