"""Webhooks da API pública: endereços, eventos, assinatura, novas tentativas e proteção de endereços (sem rede)."""

import hashlib
import hmac
import json
import socket
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.modelos.tabelas import EntregaWebhook, WebhookEndpoint
from app.servicos import webhooks
from tests.conftest import criar_empresa_com_admin
from tests.test_contabilidade import criar_fatura


@pytest.fixture
def rede(monkeypatch):
    """DNS público simulado e envio capturado; devolve (envios, respostas configuráveis)."""
    monkeypatch.setattr(webhooks.socket, "getaddrinfo",
                        lambda nome, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))])
    envios, respostas = [], [200]

    def enviar(url, corpo, cabecalhos):
        envios.append((url, corpo, cabecalhos))
        resposta = respostas[0] if len(respostas) == 1 else respostas.pop(0)
        if isinstance(resposta, Exception):
            raise resposta
        return resposta

    monkeypatch.setattr(webhooks, "_enviar", enviar)
    return envios, respostas


def _criar(cliente, eventos=("fatura.confirmada", "lancamento.criado")) -> dict:
    r = cliente.post("/api/equipa/webhooks", json={"url": "https://erp.exemplo.pt/dobra", "eventos": list(eventos)})
    assert r.status_code == 201, r.text
    return r.json()


def test_eventos_assinados_e_entregues(cliente, fabrica_sessoes, empresa_teste, rede):
    envios, _ = rede
    w = _criar(cliente)
    assert w["segredo"].startswith("dobra_whsec_")
    assert "segredo" not in cliente.get("/api/equipa/webhooks").json()["webhooks"][0]   # só aparece uma vez

    # Confirmar uma fatura e contabilizá-la: um aviso de cada (fatura.carregada não foi pedido)
    id_fatura = criar_fatura(fabrica_sessoes, empresa_teste, fornecedor="Talho Central, Lda.")
    assert cliente.post(f"/api/contabilidade/faturas/{id_fatura}/contabilizar", json={}).status_code in (200, 201)
    with fabrica_sessoes() as db:
        pendentes = list(db.scalars(select(EntregaWebhook)))
        assert [e.evento for e in pendentes] == ["lancamento.criado"]
        assert webhooks.entregar_pendentes(db) == {"entregue": 1, "falhou": 0, "pendente": 0}

    url, corpo, cab = envios[0]
    assert url == "https://erp.exemplo.pt/dobra" and cab["Dobra-Evento"] == "lancamento.criado"
    dados = json.loads(corpo)
    assert dados["evento"] == "lancamento.criado" and dados["dados"]["id"] and dados["id"] == int(cab["Dobra-Entrega"])
    # Assinatura verificável pelo recetor com o segredo
    t, v1 = (p.split("=", 1)[1] for p in cab["Dobra-Assinatura"].split(","))
    esperada = hmac.new(w["segredo"].encode(), f"{t}.".encode() + corpo, hashlib.sha256).hexdigest()
    assert hmac.compare_digest(v1, esperada)

    entregas = cliente.get(f"/api/equipa/webhooks/{w['id']}/entregas").json()
    assert entregas[0]["estado"] == "entregue" and entregas[0]["codigo_resposta"] == 200


def test_novas_tentativas_desistencia_e_desativacao(cliente, fabrica_sessoes, empresa_teste, rede, monkeypatch):
    envios, respostas = rede
    respostas[0] = 500
    w = _criar(cliente, ["lancamento.criado"])
    cliente.post("/api/contabilidade/lancamentos", json={
        "diario": "OD", "data": "2026-10-20", "descricao": "Teste",
        "linhas": [{"conta": "121", "debito": 10}, {"conta": "711", "credito": 10}]})
    agora = datetime.now()
    with fabrica_sessoes() as db:
        for i in range(webhooks.MAX_TENTATIVAS):
            webhooks.entregar_pendentes(db, agora=agora + timedelta(days=i + 1))
        [e] = db.scalars(select(EntregaWebhook))
        assert e.estado == "falhou" and e.tentativas == webhooks.MAX_TENTATIVAS and e.erro == "Resposta 500"
        assert db.get(WebhookEndpoint, w["id"]).falhas_seguidas == webhooks.MAX_TENTATIVAS
    assert len(envios) == webhooks.MAX_TENTATIVAS

    # Muitas falhas seguidas: o endereço fica desativado; o teste com sucesso reativa-o
    monkeypatch.setattr(webhooks, "FALHAS_PARA_DESATIVAR", 2)
    with fabrica_sessoes() as db:
        db.get(WebhookEndpoint, w["id"]).falhas_seguidas = 1
        db.commit()
    assert cliente.post(f"/api/equipa/webhooks/{w['id']}/testar").json()["estado"] == "falhou"
    assert cliente.get("/api/equipa/webhooks").json()["webhooks"][0]["ativo"] is False
    respostas[0] = 204
    r = cliente.post(f"/api/equipa/webhooks/{w['id']}/testar").json()
    assert r["estado"] == "entregue" and r["evento"] == "teste"
    assert cliente.get("/api/equipa/webhooks").json()["webhooks"][0]["ativo"] is True


@pytest.mark.parametrize("url, motivo", [
    ("http://erp.exemplo.pt/x", "https"),
    ("https://localhost/x", "público"),
    ("https://user:pw@erp.exemplo.pt/x", "utilizador"),
])
def test_enderecos_recusados(cliente, rede, url, motivo):
    r = cliente.post("/api/equipa/webhooks", json={"url": url, "eventos": ["lancamento.criado"]})
    assert r.status_code == 400 and motivo in r.json()["detail"]


def test_endereco_que_aponta_para_rede_interna(cliente, monkeypatch):
    monkeypatch.setattr(webhooks.socket, "getaddrinfo",
                        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 443))])
    r = cliente.post("/api/equipa/webhooks", json={"url": "https://metadados.exemplo.pt/",
                                                   "eventos": ["lancamento.criado"]})
    assert r.status_code == 400 and "rede interna" in r.json()["detail"]
    with pytest.raises(webhooks.ErroWebhook):   # e também no momento do envio, se o DNS mudar
        webhooks.validar_url("https://erp.exemplo.pt/")


def test_eventos_invalidos_limite_isolamento_e_apagar(cliente, novo_cliente, fabrica_sessoes, rede):
    assert cliente.post("/api/equipa/webhooks", json={"url": "https://erp.exemplo.pt/",
                                                      "eventos": ["fatura.apagada"]}).status_code == 400
    ids = [_criar(cliente)["id"] for _ in range(webhooks.MAX_ENDPOINTS)]
    assert cliente.post("/api/equipa/webhooks", json={"url": "https://erp.exemplo.pt/",
                                                      "eventos": ["lancamento.criado"]}).status_code == 400
    criar_empresa_com_admin(fabrica_sessoes, "Outra", "outra@teste.pt")
    outra = novo_cliente("outra@teste.pt")
    assert outra.get("/api/equipa/webhooks").json()["webhooks"] == []
    assert outra.delete(f"/api/equipa/webhooks/{ids[0]}").status_code == 404
    assert outra.post(f"/api/equipa/webhooks/{ids[0]}/testar").status_code == 404
    assert cliente.delete(f"/api/equipa/webhooks/{ids[0]}").status_code == 204
    assert len(cliente.get("/api/equipa/webhooks").json()["webhooks"]) == webhooks.MAX_ENDPOINTS - 1


def test_sem_enderecos_nao_ha_entregas(cliente, fabrica_sessoes):
    cliente.post("/api/contabilidade/lancamentos", json={
        "diario": "OD", "data": "2026-10-20", "descricao": "Teste",
        "linhas": [{"conta": "121", "debito": 10}, {"conta": "711", "credito": 10}]})
    with fabrica_sessoes() as db:
        assert list(db.scalars(select(EntregaWebhook))) == []
