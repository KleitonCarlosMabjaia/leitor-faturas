"""Faturas certificadas das subscrições, emitidas no Facturalusa quando o Stripe confirma o pagamento (sem rede)."""

import pytest
from sqlalchemy import select

from app import config
from app.modelos.tabelas import Empresa, FaturaSubscricao
from app.rotas import subscricao as rotas_subscricao
from app.servicos import faturas_certificadas
from tests.test_subscricoes import _empresa, _webhook, faturacao  # noqa: F401  (fixture do Stripe)


@pytest.fixture
def facturalusa(monkeypatch, fabrica_sessoes, faturacao):  # noqa: F811
    """Liga o Facturalusa com respostas simuladas; devolve (pedidos feitos, respostas configuráveis)."""
    monkeypatch.setattr(config, "FACTURALUSA_API_KEY", "chave-teste")
    monkeypatch.setattr(faturas_certificadas, "_artigo_garantido", False)
    monkeypatch.setattr(rotas_subscricao, "SessaoLocal", fabrica_sessoes)   # emissão em segundo plano
    pedidos = []
    respostas = {
        "items": (201, {"id": 1, "reference": "DOBRA-SUBSCRICAO"}),
        "customers/find": (404, {"message": "Não encontrado"}),
        "customers": (201, {"id": 7}),
        "sales": (201, {"id": 99, "number": 1, "document_full_number": "FR 2026/1", "url_file": "https://facturalusa.pt/f.pdf",
                        "grand_total": 60.27, "documenttypeserie": {"atcud": "ABCD1234-1"}}),
        "sales/99/send_email": (200, {}),
    }

    def falso(metodo, caminho, dados=None):
        pedidos.append((metodo, caminho, dados))
        return respostas[caminho]

    monkeypatch.setattr(faturas_certificadas, "_pedido", falso)
    return pedidos, respostas


def _pago(fatura_id="in_1", pago=6027, iva=1127, pais="PT", **extra) -> dict:
    return {"type": "invoice.paid", "data": {"object": {
        "id": fatura_id, "customer": "cus_9", "amount_paid": pago, "tax": iva, "currency": "eur",
        "customer_email": "contas@cliente.pt", "customer_name": "Cliente, Lda.",
        "customer_address": {"country": pais, "line1": "Rua A, 1", "city": "Porto", "postal_code": "4000-001"},
        "lines": {"data": [{"period": {"start": 1793836800, "end": 1796428800}}]}, **extra}}}


def _faturas(fabrica_sessoes) -> list[FaturaSubscricao]:
    with fabrica_sessoes() as db:
        return list(db.scalars(select(FaturaSubscricao)))


def test_pagamento_emite_fatura_recibo_e_envia_por_email(cliente_anonimo, cliente, fabrica_sessoes, empresa_teste,
                                                         facturalusa):
    pedidos, _ = facturalusa
    _empresa(fabrica_sessoes, empresa_teste, stripe_cliente_id="cus_9", nif="507842545", plano="contabilidade")
    assert _webhook(cliente_anonimo, _pago()).status_code == 200

    [f] = _faturas(fabrica_sessoes)
    assert f.estado == "emitida" and f.numero == "FR 2026/1" and f.atcud == "ABCD1234-1"
    assert f.total == 60.27 and f.iva == 11.27 and f.enviada
    caminhos = [c for _, c, _ in pedidos]
    assert caminhos == ["items", "customers/find", "customers", "sales", "sales/99/send_email"]
    cliente_novo = pedidos[2][2]
    assert cliente_novo["code"] == f"DOBRA-{empresa_teste}" and cliente_novo["vat_number"] == "507842545"
    venda = pedidos[3][2]
    assert venda["document_type"] == "Factura Recibo" and venda["status"] == "Terminado"
    assert venda["external_reference"] == "in_1" and venda["vat_type"] == "IVA incluído" and venda["customer"] == 7
    assert venda["items"] == [{"id": "DOBRA-SUBSCRICAO", "price": 60.27, "quantity": 1, "vat": 23,
                               "details": venda["items"][0]["details"]}]
    assert "plano Contabilidade" in venda["items"][0]["details"]
    assert pedidos[4][2]["to"] == [{"name": "Empresa de Teste", "email": "contas@cliente.pt"}]

    # O mesmo evento outra vez (o Stripe repete): não há segunda fatura
    assert _webhook(cliente_anonimo, _pago()).status_code == 200
    assert len(_faturas(fabrica_sessoes)) == 1 and [c for _, c, _ in pedidos].count("sales") == 1

    # A empresa vê a fatura na página da subscrição
    lista = cliente.get("/api/subscricao/faturas").json()
    assert lista["ativa"] is True and lista["faturas"][0]["numero"] == "FR 2026/1"
    assert lista["faturas"][0]["url_pdf"] == "https://facturalusa.pt/f.pdf"


def test_falha_fica_registada_e_volta_a_ser_tentada(cliente_anonimo, fabrica_sessoes, empresa_teste, facturalusa):
    pedidos, respostas = facturalusa
    _empresa(fabrica_sessoes, empresa_teste, stripe_cliente_id="cus_9", nif="507842545")
    certa = respostas["sales"]
    respostas["sales"] = (403, {"message": "A série não se encontra comunicada à AT no tipo de documento seleccionado"})
    _webhook(cliente_anonimo, _pago())
    [f] = _faturas(fabrica_sessoes)
    assert f.estado == "erro" and "série" in f.erro and f.tentativas == 1

    respostas["sales"] = certa
    respostas["customers/find"] = (200, {"id": 7})   # o cliente já foi criado na primeira tentativa
    with fabrica_sessoes() as db:
        assert faturas_certificadas.emitir_pendentes(db) == {"emitida": 1, "erro": 0}
    [f] = _faturas(fabrica_sessoes)
    assert f.estado == "emitida" and f.tentativas == 2 and f.erro is None
    assert [c for _, c, _ in pedidos].count("customers") == 1   # não criou o cliente duas vezes


def test_cliente_estrangeiro_com_autoliquidacao(cliente_anonimo, fabrica_sessoes, empresa_teste, facturalusa,
                                                monkeypatch):
    pedidos, _ = facturalusa
    monkeypatch.setattr(config, "STRIPE_IVA_AUTOMATICO", True)
    _empresa(fabrica_sessoes, empresa_teste, stripe_cliente_id="cus_9", nif=None)
    _webhook(cliente_anonimo, _pago(pago=4900, iva=0, pais="ES",
                                    customer_tax_ids=[{"type": "eu_vat", "value": "ESB12345678"}]))
    venda = next(d for _, c, d in pedidos if c == "sales")
    assert venda["items"][0]["vat"] == 0 and venda["items"][0]["vat_exemption"] == "M40"
    assert venda["vat_number"] == "ESB12345678" and venda["country"] == "Espanha" and venda["language"] == "EN"


def test_sem_faturacao_ou_sem_valor_nao_emite(cliente_anonimo, fabrica_sessoes, empresa_teste, facturalusa,
                                              monkeypatch):
    pedidos, _ = facturalusa
    _empresa(fabrica_sessoes, empresa_teste, stripe_cliente_id="cus_9", nif="507842545")
    _webhook(cliente_anonimo, _pago(fatura_id="in_0", pago=0, iva=0))   # período experimental: 0 €
    monkeypatch.setattr(config, "FACTURALUSA_API_KEY", "")
    _webhook(cliente_anonimo, _pago(fatura_id="in_2"))
    assert _faturas(fabrica_sessoes) == [] and pedidos == []
    with fabrica_sessoes() as db:   # o pagamento continua a ativar a subscrição
        assert db.get(Empresa, empresa_teste).estado_subscricao == "ativa"


def test_iva_em_qualquer_versao_da_api_do_stripe():
    from app.servicos.faturas_certificadas import _iva_do_stripe
    assert _iva_do_stripe({"total_taxes": [{"amount": 1127, "tax_behavior": "exclusive"}]}) == 1127   # basil+
    assert _iva_do_stripe({"tax": 1127}) == 1127                                                       # antigas
    assert _iva_do_stripe({"total_tax_amounts": [{"amount": 500}, {"amount": 627}]}) == 1127
    assert _iva_do_stripe({}) == 0
