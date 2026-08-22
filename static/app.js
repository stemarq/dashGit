/* dashGit — camada de apresentacao.
   Sem build e sem CDN: os graficos sao SVG escrito a mao a partir dos
   endpoints /api/metrics/*. A serie diaria e derivada no cliente a partir
   das transicoes de coluna que /api/metrics/issues ja devolve. */

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

