"""SAF-T (PT) da contabilidade: validado contra o XSD oficial da AT (tests/dados/SAFTPT1.04_01.xsd)."""

import xml.etree.ElementTree as ET
from datetime import date, datetime
from functools import cache
from pathlib import Path

import pytest

from app import demo
from app.modelos.tabelas import Conta, Empresa, LinhaLancamento
from app.servicos.contabilidade import lancamentos, plano_snc, saft
from app.servicos.contabilidade.lancamentos import LinhaNova

XSD = Path(__file__).parent / "dados" / "SAFTPT1.04_01.xsd"
NS = {"s": saft.NS}
HOJE = date(2026, 11, 11)


@cache
def esquema():
    xmlschema = pytest.importorskip("xmlschema")
    return xmlschema.XMLSchema11(str(XSD))


def validar(conteudo: bytes) -> ET.Element:
    esquema().validate(conteudo.decode("cp1252"))  # lança uma exceção com o erro, se o ficheiro for inválido
    return ET.fromstring(conteudo)


@pytest.fixture
def empresa_demo(fabrica_sessoes, empresa_teste):
    """Empresa de teste com a demonstração completa: 30 faturas e um ano de contabilidade."""
    with fabrica_sessoes() as db:
        demo.criar_dados_demo(db, empresa_id=empresa_teste, hoje=HOJE)
    return empresa_teste


def montante(el: ET.Element, caminho: str) -> float:
    return sum(float(x.text) for x in el.findall(caminho, NS))


def test_saft_da_demonstracao_e_valido_e_bate_certo(fabrica_sessoes, empresa_demo):
    with fabrica_sessoes() as db:
        empresa = db.get(Empresa, empresa_demo)
        conteudo = saft.gerar(db, empresa, date(2026, 1, 1), HOJE, agora=datetime(2026, 11, 11, 18, 0))
        n_lancamentos = len(lancamentos_do_ano(db, empresa_demo))

    assert conteudo.startswith(b"<?xml version='1.0' encoding='Windows-1252'?>")
    raiz = validar(conteudo)

    cabecalho = raiz.find("s:Header", NS)
    assert cabecalho.findtext("s:TaxAccountingBasis", namespaces=NS) == "C"
    assert cabecalho.findtext("s:FiscalYear", namespaces=NS) == "2026"
    assert cabecalho.findtext("s:CompanyAddress/s:PostalCode", namespaces=NS) == "1100-200"

    # Movimentos: todos os lançamentos do ano, débito = crédito
    movimentos = raiz.find("s:GeneralLedgerEntries", NS)
    assert int(movimentos.findtext("s:NumberOfEntries", namespaces=NS)) == n_lancamentos
    total_d = float(movimentos.findtext("s:TotalDebit", namespaces=NS))
    total_c = float(movimentos.findtext("s:TotalCredit", namespaces=NS))
    assert total_d == total_c > 0
    assert round(montante(movimentos, ".//s:DebitAmount"), 2) == total_d
    assert round(montante(movimentos, ".//s:CreditAmount"), 2) == total_c

    # Plano: saldos de fecho das contas de movimento equilibrados; contas de 1.º grau sem taxonomia
    contas = raiz.findall("s:MasterFiles/s:GeneralLedgerAccounts/s:Account", NS)
    gm = [c for c in contas if c.findtext("s:GroupingCategory", namespaces=NS) == "GM"]
    devedores, credores = montante_lista(gm, "s:ClosingDebitBalance"), montante_lista(gm, "s:ClosingCreditBalance")
    assert round(devedores, 2) == round(credores, 2)
    por_id = {c.findtext("s:AccountID", namespaces=NS): c for c in contas}
    assert por_id["12"].findtext("s:GroupingCategory", namespaces=NS) == "GR"
    assert por_id["121"].findtext("s:TaxonomyCode", namespaces=NS) == "2"
    assert por_id["243211"].findtext("s:TaxonomyCode", namespaces=NS) == "74"
    assert por_id["22110002"].findtext("s:TaxonomyCode", namespaces=NS) == "37"
    assert "1" not in por_id  # as classes não entram no SAF-T

    # Fornecedores: um por subconta com NIF, ligados às transações de compra
    fornecedores = {f.findtext("s:SupplierID", namespaces=NS) for f in raiz.findall("s:MasterFiles/s:Supplier", NS)}
    assert "22110002" in fornecedores
    ligados = {t.findtext("s:SupplierID", namespaces=NS) for t in movimentos.findall(".//s:Transaction", NS)}
    assert ligados - {None} <= fornecedores


def montante_lista(contas: list[ET.Element], campo: str) -> float:
    return sum(float(c.findtext(campo, namespaces=NS)) for c in contas)


def lancamentos_do_ano(db, empresa_id):
    from sqlalchemy import select

    from app.modelos.tabelas import Lancamento
    return db.scalars(select(Lancamento).where(
        Lancamento.empresa_id == empresa_id, Lancamento.data >= date(2026, 1, 1), Lancamento.data <= HOJE
    )).all()


def test_saldos_de_abertura_de_um_periodo_a_meio_do_ano(fabrica_sessoes, empresa_demo):
    with fabrica_sessoes() as db:
        empresa = db.get(Empresa, empresa_demo)
        raiz = validar(saft.gerar(db, empresa, date(2026, 10, 1), date(2026, 10, 31)))
    contas = {c.findtext("s:AccountID", namespaces=NS): c for c in
              raiz.findall("s:MasterFiles/s:GeneralLedgerAccounts/s:Account", NS)}
    # O banco tinha saldo antes de outubro; os gastos de 2026 também (abrem com o acumulado do ano)
    assert float(contas["121"].findtext("s:OpeningDebitBalance", namespaces=NS)) > 0
    assert float(contas["632"].findtext("s:OpeningDebitBalance", namespaces=NS)) > 0
    periodos = {t.findtext("s:Period", namespaces=NS) for t in raiz.iter(f"{{{saft.NS}}}Transaction")}
    assert periodos == {"10"}


def test_caracteres_portugueses_e_texto_estranho(fabrica_sessoes, empresa_teste):
    with fabrica_sessoes() as db:
        empresa = db.get(Empresa, empresa_teste)
        empresa.nif, empresa.morada, empresa.codigo_postal, empresa.localidade = (
            "500000000", "Avenida da Liberdade, 1 – 2.º Esq.", "1250-096", "Lisboa")
        plano_snc.garantir_plano(db, empresa.id)
        lancamentos.criar(db, empresa, "OD", date(2026, 3, 2), "Ação de formação — “Excel” € 😀 \x07",
                          [LinhaNova("6268", debito=10), LinhaNova("111", credito=10)])
        db.commit()
        conteudo = saft.gerar(db, empresa, date(2026, 1, 1), date(2026, 12, 31))
    raiz = validar(conteudo)
    descricao = raiz.find(".//s:Transaction/s:Description", NS).text
    assert "Ação de formação" in descricao and "€" in descricao and "\x07" not in descricao


def test_sem_nif_nem_morada_nao_gera(cliente):
    problemas = cliente.get("/api/contabilidade/saft/verificar", params={"ano": 2026}).json()["problemas"]
    assert any("NIF" in p for p in problemas) and any("código postal" in p for p in problemas)
    resposta = cliente.get("/api/contabilidade/saft", params={"ano": 2026})
    assert resposta.status_code == 409 and "NIF" in resposta.json()["detail"]


def test_configurar_dados_e_descarregar(cliente):
    assert cliente.put("/api/contabilidade/configuracao", json={
        "regime_iva": "trimestral", "nif": "123", "morada": "x", "codigo_postal": "1000-001", "localidade": "Lisboa",
    }).status_code == 422
    assert cliente.put("/api/contabilidade/configuracao", json={
        "regime_iva": "trimestral", "nif": "500000000", "morada": "Rua A, 1", "codigo_postal": "1000",
        "localidade": "Lisboa"}).status_code == 422
    assert cliente.put("/api/contabilidade/configuracao", json={
        "regime_iva": "trimestral", "nif": "500000000", "morada": "Rua A, 1", "codigo_postal": "1000-001",
        "localidade": "Lisboa"}).status_code == 200
    assert cliente.get("/api/contabilidade/saft/verificar", params={"ano": 2026}).json()["problemas"] == []

    resposta = cliente.get("/api/contabilidade/saft", params={"ano": 2025})
    assert resposta.status_code == 200
    assert "SAFT-PT-contabilidade-500000000-2025-01-01-a-2025-12-31.xml" in resposta.headers["content-disposition"]
    validar(resposta.content)  # sem lançamentos também é um ficheiro válido


def test_periodo_tem_de_ser_de_um_so_exercicio(fabrica_sessoes, empresa_demo):
    with fabrica_sessoes() as db:
        empresa = db.get(Empresa, empresa_demo)
        with pytest.raises(saft.ErroSAFT) as erro:
            saft.gerar(db, empresa, date(2025, 12, 1), date(2026, 1, 31))
    assert any("mesmo ano" in p for p in erro.value.problemas)


def test_todas_as_contas_de_movimento_do_plano_tem_taxonomia():
    codigos = {c for c, _ in plano_snc.PLANO}
    for codigo in codigos:
        movimento = not plano_snc._e_agregadora(codigo, codigos)
        if len(codigo) <= 2:
            assert not movimento, f"A conta {codigo} é de 1.º grau e tem de ser agregadora"
        elif movimento:
            assert plano_snc.taxonomia(codigo), f"A conta {codigo} não tem taxonomia"
    assert plano_snc.taxonomia("22110003") == 37
    assert plano_snc.taxonomia("27110001") == 124
    assert plano_snc.taxonomia("1211") == 2


def test_plano_antigo_e_atualizado_sem_perder_movimentos(fabrica_sessoes, empresa_teste):
    """Planos criados antes do SAF-T lançavam na conta 12: os movimentos passam para a 121."""
    with fabrica_sessoes() as db:
        empresa = db.get(Empresa, empresa_teste)
        plano_snc.garantir_plano(db, empresa.id)
        contas = plano_snc.contas_por_codigo(db, empresa.id)
        db.delete(contas["121"])
        contas["12"].movimento = True
        db.flush()
        lancamento = lancamentos.criar(db, empresa, "BAN", date(2026, 5, 4), "Depósito",
                                       [LinhaNova("12", debito=100), LinhaNova("511", credito=100)])
        db.commit()
        id_lancamento = lancamento.id

        plano_snc.garantir_plano(db, empresa.id)
        db.commit()
        contas = plano_snc.contas_por_codigo(db, empresa.id)
        assert not contas["12"].movimento and contas["121"].movimento
        linhas = db.query(LinhaLancamento).filter(LinhaLancamento.lancamento_id == id_lancamento).all()
        assert {db.get(Conta, ln.conta_id).codigo for ln in linhas} == {"121", "511"}
        # O plano fica conforme: nenhum problema com as contas (só faltam o NIF e a morada)
        problemas = saft.verificar(db, empresa, date(2026, 1, 1), date(2026, 12, 31))
        assert not any(p.startswith("A conta") for p in problemas)
