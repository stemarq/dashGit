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

