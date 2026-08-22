/* dashGit — camada de apresentacao.
   Sem build e sem CDN: os graficos sao SVG escrito a mao a partir dos
   endpoints /api/metrics/*. A serie diaria e derivada no cliente a partir
   das transicoes de coluna que /api/metrics/issues ja devolve. */

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const state = {
  colors: {},      // label -> cor, fixado pela ordem do board (nunca reciclado)
  order: [],       // ordem canonica das colunas
  contributors: null,
  columns: null,
  issues: null,
  sprints: null,
  view: "overview",
  person: null,
  focus: null,
  review: null,
  attribution: "mover",
  queues: [],
  scope: "assigned",
  commits: null,
  convention: null,
  report: null,
  summary: null,
  milestones: [],
};

const SERIES = ["--s1", "--s2", "--s3", "--s4", "--s5", "--s6"];
const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

/* ── util ─────────────────────────────────────────────────────────────── */

const DAY = 86400000;
const fmtH = (h) => {
  if (!h) return "0h";
  if (h < 1) return `${Math.round(h * 60)}m`;
  if (h < 48) return `${h.toFixed(h < 10 ? 1 : 0)}h`;
  return `${Math.floor(h / 24)}d ${Math.round(h % 24)}h`;
};
const fmtDay = (d) => d.toLocaleDateString("pt-BR", { day: "2-digit", month: "short" });
const fmtRange = (start, due) => {
  const one = (iso) => new Date(`${iso}T00:00:00`)
    .toLocaleDateString("pt-BR", { day: "2-digit", month: "short" });
  if (start && due) return `${one(start)} — ${one(due)}`;
  if (due) return `entrega ${one(due)}`;
  if (start) return `inicio ${one(start)}`;
  return "sem datas";
};
const initials = (name) => name.replace(/[^\p{L}\s]/gu, "").split(/\s+/).filter(Boolean)
  .slice(0, 2).map((w) => w[0]).join("").toUpperCase() || "?";

function params() {
  const p = new URLSearchParams();
  if ($("project").value) p.set("project", $("project").value);
  const labels = $("labels").value.trim();
  if (labels) p.set("labels", labels);
  const milestone = $("milestone").value;
  if (milestone) p.set("milestone", milestone);
  // uma sprint ja delimita um periodo: aplicar os dois recortes junto
  // esconderia trabalho que aconteceu dentro da propria sprint
  else if ($("days").value) p.set("days", $("days").value);
  return p;
}

