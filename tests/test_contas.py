"""Testes da autenticação: login, sessões, bloqueio, convites e gestão da equipa."""

from datetime import datetime, timedelta

from sqlalchemy import create_engine, inspect, text

from app.db import migrar
from app.modelos.tabelas import TokenAcesso
from app.servicos import armazenamento
from tests.conftest import PALAVRA_PASSE_TESTE, entrar


def convidar(cliente, nome="Rui Costa", email="rui@teste.pt", papel="membro") -> str:
    resposta = cliente.post("/api/equipa", json={"nome": nome, "email": email, "papel": papel})
    assert resposta.status_code == 201, resposta.text
    return resposta.json()["link"]


def token_do_link(link: str) -> str:
    return link.split("#", 1)[1]


def aceitar_convite(cliente_anonimo, link: str, palavra_passe=PALAVRA_PASSE_TESTE):
    return cliente_anonimo.post(
        "/api/auth/definir-palavra-passe", json={"token": token_do_link(link), "palavra_passe": palavra_passe}
    )


# ---------- Acesso sem sessão ----------

def test_api_exige_sessao(cliente_anonimo, empresa_teste):
    for metodo, endereco in (
        ("get", "/api/faturas"), ("get", "/api/dashboard"), ("get", "/api/fornecedores"),
        ("get", "/api/faturas/exportar"), ("get", "/api/equipa"), ("get", "/api/auth/eu"),
    ):
        assert getattr(cliente_anonimo, metodo)(endereco).status_code == 401, endereco


def test_paginas_enviam_para_o_login(cliente_anonimo, empresa_teste):
    resposta = cliente_anonimo.get("/dashboard", follow_redirects=False)
    assert resposta.status_code == 303
    assert resposta.headers["location"] == "/entrar?voltar=/dashboard"
    assert cliente_anonimo.get("/entrar").status_code == 200
    assert cliente_anonimo.get("/api/saude").status_code == 200  # a única rota da API pública


# ---------- Entrar e sair ----------

def test_cookie_de_sessao_seguro(cliente_anonimo, empresa_teste):
    resposta = cliente_anonimo.post("/api/auth/entrar", json={"email": "ADMIN@teste.pt ", "palavra_passe": PALAVRA_PASSE_TESTE})
    assert resposta.status_code == 200  # o email não distingue maiúsculas nem espaços
    cookie = resposta.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    assert resposta.json()["empresa"] == "Empresa de Teste"


def test_erro_de_login_nao_revela_se_o_email_existe(cliente_anonimo, empresa_teste):
    errada = cliente_anonimo.post("/api/auth/entrar", json={"email": "admin@teste.pt", "palavra_passe": "errada-123"})
    inexistente = cliente_anonimo.post("/api/auth/entrar", json={"email": "ninguem@teste.pt", "palavra_passe": "x"})
    assert errada.status_code == inexistente.status_code == 401
    assert errada.json()["detail"] == inexistente.json()["detail"] == "Email ou palavra-passe incorretos."


def test_bloqueio_depois_de_5_tentativas(cliente_anonimo, empresa_teste):
    for _ in range(5):
        cliente_anonimo.post("/api/auth/entrar", json={"email": "admin@teste.pt", "palavra_passe": "errada-123"})
    # Mesmo com a palavra-passe certa, fica bloqueado durante uns minutos
    resposta = cliente_anonimo.post("/api/auth/entrar", json={"email": "admin@teste.pt", "palavra_passe": PALAVRA_PASSE_TESTE})
    assert resposta.status_code == 401
    assert "Demasiadas tentativas" in resposta.json()["detail"]


def test_sair_invalida_a_sessao(cliente):
    token = cliente.cookies.get("sessao")
    assert cliente.post("/api/auth/sair").status_code == 204
    assert cliente.get("/api/faturas").status_code == 401
    # Reutilizar o cookie antigo também não funciona: a sessão foi apagada no servidor
    cliente.cookies.set("sessao", token)
    assert cliente.get("/api/faturas").status_code == 401


# ---------- Convites ----------

def test_fluxo_de_convite(cliente, novo_cliente):
    link = convidar(cliente)
    assert "/definir-palavra-passe#" in link  # token depois do "#": não chega aos registos do servidor

    convidado = novo_cliente()
    info = convidado.post("/api/auth/link", json={"token": token_do_link(link)}).json()
    assert info == {"nome": "Rui Costa", "email": "rui@teste.pt", "empresa": "Empresa de Teste"}

    # Palavra-passe fraca é recusada
    assert aceitar_convite(convidado, link, "curta").status_code == 400
    assert aceitar_convite(convidado, link, "1234567890").status_code == 400

    # Aceitar o convite entra logo na aplicação
    resposta = aceitar_convite(convidado, link)
    assert resposta.status_code == 200
    assert convidado.get("/api/auth/eu").json()["papel"] == "membro"

    # O link só serve uma vez
    assert aceitar_convite(novo_cliente(), link).status_code == 400


def test_link_expirado(cliente, novo_cliente, fabrica_sessoes):
    link = convidar(cliente)
    with fabrica_sessoes() as db:
        db.query(TokenAcesso).update({TokenAcesso.expira_em: datetime.now() - timedelta(minutes=1)})
        db.commit()
    resposta = aceitar_convite(novo_cliente(), link)
    assert resposta.status_code == 400
    assert "já não é válido" in resposta.json()["detail"]


def test_novo_link_anula_o_anterior(cliente, novo_cliente):
    primeiro = convidar(cliente)
    id_membro = cliente.get("/api/equipa").json()[1]["id"]
    segundo = cliente.post(f"/api/equipa/{id_membro}/link").json()["link"]
    assert aceitar_convite(novo_cliente(), primeiro).status_code == 400
    assert aceitar_convite(novo_cliente(), segundo).status_code == 200


def test_email_repetido(cliente):
    convidar(cliente)
    assert cliente.post("/api/equipa", json={"nome": "Outro", "email": "RUI@teste.pt"}).status_code == 409


# ---------- Papéis e desativação ----------

def test_membro_nao_gere_a_equipa(cliente, cliente_anonimo, novo_cliente):
    aceitar_convite(novo_cliente(), convidar(cliente))
    membro = novo_cliente("rui@teste.pt")
    assert membro.get("/api/equipa").status_code == 403
    assert membro.get("/equipa", follow_redirects=False).headers["location"] == "/"
    assert membro.get("/api/faturas").status_code == 200  # mas usa a aplicação normalmente


def test_desativar_corta_o_acesso_logo(cliente, novo_cliente):
    aceitar_convite(novo_cliente(), convidar(cliente))
    membro = novo_cliente("rui@teste.pt")
    id_membro = next(m["id"] for m in cliente.get("/api/equipa").json() if m["email"] == "rui@teste.pt")

    assert cliente.patch(f"/api/equipa/{id_membro}", json={"ativo": False}).status_code == 200
    assert membro.get("/api/faturas").status_code == 401  # a sessão aberta deixou de valer
    resposta = membro.post("/api/auth/entrar", json={"email": "rui@teste.pt", "palavra_passe": PALAVRA_PASSE_TESTE})
    assert "desativada" in resposta.json()["detail"]


def test_admin_nao_se_altera_a_si_proprio(cliente):
    eu = cliente.get("/api/equipa").json()[0]
    assert cliente.patch(f"/api/equipa/{eu['id']}", json={"ativo": False}).status_code == 400


# ---------- Segurança de ficheiros e migração ----------

def test_caminho_do_pdf_nao_sai_da_pasta(fabrica_sessoes):
    assert armazenamento.caminho_pdf("../../app/main.py") is None
    assert armazenamento.caminho_pdf("../dados.db") is None


def test_migracao_nif_unico_por_empresa(tmp_path):
    """Uma base de dados da Fase 4 (NIF único em tudo) passa a aceitar o mesmo NIF em empresas diferentes."""
    motor = create_engine(f"sqlite:///{tmp_path / 'fase4.db'}")
    with motor.begin() as ligacao:
        ligacao.execute(text("CREATE TABLE fornecedores (id INTEGER PRIMARY KEY, nif VARCHAR(9), nome VARCHAR)"))
        ligacao.execute(text("CREATE UNIQUE INDEX ix_fornecedores_nif ON fornecedores (nif)"))
        ligacao.execute(text("CREATE TABLE faturas (id VARCHAR(12) PRIMARY KEY)"))

    migrar(motor)

    with motor.begin() as ligacao:
        ligacao.execute(text("INSERT INTO fornecedores (nif, nome, empresa_id) VALUES ('500000000', 'A', 1)"))
        ligacao.execute(text("INSERT INTO fornecedores (nif, nome, empresa_id) VALUES ('500000000', 'B', 2)"))
    assert "empresa_id" in {c["name"] for c in inspect(motor).get_columns("faturas")}
    motor.dispose()
