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

