/* Aba de commits. Depende das primitivas de app.js e do roteador de
   contributors.js, por isso carrega depois dos dois. */

VIEWS.commits = { title: "Commits", node: "view-commits" };
SUBTITLES.commits = {
  plain: "Volume, ritmo e horario dos commits. O filtro de periodo vale aqui;"
    + " sprint e coluna nao (commit nao passa por board).",
  scoped: () => SUBTITLES.commits.plain,
};

const fmtInt = (n) => n.toLocaleString("pt-BR");
const BUCKET = {
  day: { fmt: { day: "2-digit", month: "short" }, nome: "dia" },
  week: { fmt: { day: "2-digit", month: "short" }, nome: "semana" },
  month: { fmt: { month: "short", year: "2-digit" }, nome: "mes" },
};
const fmtBucket = (iso, gran) =>
  new Date(`${iso}T00:00:00`).toLocaleDateString("pt-BR", BUCKET[gran].fmt);

/* ── barras verticais de commits por dia ──────────────────────────────── */

function renderDailyCommits(daily, gran) {
  const host = $("c-daily");
  host.innerHTML = "";
  $("c-daily-sub").textContent = gran === "day"
    ? "Um ponto por dia."
    : `Um ponto por ${BUCKET[gran].nome} — o intervalo e longo demais para barras diarias.`;
  if (daily.length < 2) {
    host.innerHTML = `<div class="empty">Sem commits no periodo.</div>`;
    return;
  }

  const W = host.clientWidth || 900;
  const H = 220, padL = 34, padR = 6, padT = 12, padB = 26;
  const iw = W - padL - padR, ih = H - padT - padB;
  const max = Math.max(1, ...daily.map((d) => d.commits));
  const nice = Math.max(4, Math.ceil(max / 4) * 4);
  const step = iw / daily.length;
  const bw = Math.max(2, Math.min(18, step - 2));

  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, height: H, role: "img" });
  svg.setAttribute("aria-label", "Commits por dia");

  const axis = svgEl("g", { class: "axis" });
  for (let i = 0; i <= 4; i++) {
    const v = (nice / 4) * i;
    const y = padT + ih - (v / nice) * ih;
    axis.appendChild(svgEl("line", { class: "grid-line", x1: padL, x2: W - padR, y1: y, y2: y }));
    const t = svgEl("text", { x: padL - 8, y: y + 4, "text-anchor": "end" });
    t.textContent = String(Math.round(v));
    axis.appendChild(t);
  }
  svg.appendChild(axis);

  daily.forEach((d, i) => {
    if (!d.commits) return;
    const h = (d.commits / nice) * ih;
    const x = padL + step * i + (step - bw) / 2;
    const bar = svgEl("rect", {
      x, y: padT + ih - h, width: bw, height: Math.max(h, 2), rx: 3,
      fill: cssVar("--s1"),
    });
    bar.style.cursor = "default";
    bar.addEventListener("mousemove", (ev) => tip.show(tipRows(fmtBucket(d.date, gran), [
      { color: cssVar("--s1"), label: "Commits", value: String(d.commits) },
      { label: "Linhas", value: `+${fmtInt(d.additions)} / -${fmtInt(d.deletions)}` },
    ]), ev.clientX, ev.clientY));
    bar.addEventListener("mouseleave", tip.hide);
    svg.appendChild(bar);
  });

  const marcas = Math.max(1, Math.ceil(daily.length / 8));
  const xa = svgEl("g", { class: "axis" });
  daily.forEach((d, i) => {
    if (i % marcas) return;
    const t = svgEl("text", {
      x: padL + step * i + step / 2, y: H - 6, "text-anchor": "middle",
    });
    t.textContent = fmtBucket(d.date, gran);
    xa.appendChild(t);
  });
  svg.appendChild(xa);
  host.appendChild(svg);
}

/* ── ranking de autores ───────────────────────────────────────────────── */

function renderCommitAuthors(autores) {
  const host = $("c-authors");
  if (!autores.length) {
    host.innerHTML = `<div class="empty">Sem commits no periodo.</div>`;
    return;
  }
  const max = Math.max(...autores.map((a) => a.commits));
  host.innerHTML = autores.map((a) => `<div class="row-item">
      <div class="row-name">
        <span class="avatar">${esc(initials(a.author))}</span>
        <span title="${esc(a.author)}${a.email ? ` · ${esc(a.email)}` : ""}">${esc(a.author)}</span>
      </div>
      <div>
        <div class="progress" style="margin-top:0">
          <i style="width:${(a.commits / max) * 100}%;background:${cssVar("--s1")}"></i>
        </div>
        <div class="sprint-when">
          <span class="pos">+${fmtInt(a.additions)}</span>
          <span class="neg">-${fmtInt(a.deletions)}</span>
          · ${a.active_days} ${a.active_days === 1 ? "dia" : "dias"}
          · ${a.avg_size} linhas/commit
        </div>
      </div>
      <div class="row-total">${a.commits}</div>
    </div>`).join("");
}

/* ── heatmap dia x hora ───────────────────────────────────────────────── */

function renderHeatmap(heat) {
  const host = $("c-heat");
  const counts = heat.counts;
  const max = Math.max(1, ...counts.flat());

  // rampa sequencial: um matiz so, claro -> escuro (nunca arco-iris)
  const cor = (n) => n === 0 ? "var(--surface-3)"
    : `color-mix(in srgb, ${cssVar("--s1")} ${12 + (n / max) * 88}%, var(--surface-3))`;

  const horas = [0, 3, 6, 9, 12, 15, 18, 21];
  host.innerHTML = `
    <div class="heat">
      <div class="heat-corner"></div>
      ${Array.from({ length: 24 }, (_, h) =>
        `<div class="heat-hour">${horas.includes(h) ? h : ""}</div>`).join("")}
      ${counts.map((linha, d) => `
        <div class="heat-day">${esc(heat.weekdays[d])}</div>
        ${linha.map((n, h) => `<i class="heat-cell" style="background:${cor(n)}"
            data-t="${esc(heat.weekdays[d])} ${String(h).padStart(2, "0")}h"
            data-n="${n}"></i>`).join("")}
      `).join("")}
    </div>
    <div class="heat-legend">
      <span>menos</span>
      ${[0, .25, .5, .75, 1].map((f) =>
        `<i style="background:${cor(f * max)}"></i>`).join("")}
      <span>mais</span>
    </div>`;

  host.querySelectorAll(".heat-cell").forEach((cell) => {
    cell.addEventListener("mousemove", (ev) => tip.show(tipRows(cell.dataset.t, [
      { color: cssVar("--s1"), label: "Commits", value: cell.dataset.n },
    ]), ev.clientX, ev.clientY));
    cell.addEventListener("mouseleave", tip.hide);
  });
}

/* ── identidades divergentes ──────────────────────────────────────────── */

function renderIdentities(autores) {
  const card = $("c-identities-card");
  // so vale mostrar quem o dash nao conseguiu casar com um usuario do GitLab
  const soltos = autores.filter((a) => !a.gitlab_name);
  if (!soltos.length) { card.hidden = true; return; }

  card.hidden = false;
  $("c-identities-sub").innerHTML =
    `O autor de um commit vem do <code>git config</code> da maquina, nao do usuario do
     GitLab. ${soltos.length} ${soltos.length === 1 ? "identidade nao bateu" : "identidades nao bateram"}
     com ninguem do board — normalmente e o mesmo nome escrito de outro jeito,
     e nesse caso os commits da pessoa aparecem divididos.`;

  $("c-identities").innerHTML =
    `<thead><tr><th>Autor no git</th><th>E-mails</th>
      <th class="num">Commits</th><th>Usuario do GitLab</th></tr></thead><tbody>` +
    autores.map((a) => `<tr>
      <td>${esc(a.author)}</td>
      <td class="muted">${a.emails.map((e) => `<span class="tag-plain">${esc(e)}</span>`).join(" ")}</td>
      <td class="num">${a.commits}</td>
      <td>${a.gitlab_name
        ? `<span class="tag-pill"><i class="swatch round" style="background:var(--pos)"></i>${esc(a.gitlab_name)}</span>`
        : `<span class="muted">nao identificado</span>`}</td>
    </tr>`).join("") + `</tbody>`;
}

/* ── tabela de commits recentes ───────────────────────────────────────── */

function renderRecentCommits(data) {
  const recent = data.recent;
  const rotulos = data.reason_labels || {};
  const quem = commitFilters.author ? ` de ${commitFilters.author}` : "";

  $("c-recent-title").textContent = commitFilters.onlyOff
    ? "Commits fora do padrao" : "Ultimos commits";
  $("c-recent-sub").innerHTML = commitFilters.onlyOff
    ? `Os ${data.off_convention} commits${esc(quem)} que fogem da convencao no periodo`
      + ` — listados os 30 mais recentes. Os numeros acima continuam sobre todos.`
    : `Os 30 commits mais recentes${esc(quem)}.`
      + (data.off_convention
          ? ` <b>${data.off_convention}</b> dos ${data.totals.commits} fogem da convencao`
            + ` e vem marcados.`
          : ` Todos seguem a convencao.`);

  $("c-recent").innerHTML =
    `<thead><tr><th>Commit</th><th>Mensagem</th><th>Autor</th>
      <th class="num">Linhas</th><th class="num">Quando</th></tr></thead><tbody>` +
    (recent.length ? recent.map((c) => {
      const fora = (c.convention || []).length;
      return `<tr class="${fora ? "off-row" : ""}">
      <td><a href="${esc(c.web_url)}" target="_blank" rel="noreferrer"
             style="font-family:ui-monospace,monospace;font-size:12px">${esc(c.short_id)}</a></td>
      <td class="title-cell">
        <span class="msg">${fora ? `<span class="off-dot" title="Fora da convencao">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor"
               stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3.5 21 19H3z"/><path d="M12 10v3.4M12 16.2v.4"/></svg>
        </span>` : ""}${esc(c.title)}</span>
        ${fora ? `<div class="tags">${c.convention.map((r) =>
          `<span class="tag-plain off">${esc(rotulos[r] || r)}</span>`).join("")}</div>` : ""}
      </td>
      <td><span class="row-name"><span class="avatar" style="width:22px;height:22px;font-size:9px">${esc(initials(c.author || "?"))}</span>
        <span>${esc(c.author)}</span></span></td>
      <td class="num"><span class="pos">+${fmtInt(c.additions)}</span>
        <span class="neg">-${fmtInt(c.deletions)}</span></td>
      <td class="num muted">${esc(new Date(c.committed_at).toLocaleString("pt-BR",
        { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }))}</td>
    </tr>`; }).join("")
      : `<tr><td colspan="5" class="muted">${commitFilters.onlyOff
          ? "Nenhum commit fora do padrao neste recorte."
          : "Sem commits no periodo."}</td></tr>`) + `</tbody>`;
}

/* ── carregamento ─────────────────────────────────────────────────────── */

/* O filtro de pessoa e a lente "so fora do padrao" tem alcances diferentes de
   proposito: escolher uma pessoa recorta a tela inteira (ritmo, ranking,
   heatmap e aderencia passam a ser dela), enquanto a lente recorta so a
   listagem — encolher os totais junto seria mentir sobre o volume. */
const commitFilters = { author: "", onlyOff: false };

function commitParams(extra = {}) {
  const p = new URLSearchParams();
  if ($("project").value) p.set("project", $("project").value);
  // sprint e coluna sao dimensoes de board; commit nao passa por elas
  if ($("days").value && !$("milestone").value) p.set("days", $("days").value);
  if (commitFilters.author) p.set("author", commitFilters.author);
  for (const [k, v] of Object.entries(extra)) p.set(k, v);
  return p;
}

async function loadCommits() {
  const p = commitParams();

  try {
    const [data, ids, conv] = await Promise.all([
      api("/metrics/commits", commitParams(
        commitFilters.onlyOff ? { only_off: "true" } : {})),
      api("/commit-authors", new URLSearchParams(
        $("project").value ? { project: $("project").value } : {})).catch(() => ({ authors: [] })),
      api("/metrics/commit-convention", p).catch(() => null),
    ]);
    state.commits = data;
    state.convention = conv;
    renderAuthorFilter(ids.authors);
    renderCommitStats(data.totals);
    renderDailyCommits(data.series, data.granularity);
    renderCommitAuthors(data.authors);
    renderHeatmap(data.heatmap);
    renderIdentities(ids.authors);
    renderConvention(conv);
    renderRecentCommits(data);
  } catch (e) {
    $("c-sub").textContent = `Nao foi possivel carregar: ${e.message}`;
  }
}

