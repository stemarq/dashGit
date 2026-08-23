/* Tela de contribuidores: quem esta no fluxo, carga atual e o perfil de
   cada pessoa. Reaproveita as primitivas de app.js ($, api, tip, colorOf,
   dailySeries, renderArea, renderDonut) — por isso carrega depois dele. */

/* ── navegacao entre telas ────────────────────────────────────────────── */

const VIEWS = {
  overview: {
    title: "Visao geral do fluxo",
    node: "view-overview",
  },
  contributors: { title: "Contribuidores", node: "view-contributors" },
};

// itens sem tela propria levam ao card correspondente na visao geral
const ANCHORS = { issues: "issues-table", columns: "cols-table" };

function showView(name) {
  const view = VIEWS[name];
  if (!view) return;
  state.view = name;
  for (const [key, cfg] of Object.entries(VIEWS)) $(cfg.node).hidden = key !== name;

  document.querySelectorAll(".nav-item[data-view]").forEach((btn) => {
    if (!VIEWS[btn.dataset.view]) return;
    if (btn.dataset.view === name) btn.setAttribute("aria-current", "page");
    else btn.removeAttribute("aria-current");
  });

  document.querySelector("h1").textContent = view.title;
  renderSubtitle($("milestone").value);
  window.scrollTo({ top: 0 });
  if (name === "contributors") renderPeople();
  if (name === "commits") loadCommits();
  if (name === "report") loadReport();
}

