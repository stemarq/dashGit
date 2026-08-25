"""Testes das metricas de commits, com um cache SQLite sintetico."""

import os
import tempfile
from datetime import datetime, timedelta, timezone

os.environ.setdefault("GITLAB_TOKEN", "test-token")
_DB = os.path.join(tempfile.mkdtemp(), "commits.db")
os.environ["DATABASE_PATH"] = _DB

from app import commits as cm  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import init_db, session  # noqa: E402

get_settings.cache_clear()
get_settings()

NOW = datetime.now(timezone.utc)


def iso(offset_hours: float) -> str:
    return (NOW + timedelta(hours=offset_hours)).isoformat()


def seed(rows=None) -> None:
    init_db()
    with session() as conn:
        conn.execute("DELETE FROM commits")
        conn.execute("DELETE FROM projects")
        conn.execute(
            "INSERT INTO projects (id, path, name, web_url, synced_at) VALUES (1,'g/p','P','u',?)",
            (iso(0),),
        )
        conn.executemany(
            "INSERT INTO commits (project_id, id, short_id, title, author_name, author_email,"
            " committed_at, additions, deletions, is_merge, web_url) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            rows if rows is not None else [
                (1, "a1", "a1", "feat: x", "Ana", "ana@x.com", iso(-5), 30, 5, 0, "u1"),
                (1, "a2", "a2", "fix: y", "Ana", "ana@x.com", iso(-4), 10, 2, 0, "u2"),
                (1, "b1", "b1", "docs: z", "Bruno", "bruno@x.com", iso(-3), 4, 40, 0, "u3"),
                (1, "m1", "m1", "Merge branch", "Bruno", "bruno@x.com", iso(-2), 999, 999, 1, "u4"),
            ],
        )


def approx(value, expected, tol=0.05):
    return abs(value - expected) <= tol


def test_merge_commit_fica_de_fora_das_linhas():
    """Merge repete as linhas dos commits que traz; contar infla tudo."""
    seed()
    t = cm.commit_report(1)["totals"]
    assert t["commits"] == 3
    assert t["additions"] == 44 and t["deletions"] == 47

    com_merge = cm.commit_report(1, include_merges=True)["totals"]
    assert com_merge["commits"] == 4
    assert com_merge["additions"] == 1043


def test_totais_por_autor():
    seed()
    autores = {a["author"]: a for a in cm.commit_report(1)["authors"]}
    assert autores["Ana"]["commits"] == 2
    assert autores["Ana"]["additions"] == 40
    assert autores["Bruno"]["commits"] == 1
    assert autores["Bruno"]["net"] == 4 - 40      # removeu mais do que somou
    assert [a["author"] for a in cm.commit_report(1)["authors"]] == ["Ana", "Bruno"]


def test_serie_diaria_preenche_dias_vazios():
    seed()
    r = cm.commit_report(1)
    assert r["granularity"] == "day"
    total = sum(p["commits"] for p in r["series"])
    assert total == 3
    # o intervalo e continuo: nenhuma data pulada entre o primeiro e o ultimo
    datas = [p["date"] for p in r["series"]]
    assert datas == sorted(datas) and len(set(datas)) == len(datas)


def test_serie_troca_de_balde_em_intervalo_longo():
    """Repo criado de template carrega commits de anos atras; forcar barras
    diarias renderiza centenas de colunas vazias."""
    seed([
        (1, "old", "old", "Initial commit", "Bot", "bot@x.com",
         (NOW - timedelta(days=1200)).isoformat(), 5, 0, 0, "u0"),
        (1, "new", "new", "feat: hoje", "Ana", "ana@x.com", iso(-2), 7, 1, 0, "u1"),
    ])
    r = cm.commit_report(1)
    assert r["granularity"] == "month"
    assert len(r["series"]) < 60          # ~40 meses, nao 1200 dias
    assert sum(p["commits"] for p in r["series"]) == 2


def test_heatmap_soma_todos_os_commits():
    seed()
    heat = cm.commit_report(1)["heatmap"]
    assert len(heat["counts"]) == 7 and len(heat["counts"][0]) == 24
    assert sum(sum(linha) for linha in heat["counts"]) == 3


def test_identidades_agrupam_emails_do_mesmo_nome():
    seed([
        (1, "c1", "c1", "a", "Ana", "ana@empresa.com", iso(-3), 1, 0, 0, "u"),
        (1, "c2", "c2", "b", "Ana", "ana@gmail.com", iso(-2), 1, 0, 0, "u"),
    ])
    ids = cm.identities(1)
    assert len(ids) == 1
    assert ids[0]["commits"] == 2
    assert ids[0]["emails"] == ["ana@empresa.com", "ana@gmail.com"]


MEMBROS = [
    "Tiago Brun de Arruda",
    "Lucas Delmirio da Silva",
    "Sofia Farias Brandão",
    "José Guilherme Gonçalves Maia",
    "João Paulo Barreto Ferreira Andrade Rodrigues de Paula",
]


def test_casa_nome_curto_unico():
    assert cm.match_member("Tiago", MEMBROS, "tiagoba2203@gmail.com") == "Tiago Brun de Arruda"


def test_casa_login_com_ponto():
    assert cm.match_member("lucas.delmirio", MEMBROS) == "Lucas Delmirio da Silva"


def test_casa_ignorando_acento():
    assert cm.match_member("sofia.brandao", MEMBROS) == "Sofia Farias Brandão"


def test_casa_nome_do_meio_com_sobrenome():
    assert cm.match_member("Guilherme Maia", MEMBROS) == "José Guilherme Gonçalves Maia"


def test_nao_chuta_por_sobrenome_comum():
    """'Rodrigues' aparece no nome de outra pessoa; um pedaco so nao basta
    quando o autor assina com nome completo."""
    assert cm.match_member("Thais Rodrigues Neubauer", MEMBROS) is None


def test_nao_identifica_bot():
    assert cm.match_member("Inteli Hub", MEMBROS, "99201292+intelihub@users.noreply.github.com") is None


def test_empate_nao_identifica():
    """Dois membros com o mesmo primeiro nome: melhor deixar em branco."""
    assert cm.match_member("Ana", ["Ana Souza", "Ana Lima"]) is None


# ── convencao de mensagem ────────────────────────────────────────────────

def test_mensagem_no_padrao_passa():
    assert cm.check_title("docs(#93): escreve a secao 4.1") == []
    assert cm.check_title("feat(#7): adiciona filtro por sprint") == []


def test_maiuscula_e_permitida():
    """Decisao do time em 24/08/2026: sigla tecnica no meio da descricao era
    o motivo mais comum de reprovacao e nao atrapalhava a leitura."""
    assert cm.check_title("feat(#7): deriva NPS_MEDIO por rota") == []
    assert cm.check_title("Fix(#1): Corrige o Bug") == []


def test_acento_continua_fora_do_padrao():
    assert cm.check_title("docs(#7): remove duplicacao da secao") == []
    assert cm.check_title("docs(#7): corrige a duplicação") == ["acento"]


def test_espacamento_e_separado_de_formato():
    assert cm.check_title("feat (#145): carrega os dados") == ["espaco"]
    assert cm.check_title("feat(#145):carrega os dados") == ["espaco"]


def test_sem_issue_e_tipo_invalido():
    assert cm.check_title("docs: escreve a secao") == ["sem_issue"]
    assert cm.check_title("wip(#3): mexe em tudo") == ["tipo"]
    assert cm.check_title("") == ["vazio"]


def test_aderencia_por_pessoa_ignora_merge():
    seed([
        (1, "c1", "c1", "feat(#1): adiciona o filtro", "Ana", "ana@x.com", iso(-5), 1, 0, 0, "u1"),
        (1, "c2", "c2", "Fix (#2): Corrige", "Ana", "ana@x.com", iso(-4), 1, 0, 0, "u2"),
        (1, "c3", "c3", "docs: sem issue", "Bruno", "bruno@x.com", iso(-3), 1, 0, 0, "u3"),
        (1, "c4", "c4", "Merge branch main", "Bruno", "bruno@x.com", iso(-2), 1, 0, 1, "u4"),
    ])
    r = cm.convention_report(1)
    por_pessoa = {a["author"]: a for a in r["authors"]}
    assert r["totals"] == {"commits": 3, "ok": 1, "off": 2, "pct": 33.3}
    assert por_pessoa["Ana"]["pct"] == 50.0 and por_pessoa["Ana"]["ok"] == 1
    assert por_pessoa["Bruno"]["pct"] == 0.0          # o merge nao entra
    assert por_pessoa["Bruno"]["reasons"] == {"sem_issue": 1}
    assert por_pessoa["Ana"]["offenders"][0]["title"] == "Fix (#2): Corrige"
    assert por_pessoa["Ana"]["reasons"] == {"espaco": 1}   # a caixa nao reprova


def test_issue_citada_na_mensagem():
    assert cm.issue_ref("feat(#145): x") == 145
    assert cm.issue_ref("mexe na issue #7 sem padrao") == 7
    assert cm.issue_ref("sem referencia") is None


# ── filtro por contribuinte ──────────────────────────────────────────────

def seed_identidades() -> None:
    """A mesma pessoa assinando de duas formas, mais um membro do GitLab."""
    seed([
        (1, "d1", "d1", "feat(#1): adiciona o filtro", "lucas.delmirio",
         "lucas@x.com", iso(-5), 1, 0, 0, "u1"),
        (1, "d2", "d2", "Fix (#2): corrige", "Lucas Delmirio",
         "lucas.delmirio@y.com", iso(-4), 1, 0, 0, "u2"),
        (1, "d3", "d3", "docs(#3): escreve a secao", "Ana", "ana@x.com", iso(-3), 1, 0, 0, "u3"),
    ])
    with session() as conn:
        conn.execute("DELETE FROM label_events")
        conn.executemany(
            "INSERT INTO label_events VALUES (?,?,?,?,?,?,?,?)",
            [(1, 1, 1, "add", "Doing", 7, "Lucas Delmirio da Silva", iso(-5)),
             (2, 1, 2, "add", "Doing", 8, "Ana Paula Souza", iso(-3))],
        )


def test_filtro_por_pessoa_junta_as_identidades_de_git():
    """Filtrar por contribuinte, nao por assinatura: quem commita como
    `lucas.delmirio` e como `Lucas Delmirio` e a mesma pessoa."""
    seed_identidades()
    r = cm.commit_report(1, author="Lucas Delmirio da Silva")
    assert r["totals"]["commits"] == 2
    assert {a["author"] for a in r["authors"]} == {"lucas.delmirio", "Lucas Delmirio"}


def test_filtro_por_assinatura_de_git_tambem_vale():
    seed_identidades()
    assert cm.commit_report(1, author="lucas.delmirio")["totals"]["commits"] == 2
    assert cm.commit_report(1, author="lucas@x.com")["totals"]["commits"] == 2


def test_filtro_por_quem_nao_e_do_time_cai_no_nome_exato():
    seed_identidades()
    assert cm.commit_report(1, author="Ana")["totals"]["commits"] == 1
    assert cm.commit_report(1, author="ninguem")["totals"]["commits"] == 0


def test_listagem_marca_os_commits_fora_da_convencao():
    seed_identidades()
    r = cm.commit_report(1)
    por_id = {c["short_id"]: c for c in r["recent"]}
    assert por_id["d1"]["convention"] == []
    assert por_id["d2"]["convention"] == ["espaco"]
    assert por_id["d1"]["issue"] == 1
    assert r["off_convention"] == 1


def test_lente_de_fora_do_padrao_nao_encolhe_os_totais():
    """`only_off` e uma lente de leitura sobre a lista; se os totais caissem
    junto, o volume de commits do periodo viraria mentira."""
    seed_identidades()
    r = cm.commit_report(1, only_off=True)
    assert r["totals"]["commits"] == 3          # os totais seguem sobre todos
    assert [c["short_id"] for c in r["recent"]] == ["d2"]


# ── quem e do time ───────────────────────────────────────────────────────

def seed_com_gente_de_fora() -> None:
    """Time de duas pessoas, mais o bot do template e um professor."""
    seed([
        (1, "e1", "e1", "feat(#1): adiciona o filtro", "Ana", "ana@x.com", iso(-5), 1, 0, 0, "u1"),
        (1, "e2", "e2", "docs(#2): escreve a secao", "Bruno", "bruno@x.com", iso(-4), 1, 0, 0, "u2"),
        (1, "e3", "e3", "Initial commit from template", "Inteli Hub",
         "hub@inteli.edu.br", iso(-3), 500, 0, 0, "u3"),
        (1, "e4", "e4", "correcoes da entrega", "Thais Neubauer",
         "thais@prof.edu.br", iso(-2), 9, 9, 0, "u4"),
    ])
    with session() as conn:
        conn.execute("DELETE FROM label_events")
        conn.executemany(
            "INSERT INTO label_events VALUES (?,?,?,?,?,?,?,?)",
            [(1, 1, 1, "add", "Doing", 7, "Ana Paula Souza", iso(-5)),
             (2, 1, 2, "add", "Doing", 8, "Bruno Lima", iso(-4))],
        )


def test_commit_de_fora_do_time_nao_entra_em_metrica_nenhuma():
    """O bot do template e a conta do professor commitam no mesmo repositorio;
    contar isso como trabalho do time distorce ritmo, ranking e aderencia."""
    seed_com_gente_de_fora()
    r = cm.commit_report(1)
    assert r["totals"]["commits"] == 2 and r["totals"]["authors"] == 2
    assert {a["author"] for a in r["authors"]} == {"Ana", "Bruno"}
    assert r["totals"]["additions"] == 2          # sem as 500 linhas do template


def test_o_que_ficou_de_fora_e_declarado():
    """Excluir calado seria pior que incluir: quem le tem de saber o que sumiu."""
    seed_com_gente_de_fora()
    nota = cm.commit_report(1)["outsiders"]
    assert nota["commits"] == 2
    assert {a["author"] for a in nota["authors"]} == {"Inteli Hub", "Thais Neubauer"}


def test_aderencia_nao_e_afundada_por_quem_nao_segue_a_convencao_do_time():
    seed_com_gente_de_fora()
    r = cm.convention_report(1)
    assert r["totals"] == {"commits": 2, "ok": 2, "off": 0, "pct": 100.0}
    assert r["outsiders"]["commits"] == 2


def test_sem_board_sincronizado_ninguem_e_excluido():
    """Sem eventos de label nao ha lista de membros: excluir todo mundo
    zeraria a tela em vez de protege-la do ruido."""
    seed_com_gente_de_fora()
    with session() as conn:
        conn.execute("DELETE FROM label_events")
    assert cm.commit_report(1)["totals"]["commits"] == 4


def test_flag_traz_os_de_fora_de_volta(monkeypatch):
    seed_com_gente_de_fora()
    settings = get_settings()
    monkeypatch.setattr(settings, "count_non_members", True)
    assert cm.commit_report(1)["totals"]["commits"] == 4
    assert cm.commit_report(1)["outsiders"]["commits"] == 0


# ── recorte por sprint ───────────────────────────────────────────────────

def seed_com_sprints() -> None:
    """Commits citando issues de duas sprints, mais um sem issue nenhuma."""
    seed([
        (1, "s1", "s1", "feat(#1): adiciona o filtro", "Ana", "ana@x.com", iso(-9), 1, 0, 0, "u1"),
        (1, "s2", "s2", "Fix (#1): corrige", "Ana", "ana@x.com", iso(-8), 1, 0, 0, "u2"),
        (1, "s3", "s3", "docs(#2): documenta", "Ana", "ana@x.com", iso(-7), 1, 0, 0, "u3"),
        (1, "s4", "s4", "chore: sem issue", "Ana", "ana@x.com", iso(-6), 1, 0, 0, "u4"),
    ])
    with session() as conn:
        conn.execute("DELETE FROM issues")
        conn.execute("DELETE FROM label_events")
        conn.executemany(
            "INSERT INTO issues (project_id, iid, id, title, state, milestone, assignee_name)"
            " VALUES (?,?,?,?,?,?,?)",
            [(1, 1, 11, "Login", "closed", "Sprint 1", "Ana"),
             (1, 2, 12, "Cache", "opened", "Sprint 2", "Ana"),
             (1, 3, 13, "Solta", "opened", None, "Ana")],
        )
        conn.execute(
            "INSERT INTO label_events VALUES (1,1,1,'add','Doing',7,'Ana Paula Souza',?)",
            (iso(-9),),
        )


def test_commit_entra_na_sprint_da_issue_que_cita():
    """Data nao serve: commit do primeiro dia da sprint 2 pode ser de uma
    issue arrastada da sprint 1."""
    seed_com_sprints()
    assert cm.commit_report(1, milestone="Sprint 1")["totals"]["commits"] == 2
    assert cm.commit_report(1, milestone="Sprint 2")["totals"]["commits"] == 1


def test_commit_sem_issue_fica_de_fora_e_e_declarado():
    seed_com_sprints()
    r = cm.commit_report(1, milestone="Sprint 1")
    assert r["unlinked_commits"] == 1
    assert r["milestone"] == "Sprint 1"
    assert "s4" not in {c["short_id"] for c in r["recent"]}


def test_sem_sprint_escolhida_tudo_entra():
    seed_com_sprints()
    r = cm.commit_report(1)
    assert r["totals"]["commits"] == 4 and r["unlinked_commits"] == 0


def test_aderencia_tambem_recorta_por_sprint():
    seed_com_sprints()
    s1 = cm.convention_report(1, milestone="Sprint 1")
    s2 = cm.convention_report(1, milestone="Sprint 2")
    assert s1["totals"] == {"commits": 2, "ok": 1, "off": 1, "pct": 50.0}
    assert s2["totals"]["pct"] == 100.0


def test_issue_citada_de_outro_projeto_nao_entra():
    """`#99` nao existe no cache: nao da para dizer a sprint dele."""
    seed_com_sprints()
    with session() as conn:
        conn.execute(
            "INSERT INTO commits (project_id, id, short_id, title, author_name,"
            " author_email, committed_at, additions, deletions, is_merge, web_url)"
            " VALUES (1,'s5','s5','feat(#99): de outro repo','Ana','ana@x.com',?,1,0,0,'u5')",
            (iso(-5),),
        )
    r = cm.commit_report(1, milestone="Sprint 1")
    assert r["totals"]["commits"] == 2 and r["unlinked_commits"] == 2
