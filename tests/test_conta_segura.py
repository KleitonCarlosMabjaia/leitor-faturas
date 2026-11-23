"""A minha conta: palavra-passe, verificação em dois passos, sessões e registo de atividade."""

import base64

from app.servicos import dois_passos
from tests.conftest import PALAVRA_PASSE_TESTE, entrar

NOVA = "OutraPalavra2026"


def _codigo_atual(segredo: str) -> str:
    return dois_passos.codigo(segredo, dois_passos.passo_atual())


def test_totp_confere_com_o_rfc_6238():
    segredo = base64.b32encode(b"12345678901234567890").decode().rstrip("=")
    assert dois_passos.codigo(segredo, 59 // 30) == "287082"
    assert dois_passos.codigo(segredo, 1111111109 // 30) == "081804"
    # O mesmo passo não pode ser usado duas vezes
    passo = dois_passos.verificar_codigo(segredo, "287082", None, agora=59)
    assert passo == 1 and dois_passos.verificar_codigo(segredo, "287082", passo, agora=59) is None


def _ligar_dois_passos(cliente) -> tuple[str, list[str]]:
    r = cliente.post("/api/conta/dois-passos/iniciar", json={"palavra_passe": PALAVRA_PASSE_TESTE})
    assert r.status_code == 200, r.text
    assert r.json()["qr"].startswith("data:image/png;base64,")
    segredo = r.json()["segredo"]
    assert cliente.post("/api/conta/dois-passos/ativar", json={"codigo": "000000"}).status_code == 400
    r = cliente.post("/api/conta/dois-passos/ativar", json={"codigo": _codigo_atual(segredo)})
    assert r.status_code == 200, r.text
    codigos = r.json()["codigos_recuperacao"]
    assert len(codigos) == 8
    return segredo, codigos


def test_entrar_com_verificacao_em_dois_passos(cliente, novo_cliente):
    segredo, codigos = _ligar_dois_passos(cliente)
    assert cliente.get("/api/conta").json()["dois_passos"] is True

    outro = novo_cliente()
    credenciais = {"email": "admin@teste.pt", "palavra_passe": PALAVRA_PASSE_TESTE}
    r = outro.post("/api/auth/entrar", json=credenciais)
    assert r.status_code == 401 and r.headers["x-dois-passos"] == "necessario"
    assert outro.post("/api/auth/entrar", json={**credenciais, "codigo": "123456"}).status_code == 401
    # Código de recuperação: serve uma vez
    assert outro.post("/api/auth/entrar", json={**credenciais, "codigo": codigos[0].lower()}).status_code == 200
    terceiro = novo_cliente()
    assert terceiro.post("/api/auth/entrar", json={**credenciais, "codigo": codigos[0]}).status_code == 401
    assert cliente.get("/api/conta").json()["codigos_restantes"] == 7

    # Desligar pede a palavra-passe e um código
    r = cliente.post("/api/conta/dois-passos/desativar", json={"palavra_passe": "errada", "codigo": codigos[1]})
    assert r.status_code == 400
    corpo = {"palavra_passe": PALAVRA_PASSE_TESTE, "codigo": codigos[1]}
    r = cliente.post("/api/conta/dois-passos/desativar", json=corpo)
    assert r.status_code == 204
    assert novo_cliente().post("/api/auth/entrar", json=credenciais).status_code == 200


def test_mudar_palavra_passe_termina_as_outras_sessoes(cliente, novo_cliente):
    outro = novo_cliente("admin@teste.pt")
    assert len(cliente.get("/api/conta").json()["sessoes"]) == 2
    r = cliente.post("/api/conta/palavra-passe", json={"atual": "errada", "nova": NOVA})
    assert r.status_code == 400
    r = cliente.post("/api/conta/palavra-passe", json={"atual": PALAVRA_PASSE_TESTE, "nova": NOVA})
    assert r.status_code == 204
    assert outro.get("/api/auth/eu").status_code == 401      # a outra sessão terminou
    assert cliente.get("/api/auth/eu").status_code == 200    # esta continua
    entrar(novo_cliente(), "admin@teste.pt", NOVA)


def test_sessoes_abertas_e_terminar(cliente, novo_cliente):
    outro = novo_cliente("admin@teste.pt")
    sessoes = cliente.get("/api/conta").json()["sessoes"]
    assert sum(s["atual"] for s in sessoes) == 1
    alheia = next(s for s in sessoes if not s["atual"])
    assert cliente.post(f"/api/conta/sessoes/{alheia['id']}/terminar").status_code == 204
    assert outro.get("/api/auth/eu").status_code == 401
    novo_cliente("admin@teste.pt")
    assert cliente.post("/api/conta/sessoes/terminar-outras").json() == {"terminadas": 1}


def test_registo_de_atividade(cliente, novo_cliente):
    falhado = novo_cliente()
    falhado.post("/api/auth/entrar", json={"email": "admin@teste.pt", "palavra_passe": "errada"})
    cliente.post("/api/equipa", json={"nome": "Ana", "email": "ana@teste.pt"})
    cliente.post("/api/contabilidade/salarios/simular", json={})  # consulta: não fica registada
    registos = cliente.get("/api/equipa/atividade").json()
    acoes = [r["acao"] for r in registos]
    assert acoes[0] == "Convidou um colega"
    assert "Tentativa de entrada falhada" in acoes and "Entrou na aplicação" in acoes
    assert not any("simular" in a for a in acoes)
    assert registos[0]["quem"] == "Admin Teste" or registos[0]["quem"]


def test_atividade_e_so_para_administradores(cliente, novo_cliente):
    link = cliente.post("/api/equipa", json={"nome": "Rui", "email": "rui@teste.pt"}).json()["link"]
    token = link.split("#", 1)[1]
    membro = novo_cliente()
    r = membro.post("/api/auth/definir-palavra-passe", json={"token": token, "palavra_passe": NOVA})
    assert r.status_code == 200
    assert membro.get("/api/equipa/atividade").status_code == 403


def test_pagina_404_com_a_marca(cliente_anonimo):
    r = cliente_anonimo.get("/nao-existe", headers={"Accept": "text/html"})
    assert r.status_code == 404 and "Dobra" in r.text
    assert cliente_anonimo.get("/api/nao-existe").json() == {"detail": "Not Found"}


def test_primeiros_passos_de_uma_empresa_nova(cliente):
    d = cliente.get("/api/contabilidade/primeiros-passos").json()
    chaves = [p["chave"] for p in d["passos"]]
    assert chaves == ["empresa", "abertura", "fatura", "contabilizar", "banco", "equipa", "dois-passos"]
    assert d["feitos"] == 0 and d["total"] == 7
    cliente.post("/api/equipa", json={"nome": "Ana", "email": "ana@teste.pt"})
    d = cliente.get("/api/contabilidade/primeiros-passos").json()
    assert next(p for p in d["passos"] if p["chave"] == "equipa")["feito"] is True
    assert d["feitos"] == 1


def test_plano_de_contas_criado_em_simultaneo_nao_da_erro(fabrica_sessoes, empresa_teste, monkeypatch):
    """Dois pedidos na primeira visita criam o plano ao mesmo tempo: o que perde a corrida repete e não falha."""
    from sqlalchemy.exc import OperationalError

    from app.modelos.tabelas import Utilizador
    from app.rotas import contabilidade as rotas
    from app.servicos.contabilidade import plano_snc

    original = plano_snc.garantir_plano
    chamadas = []

    def com_corrida(db, empresa_id):
        chamadas.append(1)
        if len(chamadas) == 1:
            with fabrica_sessoes() as outro:      # o outro pedido grava o plano primeiro...
                original(outro, empresa_id)
                outro.commit()
            raise OperationalError("INSERT", {}, Exception("database is locked"))  # ...e este perde
        return original(db, empresa_id)

    monkeypatch.setattr(rotas.plano_snc, "garantir_plano", com_corrida)
    with fabrica_sessoes() as db:
        utilizador = db.query(Utilizador).filter_by(empresa_id=empresa_teste).first()
        assert rotas._empresa(db, utilizador).id == empresa_teste
    assert len(chamadas) == 2
