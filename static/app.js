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

function windowDelta(series) {
  // metade recente contra a metade anterior — sem periodo, sem delta
  if (series.length < 4) return null;
  const half = Math.floor(series.length / 2);
  const prev = series.slice(0, half).reduce((a, d) => a + sumDay(d), 0);
  const curr = series.slice(half).reduce((a, d) => a + sumDay(d), 0);
  if (!prev) return null;
  return ((curr - prev) / prev) * 100;
}

/* ── primitivas de grafico ────────────────────────────────────────────── */

function svgEl(tag, attrs = {}) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  return el;
}

/** Caminho suavizado por cubica monotona (Fritsch-Carlson).
 *  Catmull-Rom e mais simples, mas ultrapassa os pontos quando a serie e
 *  esparsa — numa area empilhada isso faz as camadas se cruzarem. A versao
 *  monotona nunca passa do valor real. */
function smoothPath(pts) {
  const n = pts.length;
  if (n < 2) return n ? `M${pts[0][0]},${pts[0][1]}` : "";

  const dx = [], slope = [];
  for (let i = 0; i < n - 1; i++) {
    const h = pts[i + 1][0] - pts[i][0];
    dx.push(h);
    slope.push(h ? (pts[i + 1][1] - pts[i][1]) / h : 0);
  }

  const tan = new Array(n);
  tan[0] = slope[0];
  tan[n - 1] = slope[n - 2];
  for (let i = 1; i < n - 1; i++) {
    if (slope[i - 1] * slope[i] <= 0) {
      tan[i] = 0;                       // extremo local: tangente plana
    } else {
      const t = (slope[i - 1] + slope[i]) / 2;
      const limit = 3 * Math.min(Math.abs(slope[i - 1]), Math.abs(slope[i]));
      tan[i] = Math.sign(t) * Math.min(Math.abs(t), limit);
    }
  }

  let d = `M${pts[0][0]},${pts[0][1]}`;
  for (let i = 0; i < n - 1; i++) {
    const third = dx[i] / 3;
    d += ` C${pts[i][0] + third},${pts[i][1] + tan[i] * third}` +
         ` ${pts[i + 1][0] - third},${pts[i + 1][1] - tan[i + 1] * third}` +
         ` ${pts[i + 1][0]},${pts[i + 1][1]}`;
  }
  return d;
}

function sparkline(values, color, w = 74, h = 30) {
  if (values.length < 2) return "";
  const max = Math.max(...values), min = Math.min(...values);
  const span = max - min || 1;
  const pts = values.map((v, i) => [
    (i / (values.length - 1)) * w,
    h - 3 - ((v - min) / span) * (h - 6),
  ]);
  return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true">
    <path d="${smoothPath(pts)}" fill="none" stroke="${color}" stroke-width="2"
          stroke-linecap="round" stroke-linejoin="round"/>
  </svg>`;
}

/* ── area empilhada com crosshair ─────────────────────────────────────── */

function renderArea(series, labels, hostId = "area") {
  const host = $(hostId);
  const W = host.clientWidth || 720;
  const H = 260, padL = 44, padR = 8, padT = 12, padB = 26;
  host.innerHTML = "";

  if (series.length < 2) {
    host.innerHTML = `<div class="empty">Sem historico suficiente para a serie diaria.</div>`;
    return;
  }

  const iw = W - padL - padR, ih = H - padT - padB;
  const totals = series.map(sumDay);
  const max = Math.max(1, ...totals);
  const nice = Math.ceil(max / 4) * 4;
  const x = (i) => padL + (i / (series.length - 1)) * iw;
  const y = (v) => padT + ih - (v / nice) * ih;

  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, height: H, role: "img" });
  svg.setAttribute("aria-label", "Horas em coluna por dia");

  // grade + eixo y
  const axis = svgEl("g", { class: "axis" });
  for (let i = 0; i <= 4; i++) {
    const v = (nice / 4) * i;
    axis.appendChild(svgEl("line", {
      class: "grid-line", x1: padL, x2: W - padR, y1: y(v), y2: y(v),
    }));
    const t = svgEl("text", { x: padL - 8, y: y(v) + 4, "text-anchor": "end" });
    t.textContent = `${Math.round(v)}h`;
    axis.appendChild(t);
  }
  svg.appendChild(axis);

  // areas empilhadas, da base para o topo
  const running = new Array(series.length).fill(0);
  for (const label of labels) {
    const lower = running.slice();
    const upper = series.map((d, i) => running[i] + (d.values[label] || 0));
    if (upper.every((v, i) => v === lower[i])) continue;
    const top = upper.map((v, i) => [x(i), y(v)]);
    const bottom = lower.map((v, i) => [x(i), y(v)]).reverse();
    const color = colorOf(label);
    svg.appendChild(svgEl("path", {
      d: `${smoothPath(top)} L${bottom.map((p) => p.join(",")).join(" L")} Z`,
      fill: color, "fill-opacity": .16,
    }));
    svg.appendChild(svgEl("path", {
      d: smoothPath(top), fill: "none", stroke: color, "stroke-width": 2,
      "stroke-linecap": "round", "stroke-linejoin": "round",
    }));
    upper.forEach((v, i) => (running[i] = v));
  }

  // eixo x (~6 marcas, sem deixar a ultima colidir com a penultima)
  const step = Math.max(1, Math.ceil(series.length / 6));
  const xa = svgEl("g", { class: "axis" });
  const ticks = [];
  for (let i = 0; i < series.length; i += step) ticks.push(i);
  const last = series.length - 1;
  if (last - ticks[ticks.length - 1] > step / 2) ticks.push(last);
  else ticks[ticks.length - 1] = last;
  for (const i of ticks) {
    const t = svgEl("text", { x: x(i), y: H - 6, "text-anchor": "middle" });
    t.textContent = fmtDay(series[i].date);
    xa.appendChild(t);
  }
  svg.appendChild(xa);

  // camada de hover
  const cross = svgEl("line", { class: "crosshair", y1: padT, y2: padT + ih, opacity: 0 });
  const dot = svgEl("circle", { r: 4.5, fill: cssVar("--surface"), "stroke-width": 2, opacity: 0 });
  svg.appendChild(cross); svg.appendChild(dot);

  const hit = svgEl("rect", {
    x: padL, y: padT, width: iw, height: ih, fill: "transparent", style: "cursor:crosshair",
  });
  hit.addEventListener("mousemove", (ev) => {
    const box = svg.getBoundingClientRect();
    const px = ((ev.clientX - box.left) / box.width) * W;
    const i = Math.round(((px - padL) / iw) * (series.length - 1));
    const d = series[Math.min(Math.max(i, 0), series.length - 1)];
    if (!d) return;
    cross.setAttribute("x1", x(i)); cross.setAttribute("x2", x(i));
    cross.setAttribute("opacity", 1);
    dot.setAttribute("cx", x(i)); dot.setAttribute("cy", y(sumDay(d)));
    dot.setAttribute("stroke", cssVar("--brand"));
    dot.setAttribute("opacity", 1);
    const rows = labels
      .filter((l) => d.values[l])
      .sort((a, b) => d.values[b] - d.values[a])
      .map((l) => ({ color: colorOf(l), label: l, value: fmtH(d.values[l]) }));
    rows.push({ color: "", label: "Total", value: fmtH(sumDay(d)) });
    tip.show(tipRows(fmtDay(d.date), rows), ev.clientX, box.top + y(sumDay(d)));
  });
  hit.addEventListener("mouseleave", () => {
    cross.setAttribute("opacity", 0); dot.setAttribute("opacity", 0); tip.hide();
  });
  svg.appendChild(hit);

  host.appendChild(svg);
}

/* ── rosca ────────────────────────────────────────────────────────────── */

