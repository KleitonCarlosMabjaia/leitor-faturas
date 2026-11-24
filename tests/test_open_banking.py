"""Open banking: ligação ao banco, regresso do banco, escolha da conta, sincronização e expiração."""

import json
from datetime import date, datetime, timedelta

import pytest

from app import config
from app.modelos.tabelas import LigacaoBancaria
from app.servicos.contabilidade import extratos, open_banking

DADOS_EB = {
    "transactions": [
        {"entry_reference": "E1", "transaction_amount": {"currency": "EUR", "amount": "1234.50"},
         "credit_debit_indicator": "CRDT", "status": "BOOK", "booking_date": "2026-11-02",
         "debtor": {"name": "RESTAURANTE O PATEO LDA"}, "remittance_information": ["FT 2026/88"],
         "balance_after_transaction": {"currency": "EUR", "amount": "5234.50"}},
        {"transaction_id": "T2", "transaction_amount": {"currency": "EUR", "amount": "84.37"},
         "credit_debit_indicator": "DBIT", "status": "BOOK", "value_date": "2026-11-10",
         "creditor": {"name": "EDP COMERCIAL"}, "remittance_information": ["DD EDP COMERCIAL 123"]},
        {"transaction_amount": {"currency": "EUR", "amount": "10.00"}, "credit_debit_indicator": "DBIT",
         "status": "PDNG", "booking_date": "2026-11-11"},
    ],
    "continuation_key": None,
}


@pytest.fixture
def simulado(monkeypatch):
    monkeypatch.setattr(config, "OPEN_BANKING_SIMULADO", True)
    monkeypatch.setattr(config, "ENABLE_BANKING_APP_ID", "")
    monkeypatch.setattr(config, "ENABLE_BANKING_CHAVE_PRIVADA", "")
    open_banking._cache_bancos.clear()


@pytest.fixture(autouse=True)
def sem_rede(monkeypatch):
    def proibido(*_a, **_k):
        raise AssertionError("Um teste tentou falar com a Enable Banking")

    monkeypatch.setattr(open_banking.EnableBanking, "_pedido", proibido)


def _ligar(cliente, conta="121") -> dict:
    r = cliente.post("/api/contabilidade/open-banking/ligacoes",
                     json={"conta": conta, "banco": open_banking.BANCO_SIMULADO})
    assert r.status_code == 201, r.text
    return r.json()


def test_indisponivel_sem_configuracao(cliente, monkeypatch):
    monkeypatch.setattr(config, "OPEN_BANKING_SIMULADO", False)
    monkeypatch.setattr(config, "ENABLE_BANKING_APP_ID", "")
    estado = cliente.get("/api/contabilidade/open-banking").json()
    assert estado == {"disponivel": False, "simulado": False, "ligacoes": []}
    r = cliente.post("/api/contabilidade/open-banking/ligacoes", json={"conta": "121", "banco": "X"})
    assert r.status_code == 409


def test_ligar_sincronizar_e_desligar(cliente, simulado):
    assert cliente.get("/api/contabilidade/open-banking").json()["disponivel"] is True
    assert cliente.get("/api/contabilidade/open-banking/bancos").json()[0]["nome"] == open_banking.BANCO_SIMULADO
    pedido = _ligar(cliente)
    assert pedido["url"].startswith("/open-banking/retorno?state=")

    # O banco devolve o utilizador à aplicação
    r = cliente.get(pedido["url"], follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/contabilidade?open_banking=ligado#bancos:121"
    lig = cliente.get("/api/contabilidade/open-banking?conta=121").json()["ligacoes"][0]
    assert lig["estado"] == "ativa" and lig["iban"] == "PT50 •••• 0017" and lig["desatualizada"] is True
    # O mesmo regresso não serve duas vezes
    assert "open_banking=erro" in cliente.get(pedido["url"], follow_redirects=False).headers["location"]

    s = cliente.post(f"/api/contabilidade/open-banking/ligacoes/{lig['id']}/sincronizar").json()
    assert s["importados"] > 5 and s["importados"] == s["lidos"] and s["ligacao"]["desatualizada"] is False
    # Sincronizar de novo não duplica
    s2 = cliente.post(f"/api/contabilidade/open-banking/ligacoes/{lig['id']}/sincronizar").json()
    assert s2["importados"] == 0 and s2["lidos"] > 0
    extrato = cliente.get("/api/contabilidade/banco/extratos?conta=121").json()
    # O banco simulado não indica saldo e não há extrato anterior: o saldo fica por indicar
    assert len(extrato) == 1 and extrato[0]["formato"] == "banco" and extrato[0]["saldo_final"] is None
    movs = cliente.get("/api/contabilidade/banco/movimentos?conta=121&estado=pendente").json()["movimentos"]
    assert any("COMISSAO MANUTENCAO" in m["descricao"] for m in movs)

    assert cliente.delete(f"/api/contabilidade/open-banking/ligacoes/{lig['id']}").status_code == 204
    assert cliente.get("/api/contabilidade/open-banking").json()["ligacoes"] == []
    # Os movimentos já importados ficam
    assert cliente.get("/api/contabilidade/banco/movimentos?conta=121").json()["movimentos"]
    acoes = [a["acao"] for a in cliente.get("/api/equipa/atividade").json()]
    assert any("Sincronizou os movimentos do banco" in a for a in acoes)


def test_primeira_leitura_continua_do_ultimo_extrato(cliente, simulado):
    """Com movimentos já importados por ficheiro, o banco não os repete e o saldo continua o do extrato."""
    from app.servicos.contabilidade.open_banking import _movimentos_do_dia

    hoje = date.today()
    ultimo = hoje - timedelta(days=10)
    csv = f"Data;Descrição;Montante\n{ultimo:%d-%m-%Y};DEPOSITO INICIAL;1000,00\n".encode()
    r = cliente.post("/api/contabilidade/banco/importar", data={"conta": "121", "saldo_final": "2500,00"},
                     files={"ficheiro": ("extrato.csv", csv, "text/csv")})
    assert r.status_code == 201, r.text

    cliente.get(_ligar(cliente)["url"])
    lig = cliente.get("/api/contabilidade/open-banking").json()["ligacoes"][0]
    s = cliente.post(f"/api/contabilidade/open-banking/ligacoes/{lig['id']}/sincronizar").json()
    assert s["desde"] == (ultimo + timedelta(days=1)).isoformat()
    esperados = [m for d in range(1, 11) for m in _movimentos_do_dia(ultimo + timedelta(days=d))]
    assert s["importados"] == len(esperados)
    if esperados:
        mapa = cliente.get("/api/contabilidade/banco/reconciliacao?conta=121").json()
        assert mapa["saldo_banco"] == round(2500 + sum(v for _, v in esperados), 2)


def test_cancelado_no_banco(cliente, simulado):
    pedido = _ligar(cliente)
    estado = pedido["url"].split("state=")[1].split("&")[0]
    r = cliente.get(f"/open-banking/retorno?state={estado}&error=access_denied", follow_redirects=False)
    assert "open_banking=erro" in r.headers["location"] and "cancelada" in r.headers["location"]
    assert cliente.get("/api/contabilidade/open-banking").json()["ligacoes"] == []
    # Um "state" inventado é recusado
    r = cliente.get("/open-banking/retorno?state=inventado&code=x", follow_redirects=False)
    assert "open_banking=erro" in r.headers["location"]


def test_conta_invalida_e_banco_desconhecido(cliente, simulado):
    assert cliente.post("/api/contabilidade/open-banking/ligacoes",
                        json={"conta": "111", "banco": open_banking.BANCO_SIMULADO}).status_code == 422
    assert cliente.post("/api/contabilidade/open-banking/ligacoes",
                        json={"conta": "121", "banco": "Banco que não existe"}).status_code == 409


def test_varias_contas_escolher_uma(cliente, simulado, monkeypatch):
    def duas_contas(self, codigo):
        return open_banking.SessaoExterna("sess-2", [
            open_banking.ContaExterna("uid-a", "PT50000000000000000000001", "Ordem", "EUR"),
            open_banking.ContaExterna("uid-b", "PT50000000000000000000002", "Poupança", "EUR"),
        ], datetime.now() + timedelta(days=90))

    monkeypatch.setattr(open_banking.Simulado, "criar_sessao", duas_contas)
    r = cliente.get(_ligar(cliente)["url"], follow_redirects=False)
    assert "open_banking=escolher" in r.headers["location"]
    lig = cliente.get("/api/contabilidade/open-banking").json()["ligacoes"][0]
    assert lig["estado"] == "escolher" and [c["uid"] for c in lig["contas_disponiveis"]] == ["uid-a", "uid-b"]
    url = f"/api/contabilidade/open-banking/ligacoes/{lig['id']}"
    assert cliente.post(f"{url}/sincronizar").status_code == 409
    assert cliente.post(f"{url}/conta", json={"uid": "uid-x"}).status_code == 409
    escolhida = cliente.post(f"{url}/conta", json={"uid": "uid-b"}).json()
    assert escolhida["estado"] == "ativa" and escolhida["iban"].endswith("0002")


def test_consentimento_expirado(cliente, simulado, fabrica_sessoes):
    cliente.get(_ligar(cliente)["url"])
    with fabrica_sessoes() as db:
        lig = db.query(LigacaoBancaria).one()
        lig.valido_ate = datetime.now() - timedelta(minutes=1)
        db.commit()
        id_lig = lig.id
    r = cliente.post(f"/api/contabilidade/open-banking/ligacoes/{id_lig}/sincronizar")
    assert r.status_code == 409 and "volte a ligar" in r.json()["detail"]
    assert cliente.get("/api/contabilidade/open-banking").json()["ligacoes"][0]["estado"] == "expirada"
    # Renovar: um pedido novo para a mesma conta substitui a ligação expirada
    cliente.get(_ligar(cliente)["url"])
    ligacoes = cliente.get("/api/contabilidade/open-banking").json()["ligacoes"]
    assert [x["estado"] for x in ligacoes] == ["ativa"]


def test_isolamento_entre_empresas(cliente, simulado, fabrica_sessoes, novo_cliente):
    from tests.conftest import criar_empresa_com_admin

    pedido = _ligar(cliente)
    criar_empresa_com_admin(fabrica_sessoes, "Outra", "admin@outra.pt")
    outra = novo_cliente("admin@outra.pt")
    # Outra empresa não consegue concluir o pedido desta (nem ver as ligações)
    assert "open_banking=erro" in outra.get(pedido["url"], follow_redirects=False).headers["location"]
    assert outra.get("/api/contabilidade/open-banking").json()["ligacoes"] == []
    assert outra.delete(f"/api/contabilidade/open-banking/ligacoes/{pedido['id']}").status_code == 404


def test_retorno_sem_sessao_vai_para_o_login(cliente_anonimo):
    r = cliente_anonimo.get("/open-banking/retorno?state=x&code=y", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/entrar")


# ---------- Enable Banking (sem rede) ----------

def test_transacoes_enable_banking():
    movs = [m for m in map(open_banking.movimento_enable_banking, DADOS_EB["transactions"]) if m]
    assert len(movs) == 2   # a pendente fica de fora
    assert (movs[0].data, movs[0].montante, movs[0].saldo, movs[0].referencia) == (
        date(2026, 11, 2), 1234.5, 5234.5, "eb:E1")
    assert movs[0].descricao == "RESTAURANTE O PATEO LDA FT 2026/88"
    assert (movs[1].montante, movs[1].referencia, movs[1].descricao) == (-84.37, "eb:T2", "DD EDP COMERCIAL 123")


def test_enable_banking_paginas_e_pedidos(monkeypatch):
    pedidos = []
    paginas = [{**DADOS_EB, "continuation_key": "k2"}, {"transactions": DADOS_EB["transactions"][:1]}]

    def falso(self, metodo, caminho, *, params=None, corpo=None, ip=None):
        pedidos.append((metodo, caminho, dict(params or {}), corpo))
        if caminho == "/auth":
            return {"url": "https://banco.exemplo/autorizar", "authorization_id": "a1"}
        if caminho == "/sessions":
            return {"session_id": "s1", "access": {"valid_until": "2027-05-20T12:00:00+00:00"},
                    "accounts": [{"uid": "u1", "account_id": {"iban": "PT50003300004555698867005"},
                                  "currency": "EUR", "name": "EMPRESA LDA"}]}
        return paginas.pop(0)

    monkeypatch.setattr(open_banking.EnableBanking, "_pedido", falso)
    eb = open_banking.EnableBanking()
    assert eb.iniciar("Banco X", "PT", "estado1", datetime(2027, 5, 20).astimezone()) == \
        "https://banco.exemplo/autorizar"
    corpo = pedidos[0][3]
    assert corpo["aspsp"] == {"name": "Banco X", "country": "PT"} and corpo["state"] == "estado1"
    assert corpo["redirect_url"].endswith("/open-banking/retorno") and corpo["psu_type"] == "business"
    sessao = eb.criar_sessao("codigo")
    assert sessao.sessao == "s1" and sessao.contas[0].iban.startswith("PT50") and sessao.valido_ate.year == 2027
    movs = eb.transacoes("u1", date(2026, 11, 1), date(2026, 11, 30))
    assert len(movs) == 3
    assert pedidos[-1][2]["continuation_key"] == "k2" and pedidos[-2][2]["transaction_status"] == "BOOK"


def test_jwt_enable_banking(monkeypatch):
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa

    chave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = chave.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                              serialization.NoEncryption()).decode().replace("\n", "\\n")
    monkeypatch.setattr(config, "ENABLE_BANKING_APP_ID", "app-123")
    monkeypatch.setattr(config, "ENABLE_BANKING_CHAVE_PRIVADA", pem)
    token = open_banking.jwt_enable_banking(agora=1_800_000_000)
    cab, corpo, assinatura = token.split(".")

    def decodificar(parte):
        import base64
        return base64.urlsafe_b64decode(parte + "=" * (-len(parte) % 4))

    assert json.loads(decodificar(cab)) == {"typ": "JWT", "alg": "RS256", "kid": "app-123"}
    assert json.loads(decodificar(corpo)) == {"iss": "enablebanking.com", "aud": "api.enablebanking.com",
                                              "iat": 1_800_000_000, "exp": 1_800_003_600}
    chave.public_key().verify(decodificar(assinatura), f"{cab}.{corpo}".encode(), padding.PKCS1v15(), hashes.SHA256())
    # Com a configuração completa, o fornecedor ativo passa a ser a Enable Banking
    assert open_banking.fornecedor().nome == "enablebanking"


def test_simulado_e_deterministico():
    s = open_banking.Simulado()
    a = s.transacoes("x", date(2026, 3, 1), date(2026, 3, 31))
    b = s.transacoes("x", date(2026, 3, 10), date(2026, 3, 31))
    assert [m.referencia for m in a if m.data >= date(2026, 3, 10)] == [m.referencia for m in b]
    assert all(m.saldo is None for m in a) and {m.data for m in b} >= {date(2026, 3, 10), date(2026, 3, 15)}
    assert all(isinstance(m, extratos.MovimentoLido) for m in a)
