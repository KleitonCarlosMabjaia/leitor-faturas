"""Proposta de diferimento ao contabilizar faturas de seguros, rendas ou assinaturas que cobrem vários meses."""

from datetime import date
from types import SimpleNamespace

from app.servicos.contabilidade import periodizacao
from tests.test_contabilidade import criar_fatura
from tests.test_fecho import saldos


def fatura_seguro(fabrica_sessoes, empresa_id, **extra) -> str:
    # Seguros estão isentos de IVA
    return criar_fatura(fabrica_sessoes, empresa_id, fornecedor="Fidelidade - Companhia de Seguros, S.A.",
                        nif_fornecedor="500918880", numero_fatura="AP 2026/77", data_emissao="2026-03-10",
                        iva=[], valor_sem_iva=1200.0, total=1200.0, **extra)


def test_proposta_e_diferimento_ao_contabilizar(cliente, fabrica_sessoes, empresa_teste):
    id_fatura = fatura_seguro(fabrica_sessoes, empresa_teste)
    proposta = cliente.get(f"/api/contabilidade/faturas/{id_fatura}").json()["proposta"]
    assert proposta["classificacao"]["conta"] == "6263"
    dif = proposta["diferimento"]
    assert (dif["inicio"], dif["meses"], dif["valor"], dif["mensal"], dif["origem"]) == ("2026-03-01", 12, 1200, 100,
                                                                                         "omissao")

    r = cliente.post(f"/api/contabilidade/faturas/{id_fatura}/contabilizar",
                     json={"diferimento": {"inicio": "2026-04-01", "meses": 12}})
    assert r.status_code == 201, r.text
    s = saldos(cliente, "2026-01-01", "2026-12-31")
    assert s.get("6263", 0) == 0 and s["281"] == 1200  # o gasto sai todo para gastos a reconhecer

    contab = cliente.get(f"/api/contabilidade/faturas/{id_fatura}").json()
    assert contab["diferimento"]["meses"] == 12 and contab["diferimento"]["fatura_id"] == id_fatura
    assert [x["origem"] for x in contab["lancamentos"]] == ["fatura", "diferimento"]

    cliente.post("/api/contabilidade/periodizacao/reconhecer", json={"ate": "2026-12-31"})
    s = saldos(cliente, "2026-01-01", "2026-12-31")
    assert (s["6263"], s["281"]) == (900, 300)  # abril a dezembro em 2026, o resto em 2027

    # Não se difere duas vezes a mesma fatura
    assert cliente.post(f"/api/contabilidade/faturas/{id_fatura}/diferir",
                        json={"inicio": "2026-04-01", "meses": 12}).status_code == 409


def test_fatura_contabilizada_em_lote_fica_por_diferir(cliente, fabrica_sessoes, empresa_teste):
    id_fatura = fatura_seguro(fabrica_sessoes, empresa_teste)
    assert cliente.post("/api/contabilidade/contabilizar-lote", json={"ids": [id_fatura]}).json()["contabilizadas"] == 1
    lista = cliente.get("/api/contabilidade/periodizacao").json()["faturas_por_diferir"]
    assert [x["fatura_id"] for x in lista] == [id_fatura] and lista[0]["proposta"]["meses"] == 12

    r = cliente.post(f"/api/contabilidade/faturas/{id_fatura}/diferir", json={"inicio": "2026-03-01", "meses": 12})
    assert r.status_code == 201, r.text
    assert r.json()["valor"] == 1200
    assert cliente.get("/api/contabilidade/periodizacao").json()["faturas_por_diferir"] == []


def test_estornar_a_fatura_anula_o_diferimento(cliente, fabrica_sessoes, empresa_teste):
    id_fatura = fatura_seguro(fabrica_sessoes, empresa_teste)
    compra = cliente.post(f"/api/contabilidade/faturas/{id_fatura}/contabilizar",
                          json={"diferimento": {"inicio": "2026-03-01", "meses": 12}}).json()
    assert cliente.post(f"/api/contabilidade/lancamentos/{compra['id']}/estornar").status_code == 201
    s = saldos(cliente, "2026-01-01", "2026-12-31")
    assert s.get("6263", 0) == 0 and s.get("281", 0) == 0
    assert cliente.get("/api/contabilidade/periodizacao").json()["diferimentos"][0]["anulado"] is True

    # Com meses já reconhecidos, o estorno da fatura é recusado
    id2 = criar_fatura(fabrica_sessoes, empresa_teste, fornecedor="Fidelidade - Companhia de Seguros, S.A.",
                       nif_fornecedor="500918880", numero_fatura="AP 2026/78", data_emissao="2026-03-10", iva=[],
                       valor_sem_iva=600.0, total=600.0)
    compra2 = cliente.post(f"/api/contabilidade/faturas/{id2}/contabilizar",
                           json={"diferimento": {"inicio": "2026-03-01", "meses": 6}}).json()
    cliente.post("/api/contabilidade/periodizacao/reconhecer", json={"ate": "2026-04-30"})
    recusa = cliente.post(f"/api/contabilidade/lancamentos/{compra2['id']}/estornar")
    assert recusa.status_code == 409 and "reconhecidos" in recusa.json()["detail"]


def test_periodo_lido_do_texto_ou_da_ia():
    fatura = SimpleNamespace(data_emissao=date(2026, 9, 20), tipo_documento="FT")
    texto = "Assinatura anual do software. Período: 01/10/2026 a 30/09/2027. Vencimento 20/10/2026"
    p = periodizacao.propor(fatura, "6228", 600.0, texto)
    assert (p.inicio, p.meses, p.origem) == (date(2026, 10, 1), 12, "texto")
    # Sem período no texto e numa conta que não é de seguros: nada a propor...
    assert periodizacao.propor(fatura, "6228", 600.0, "Emissão 20/09/2026 Vencimento 20/10/2026") is None
    # ...a não ser que a IA tenha encontrado o período
    p = periodizacao.propor(fatura, "6228", 600.0, "", (date(2026, 1, 1), date(2026, 6, 30)))
    assert (p.meses, p.origem) == (6, "ia")
    # Notas de crédito e contas que não são de gasto nunca se diferem
    assert periodizacao.propor(SimpleNamespace(data_emissao=date(2026, 9, 20), tipo_documento="NC"), "6263", 10) is None
    assert periodizacao.propor(fatura, "312", 600.0, texto) is None

