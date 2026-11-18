"""Vendas: importação do SAF-T de faturação, registo manual, contabilização, recebimentos e ligações."""

from datetime import date, timedelta
from functools import cache
from pathlib import Path

import pytest

from tests.conftest import criar_empresa_com_admin
from tests.saft_faturacao import Documento, Linha, Recibo, Saft, gerar

NIF_EMPRESA = "500000000"
XSD = Path(__file__).parent / "dados" / "SAFTPT1.04_01.xsd"


@cache
def esquema():
    xmlschema = pytest.importorskip("xmlschema")
    return xmlschema.XMLSchema11(str(XSD))


def saft_outubro(**alteracoes) -> Saft:
    documentos = [
        Documento("FT A/1", "2026-10-05", "C1", [Linha("P1", 23, 100), Linha("S1", 6, 50)]),
        Documento("NC A/1", "2026-10-10", "C1", [Linha("P1", 23, 20)]),
        Documento("FR A/1", "2026-10-12", "C2", [Linha("P2", 13, 10)], meio="NU"),
        Documento("FT A/2", "2026-10-15", "C3", [Linha("S1", 0, 80)], estado="A"),
        Documento("FT A/3", "2026-10-20", "C3", [Linha("S1", 23, 200)]),
    ]
    recibos = [Recibo("RG A/1", "2026-10-25", "C1", "FT A/1", "2026-10-05", 176.0)]
    return Saft(alteracoes.get("nif", NIF_EMPRESA), alteracoes.get("documentos", documentos),
                alteracoes.get("recibos", recibos), alteracoes.get("base", "F"))


@pytest.fixture
def com_nif(cliente):
    resposta = cliente.put("/api/contabilidade/configuracao", json={
        "regime_iva": "trimestral", "nif": NIF_EMPRESA, "morada": "Rua A, 1", "codigo_postal": "1000-001",
        "localidade": "Lisboa"})
    assert resposta.status_code == 200
    return cliente


def importar(cliente, saft: Saft, esperado: int = 201):
    resposta = cliente.post("/api/contabilidade/vendas/importar-saft",
                            files={"ficheiro": ("saft.xml", gerar(saft), "application/xml")})
    assert resposta.status_code == esperado, resposta.text
    return resposta.json()


def vendas(cliente, estado="todas") -> dict[str, dict]:
    resposta = cliente.get("/api/contabilidade/vendas", params={"estado": estado, "de": "2026-01-01",
                                                                 "ate": "2026-12-31"}).json()
    return {v["numero"]: v for v in resposta["vendas"]}


def lancamentos(cliente, venda_id) -> list[dict]:
    return cliente.get(f"/api/contabilidade/vendas/{venda_id}").json()["lancamentos"]


def contas(lancamento) -> dict[str, tuple[float, float]]:
    r: dict[str, tuple[float, float]] = {}
    for ln in lancamento["linhas"]:
        d, c = r.get(ln["conta"], (0.0, 0.0))
        r[ln["conta"]] = (round(d + ln["debito"], 2), round(c + ln["credito"], 2))
    return r


def test_o_ficheiro_de_teste_e_um_saft_valido():
    esquema().validate(gerar(saft_outubro()).decode("cp1252"))


def test_importa_e_contabiliza_o_saft_de_faturacao(com_nif):
    cliente = com_nif
    r = importar(cliente, saft_outubro())
    assert (r["importadas"], r["anuladas"], r["recebimentos"], r["nao_contabilizadas"]) == (4, 1, 1, 0)

    v = vendas(cliente)
    # Fatura: cliente a débito; vendas (711), serviços (721) e IVA liquidado por taxa a crédito
    ft = contas(next(la for la in lancamentos(cliente, v["FT A/1"]["id"]) if la["origem"] == "venda"))
    assert ft["21110001"] == (176.0, 0.0)
    assert ft["711"] == (0.0, 100.0) and ft["24333"] == (0.0, 23.0)
    assert ft["721"] == (0.0, 50.0) and ft["24331"] == (0.0, 3.0)

    # Nota de crédito: o mesmo lançamento com os lados trocados
    nc = contas(lancamentos(cliente, v["NC A/1"]["id"])[0])
    assert nc["711"] == (20.0, 0.0) and nc["21110001"] == (0.0, 24.6)

    # Fatura-recibo paga em numerário: recebida, com entrada na caixa (111)
    assert v["FR A/1"]["recebida"]
    recebimento = next(la for la in lancamentos(cliente, v["FR A/1"]["id"]) if la["origem"] == "recebimento")
    assert "111" in contas(recebimento)

    # Recibo do SAF-T: a FT A/1 ficou recebida, com a data do recibo
    assert v["FT A/1"]["recebida"]
    rec = next(la for la in lancamentos(cliente, v["FT A/1"]["id"]) if la["origem"] == "recebimento")
    assert rec["data"] == "2026-10-25" and contas(rec)["121"] == (176.0, 0.0)

    # Anulada: guardada, mas sem lançamentos
    assert v["FT A/2"]["estado"] == "anulada" and lancamentos(cliente, v["FT A/2"]["id"]) == []

    # IVA liquidado no mapa da declaração periódica
    mapa = cliente.get("/api/contabilidade/iva", params={"de": "2026-10-01", "ate": "2026-10-31"}).json()
    por_conta = {ln["conta"]: ln for ln in mapa["liquidado"]}
    assert por_conta["24333"]["imposto"] == round(23 - 4.6 + 46, 2)
    assert por_conta["24333"]["base"] == round(100 - 20 + 200, 2)


def test_reimportar_nao_duplica_e_anulacao_posterior_estorna(com_nif):
    cliente = com_nif
    importar(cliente, saft_outubro())
    r = importar(cliente, saft_outubro())
    assert r["importadas"] == 0 and r["duplicadas"] == 5

    # O programa de faturação anulou a FT A/3 depois de exportada
    documentos = saft_outubro().documentos
    documentos[4].estado = "A"
    r = importar(cliente, saft_outubro(documentos=documentos, recibos=[]))
    assert r["anuladas"] == 1
    ft3 = vendas(cliente)["FT A/3"]
    assert ft3["estado"] == "anulada"
    origens = [la["origem"] for la in lancamentos(cliente, ft3["id"])]
    assert origens == ["venda", "estorno"]


@pytest.mark.parametrize("saft, mensagem", [
    (saft_outubro(nif="508000009"), "não desta"),
    (saft_outubro(base="C"), "SAF-T da contabilidade"),
])
def test_saft_de_outra_empresa_ou_da_contabilidade(com_nif, saft, mensagem):
    assert mensagem in importar(com_nif, saft, 422)["detail"]


def test_sem_nif_da_empresa_nao_importa(cliente):
    assert "NIF da empresa" in importar(cliente, saft_outubro(), 422)["detail"]


def test_registo_manual_e_recebimentos_parciais(cliente):
    resposta = cliente.post("/api/contabilidade/vendas", json={
        "tipo": "FT", "numero": "A/77", "data": "2026-10-01", "cliente_nif": "PT 508000009",
        "cliente_nome": "Empresa de Eventos Lisboa, Lda.",
        "linhas": [{"taxa": 23, "base": 1000, "conta": "721"}, {"taxa": 13, "base": 100, "conta": "711"}]})
    assert resposta.status_code == 201, resposta.text
    venda = resposta.json()
    assert venda["numero"] == "FT A/77" and venda["total"] == 1343.0
    assert venda["data_vencimento"] == "2026-10-31"  # 30 dias por omissão

    # Mesmo número: recusado; NIF inválido: recusado
    duplicada = {"tipo": "FT", "numero": "FT A/77", "data": "2026-10-02", "linhas": [{"taxa": 23, "base": 1}]}
    assert cliente.post("/api/contabilidade/vendas", json=duplicada).status_code == 422
    invalido = {**duplicada, "numero": "A/78", "cliente_nif": "123456780"}
    assert "NIF" in cliente.post("/api/contabilidade/vendas", json=invalido).json()["detail"]

    # Recebimento parcial e depois o resto
    url = f"/api/contabilidade/vendas/{venda['id']}/receber"
    assert cliente.post(url, json={"data": "2026-10-15", "valor": 343}).status_code == 201
    parcial = cliente.get(f"/api/contabilidade/vendas/{venda['id']}").json()
    assert parcial["em_divida"] == 1000.0 and not parcial["recebida"]
    assert cliente.post(url, json={"data": "2026-10-20", "valor": 2000}).status_code == 409  # mais do que deve
    resto = cliente.post(url, json={"data": "2026-10-20", "conta": "111"})
    assert resto.status_code == 201 and resto.json()["diario"] == "CX"
    assert cliente.get(f"/api/contabilidade/vendas/{venda['id']}").json()["recebida"]

    # Estornar um recebimento volta a pôr o valor em dívida
    assert cliente.post(f"/api/contabilidade/lancamentos/{resto.json()['id']}/estornar").status_code == 201
    depois = cliente.get(f"/api/contabilidade/vendas/{venda['id']}").json()
    assert depois["em_divida"] == 1000.0 and not depois["recebida"]


def test_consumidor_final_vai_para_clientes_diversos(cliente):
    venda = cliente.post("/api/contabilidade/vendas", json={
        "tipo": "FS", "numero": "B/1", "data": "2026-10-03", "linhas": [{"taxa": 13, "base": 10}]}).json()
    la = lancamentos(cliente, venda["id"])[0]
    assert contas(la)["21110000"] == (11.3, 0.0)


def test_recebimento_no_banco_e_sugerido_e_reconciliado(cliente):
    venda = cliente.post("/api/contabilidade/vendas", json={
        "tipo": "FT", "numero": "A/90", "data": "2026-10-01", "cliente_nif": "508000009",
        "cliente_nome": "Empresa de Eventos Lisboa, Lda.", "linhas": [{"taxa": 23, "base": 500}]}).json()
    extrato = "Data;Descrição;Montante\n15-10-2026;TRF RECEBIDA EVENTOS LISBOA;615,00\n".encode()
    assert cliente.post("/api/contabilidade/banco/importar", data={"conta": "121"},
                        files={"ficheiro": ("e.csv", extrato, "text/csv")}).status_code == 201
    mov = cliente.get("/api/contabilidade/banco/movimentos", params={"conta": "121"}).json()["movimentos"][0]
    assert mov["sugestoes"][0]["tipo"] == "receber_venda"
    r = cliente.post("/api/contabilidade/banco/reconciliar-automaticamente", json={"conta": "121"}).json()
    assert r["receber_venda"] == 1
    recebida = cliente.get(f"/api/contabilidade/vendas/{venda['id']}").json()
    assert recebida["recebida"]
    assert next(la for la in recebida["lancamentos"] if la["origem"] == "recebimento")["data"] == "2026-10-15"


def test_contas_correntes_de_clientes_e_alerta_de_vencidas(cliente):
    data = (date.today() - timedelta(days=75)).isoformat()
    cliente.post("/api/contabilidade/vendas", json={
        "tipo": "FT", "numero": "A/5", "data": data, "cliente_nif": "508000009",
        "cliente_nome": "Empresa de Eventos Lisboa, Lda.", "linhas": [{"taxa": 23, "base": 100}]})
    correntes = cliente.get("/api/contabilidade/vendas/clientes").json()
    assert correntes["total"] == 123.0
    assert correntes["linhas"][0]["antiguidade"]["ate_60"] == 123.0  # venceu há 45 dias
    alertas = cliente.get("/api/contabilidade/resumo").json()["alertas"]
    assert any("venda vencida" in a["titulo"] for a in alertas)


def test_saft_da_contabilidade_inclui_os_clientes(com_nif, fabrica_sessoes, empresa_teste):
    from app.modelos.tabelas import Empresa
    from app.servicos.contabilidade import saft

    importar(com_nif, saft_outubro())
    with fabrica_sessoes() as db:
        conteudo = saft.gerar(db, db.get(Empresa, empresa_teste), date(2026, 1, 1), date(2026, 12, 31))
    texto = conteudo.decode("cp1252")
    esquema().validate(texto)
    assert "<CustomerTaxID>500000000</CustomerTaxID>" in texto
    assert "<CustomerID>21110001</CustomerID>" in texto


def test_vendas_isoladas_entre_empresas(cliente, fabrica_sessoes, novo_cliente):
    venda = cliente.post("/api/contabilidade/vendas", json={
        "tipo": "FT", "numero": "A/1", "data": "2026-10-01", "linhas": [{"taxa": 23, "base": 100}]}).json()
    criar_empresa_com_admin(fabrica_sessoes, "Outra", "admin@outra.pt")
    outro = novo_cliente("admin@outra.pt")
    assert outro.get(f"/api/contabilidade/vendas/{venda['id']}").status_code == 404
    assert outro.post(f"/api/contabilidade/vendas/{venda['id']}/receber", json={}).status_code == 404
    assert outro.get("/api/contabilidade/vendas").json()["vendas"] == []
