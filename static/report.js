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

// PDF sai pela caixa de impressao do navegador: a pagina se manda imprimir e
// o "Salvar como PDF" faz o resto — nenhum motor de PDF no servidor
$("r-pdf").addEventListener("click", () => {
  const aba = window.open(`/api/report/sprints.html?${reportParams({ print: "1" })}`, "_blank");
  toast(aba
    ? "Relatorio aberto numa aba: escolha 'Salvar como PDF' no destino."
    : "O navegador bloqueou a aba. Libere o popup deste site e tente de novo.", 5000);
});

$("r-export").addEventListener("click", () => {
  // o proprio endpoint devolve Content-Disposition: attachment
  window.location.href = `/api/report/sprints.html?${reportParams()}`;
  toast("Gerando o relatorio para download...");
});


/* ── resumo de uma sprint ─────────────────────────────────────────────── */

function renderSummary(d) {
  const m = d.milestone;
  const focus = d.focus_label;
  const c = d.commits;

  $("s-title").textContent = m.milestone;
  $("s-sub").innerHTML = `${esc(m.start_date || m.due_date
      ? fmtRange(m.start_date, m.due_date)
      : m.state === "closed" ? "sprint encerrada" : "sprint em andamento")}`
    + (d.compared_to
        ? ` · variacao contra <b>${esc(d.compared_to)}</b>`
        : ` · primeira sprint com dados, nao ha com o que comparar`)
    + `. Taxas comparam em pontos percentuais (pp).`;

  $("s-stats").innerHTML = [
    { label: "Issues fechadas", value: `${m.closed_issues}/${m.issues}`,
      note: `${m.completion}% da sprint`, d: delta(d.delta.completion_pp, { unit: "pp" }) },
    { label: focus ? `Tempo em ${focus}` : "Tempo de trabalho",
      value: focus ? (m.by_label[focus]?.human || "0m") : m.total_human,
      note: `${m.contributors} pessoas no fluxo`, d: delta(d.delta.focus_hours) },
    { label: "Lead time medio", value: fmtH(m.avg_lead_hours),
      note: "da criacao ao fechamento", d: delta(d.delta.avg_lead_hours, { lower: true }) },
    { label: "Commits", value: String(c.total),
      note: `${c.off} fora da convencao`, d: delta(d.delta.commits) },
    { label: "Convencao", value: c.pct === null ? "—" : `${c.pct}%`,
      note: `${c.ok} de ${c.total} commits`, d: delta(d.delta.convention_pp, { unit: "pp" }) },
  ].map((s) => `<div class="stat">
      <div class="stat-label">${esc(s.label)}</div>
      <div class="stat-row"><div class="stat-value">${esc(s.value)}</div></div>
      <div class="delta"><span>${esc(s.note)}</span></div>
      ${s.d}
    </div>`).join("");

  renderSummaryPeople(d);
  renderSummaryColumns(d);
  renderSummaryIssues(d);
  renderSummaryCommits(d);
}

/** Mesma faixa de cor da tela de commits: verde >= 80%, vermelho < 50%. */
const convRate = (pct) => pct >= 80 ? "var(--pos)" : pct < 50 ? "var(--neg)" : "var(--warn)";

function renderSummaryPeople(d) {
  const temFila = state.queues.length > 0;
  $("s-people-sub").innerHTML = `Todas as pessoas com tempo registrado em`
    + ` <b>${esc(d.milestone.milestone)}</b>`
    + (d.review_label ? `, com o quanto foi revisando em ${esc(d.review_label)}` : "")
    + `. A convencao e a dos commits desta sprint, por pessoa.`
    + (d.commit_only.length
        ? ` ${d.commit_only.map((c) => esc(c.person)).join(", ")} commitou na sprint`
          + ` sem mover card nenhum e nao aparece aqui.`
        : "");

  const max = Math.max(1, ...d.people.map((p) => p.total_hours));
  $("s-people").innerHTML =
    `<thead><tr><th>Pessoa</th><th class="num">Acumulado</th>
      ${d.review_label ? `<th class="num">Revisando</th>` : ""}
      ${temFila ? `<th class="num">Espera causada</th>` : ""}
      <th class="num">Issues</th><th class="num">Convencao</th></tr></thead><tbody>`
    + (d.people.length ? d.people.map((p) => `<tr>
        <td><span class="row-name">
          <span class="avatar" style="width:24px;height:24px;font-size:10px">${esc(initials(p.contributor))}</span>
          <span title="${esc(p.contributor)}">${esc(p.contributor)}</span></span></td>
        <td class="num"><b>${esc(p.total_human)}</b>
          <div class="mini-bar"><i style="width:${(p.total_hours / max) * 100}%;background:${colorOf(state.focus)}"></i></div></td>
        ${d.review_label ? `<td class="num ${p.review_hours ? "review-cell" : "muted"}">${esc(fmtH(p.review_hours))}</td>` : ""}
        ${temFila ? `<td class="num ${p.waiting_hours ? "debt-cell" : "muted"}">${esc(fmtH(p.waiting_hours))}</td>` : ""}
        <td class="num muted">${p.closed_issues}/${p.issues}</td>
        <td class="num">${p.convention
          ? `<b style="color:${convRate(p.convention.pct)}">${p.convention.pct}%</b>
             <div class="mini-bar"><i style="width:${p.convention.pct}%;background:${convRate(p.convention.pct)}"></i></div>
             <span class="sprint-when">${p.convention.ok}/${p.convention.commits} commits</span>`
          : `<span class="muted">sem commit</span>`}</td>
      </tr>`).join("")
      : `<tr><td colspan="6" class="muted">Sem tempo registrado nesta sprint.</td></tr>`)
    + `</tbody>`;
}

function renderSummaryColumns(d) {
  $("s-columns").innerHTML =
    `<thead><tr><th>Coluna</th><th class="num">Media</th><th class="num">Mediana</th>
      <th class="num">Maximo</th><th class="num">Passagens</th><th class="num">Agora</th></tr></thead><tbody>`
    + (d.columns.length ? d.columns.map((c) => `<tr>
        <td><span class="tag-pill"><i class="swatch round" style="background:${colorOf(c.label)}"></i>${esc(c.label)}</span></td>
        <td class="num">${esc(c.avg_human)}</td>
        <td class="num">${esc(fmtH(c.median_hours))}</td>
        <td class="num">${esc(fmtH(c.max_hours))}</td>
        <td class="num muted">${c.completed_passes}</td>
        <td class="num muted">${c.wip}</td>
      </tr>`).join("")
      : `<tr><td colspan="6" class="muted">Sem passagens nesta sprint.</td></tr>`)
    + `</tbody>`;
}

