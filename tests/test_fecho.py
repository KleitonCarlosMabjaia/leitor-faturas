"""Fecho do exercício: estimativa de IRC, apuramento de resultados, transferência e reabertura."""

from datetime import date
from functools import cache
from pathlib import Path

import pytest

from tests.conftest import PALAVRA_PASSE_TESTE, entrar

XSD = Path(__file__).parent / "dados" / "SAFTPT1.04_01.xsd"


@cache
def esquema():
    xmlschema = pytest.importorskip("xmlschema")
    return xmlschema.XMLSchema11(str(XSD))


def lancar(cliente, data, descricao, linhas, diario="OD"):
    r = cliente.post("/api/contabilidade/lancamentos", json={"diario": diario, "data": data, "descricao": descricao,
                                                              "linhas": linhas})
    assert r.status_code == 201, r.text
    return r.json()


def exercicio_2025(cliente):
    """Lucro de 6 800 €: 10 000 de serviços, 3 000 de renda e 200 de despesas de representação."""
    lancar(cliente, "2025-03-01", "Serviços", [{"conta": "121", "debito": 10000}, {"conta": "721", "credito": 10000}])
    lancar(cliente, "2025-04-01", "Renda", [{"conta": "6261", "debito": 3000}, {"conta": "121", "credito": 3000}])
    lancar(cliente, "2025-05-01", "Almoço com cliente", [{"conta": "6266", "debito": 200},
                                                         {"conta": "121", "credito": 200}])


def saldos(cliente, de, ate) -> dict[str, float]:
    linhas = cliente.get("/api/contabilidade/balancete", params={"de": de, "ate": ate}).json()["linhas"]
    return {ln["codigo"]: round(ln["saldo_devedor"] - ln["saldo_credor"], 2) for ln in linhas}


def test_estimativa_de_irc(cliente):
    exercicio_2025(cliente)
    e = cliente.get("/api/contabilidade/fecho", params={"ano": 2025}).json()["estimativa"]
    # PME: 6 800 x 15% = 1 020; derrama 1,5% = 102; tributação autónoma 10% de 200 = 20
    assert (e["resultado_antes_de_impostos"], e["irc"], e["derrama"], e["tributacao_autonoma"]) == (6800, 1020, 102, 20)
    assert (e["imposto_total"], e["resultado_liquido"]) == (1142, 5658)

    # Sem regime das PME e com ajustes fiscais de +1 200 €: 8 000 x 19% = 1 520
    e = cliente.get("/api/contabilidade/fecho", params={"ano": 2025, "pme": False, "ajustes": 1200}).json()
    assert e["estimativa"]["irc"] == 1520 and e["estimativa"]["derrama"] == 120


def test_fecho_transferencia_e_reabertura(cliente):
    exercicio_2025(cliente)
    assert cliente.put("/api/contabilidade/configuracao", json={
        "regime_iva": "trimestral", "nif": "500000000", "morada": "Rua A, 1", "codigo_postal": "1000-001",
        "localidade": "Lisboa"}).status_code == 200

    r = cliente.post("/api/contabilidade/fecho", json={"ano": 2025, "imposto": 1142, "fechar_periodo": True})
    assert r.status_code == 201, r.text
    assert len(r.json()) == 3

    s = saldos(cliente, "2025-01-01", "2025-12-31")
    assert all(abs(v) < 0.005 for c, v in s.items() if c[:1] in "67")  # gastos e rendimentos saldados
    assert (s["818"], s["241"]) == (-5658.0, -1142.0)                    # lucro líquido e IRC a pagar
    assert s.get("811", 0) == 0 and s.get("8121", 0) == 0
    # A demonstração de resultados continua a mostrar o exercício
    dr = cliente.get("/api/contabilidade/resultados", params={"de": "2025-01-01", "ate": "2025-12-31"}).json()
    assert dr["resultado"] == 6800

    # SAF-T: os lançamentos do fecho são do tipo A (apuramento de resultados) e o ficheiro é válido
    saft = cliente.get("/api/contabilidade/saft", params={"ano": 2025})
    assert saft.status_code == 200
    texto = saft.content.decode("cp1252")
    esquema().validate(texto)
    assert texto.count("<TransactionType>A</TransactionType>") == 3

    # Não se fecha duas vezes, nem o ano em curso
    assert cliente.post("/api/contabilidade/fecho", json={"ano": 2025, "imposto": 0}).status_code == 409
    em_curso = cliente.post("/api/contabilidade/fecho", json={"ano": date.today().year, "imposto": 0})
    assert em_curso.status_code == 409 and "depois de 31/12" in em_curso.json()["detail"]

    # Transferência para resultados transitados em 2026
    t = cliente.post("/api/contabilidade/fecho/transferir", json={"ano": 2025})
    assert t.status_code == 201 and t.json()["data"] == "2026-01-01"
    s = saldos(cliente, "2026-01-01", "2026-12-31")
    assert s.get("818", 0) == 0 and s["561"] == -5658.0

    # Reabrir: primeiro estornar a transferência, depois reabrir o período
    assert "transferência" in cliente.post("/api/contabilidade/fecho/reabrir", json={"ano": 2025}).json()["detail"]
    assert cliente.post(f"/api/contabilidade/lancamentos/{t.json()['id']}/estornar").status_code == 201
    assert "reabra-o" in cliente.post("/api/contabilidade/fecho/reabrir", json={"ano": 2025}).json()["detail"]
    cliente.put("/api/contabilidade/configuracao", json={"regime_iva": "trimestral", "fechado_ate": None})
    r = cliente.post("/api/contabilidade/fecho/reabrir", json={"ano": 2025})
    assert r.status_code == 200 and r.json()["estornados"] == 3
    s = saldos(cliente, "2025-01-01", "2025-12-31")
    assert s["721"] == -10000.0 and s.get("818", 0) == 0


def test_prejuizo_so_paga_tributacao_autonoma(cliente):
    lancar(cliente, "2025-06-01", "Almoço", [{"conta": "6266", "debito": 500}, {"conta": "121", "credito": 500}])
    e = cliente.get("/api/contabilidade/fecho", params={"ano": 2025}).json()["estimativa"]
    assert (e["irc"], e["derrama"], e["tributacao_autonoma"], e["imposto_total"]) == (0, 0, 50, 50)
    assert cliente.post("/api/contabilidade/fecho", json={"ano": 2025, "imposto": 50}).status_code == 201
    s = saldos(cliente, "2025-01-01", "2025-12-31")
    assert s["818"] == 550.0  # prejuízo líquido (saldo devedor)


def test_verificacoes_antes_de_fechar(cliente):
    lancar(cliente, "2025-03-01", "Venda", [{"conta": "121", "debito": 123}, {"conta": "711", "credito": 100},
                                             {"conta": "24333", "credito": 23}])
    itens = {i["nome"]: i for i in cliente.get("/api/contabilidade/fecho", params={"ano": 2025}).json()["verificacoes"]}
    assert itens["IVA apurado"]["estado"] == "aviso"
    assert itens["Faturas de compra contabilizadas"]["estado"] == "ok"


def test_so_administradores_fecham(cliente_anonimo, fabrica_sessoes, empresa_teste):
    from app.modelos.tabelas import PAPEL_MEMBRO, Utilizador
    from app.servicos.autenticacao import hash_palavra_passe
    with fabrica_sessoes() as db:
        db.add(Utilizador(empresa_id=empresa_teste, nome="Membro", email="membro@teste.pt", papel=PAPEL_MEMBRO,
                          hash_palavra_passe=hash_palavra_passe(PALAVRA_PASSE_TESTE)))
        db.commit()
    membro = entrar(cliente_anonimo, "membro@teste.pt")
    assert membro.post("/api/contabilidade/fecho", json={"ano": 2025, "imposto": 0}).status_code == 403
    assert membro.get("/api/contabilidade/fecho", params={"ano": 2025}).status_code == 200
