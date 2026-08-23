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

