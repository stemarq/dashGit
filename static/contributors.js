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

document.querySelectorAll(".nav-item[data-view]").forEach((btn) => {
  btn.addEventListener("click", () => {
    const target = btn.dataset.view;
    if (VIEWS[target]) return showView(target);
    if (target === "sync") return $("sync").click();
    const anchor = ANCHORS[target];
    if (!anchor) return;
    showView("overview");
    $(anchor).closest(".card").scrollIntoView({ behavior: "smooth", block: "start" });
  });
});

/* ── grade de pessoas ─────────────────────────────────────────────────── */

function wipOf(person, columns) {
  return columns.reduce((a, col) => a + (person.by_label[col]?.still_in_column || 0), 0);
}

function renderPeople() {
  const data = state.contributors;
  if (!data) return;
  const cols = data.columns;
  const people = data.contributors;

  $("people-sub").textContent = people.length
    ? `${people.length} ${people.length === 1 ? "pessoa" : "pessoas"} com tempo`
      + " registrado no recorte atual. Clique para abrir o perfil."
    : "Ninguem com tempo registrado neste recorte.";

  $("people").innerHTML = people.map((c) => {
    const segs = cols.map((col) => {
      const bucket = c.by_label[col];
      if (!bucket || !bucket.hours) return "";
      return `<i style="flex:${(bucket.hours / c.total_hours) * 100};background:${colorOf(col)}"
                 data-label="${esc(col)}" data-value="${esc(bucket.human)}"></i>`;
    }).join("");
    const focusTime = state.focus ? c.by_label[state.focus]?.human || "0h" : null;
    return `<button class="person" data-name="${esc(c.contributor)}"
                    aria-pressed="${state.person === c.contributor}">
      <div class="person-top">
        <span class="avatar">${esc(initials(c.contributor))}</span>
        <span class="person-id">
          <b title="${esc(c.contributor)}">${esc(c.contributor)}</b>
          <span>${c.issues} ${c.issues === 1 ? "issue" : "issues"} ·
                ${c.closed_issues} fechadas</span>
        </span>
      </div>
      <div class="person-figure"><b>${esc(c.total_human)}</b><span>acumuladas</span></div>
      <div class="stack" data-name="${esc(c.contributor)}">${segs}</div>
      <div class="person-foot">
        <span><em>${wipOf(c, cols)}</em> em coluna agora</span>
        ${focusTime ? `<span><em>${esc(focusTime)}</em> em ${esc(state.focus)}</span>` : ""}
      </div>
      ${c.review_hours ? `<div class="person-review" title="Tempo em ${esc(state.review || "revisao")}, contando os cards de outras pessoas">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
             stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6-10-6-10-6z"/><circle cx="12" cy="12" r="2.6"/></svg>
        <em>${esc(c.review_human)}</em> revisando
        <span>· ${c.review_issues} ${c.review_issues === 1 ? "card" : "cards"}</span>
      </div>` : ""}
      ${c.waiting_hours ? `<div class="person-debt" title="Tempo que cards ficaram parados na fila esperando por esta pessoa">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
             stroke-width="1.9" stroke-linecap="round"><circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/></svg>
        <em>${esc(c.waiting_human)}</em> de espera causada
        <span>· ${c.waiting_issues} ${c.waiting_issues === 1 ? "card" : "cards"}</span>
      </div>` : ""}
    </button>`;
  }).join("");

  $("people").querySelectorAll(".stack i").forEach((seg) => {
    seg.addEventListener("mousemove", (ev) => {
      ev.stopPropagation();
      tip.show(tipRows(seg.parentElement.dataset.name, [
        { color: seg.style.background, label: seg.dataset.label, value: seg.dataset.value },
      ]), ev.clientX, ev.clientY);
    });
    seg.addEventListener("mouseleave", tip.hide);
  });
  $("people").querySelectorAll(".person").forEach((card) => {
    card.addEventListener("click", () => openProfile(card.dataset.name));
  });

  renderLoad(people, cols);
}

/** Carga = cards que a pessoa tem parados numa coluna agora. Nao e tempo:
 *  e quantas frentes ela esta segurando ao mesmo tempo. */
function renderLoad(people, cols) {
  const load = people
    .map((c) => ({ name: c.contributor, wip: wipOf(c, cols) }))
    .filter((p) => p.wip > 0)
    .sort((a, b) => b.wip - a.wip);

  const sprint = $("milestone").value;
  $("load-sub").textContent = sprint
    ? `Cards de ${sprint} que continuam numa coluna. Sprint encerrada normalmente`
      + " nao tem nenhum."
    : "Issues em coluna agora. Barra cheia = quem esta com mais coisa aberta ao mesmo tempo.";

  if (!load.length) {
    $("load").innerHTML = `<div class="empty">${sprint
      ? `Nenhum card de ${esc(sprint)} continua em coluna.`
      : "Nenhum card em coluna neste momento."}</div>`;
    return;
  }
  const max = Math.max(...load.map((p) => p.wip));
  $("load").innerHTML = load.map((p) => `<div class="load-row">
      <div>
        <div class="row-name">
          <span class="avatar">${esc(initials(p.name))}</span>
          <span title="${esc(p.name)}">${esc(p.name)}</span>
        </div>
        <div class="progress">
          <i style="width:${(p.wip / max) * 100}%;background:${colorOf(state.focus)}"></i>
        </div>
      </div>
      <div class="row-total">${p.wip}</div>
    </div>`).join("");
}

/* ── perfil ───────────────────────────────────────────────────────────── */

async function openProfile(name) {
  state.person = name;
  $("people").querySelectorAll(".person").forEach((card) =>
    card.setAttribute("aria-pressed", String(card.dataset.name === name)));

  const panel = $("profile");
  try {
    const p = params();
    p.set("name", name);
    const data = await api("/metrics/contributor", p);
    panel.hidden = false;
    renderProfile(data);
    panel.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (e) {
    panel.hidden = true;
    state.person = null;
    toast(`Nao foi possivel abrir o perfil: ${e.message}`, 5000);
  }
}

function renderProfile(d) {
  const focus = d.focus_label;
  $("p-avatar").textContent = initials(d.contributor);
  $("p-name").textContent = d.contributor;
  $("p-meta").textContent =
    `${d.issues_count} issues · ${d.closed_issues} fechadas · ${d.open_issues} abertas`
    + ($("milestone").value ? ` · recorte de ${$("milestone").value}` : "");

  const focusBucket = focus ? d.by_label[focus] : null;
  $("p-stats").innerHTML = [
    {
      label: `Tempo em ${focus || "coluna de trabalho"}`,
      value: focusBucket?.human || "0h",
      note: `${focusBucket?.issues || 0} issues passaram por la`,
    },
    ...(d.review_label ? [{
      label: `Tempo revisando`,
      value: d.review_human,
      note: d.review_issues
        ? `${d.review_issues} ${d.review_issues === 1 ? "card revisado" : "cards revisados"}`
          + ` em ${esc(d.review_label)}`
        : "nao revisou nada no recorte",
      review: true,
    }] : []),
    { label: "Lead time medio", value: fmtH(d.avg_lead_hours), note: "da criacao ao fechamento" },
    {
      label: "Cards em coluna agora",
      value: String(d.wip),
      note: d.wip ? "frentes abertas ao mesmo tempo" : "nada em aberto",
    },
    ...(state.queues.length ? [{
      label: "Espera causada",
      value: d.waiting_human,
      note: `${d.waiting_issues} cards pararam na fila esperando por ela`,
      debt: true,
    }] : []),
  ].map((c) => `<div class="stat${c.debt ? " debt" : ""}${c.review ? " review" : ""}">
      <div class="stat-label">${esc(c.label)}</div>
      <div class="stat-row"><div class="stat-value">${esc(c.value)}</div></div>
      <div class="delta"><span>${esc(c.note)}</span></div>
    </div>`).join("");

  // sprint escolhida ja delimita o periodo, como na visao geral
  const days = $("days").value && !$("milestone").value ? Number($("days").value) : null;
  renderArea(dailySeries(d.issues, d.columns.length ? d.columns : null, days),
             d.columns, "p-area");

  renderDonut(Object.fromEntries(Object.entries(d.by_label)
    .map(([label, v]) => [label, { hours: v.hours, human: v.human }])), "p-donut");
  // a revisao de card alheio nao entra em by_label quando o escopo e assigned:
  // sem essa nota a rosca parece contradizer o stat de "Tempo revisando"
  $("p-donut-sub").innerHTML = "Distribuicao entre as colunas"
    + (d.review_label && d.review_hours && !d.by_label[d.review_label]
        ? ` — as <b>${esc(d.review_human)}</b> em ${esc(d.review_label)} ficam de fora:`
          + ` sao cards de outras pessoas.`
        : "");

  renderProfileSprints(d);
  renderProfileIssues(d);
}

function renderProfileSprints(d) {
  if (!d.by_milestone.length) {
    $("p-sprints").innerHTML = `<div class="empty">Sem sprints neste recorte.</div>`;
    return;
  }
  const max = Math.max(...d.by_milestone.map((s) => s.hours)) || 1;
  $("p-sprints").innerHTML = d.by_milestone.map((s) => `<div class="sprint static">
      <div><div class="sprint-name"><span class="dot-state"></span><b>${esc(s.milestone)}</b></div></div>
      <div>
        <div class="stack" style="width:${Math.max((s.hours / max) * 100, 4)}%">
          <i style="flex:1;background:${colorOf(state.focus)}"></i>
        </div>
        <div class="sprint-when">${esc(s.human)} acumuladas</div>
      </div>
      <div>
        <div class="progress-label"><b>${s.completion}%</b>
          <span>${s.closed_issues}/${s.issues} fechadas</span></div>
        <div class="progress"><i style="width:${Math.min(s.completion, 100)}%"></i></div>
      </div>
      <div class="row-total">${s.issues}
        <div class="sprint-when" style="text-align:right">issues</div>
      </div>
    </div>`).join("");
}

function renderProfileIssues(d) {
  const focus = d.focus_label;
  $("p-issues-sub").innerHTML =
    (state.scope === "assigned"
      ? `Issues atribuidas a ${esc(d.contributor)}`
        + (d.review_label ? ` e as que ela revisou` : "")
      : `Issues em que ${esc(d.contributor)} fez alguma etapa`)
    + (focus
        ? `, ranqueadas pelo maior tempo dela no card (<b>${esc(focus)}</b>`
          + (d.review_label ? ` ou <b>${esc(d.review_label)}</b>)` : ")")
        : "")
    + (state.scope === "assigned"
        ? `. As horas sao das etapas que ela mesma fez.`
        : `. As horas sao so das colunas que foram dela.`)
    + (d.review_label
        ? ` A coluna <b>Revisando</b> e o tempo dela em <b>${esc(d.review_label)}</b>,`
          + ` medido mesmo nos cards de outras pessoas.`
        : "");

  const review = d.review_label;
  const max = Math.max(1, ...d.issues.map((i) => i.focus_hours));
  $("p-issues").innerHTML =
    `<thead><tr><th>#</th><th>Issue</th><th>Sprint</th><th>Fez</th>
      <th class="num">${esc(focus ? `Tempo em ${focus}` : "Tempo trabalhado")}</th>
      <th class="num">${esc(review ? `Revisando (${review})` : "Tempo dela")}</th>
      ${state.queues.length ? `<th class="num">Deixou esperando</th>` : ""}
      </tr></thead><tbody>` +
    (d.issues.length ? d.issues.slice(0, 25).map((i) => `<tr>
        <td class="muted">${i.iid}</td>
        <td class="title-cell">
          <a href="${esc(i.web_url)}" target="_blank" rel="noreferrer">${esc(i.title)}</a>
          ${(i.tags || []).length ? `<div class="tags">${i.tags.slice(0, 4).map((tag) =>
            `<span class="tag-plain">${esc(tag)}</span>`).join("")}</div>` : ""}
        </td>
        <td class="muted">${esc(i.milestone)}</td>
        <td>${(i.role || []).map((label) =>
          `<span class="tag-pill" style="margin-right:4px"><i class="swatch round" style="background:${colorOf(label)}"></i>${esc(label)}</span>`
        ).join("") || `<span class="muted">—</span>`}</td>
        <td class="num"><b>${esc(fmtH(i.focus_hours))}</b>
          <div class="mini-bar">
            <i style="width:${(i.focus_hours / max) * 100}%;background:${colorOf(focus)}"></i>
          </div>
        </td>
        <td class="num ${review ? (i.review_hours ? "review-cell" : "muted") : "muted"}">${
          esc(fmtH(review ? i.review_hours : i.working_hours))}</td>
        ${state.queues.length
          ? `<td class="num ${i.waiting_hours ? "debt-cell" : "muted"}">${esc(fmtH(i.waiting_hours))}</td>`
          : ""}
      </tr>`).join("")
      : `<tr><td colspan="7" class="muted">Sem issues neste recorte.</td></tr>`) + `</tbody>`;
}

$("p-close").addEventListener("click", () => {
  $("profile").hidden = true;
  state.person = null;
  $("people").querySelectorAll(".person")
    .forEach((card) => card.setAttribute("aria-pressed", "false"));
});
