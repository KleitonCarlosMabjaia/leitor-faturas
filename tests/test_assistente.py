"""Assistente de contabilidade: ciclo de ferramentas com um cliente da API simulado (sem Internet nem custos)."""

from datetime import date
from types import SimpleNamespace

from app.modelos.tabelas import Empresa
from app.servicos.contabilidade import assistente, ia, plano_snc


def bloco_texto(texto):
    return SimpleNamespace(type="text", text=texto)


def bloco_ferramenta(id_, nome, entrada):
    return SimpleNamespace(type="tool_use", id=id_, name=nome, input=entrada)


class ClienteFalso:
    """Devolve as respostas pela ordem dada e guarda os pedidos recebidos."""

    def __init__(self, respostas):
        self.respostas = list(respostas)
        self.pedidos = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._criar))

    def _criar(self, **pedido):
        self.pedidos.append(pedido)
        return self.respostas.pop(0)


def test_todas_as_ferramentas_respondem(fabrica_sessoes, empresa_teste):
    with fabrica_sessoes() as db:
        empresa = db.get(Empresa, empresa_teste)
        plano_snc.garantir_plano(db, empresa.id)
        sessao = assistente.Sessao(db, empresa, date(2026, 11, 18))
        entradas = {
            "balancete": {"de": "2026-01-01", "ate": "2026-11-18"},
            "extrato_conta": {"codigo": "12", "de": "2026-01-01", "ate": "2026-11-18"},
            "pesquisar_lancamentos": {"texto": "", "de": "2026-01-01", "ate": "2026-11-18"},
            "mapa_iva": {"de": "2026-07-01", "ate": "2026-09-30"},
            "demonstracao_resultados": {"de": "2026-01-01", "ate": "2026-11-18"},
            "contas_correntes_fornecedores": {},
            "plano_de_contas": {"prefixo": "62"},
            "obrigacoes_fiscais": {},
            "alertas": {},
        }
        assert set(entradas) | {"propor_lancamento"} == {f["name"] for f in assistente.FERRAMENTAS}
        for nome, entrada in entradas.items():
            assert sessao.executar(nome, entrada).startswith(("{", "["))


def test_pergunta_com_ferramenta_e_proposta(fabrica_sessoes, empresa_teste, monkeypatch):
    proposta = {
        "diario": "BAN", "data": "2026-12-08", "descricao": "Renda de dezembro", "documento": "",
        "linhas": [
            {"conta": "6261", "debito": 1500, "credito": 0, "descricao": "Renda"},
            {"conta": "12", "debito": 0, "credito": 1500, "descricao": ""},
        ],
    }
    cliente = ClienteFalso([
        SimpleNamespace(stop_reason="tool_use", content=[
            bloco_ferramenta("t1", "mapa_iva", {"de": "2026-10-01", "ate": "2026-12-31"}),
            bloco_ferramenta("t2", "propor_lancamento", proposta),
        ]),
        SimpleNamespace(stop_reason="end_turn", content=[bloco_texto("Preparei o lançamento da renda.")]),
    ])
    monkeypatch.setattr(ia, "cliente", lambda: cliente)

    with fabrica_sessoes() as db:
        empresa = db.get(Empresa, empresa_teste)
        resposta = assistente.perguntar(db, empresa, "Lança a renda de dezembro", [], hoje=date(2026, 11, 18))

    assert resposta["resposta"] == "Preparei o lançamento da renda."
    assert resposta["ferramentas"] == ["mapa_iva", "propor_lancamento"]
    assert resposta["propostas"][0]["linhas"][0]["nome"] == "Rendas e alugueres"
    # Os dois resultados vão juntos numa única mensagem, sem erros
    resultados = cliente.pedidos[1]["messages"][-1]["content"]
    assert [r["tool_use_id"] for r in resultados] == ["t1", "t2"]
    assert not any(r["is_error"] for r in resultados)
    assert cliente.pedidos[0]["fallbacks"] == "default"


def test_proposta_invalida_volta_a_ia_como_erro(fabrica_sessoes, empresa_teste, monkeypatch):
    desequilibrada = {
        "diario": "OD", "data": "2026-11-18", "descricao": "Errada", "documento": "",
        "linhas": [{"conta": "6261", "debito": 100, "credito": 0, "descricao": ""},
                   {"conta": "12", "debito": 0, "credito": 90, "descricao": ""}],
    }
    cliente = ClienteFalso([
        SimpleNamespace(stop_reason="tool_use", content=[bloco_ferramenta("t1", "propor_lancamento", desequilibrada)]),
        SimpleNamespace(stop_reason="end_turn", content=[bloco_texto("Não consegui equilibrar o lançamento.")]),
    ])
    monkeypatch.setattr(ia, "cliente", lambda: cliente)
    with fabrica_sessoes() as db:
        resposta = assistente.perguntar(db, db.get(Empresa, empresa_teste), "x", [])
    resultado = cliente.pedidos[1]["messages"][-1]["content"][0]
    assert resultado["is_error"] and "equilibrado" in resultado["content"]
    assert resposta["propostas"] == []


def test_historico_comeca_sempre_pelo_utilizador(fabrica_sessoes, empresa_teste, monkeypatch):
    cliente = ClienteFalso([SimpleNamespace(stop_reason="end_turn", content=[bloco_texto("ok")])])
    monkeypatch.setattr(ia, "cliente", lambda: cliente)
    historico = [{"papel": "assistente", "texto": "Olá"}, {"papel": "utilizador", "texto": "Pergunta 1"},
                 {"papel": "assistente", "texto": "Resposta 1"}]
    with fabrica_sessoes() as db:
        assistente.perguntar(db, db.get(Empresa, empresa_teste), "Pergunta 2", historico)
    mensagens = cliente.pedidos[0]["messages"]
    assert [m["role"] for m in mensagens] == ["user", "assistant", "user"]
    assert mensagens[-1]["content"].endswith("Pergunta 2")
