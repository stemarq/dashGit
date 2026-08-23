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

