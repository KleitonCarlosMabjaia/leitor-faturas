"""Emails transacionais: convites, recuperação da palavra-passe, nova sessão e alertas de prazos."""

import re
from datetime import date

from app import alertas
from app.servicos import email
from tests.conftest import PALAVRA_PASSE_TESTE

NOVA = "OutraPalavra2026"


def _link(enviado: dict) -> str:
    return re.search(r"https?://\S+/definir-palavra-passe#[\w-]+", enviado["text"]).group(0)


def test_sem_configuracao_nada_e_enviado_e_tudo_funciona(cliente):
    r = cliente.post("/api/equipa", json={"nome": "Rui", "email": "rui@teste.pt"})
    assert r.status_code == 201 and r.json()["email_enviado"] is False and "#" in r.json()["link"]
    assert cliente.get("/api/auth/opcoes").json()["recuperacao_por_email"] is False
    assert cliente.post("/api/auth/recuperar", json={"email": "admin@teste.pt"}).status_code == 400


def test_convite_por_email(cliente, emails):
    r = cliente.post("/api/equipa", json={"nome": "Rui <b>", "email": "rui@teste.pt"})
    assert r.json()["email_enviado"] is True
    convite = next(e for e in emails if e["to"] == ["rui@teste.pt"])
    assert "convidou-o" in convite["subject"]
    assert "Rui &lt;b&gt;" in convite["html"] and "<b>" not in convite["html"].split("<body")[1].split("Olá")[1][:30]
    assert _link(convite) == r.json()["link"]


def test_esqueci_me_da_palavra_passe(cliente_anonimo, empresa_teste, emails, novo_cliente):
    assert cliente_anonimo.get("/api/auth/opcoes").json()["recuperacao_por_email"] is True
    # Email desconhecido: a mesma resposta, sem email
    assert cliente_anonimo.post("/api/auth/recuperar", json={"email": "ninguem@teste.pt"}).status_code == 204
    assert emails == []
    assert cliente_anonimo.post("/api/auth/recuperar", json={"email": " ADMIN@teste.pt"}).status_code == 204
    assert len(emails) == 1 and "Recuperar" in emails[0]["subject"] and "60 minutos" in emails[0]["text"]
    token = _link(emails[0]).split("#", 1)[1]
    outro = novo_cliente()
    r = outro.post("/api/auth/definir-palavra-passe", json={"token": token, "palavra_passe": NOVA})
    assert r.status_code == 200
    # O link só serve uma vez e a palavra-passe antiga deixa de funcionar
    segunda = outro.post("/api/auth/definir-palavra-passe", json={"token": token, "palavra_passe": NOVA})
    assert segunda.status_code == 400
    credenciais = {"email": "admin@teste.pt", "palavra_passe": PALAVRA_PASSE_TESTE}
    assert novo_cliente().post("/api/auth/entrar", json=credenciais).status_code == 401


def test_aviso_de_nova_sessao_so_em_dispositivo_novo(cliente_anonimo, empresa_teste, emails, novo_cliente):
    credenciais = {"email": "admin@teste.pt", "palavra_passe": PALAVRA_PASSE_TESTE}
    cabecalhos = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0) Chrome/130.0"}
    assert cliente_anonimo.post("/api/auth/entrar", json=credenciais, headers=cabecalhos).status_code == 200
    avisos = [e for e in emails if "Nova entrada" in e["subject"]]
    assert len(avisos) == 1 and "Chrome em Windows" in avisos[0]["text"]
    # O mesmo navegador (já com sessão aberta) não gera novo aviso
    assert novo_cliente().post("/api/auth/entrar", json=credenciais, headers=cabecalhos).status_code == 200
    assert len([e for e in emails if "Nova entrada" in e["subject"]]) == 1


def test_alertas_de_prazos(fabrica_sessoes, empresa_teste, monkeypatch):
    # 18/12/2026: o IVA mensal/trimestral e as retenções de novembro terminam a 20 e 21 de dezembro
    with fabrica_sessoes() as db:
        hoje = date(2026, 12, 14)
        mensagens = alertas.alertas_do_dia(db, hoje)
    assert mensagens, "devia haver prazos a 7 dias"
    m = mensagens[0]
    assert m.para == "admin@teste.pt" and "Prazos fiscais" in m.assunto
    assert all("daqui a 7 dias" in i or "amanhã" in i for i in m.lista)
    # Quem desliga os alertas deixa de os receber
    from app.modelos.tabelas import Utilizador

    with fabrica_sessoes() as db:
        db.query(Utilizador).update({"alertas_email": False})
        db.commit()
        assert alertas.alertas_do_dia(db, hoje) == []


def test_preferencia_de_alertas_na_conta(cliente):
    assert cliente.get("/api/conta").json()["alertas_email"] is True
    assert cliente.put("/api/conta/preferencias", json={"alertas_email": False}).status_code == 204
    assert cliente.get("/api/conta").json()["alertas_email"] is False


def test_falha_no_envio_nao_estraga_o_pedido(cliente, emails, monkeypatch):
    def falha(_dados):
        raise RuntimeError("serviço em baixo")

    monkeypatch.setattr(email, "_post", falha)
    r = cliente.post("/api/equipa", json={"nome": "Rui", "email": "rui@teste.pt"})
    assert r.status_code == 201 and r.json()["link"]
