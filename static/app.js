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

async function api(path, p, opts) {
  const qs = p && p.toString() ? "?" + p.toString() : "";
  const res = await fetch("/api" + path + qs, opts);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || res.statusText);
  return body;
}

let toastTimer;
function toast(msg, ms = 3200) {
  const el = $("toast");
  el.textContent = msg;
  el.classList.add("on");
  clearTimeout(toastTimer);
  if (ms) toastTimer = setTimeout(() => el.classList.remove("on"), ms);
}

/* ── tooltip compartilhado ────────────────────────────────────────────── */

const tip = {
  show(html, x, y) {
    const el = $("tooltip");
    el.innerHTML = html;
    el.classList.add("on");
    const r = el.getBoundingClientRect();
    const left = Math.min(Math.max(8, x - r.width / 2), window.innerWidth - r.width - 8);
    el.style.left = `${left + window.scrollX}px`;
    el.style.top = `${y - r.height - 12 + window.scrollY}px`;
  },
  hide() { $("tooltip").classList.remove("on"); },
};

const tipRows = (title, rows) =>
  `<div class="tt-title">${esc(title)}</div>` +
  rows.map((r) => `<div class="tt-row">
      <span class="swatch round" style="background:${r.color || "transparent"}"></span>
      <span>${esc(r.label)}</span><b>${esc(r.value)}</b>
    </div>`).join("");

/* ── cores por coluna ─────────────────────────────────────────────────── */

function assignColors(order) {
  state.order = order;
  state.colors = {};
  order.forEach((label, i) => {
    // >6 colunas: as excedentes caem em "Outras" no lugar de gerar matiz nova
    state.colors[label] = i < SERIES.length ? cssVar(SERIES[i]) : cssVar("--ink-3");
  });
}
const colorOf = (label) => state.colors[label] || cssVar("--ink-3");

/* ── serie diaria a partir das transicoes ─────────────────────────────── */

function dailySeries(issues, labels, sinceDays) {
  const until = Date.now();
  const since = sinceDays ? until - sinceDays * DAY : null;
  const byDay = new Map();                       // 'YYYY-MM-DD' -> {label: horas}
  let floor = Infinity;

  for (const issue of issues) {
    for (const t of issue.transitions) {
      if (labels && !labels.includes(t.label)) continue;
      let start = new Date(t.start).getTime();
      let end = t.end ? new Date(t.end).getTime() : until;
      if (since) start = Math.max(start, since);
      end = Math.min(end, until);
      if (!(end > start)) continue;
      floor = Math.min(floor, start);

      // reparte o intervalo entre os dias que ele cobre
      let cursor = start;
      while (cursor < end) {
        const dayStart = new Date(cursor); dayStart.setHours(0, 0, 0, 0);
        const dayEnd = dayStart.getTime() + DAY;
        const slice = Math.min(end, dayEnd) - cursor;
        const key = dayStart.toISOString().slice(0, 10);
        const bucket = byDay.get(key) || {};
        bucket[t.label] = (bucket[t.label] || 0) + slice / 3600000;
        byDay.set(key, bucket);
        cursor = dayEnd;
      }
    }
  }
  if (!byDay.size) return [];

  // preenche os dias vazios para a linha nao "pular" buracos
  const start = new Date(floor); start.setHours(0, 0, 0, 0);
  const out = [];
  for (let t = start.getTime(); t <= until; t += DAY) {
    const key = new Date(t).toISOString().slice(0, 10);
    out.push({ date: new Date(t), values: byDay.get(key) || {} });
  }
  return out;
}

const sumDay = (d) => Object.values(d.values).reduce((a, b) => a + b, 0);

