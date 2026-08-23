/* Tela de relatorio: comparativo entre sprints. Depende de app.js e do
   roteador de contributors.js, por isso carrega depois dos dois.

   O filtro de periodo nao vale aqui de proposito — cada sprint e comparada
   pela sua duracao inteira, como no card de sprints da visao geral. */

VIEWS.report = { title: "Relatorio de sprints", node: "view-report" };
SUBTITLES.report = {
  plain: "Board, pessoas e commits lado a lado, sprint a sprint,"
    + " com a variacao contra a sprint anterior.",
  scoped: (m) => `Resumo de ${m}: numeros, gargalo, pessoas, issues e commits.`
    + " Escolha 'Todas as sprints' para voltar ao comparativo.",
};

