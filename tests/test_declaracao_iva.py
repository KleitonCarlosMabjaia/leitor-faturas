"""Declaração periódica do IVA em XML, validada contra o XSD oficial da AT (tests/dados/dpiva-06.xsd)."""

import xml.etree.ElementTree as ET
from datetime import date
from functools import cache
from pathlib import Path

import pytest

from app.modelos.tabelas import Empresa
from app.servicos.contabilidade import declaracao_iva, lancamentos, plano_snc
from app.servicos.contabilidade.lancamentos import LinhaNova

XSD = Path(__file__).parent / "dados" / "dpiva-06.xsd"
NS = {"d": declaracao_iva.NS}


@cache
def esquema():
    xmlschema = pytest.importorskip("xmlschema")
    return xmlschema.XMLSchema(str(XSD))


def validar(conteudo: bytes) -> ET.Element:
    esquema().validate(conteudo.decode("utf-8"))  # lança uma exceção com o erro, se o ficheiro for inválido
    return ET.fromstring(conteudo)


def valor(raiz: ET.Element, caminho: str) -> str | None:
    el = raiz.find(caminho, NS)
    return el.text if el is not None else None


def _empresa(fabrica_sessoes, empresa_id: int, **dados) -> None:
    with fabrica_sessoes() as db:
        e = db.get(Empresa, empresa_id)
        e.nif = "507842545"
        for k, v in dados.items():
            setattr(e, k, v)
        db.commit()


def _movimentos(fabrica_sessoes, empresa_id: int) -> None:
    """Outubro: vendas à taxa normal e reduzida, compras com IVA dedutível e um serviço intracomunitário."""
    with fabrica_sessoes() as db:
        e = db.get(Empresa, empresa_id)
        plano_snc.garantir_plano(db, e.id)
        lancamentos.criar(db, e, "VEN", date(2026, 10, 10), "Vendas", [
            LinhaNova("121", debito=1336.0),
            LinhaNova("711", credito=1000.0, tipo_iva="base", taxa_iva=23),
            LinhaNova("24333", credito=230.0),
            LinhaNova("711", credito=100.0, tipo_iva="base", taxa_iva=6),
            LinhaNova("24331", credito=6.0),
        ])
        lancamentos.criar(db, e, "COM", date(2026, 10, 12), "Compras", [
            LinhaNova("6242", debito=400.0), LinhaNova("24323", debito=92.0), LinhaNova("121", credito=492.0),
        ])
        lancamentos.criar(db, e, "COM", date(2026, 11, 3), "Serviço de software da Irlanda", [
            LinhaNova("6242", debito=200.0, tipo_iva="base_is", taxa_iva=23),
            LinhaNova("24323", debito=46.0), LinhaNova("24335", credito=46.0), LinhaNova("121", credito=200.0),
        ])
        db.commit()


def test_xml_trimestral_valido_e_com_os_campos(cliente, fabrica_sessoes, empresa_teste):
    _empresa(fabrica_sessoes, empresa_teste, nif_contabilista="123456789")
    _movimentos(fabrica_sessoes, empresa_teste)
    periodo = {"de": "2026-10-01", "ate": "2026-12-31"}

    resumo = cliente.get("/api/contabilidade/iva/declaracao", params=periodo).json()
    campos = {c["campo"]: c["valor"] for c in resumo["campos"]}
    assert resumo["periodo"] == "12T" and resumo["pronto"] is True
    assert campos["1"] == 100 and campos["2"] == 6 and campos["3"] == 1000 and campos["4"] == 230
    assert campos["16"] == 200 and campos["17"] == 46 and campos["24"] == 138
    assert campos["90"] == 1300 and campos["92"] == 282 and campos["91"] == 138 and campos["93"] == 144
    assert "94" not in campos and "61" not in campos

    r = cliente.get("/api/contabilidade/iva/declaracao.xml", params=periodo)
    assert r.status_code == 200, r.text
    assert 'filename="DPIVA_507842545_2026_12T.xml"' in r.headers["content-disposition"]
    raiz = validar(r.content)
    assert raiz.tag == f"{{{declaracao_iva.NS}}}dpiva" and raiz.get("version") == "06"
    inicio = "d:rosto/d:inicio/d:"
    assert valor(raiz, inicio + "nif") == "507842545" and valor(raiz, inicio + "nifCC") == "123456789"
    assert valor(raiz, inicio + "anoDeclaracao") == "2026" and valor(raiz, inicio + "periodoDeclaracao") == "12T"
    assert valor(raiz, inicio + "localizacaoSede") == "1" and valor(raiz, inicio + "semOperacoes") == "false"
    apuramento = "d:rosto/d:apuramento/d:"
    assert valor(raiz, apuramento + "btTaxaNormal") == "100000"        # cêntimos
    assert valor(raiz, apuramento + "ivaTaxaNormal") == "23000"
    assert valor(raiz, apuramento + "btServicosUE") == "20000"
    assert valor(raiz, apuramento + "ivaDedutivelOutros") == "13800"
    assert valor(raiz, apuramento + "ivaDedutivelTotal") == "13800"
    assert valor(raiz, apuramento + "ivaAEntregar") == "14400"
    assert valor(raiz, apuramento + "temOperacoesAdquirenteComLiqImposto") == "S"
    assert valor(raiz, apuramento + "btTaxaIntermedia") is None        # campos sem valor não vão no ficheiro


def test_credito_transita_para_o_periodo_seguinte(cliente, fabrica_sessoes, empresa_teste):
    _empresa(fabrica_sessoes, empresa_teste, regime_iva="mensal", regiao_sede="madeira")
    with fabrica_sessoes() as db:
        e = db.get(Empresa, empresa_teste)
        plano_snc.garantir_plano(db, e.id)
        lancamentos.criar(db, e, "COM", date(2026, 9, 5), "Equipamento", [
            LinhaNova("435", debito=1000.0), LinhaNova("24322", debito=230.0), LinhaNova("121", credito=1230.0)])
        db.commit()
    setembro = {"de": "2026-09-01", "ate": "2026-09-30"}
    d = cliente.get("/api/contabilidade/iva/declaracao", params=setembro).json()
    campos = {c["campo"]: c["valor"] for c in d["campos"]}
    assert d["periodo"] == "09" and campos["20"] == 230 and campos["94"] == 230 and campos["96"] == 230
    raiz = validar(cliente.get("/api/contabilidade/iva/declaracao.xml", params=setembro).content)
    assert valor(raiz, "d:rosto/d:inicio/d:localizacaoSede") == "3"
    assert valor(raiz, "d:rosto/d:apuramento/d:excessoAReportar") == "23000"

    assert cliente.post("/api/contabilidade/iva/apurar", json=setembro).status_code == 201
    outubro = cliente.get("/api/contabilidade/iva/declaracao", params={"de": "2026-10-01", "ate": "2026-10-31"}).json()
    campos = {c["campo"]: c["valor"] for c in outubro["campos"]}
    assert campos["61"] == 230 and campos["91"] == 230 and campos["94"] == 230


def test_periodo_sem_operacoes(cliente, fabrica_sessoes, empresa_teste):
    _empresa(fabrica_sessoes, empresa_teste)
    r = cliente.get("/api/contabilidade/iva/declaracao.xml", params={"de": "2026-04-01", "ate": "2026-06-30"})
    raiz = validar(r.content)
    assert valor(raiz, "d:rosto/d:inicio/d:semOperacoes") == "true"
    assert valor(raiz, "d:rosto/d:inicio/d:periodoDeclaracao") == "06T"


def test_periodos_invalidos_e_dados_em_falta(cliente, fabrica_sessoes, empresa_teste):
    # Sem NIF: o resumo mostra os campos, mas o ficheiro não é gerado
    resumo = cliente.get("/api/contabilidade/iva/declaracao", params={"de": "2026-04-01", "ate": "2026-06-30"}).json()
    assert resumo["pronto"] is False and "NIF da empresa" in resumo["erro"]
    r = cliente.get("/api/contabilidade/iva/declaracao.xml", params={"de": "2026-04-01", "ate": "2026-06-30"})
    assert r.status_code == 422
    _empresa(fabrica_sessoes, empresa_teste)
    for de, ate in (("2026-04-01", "2026-05-31"), ("2026-02-01", "2026-04-30"), ("2026-04-02", "2026-06-30")):
        r = cliente.get("/api/contabilidade/iva/declaracao.xml", params={"de": de, "ate": ate})
        assert r.status_code == 422, (de, ate)


def test_dados_da_empresa_para_as_declaracoes(cliente):
    base = {"regime_iva": "trimestral"}
    assert cliente.put("/api/contabilidade/configuracao", json=base | {"servico_financas": "12"}).status_code == 422
    r = cliente.put("/api/contabilidade/configuracao", json=base | {"nif_contabilista": "123456780"})
    assert r.status_code == 422
    r = cliente.put("/api/contabilidade/configuracao", json=base | {
        "servico_financas": "3085", "nif_contabilista": "123456789", "nif_representante": "234567899",
        "regiao_sede": "acores"})
    assert r.status_code == 200, r.text
    dados = r.json()
    assert dados["servico_financas"] == "3085" and dados["nif_contabilista"] == "123456789"
    assert dados["regiao_sede"] == "acores"
