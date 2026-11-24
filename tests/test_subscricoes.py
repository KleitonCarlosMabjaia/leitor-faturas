"""Planos, registo online, período experimental, bloqueio suave e Stripe (checkout, portal e webhooks)."""

import hashlib
import hmac
import json
import time
from datetime import date, timedelta

import pytest

from app import config
from app.modelos.tabelas import Empresa
from app.servicos import subscricoes
from tests.conftest import criar_pdf_com_texto

REGISTO = {
    "empresa": "Padaria Nova, Lda.", "nif": "503504564", "nome": "Joana Lopes", "email": "joana@padaria.pt",
    "palavra_passe": "Padaria2026segura", "plano": "contabilidade", "aceito_termos": True,
}


@pytest.fixture
def faturacao(monkeypatch):
    """Liga a faturação (Stripe) e devolve a lista de pedidos que teriam sido feitos ao Stripe."""
    pedidos = []
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setattr(config, "STRIPE_WEBHOOK_SECRET", "whsec_teste")
    monkeypatch.setattr(config, "STRIPE_PRECO_ESSENCIAL", "price_ess")
    monkeypatch.setattr(config, "STRIPE_PRECO_CONTABILIDADE", "price_cont")

    def falso(metodo, caminho, dados=None):
        pedidos.append((metodo, caminho, dados))
        return {"url": f"https://stripe.test/{caminho}"}

    monkeypatch.setattr(subscricoes, "_stripe", falso)
    return pedidos


def _empresa(fabrica_sessoes, empresa_id, **campos):
    with fabrica_sessoes() as db:
        empresa = db.get(Empresa, empresa_id)
        for chave, valor in campos.items():
            setattr(empresa, chave, valor)
        db.commit()


# ---------- Registo online ----------

def test_registo_cria_empresa_em_periodo_experimental(cliente_anonimo):
    r = cliente_anonimo.post("/api/registo", json=REGISTO)
    assert r.status_code == 201, r.text
    assert r.json()["empresa"] == "Padaria Nova, Lda." and r.json()["papel"] == "admin"
    s = cliente_anonimo.get("/api/subscricao").json()
    assert s["plano"] == "contabilidade" and s["estado"] == "experimental"
    assert s["experimental_ate"] == (date.today() + timedelta(days=30)).isoformat()
    # Já tem sessão: entra direto na contabilidade da empresa nova
    assert cliente_anonimo.get("/api/contabilidade/resumo").status_code == 200


@pytest.mark.parametrize("alteracao, mensagem", [
    ({"nif": "123456780"}, "NIF"), ({"aceito_termos": False}, "termos"), ({"palavra_passe": "curta"}, "10 caracteres"),
    ({"plano": "gabinete"}, "Essencial ou Contabilidade"),
])
def test_registo_valida_os_dados(cliente_anonimo, alteracao, mensagem):
    r = cliente_anonimo.post("/api/registo", json={**REGISTO, **alteracao})
    assert r.status_code == 400 and mensagem in r.json()["detail"]


def test_registo_com_email_existente_ou_fechado(cliente_anonimo, empresa_teste, monkeypatch):
    r = cliente_anonimo.post("/api/registo", json={**REGISTO, "email": "admin@teste.pt"})
    assert r.status_code == 409
    monkeypatch.setattr(config, "REGISTO_ABERTO", False)
    assert cliente_anonimo.post("/api/registo", json=REGISTO).status_code == 403
    assert cliente_anonimo.get("/registar", follow_redirects=False).headers["location"] == "/entrar"


# ---------- Limites dos planos ----------

def test_plano_essencial_sem_contabilidade_e_com_dois_utilizadores(cliente, fabrica_sessoes, empresa_teste):
    _empresa(fabrica_sessoes, empresa_teste, plano="essencial")
    assert cliente.get("/api/auth/eu").json()["funcionalidades"] == ["compras", "gestao"]
    r = cliente.get("/api/contabilidade/resumo")
    assert r.status_code == 403 and "Essencial" in r.json()["detail"]
    destino = cliente.get("/contabilidade", follow_redirects=False).headers["location"]
    assert destino == "/subscricao?recurso=contabilidade"
    assert cliente.get("/api/faturas").status_code == 200         # as compras continuam disponíveis
    assert cliente.post("/api/equipa", json={"nome": "Rui", "email": "rui@teste.pt"}).status_code == 201
    r = cliente.post("/api/equipa", json={"nome": "Eva", "email": "eva@teste.pt"})
    assert r.status_code == 403 and "2 utilizadores" in r.json()["detail"]


def test_gabinete_sem_limite_de_utilizadores(cliente, fabrica_sessoes, empresa_teste):
    _empresa(fabrica_sessoes, empresa_teste, plano="gabinete")
    for n in range(6):
        assert cliente.post("/api/equipa", json={"nome": f"P{n}", "email": f"p{n}@teste.pt"}).status_code == 201


# ---------- Período experimental e bloqueio suave ----------

def test_sem_stripe_nao_ha_bloqueios(cliente, fabrica_sessoes, empresa_teste):
    _empresa(fabrica_sessoes, empresa_teste, estado_subscricao="experimental", experimental_ate=date(2020, 1, 1))
    assert cliente.post("/api/equipa", json={"nome": "Rui", "email": "rui@teste.pt"}).status_code == 201


def test_experimental_terminado_fica_so_de_leitura(cliente, fabrica_sessoes, empresa_teste, faturacao):
    _empresa(fabrica_sessoes, empresa_teste, estado_subscricao="experimental",
             experimental_ate=date.today() - timedelta(days=1))
    eu = cliente.get("/api/auth/eu").json()
    assert "período experimental terminou" in eu["aviso_subscricao"]
    assert eu["dias_experimentais"] == -1
    pdf = criar_pdf_com_texto(["Fatura"])
    r = cliente.post("/api/faturas", files={"ficheiro": ("f.pdf", pdf, "application/pdf")})
    assert r.status_code == 402 and r.json()["so_leitura"] is True
    assert cliente.get("/api/faturas").status_code == 200                    # consultar continua
    assert cliente.get("/api/faturas/exportar?formato=csv").status_code == 200  # exportar também
    assert cliente.put("/api/conta/preferencias", json={"alertas_email": False}).status_code == 204
    assert cliente.post("/api/subscricao/checkout", json={"plano": "essencial"}).status_code == 200


def test_pagamento_em_atraso_tem_tolerancia(cliente, fabrica_sessoes, empresa_teste, faturacao):
    _empresa(fabrica_sessoes, empresa_teste, estado_subscricao="em_atraso",
             em_atraso_desde=date.today() - timedelta(days=3))
    assert cliente.post("/api/equipa", json={"nome": "Rui", "email": "rui@teste.pt"}).status_code == 201
    _empresa(fabrica_sessoes, empresa_teste, em_atraso_desde=date.today() - timedelta(days=8))
    r = cliente.post("/api/equipa", json={"nome": "Eva", "email": "eva@teste.pt"})
    assert r.status_code == 402 and "pagamento em atraso" in r.json()["detail"]


# ---------- Stripe ----------

def test_checkout_e_portal(cliente, fabrica_sessoes, empresa_teste, faturacao):
    r = cliente.post("/api/subscricao/checkout", json={"plano": "contabilidade"})
    assert r.json()["url"] == "https://stripe.test/checkout/sessions"
    _, caminho, dados = faturacao[-1]
    assert dados["line_items[0][price]"] == "price_cont" and dados["client_reference_id"] == str(empresa_teste)
    assert dados["mode"] == "subscription" and dados["customer_email"] == "admin@teste.pt"
    assert cliente.post("/api/subscricao/checkout", json={"plano": "gabinete"}).status_code == 400
    # Sem cliente no Stripe ainda não há portal
    assert cliente.post("/api/subscricao/portal").status_code == 400
    _empresa(fabrica_sessoes, empresa_teste, stripe_cliente_id="cus_1")
    assert cliente.post("/api/subscricao/portal").json()["url"].endswith("billing_portal/sessions")


def _assinar(evento: dict, segredo="whsec_teste", instante=None) -> tuple[bytes, str]:
    corpo = json.dumps(evento).encode()
    t = int(instante or time.time())
    assinatura = hmac.new(segredo.encode(), f"{t}.".encode() + corpo, hashlib.sha256).hexdigest()
    return corpo, f"t={t},v1={assinatura}"


def _webhook(cliente_anonimo, evento, **kw):
    corpo, cabecalho = _assinar(evento, **kw)
    return cliente_anonimo.post("/api/stripe/webhook", content=corpo,
                                headers={"Stripe-Signature": cabecalho, "Content-Type": "application/json"})


def test_webhooks_atualizam_a_subscricao(cliente_anonimo, fabrica_sessoes, empresa_teste, faturacao):
    _empresa(fabrica_sessoes, empresa_teste, estado_subscricao="experimental", experimental_ate=date.today())
    r = _webhook(cliente_anonimo, {"type": "checkout.session.completed", "data": {"object": {
        "client_reference_id": str(empresa_teste), "customer": "cus_9", "subscription": "sub_9"}}})
    assert r.status_code == 200
    with fabrica_sessoes() as db:
        e = db.get(Empresa, empresa_teste)
        assert (e.estado_subscricao, e.stripe_cliente_id, e.stripe_subscricao_id) == ("ativa", "cus_9", "sub_9")

    fim = int(time.mktime((date.today() + timedelta(days=30)).timetuple()))
    assinatura = {"id": "sub_9", "customer": "cus_9", "status": "past_due", "current_period_end": fim,
                  "items": {"data": [{"price": {"id": "price_ess"}}]}, "metadata": {}}
    _webhook(cliente_anonimo, {"type": "customer.subscription.updated", "data": {"object": assinatura}})
    with fabrica_sessoes() as db:
        e = db.get(Empresa, empresa_teste)
        assert (e.estado_subscricao, e.plano, e.em_atraso_desde) == ("em_atraso", "essencial", date.today())
        assert e.fim_periodo == date.today() + timedelta(days=30)

    _webhook(cliente_anonimo, {"type": "invoice.paid", "data": {"object": {"customer": "cus_9"}}})
    cancelada = {**assinatura, "status": "canceled"}
    _webhook(cliente_anonimo, {"type": "customer.subscription.deleted", "data": {"object": cancelada}})
    with fabrica_sessoes() as db:
        assert db.get(Empresa, empresa_teste).estado_subscricao == "cancelada"


def test_webhook_recusa_assinaturas_falsas_ou_antigas(cliente_anonimo, empresa_teste, faturacao):
    evento = {"type": "invoice.paid", "data": {"object": {"customer": "cus_x"}}}
    assert _webhook(cliente_anonimo, evento, segredo="outro").status_code == 400
    assert _webhook(cliente_anonimo, evento, instante=time.time() - 3600).status_code == 400
    r = cliente_anonimo.post("/api/stripe/webhook", content=b"{}", headers={"Content-Type": "application/json"})
    assert r.status_code == 400


def test_webhook_com_api_basil_fim_do_periodo_nos_itens(cliente_anonimo, fabrica_sessoes, empresa_teste, faturacao):
    """Endpoints criados com a API 2025-03-31.basil ou seguintes trazem o fim do período em cada item."""
    _empresa(fabrica_sessoes, empresa_teste, stripe_cliente_id="cus_9")
    fim = int(time.time()) + 30 * 86400
    r = _webhook(cliente_anonimo, {"type": "customer.subscription.updated", "data": {"object": {
        "id": "sub_9", "customer": "cus_9", "status": "active", "metadata": {"empresa_id": str(empresa_teste)},
        "items": {"data": [{"price": {"id": "price_cont"}, "current_period_end": fim}]}}}})
    assert r.status_code == 200
    with fabrica_sessoes() as db:
        assert db.get(Empresa, empresa_teste).fim_periodo == date.today() + timedelta(days=30)
