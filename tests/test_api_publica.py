"""API pública (/api/v1): chaves por empresa, permissões, isolamento, limites e contrato OpenAPI."""

from fastapi.testclient import TestClient

from app import config
from app.main import app
from app.servicos import limites
from tests.conftest import criar_empresa_com_admin

MOVIMENTOS = {"conta": "121", "saldo_final": 1494.8, "origem": "Agregador X", "movimentos": [
    {"data": "2026-11-02", "descricao": "TRF RECEBIDA CLIENTE", "montante": 500, "referencia": "tx-1"},
    {"data": "2026-11-03", "descricao": "COMISSAO MANUTENCAO", "montante": -5.2, "saldo": 1494.8},
]}


def _chave(cliente, permissao="leitura", nome="ERP") -> str:
    r = cliente.post("/api/equipa/chaves-api", json={"nome": nome, "permissao": permissao})
    assert r.status_code == 201, r.text
    assert r.json()["chave"].startswith("dobra_")
    return r.json()["chave"]


def _api(chave: str) -> TestClient:
    # Cliente sem cookies: a API pública só aceita a chave
    return TestClient(app, headers={"Authorization": f"Bearer {chave}"})


def test_chaves_criar_listar_revogar(cliente):
    chave = _chave(cliente)
    lista = cliente.get("/api/equipa/chaves-api").json()
    assert len(lista) == 1 and lista[0]["nome"] == "ERP" and "chave" not in lista[0]
    assert chave.startswith(lista[0]["prefixo"])
    api = _api(chave)
    r = api.get("/api/v1/empresa")
    assert r.status_code == 200 and r.json()["nome"] == "Empresa de Teste" and r.json()["permissao"] == "leitura"
    assert cliente.get("/api/equipa/chaves-api").json()[0]["ultimo_uso"] is not None

    assert cliente.delete(f"/api/equipa/chaves-api/{lista[0]['id']}").status_code == 204
    assert api.get("/api/v1/empresa").status_code == 401
    assert cliente.get("/api/equipa/chaves-api").json() == []
    # A revogação aparece no registo de atividade
    acoes = [a["acao"] for a in cliente.get("/api/equipa/atividade").json()]
    assert any("Revogou a chave" in a for a in acoes) and any("Criou uma chave" in a for a in acoes)


def test_sem_chave_ou_com_cookie_nao_entra(cliente):
    assert TestClient(app).get("/api/v1/empresa").status_code == 401
    # O cookie de sessão não serve para a API pública
    assert cliente.get("/api/v1/empresa").status_code == 401
    assert _api("dobra_inventada").get("/api/v1/empresa").status_code == 401


def test_leitura_dos_dados(cliente):
    r = cliente.post("/api/contabilidade/lancamentos", json={
        "diario": "OD", "data": "2026-03-10", "descricao": "Renda de março",
        "linhas": [{"conta": "6261", "debito": 1000}, {"conta": "121", "credito": 1000}]})
    assert r.status_code == 201
    api = _api(_chave(cliente))
    lanc = api.get("/api/v1/lancamentos", params={"de": "2026-01-01", "ate": "2026-12-31"}).json()
    assert lanc["total"] == 1 and lanc["dados"][0]["linhas"][0] == {
        "conta": "6261", "debito": 1000, "credito": 0, "descricao": None, "tipo_iva": None, "taxa_iva": None,
        "centro_custo_id": None}
    assert any(c["codigo"] == "6261" for c in api.get("/api/v1/contas").json())
    bal = api.get("/api/v1/balancete", params={"de": "2026-01-01", "ate": "2026-12-31"})
    assert bal.status_code == 200
    assert api.get("/api/v1/faturas").json() == {"dados": [], "pagina": 1, "por_pagina": 50, "total": 0}
    assert api.get("/api/v1/vendas").json()["total"] == 0
    assert api.get("/api/v1/faturas/naoexiste").status_code == 404
    assert api.get("/api/v1/lancamentos", params={"por_pagina": 500}).status_code == 422


def test_escrita_de_movimentos_bancarios(cliente):
    leitura = _api(_chave(cliente))
    assert leitura.post("/api/v1/bancos/movimentos", json=MOVIMENTOS).status_code == 403

    escrita = _api(_chave(cliente, "escrita", "Agregador"))
    r = escrita.post("/api/v1/bancos/movimentos", json=MOVIMENTOS)
    assert r.status_code == 201, r.text
    assert r.json()["importados"] == 2
    # Reenviar não duplica
    repetido = escrita.post("/api/v1/bancos/movimentos", json=MOVIMENTOS).json()
    assert (repetido["importados"], repetido["duplicados"]) == (0, 2)
    movs = escrita.get("/api/v1/bancos/movimentos", params={"conta": "121"}).json()
    assert movs["total"] == 2 and {m["estado"] for m in movs["dados"]} == {"pendente"}
    # Aparecem na reconciliação da aplicação, com a origem
    extratos = cliente.get("/api/contabilidade/banco/extratos?conta=121").json()
    assert extratos[0]["formato"] == "api" and "Agregador X" in extratos[0]["nome_ficheiro"]
    pendentes = cliente.get("/api/contabilidade/banco/movimentos?conta=121&estado=pendente").json()
    assert len(pendentes["movimentos"]) == 2
    # Conta que não existe no plano
    assert escrita.post("/api/v1/bancos/movimentos", json={**MOVIMENTOS, "conta": "1299"}).status_code == 409
    acoes = [a["acao"] for a in cliente.get("/api/equipa/atividade").json()]
    assert any(a.startswith("API: enviou 2 movimento") for a in acoes)


def test_isolamento_entre_empresas(cliente, fabrica_sessoes, novo_cliente):
    criar_empresa_com_admin(fabrica_sessoes, "Outra", "admin@outra.pt")
    outra = novo_cliente("admin@outra.pt")
    _api(_chave(outra, "escrita")).post("/api/v1/bancos/movimentos", json=MOVIMENTOS)
    minha = _api(_chave(cliente))
    assert minha.get("/api/v1/bancos/movimentos").json()["total"] == 0
    assert minha.get("/api/v1/empresa").json()["nome"] == "Empresa de Teste"
    # Um administrador não revoga chaves de outra empresa
    id_outra = outra.get("/api/equipa/chaves-api").json()[0]["id"]
    assert cliente.delete(f"/api/equipa/chaves-api/{id_outra}").status_code == 404


def test_maximo_de_chaves_e_limite_de_pedidos(cliente, monkeypatch):
    assert cliente.post("/api/equipa/chaves-api", json={"nome": ""}).status_code == 422
    for i in range(10):
        _chave(cliente, nome=f"k{i}")
    assert cliente.post("/api/equipa/chaves-api", json={"nome": "a mais"}).status_code == 400
    cliente.delete(f"/api/equipa/chaves-api/{cliente.get('/api/equipa/chaves-api').json()[0]['id']}")
    api = _api(_chave(cliente))

    monkeypatch.setattr(limites, "api", limites.LimitePedidos(2, 60))
    respostas = [api.get("/api/v1/empresa") for _ in range(3)]
    assert [r.status_code for r in respostas] == [200, 200, 429]
    assert respostas[-1].headers["Retry-After"]


def test_pedido_de_outra_origem_e_aceite(cliente):
    # Sem cookie não há CSRF: outro site/programa pode chamar a API pública
    escrita = _api(_chave(cliente, "escrita"))
    r = escrita.post("/api/v1/bancos/movimentos", json=MOVIMENTOS, headers={"Origin": "https://outro.exemplo"})
    assert r.status_code == 201


def test_contrato_openapi_so_da_api_publica(cliente_anonimo, monkeypatch):
    monkeypatch.setattr(config, "AMBIENTE", "producao")
    spec = cliente_anonimo.get("/api/v1/openapi.json").json()
    assert spec["info"]["title"] == "API do Dobra"
    assert spec["paths"] and all(p.startswith("/api/v1/") for p in spec["paths"])
    assert "/api/v1/bancos/movimentos" in spec["paths"]
    assert "HTTPBearer" in spec["components"]["securitySchemes"]
    pagina = cliente_anonimo.get("/documentacao-api")
    assert pagina.status_code == 200 and "documentacao-api.js" in pagina.text


def test_membro_nao_gere_chaves(cliente, novo_cliente):
    from tests.test_contas import aceitar_convite, convidar

    aceitar_convite(novo_cliente(), convidar(cliente, email="membro@teste.pt"))
    membro = novo_cliente("membro@teste.pt")
    assert membro.get("/api/equipa/chaves-api").status_code == 403
    assert membro.post("/api/equipa/chaves-api", json={"nome": "x"}).status_code == 403
