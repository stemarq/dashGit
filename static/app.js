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

function renderDonut(totals, hostId = "donut") {
  const host = $(hostId);
  const entries = Object.entries(totals).sort((a, b) => b[1].hours - a[1].hours);
  const sum = entries.reduce((a, [, v]) => a + v.hours, 0);
  host.innerHTML = "";
  if (!sum) { host.innerHTML = `<div class="empty">Sem dados no periodo.</div>`; return; }

  const S = 176, r = 66, cx = S / 2, cy = S / 2, gap = 0.035;
  const svg = svgEl("svg", { viewBox: `0 0 ${S} ${S}`, height: S, style: "margin:0 auto" });
  svg.setAttribute("aria-label", "Distribuicao do tempo por coluna");

  let angle = -Math.PI / 2;
  for (const [label, v] of entries) {
    const frac = v.hours / sum;
    const sweep = frac * Math.PI * 2;
    if (sweep <= gap) { angle += sweep; continue; }
    const a0 = angle + gap / 2, a1 = angle + sweep - gap / 2;
    const arc = svgEl("path", {
      d: `M${cx + r * Math.cos(a0)},${cy + r * Math.sin(a0)}` +
         ` A${r},${r} 0 ${a1 - a0 > Math.PI ? 1 : 0} 1` +
         ` ${cx + r * Math.cos(a1)},${cy + r * Math.sin(a1)}`,
      fill: "none", stroke: colorOf(label), "stroke-width": 15, "stroke-linecap": "round",
    });
    arc.style.cursor = "default";
    arc.addEventListener("mousemove", (ev) => tip.show(
      tipRows(label, [{ color: colorOf(label), label: "Tempo", value: v.human },
                      { label: "Participacao", value: `${(frac * 100).toFixed(1)}%` }]),
      ev.clientX, ev.clientY));
    arc.addEventListener("mouseleave", tip.hide);
    svg.appendChild(arc);
    angle += sweep;
  }

  const top = entries[0];
  const pct = svgEl("text", {
    x: cx, y: cy + 2, "text-anchor": "middle",
    style: `font-size:26px;font-weight:600;letter-spacing:-.03em;fill:${cssVar("--ink")}`,
  });
  pct.textContent = `${Math.round((top[1].hours / sum) * 100)}%`;
  const cap = svgEl("text", {
    x: cx, y: cy + 20, "text-anchor": "middle",
    style: `font-size:11px;fill:${cssVar("--ink-3")}`,
  });
  cap.textContent = top[0];
  svg.appendChild(pct); svg.appendChild(cap);
  host.appendChild(svg);

  host.insertAdjacentHTML("beforeend",
    `<div class="legend" style="margin-top:12px;justify-content:center">` +
    entries.map(([l, v]) => `<span class="legend-item">
        <i class="swatch" style="background:${colorOf(l)}"></i>${esc(l)} · ${esc(v.human)}
      </span>`).join("") + `</div>`);
}

/* ── treemap (squarify) ───────────────────────────────────────────────── */

function squarify(items, x, y, w, h, out = []) {
  if (!items.length) return out;
  if (items.length === 1) {
    out.push({ ...items[0], x, y, w, h });
    return out;
  }
  const total = items.reduce((a, i) => a + i.value, 0);
  const vertical = w >= h;
  const side = vertical ? h : w;
  let best = 1, split = 1, acc = 0;

  for (let i = 1; i <= items.length; i++) {
    acc += items[i - 1].value;
    const frac = acc / total;
    const len = (vertical ? w : h) * frac;
    const worst = Math.max(...items.slice(0, i).map((it) => {
      const other = (it.value / acc) * side;
      return Math.max(len / other, other / len);
    }));
    if (i === 1 || worst < best) { best = worst; split = i; } else break;
  }

  const head = items.slice(0, split), tail = items.slice(split);
  const frac = head.reduce((a, i) => a + i.value, 0) / total;
  const len = (vertical ? w : h) * frac;
  let offset = 0;
  const headTotal = head.reduce((a, i) => a + i.value, 0);
  for (const it of head) {
    const seg = (it.value / headTotal) * side;
    out.push(vertical
      ? { ...it, x, y: y + offset, w: len, h: seg }
      : { ...it, x: x + offset, y, w: seg, h: len });
    offset += seg;
  }
  return vertical
    ? squarify(tail, x + len, y, w - len, h, out)
    : squarify(tail, x, y + len, w, h - len, out);
}

function renderTreemap(totals, columnStats) {
  const host = $("treemap");
  const items = Object.entries(totals)
    .map(([label, v]) => ({ label, value: v.hours, human: v.human }))
    .filter((i) => i.value > 0)
    .sort((a, b) => b.value - a.value);
  host.innerHTML = "";
  if (!items.length) { host.innerHTML = `<div class="empty">Sem dados no periodo.</div>`; return; }

  const W = host.clientWidth || 360, H = 260, sum = items.reduce((a, i) => a + i.value, 0);
  const wipOf = Object.fromEntries((columnStats || []).map((c) => [c.label, c.wip]));

  for (const t of squarify(items, 0, 0, W, H)) {
    const color = colorOf(t.label);
    // limao e roxo claro pedem tinta escura; o resto, tinta clara
    const light = ["--s2", "--s5"].some((v) => cssVar(v).toLowerCase() === color.toLowerCase());
    const el = document.createElement("div");
    el.className = `tile ${light ? "on-light" : "on-dark"}`;
    el.style.cssText = `left:${t.x + 2}px;top:${t.y + 2}px;width:${t.w - 4}px;height:${t.h - 4}px;background:${color}`;
    const compact = t.h < 54 || t.w < 84;
    const roomy = t.w >= 132;
    el.innerHTML = `
      <div class="t-head"><span class="swatch round" style="background:currentColor;opacity:.55"></span>
        ${esc(t.label)}</div>
      ${compact ? "" : `<div class="t-foot">
        <span class="t-val">${Math.round((t.value / sum) * 100)}%</span>
        ${roomy ? `<span class="t-delta">${esc(t.human)}</span>` : ""}</div>`}`;
    el.addEventListener("mousemove", (ev) => tip.show(
      tipRows(t.label, [
        { color, label: "Tempo acumulado", value: t.human },
        { label: "Participacao", value: `${((t.value / sum) * 100).toFixed(1)}%` },
        { label: "Cards agora", value: String(wipOf[t.label] ?? 0) },
      ]), ev.clientX, ev.clientY));
    el.addEventListener("mouseleave", tip.hide);
    host.appendChild(el);
  }
}

/* ── barras verticais de gargalo ──────────────────────────────────────── */

function renderBars(columns) {
  const host = $("bars");
  host.innerHTML = "";
  const data = columns.filter((c) => c.avg_hours > 0 || c.wip > 0);
  if (!data.length) { host.innerHTML = `<div class="empty">Sem passagens registradas.</div>`; return; }

  const W = host.clientWidth || 720, H = 210, padT = 28, padB = 30;
  const ih = H - padT - padB;
  const max = Math.max(...data.map((c) => Math.max(c.avg_hours, c.median_hours))) || 1;
  const slot = W / data.length;
  const bw = Math.min(58, slot * 0.5);

  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, height: H, role: "img" });
  svg.setAttribute("aria-label", "Tempo medio por coluna");

  data.forEach((c, i) => {
    const cx = slot * i + slot / 2;
    const h = (c.avg_hours / max) * ih;
    const y = padT + ih - h;
    const color = colorOf(c.label);

    const bar = svgEl("rect", {
      x: cx - bw / 2, y, width: bw, height: Math.max(h, 3), rx: 4, fill: color,
    });
    bar.style.cursor = "default";
    bar.addEventListener("mousemove", (ev) => tip.show(
      tipRows(c.label, [
        { color, label: "Media", value: c.avg_human },
        { label: "Mediana", value: fmtH(c.median_hours) },
        { label: "Maximo", value: fmtH(c.max_hours) },
        { label: "Passagens", value: String(c.completed_passes) },
        { label: "Cards agora", value: String(c.wip) },
      ]), ev.clientX, ev.clientY));
    bar.addEventListener("mouseleave", tip.hide);
    svg.appendChild(bar);

    // marca da mediana — pode ficar acima do topo da barra quando a
    // distribuicao e assimetrica, entao o rotulo sobe junto
    let labelY = y;
    if (c.median_hours > 0) {
      const my = padT + ih - (c.median_hours / max) * ih;
      svg.appendChild(svgEl("line", {
        x1: cx - bw / 2 - 3, x2: cx + bw / 2 + 3, y1: my, y2: my,
        stroke: cssVar("--ink"), "stroke-width": 2, "stroke-linecap": "round", opacity: .8,
      }));
      labelY = Math.min(labelY, my);
    }

    const val = svgEl("text", {
      x: cx, y: labelY - 9, "text-anchor": "middle",
      style: `font-size:12px;font-weight:600;fill:${cssVar("--ink")}`,
    });
    val.textContent = c.avg_human;
    svg.appendChild(val);

    const lab = svgEl("text", {
      x: cx, y: H - 10, "text-anchor": "middle",
      style: `font-size:11.5px;fill:${cssVar("--ink-2")}`,
    });
    lab.textContent = c.label.length > 14 ? c.label.slice(0, 13) + "…" : c.label;
    svg.appendChild(lab);
  });

  svg.appendChild(svgEl("line", {
    x1: 0, x2: W, y1: padT + ih, y2: padT + ih, stroke: cssVar("--line-2"), "stroke-width": 1,
  }));
  host.appendChild(svg);
}

/* ── blocos de conteudo ───────────────────────────────────────────────── */

/** Com uma sprint selecionada, "periodo anterior" nao quer dizer nada:
 *  o comparativo util e contra a sprint anterior da lista. */
function focusDelta(series, sprintData, milestone, focus) {
  if (!milestone) return { delta: windowDelta(series), note: "vs. periodo anterior" };
  const ordered = sprintData.milestones || [];
  const at = ordered.findIndex((m) => m.milestone === milestone);
  const prev = at >= 0 ? ordered[at + 1] : null;
  const before = prev?.by_label?.[focus]?.hours;
  const current = ordered[at]?.by_label?.[focus]?.hours ?? 0;
  return {
    delta: before ? ((current - before) / before) * 100 : null,
    note: prev ? `vs. ${prev.milestone}` : "sem sprint anterior para comparar",
  };
}

function renderStats(series, contribData, columnData, issues, sprintData, milestone) {
  const focus = state.order.find((l) => /doing|andamento|progress|desenvolv/i.test(l))
    || state.order[1] || state.order[0];

  const focusSeries = series.map((d) => d.values[focus] || 0);
  const wip = (columnData.columns || []).reduce((a, c) => a + c.wip, 0);
  const openIssues = issues.filter((i) => i.state === "opened");
  const leadAvg = issues.length
    ? issues.reduce((a, i) => a + i.lead_time_hours, 0) / issues.length : 0;

  const focusTotal = contribData.totals[focus]?.human || "0h";
  const { delta, note } = focusDelta(series, sprintData, milestone, focus);

  const cards = [
    {
      label: `Tempo acumulado em ${focus || "—"}`,
      value: focusTotal,
      spark: focusSeries,
      color: colorOf(focus),
      delta,
      note,
    },
    {
      label: "Lead time medio",
      value: fmtH(leadAvg),
      spark: series.map(sumDay),
      color: cssVar("--s3"),
      delta: null,
      note: `${issues.length} issues com historico`,
    },
    {
      label: "Cards em andamento agora",
      value: String(wip),
      spark: series.slice(-14).map(sumDay),
      color: cssVar("--s1"),
      delta: null,
      note: `${openIssues.length} issues abertas`,
    },
  ];

  $("stats").innerHTML = cards.map((c) => {
    const dir = c.delta == null ? "" : c.delta >= 0 ? "up" : "down";
    const arrow = c.delta == null ? "" :
      `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
            stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
        <path d="${c.delta >= 0 ? "M12 19V5M6 11l6-6 6 6" : "M12 5v14M6 13l6 6 6-6"}"/></svg>`;
    return `<div class="stat">
      <div class="stat-label">${esc(c.label)}</div>
      <div class="stat-row">
        <div class="stat-value">${esc(c.value)}</div>
        <div class="stat-spark">${sparkline(c.spark, c.color)}</div>
      </div>
      <div class="delta ${dir}">
        ${c.delta == null ? "" :
          `<span class="chip">${arrow}${Math.abs(c.delta).toFixed(1)}%</span>`}
        <span>${esc(c.note)}</span>
      </div>
    </div>`;
  }).join("");
}

function renderHero(totals, delta) {
  const total = Object.values(totals).reduce((a, v) => a + v.hours, 0);
  $("hero").innerHTML =
    `<span>${esc(fmtH(total))}</span>
     <span class="unit">acumuladas</span>` +
    (delta == null ? "" :
      `<span class="badge ${delta >= 0 ? "" : "soft"}">${delta >= 0 ? "+" : ""}${delta.toFixed(1)}%</span>`);
}

function renderLegend(el, labels) {
  el.innerHTML = labels.map((l) =>
    `<span class="legend-item"><i class="swatch" style="background:${colorOf(l)}"></i>${esc(l)}</span>`
  ).join("");
}

function renderContributors(data) {
  const host = $("contrib");
  const cols = data.columns;
  if (!data.contributors.length) {
    host.innerHTML = `<div class="empty">Nenhum contribuidor com tempo registrado no periodo.</div>`;
    return;
  }
  const max = Math.max(...data.contributors.map((c) => c.total_hours)) || 1;

  host.innerHTML = data.contributors.map((c) => {
    const width = Math.max((c.total_hours / max) * 100, 4);
    const segs = cols.map((col) => {
      const b = c.by_label[col];
      if (!b || !b.hours) return "";
      const pct = (b.hours / c.total_hours) * 100;
      return `<i style="flex:${pct};background:${colorOf(col)}"
                 data-label="${esc(col)}" data-value="${esc(b.human)}"
                 data-issues="${b.issues}"></i>`;
    }).join("");
    return `<div class="row-item">
      <div class="row-name">
        <span class="avatar">${esc(initials(c.contributor))}</span>
        <span title="${esc(c.contributor)}">${esc(c.contributor)}</span>
      </div>
      <div class="stack" style="width:${width}%" data-name="${esc(c.contributor)}">${segs}</div>
      <div class="row-total">${esc(c.total_human)}</div>
    </div>`;
  }).join("");

  host.querySelectorAll(".stack i").forEach((seg) => {
    seg.addEventListener("mousemove", (ev) => tip.show(tipRows(
      seg.parentElement.dataset.name,
      [{ color: seg.style.background, label: seg.dataset.label, value: seg.dataset.value },
       { label: "Issues", value: seg.dataset.issues }],
    ), ev.clientX, ev.clientY));
    seg.addEventListener("mouseleave", tip.hide);
  });
}

function renderSprints(data, selected) {
  const host = $("sprints");
  const cols = data.columns;
  renderLegend($("sprint-legend"), cols);

  if (!data.milestones.length) {
    host.innerHTML = `<div class="empty">Nenhuma sprint no cache. As milestones vem no proximo sync.</div>`;
    return;
  }
  const max = Math.max(...data.milestones.map((m) => m.total_hours)) || 1;

  host.innerHTML = data.milestones.map((m) => {
    const width = Math.max((m.total_hours / max) * 100, 4);
    const segs = cols.map((col) => {
      const b = m.by_label[col];
      if (!b || !b.hours) return "";
      const pct = (b.hours / m.total_hours) * 100;
      return `<i style="flex:${pct};background:${colorOf(col)}"
                 data-label="${esc(col)}" data-value="${esc(b.human)}"></i>`;
    }).join("");
    const dot = m.state === "active" ? "active" : m.state === "closed" ? "closed" : "";
    return `<button class="sprint" data-milestone="${esc(m.milestone)}"
                    aria-pressed="${selected === m.milestone}">
      <div>
        <div class="sprint-name">
          <span class="dot-state ${dot}"></span><b>${esc(m.milestone)}</b>
        </div>
        <div class="sprint-when">${esc(fmtRange(m.start_date, m.due_date))}</div>
      </div>
      <div>
        <div class="stack" style="width:${width}%"
             data-name="${esc(m.milestone)}">${segs}</div>
        <div class="sprint-when">${esc(m.total_human)} acumuladas ·
          ${m.contributors} ${m.contributors === 1 ? "pessoa" : "pessoas"}</div>
      </div>
      <div>
        <div class="progress-label"><b>${m.completion}%</b>
          <span>${m.closed_issues}/${m.issues} fechadas</span></div>
        <div class="progress"><i style="width:${Math.min(m.completion, 100)}%"></i></div>
      </div>
      <div class="row-total">${esc(fmtH(m.avg_lead_hours))}
        <div class="sprint-when" style="text-align:right">lead medio</div>
      </div>
    </button>`;
  }).join("");

  host.querySelectorAll(".stack i").forEach((seg) => {
    seg.addEventListener("mousemove", (ev) => {
      ev.stopPropagation();
      tip.show(tipRows(seg.parentElement.dataset.name,
        [{ color: seg.style.background, label: seg.dataset.label, value: seg.dataset.value }]),
        ev.clientX, ev.clientY);
    });
    seg.addEventListener("mouseleave", tip.hide);
  });

  host.querySelectorAll(".sprint").forEach((row) => {
    row.addEventListener("click", () => {
      const value = row.dataset.milestone;
      // clicar na sprint ja selecionada volta para "todas"
      $("milestone").value = $("milestone").value === value ? "" : value;
      safeRefresh();
    });
  });
}

function renderAttention(issues, columnData) {
  const now = Date.now();
  const stale = issues.filter((i) => i.current_column && i.transitions.some(
    (t) => !t.end && now - new Date(t.start).getTime() > 7 * DAY)).length;
  const unassigned = issues.filter((i) => i.assignee === "(sem responsavel)").length;
  const wip = (columnData.columns || []).reduce((a, c) => a + c.wip, 0);

  const rows = [
    { icon: "alert", color: "var(--neg)", label: "Paradas ha mais de 7 dias", n: stale },
    { icon: "clock", color: "var(--warn)", label: "Sem responsavel", n: unassigned },
    { icon: "board", color: "var(--brand)", label: "Cards em coluna agora", n: wip },
  ];
  const glyphs = {
    alert: `<path d="M12 3.5 21 19H3z"/><path d="M12 10v4M12 16.5v.5"/>`,
    clock: `<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>`,
    board: `<rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M9 4v16M15 4v16"/>`,
  };
  $("attention").innerHTML = rows.map((r) => `<div class="att-row">
      <span class="glyph" style="color:${r.color}">
        <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor"
             stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${glyphs[r.icon]}</svg>
      </span>
      <span>${esc(r.label)}</span><span class="n">${r.n}</span>
    </div>`).join("");
}

function renderColumnsTable(columns) {
  $("cols-table").innerHTML =
    `<thead><tr><th>Coluna</th><th class="num">Media</th><th class="num">Mediana</th>
      <th class="num">Maximo</th><th class="num">Passagens</th><th class="num">Agora</th></tr></thead>
     <tbody>` + (columns.length ? columns.map((c) => `<tr>
        <td><span class="tag-pill"><i class="swatch round" style="background:${colorOf(c.label)}"></i>${esc(c.label)}</span></td>
        <td class="num">${esc(c.avg_human)}</td>
        <td class="num">${esc(fmtH(c.median_hours))}</td>
        <td class="num">${esc(fmtH(c.max_hours))}</td>
        <td class="num">${c.completed_passes}</td>
        <td class="num">${c.wip}</td></tr>`).join("")
      : `<tr><td colspan="6" class="muted">Sem dados.</td></tr>`) + `</tbody>`;
}

function renderIssuesTable(data, query) {
  const focus = data.focus_label;
  const rows = data.issues
    .filter((i) => !query || (i.title + i.assignee).toLowerCase().includes(query))
    .slice(0, 20);
  const max = Math.max(1, ...rows.map((i) => i.focus_hours));

  $("issues-table").innerHTML =
    `<thead><tr><th>#</th><th>Issue</th><th>Responsavel</th><th>Coluna atual</th>
      <th class="num">${esc(focus ? `Tempo em ${focus}` : "Tempo trabalhado")}</th>
      <th class="num">Lead time</th><th class="col-extra">Onde o tempo foi</th></tr></thead>
     <tbody>` + (rows.length ? rows.map((i) => {
        const top = Object.entries(i.time_by_column)
          .filter(([label]) => label !== focus).slice(0, 2);
        const bar = (i.focus_hours / max) * 100;
        return `<tr class="row-link" data-iid="${i.iid}" tabindex="0"
                    title="Ver quem trabalhou neste card">
        <td class="muted">${i.iid}</td>
        <td class="title-cell">
          <a href="${esc(i.web_url)}" target="_blank" rel="noreferrer">${esc(i.title)}</a>
          ${(i.tags || []).length ? `<div class="tags">${i.tags.slice(0, 4).map((tag) =>
              `<span class="tag-plain">${esc(tag)}</span>`).join("")}</div>` : ""}
        </td>
        <td><span class="row-name"><span class="avatar" style="width:22px;height:22px;font-size:9px">${esc(initials(i.assignee))}</span>
            <span>${esc(i.assignee)}</span></span></td>
        <td>${i.current_column
              ? `<span class="tag-pill"><i class="swatch round" style="background:${colorOf(i.current_column)}"></i>${esc(i.current_column)}</span>`
              : `<span class="muted">fechada</span>`}</td>
        <td class="num">
          <b>${esc(fmtH(i.focus_hours))}</b>
          <div class="mini-bar"><i style="width:${bar}%;background:${colorOf(focus)}"></i></div>
        </td>
        <td class="num muted">${esc(fmtH(i.lead_time_hours))}</td>
        <td class="muted col-extra">${top.map(([l, v]) =>
              `<span class="tag-pill" style="margin-right:4px"><i class="swatch round" style="background:${colorOf(l)}"></i>${esc(l)} ${esc(v.human)}</span>`).join("")}</td>
      </tr>`; }).join("")
      : `<tr><td colspan="7" class="muted">Nenhuma issue corresponde ao filtro.</td></tr>`) + `</tbody>`;

  $("issues-table").querySelectorAll("tr[data-iid]").forEach((tr) => {
    tr.addEventListener("click", (ev) => {
      if (ev.target.closest("a")) return;   // o titulo continua levando ao GitLab
      openIssue(Number(tr.dataset.iid));
    });
    tr.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" || ev.key === " ") {
        ev.preventDefault();
        openIssue(Number(tr.dataset.iid));
      }
    });
  });
}

/* ── drill-down de uma issue ──────────────────────────────────────────── */

const fmtWhen = (iso) => new Date(iso).toLocaleString("pt-BR",
  { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });

function openIssue(iid) {
  const issue = (state.issues?.issues || []).find((i) => i.iid === iid);
  if (!issue) return;
  renderIssueDetail(issue, state.issues.focus_label);
  $("issue-modal").hidden = false;
  document.body.style.overflow = "hidden";
  $("i-close").focus();
}

function closeIssue() {
  $("issue-modal").hidden = true;
  document.body.style.overflow = "";
}

