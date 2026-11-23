"""Várias empresas por utilizador: seletor, permissões por empresa e isolamento total dos dados."""

from app.modelos.tabelas import Empresa
from tests.conftest import PALAVRA_PASSE_TESTE, criar_empresa_com_admin, criar_pdf_com_texto, entrar


def _carregar_fatura(cliente, texto: str) -> str:
    pdf = criar_pdf_com_texto([texto])
    r = cliente.post("/api/faturas", files={"ficheiro": (f"{texto}.pdf", pdf, "application/pdf")})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _dar_acesso(admin_cliente, email: str, papel: str = "membro"):
    r = admin_cliente.post("/api/equipa", json={"nome": "Contabilista", "email": email, "papel": papel})
    assert r.status_code == 201, r.text
    return r.json()


def test_contabilista_com_duas_empresas_e_isolamento(cliente, novo_cliente, fabrica_sessoes, empresa_teste):
    """O admin da empresa de teste convida o contabilista de outra empresa: a mesma conta passa a ver as duas."""
    outra = criar_empresa_com_admin(fabrica_sessoes, "Gabinete Silva", "silva@gabinete.pt")
    contabilista = novo_cliente("silva@gabinete.pt")
    fatura_gabinete = _carregar_fatura(contabilista, "gabinete")

    r = _dar_acesso(cliente, "silva@gabinete.pt", "admin")
    assert r["conta_existente"] is True and r["link"] is None

    empresas = contabilista.get("/api/empresas").json()
    assert {e["id"] for e in empresas["empresas"]} == {empresa_teste, outra}
    assert empresas["atual"] == outra
    assert contabilista.get("/api/auth/eu").json()["n_empresas"] == 2

    # Na empresa de teste: só vê as faturas dela
    assert contabilista.post(f"/api/empresas/{empresa_teste}/selecionar").status_code == 200
    assert contabilista.get("/api/auth/eu").json()["empresa"] == "Empresa de Teste"
    assert contabilista.get("/api/faturas").json() == []
    assert contabilista.get(f"/api/faturas/{fatura_gabinete}").status_code == 404
    fatura_teste = _carregar_fatura(contabilista, "teste")
    assert contabilista.get("/api/equipa").status_code == 200  # é admin nesta empresa

    # Outro dispositivo da mesma pessoa continua na sua empresa (a escolha é por sessão)
    telemovel = novo_cliente("silva@gabinete.pt")
    assert [f["id"] for f in telemovel.get("/api/faturas").json()] == [fatura_teste]  # última usada
    telemovel.post(f"/api/empresas/{outra}/selecionar")
    assert [f["id"] for f in telemovel.get("/api/faturas").json()] == [fatura_gabinete]
    assert [f["id"] for f in contabilista.get("/api/faturas").json()] == [fatura_teste]

    # O admin da empresa de teste não vê nada da outra empresa
    assert [f["id"] for f in cliente.get("/api/faturas").json()] == [fatura_teste]
    assert cliente.get(f"/api/faturas/{fatura_gabinete}").status_code == 404


def test_nao_se_escolhe_empresa_sem_acesso(cliente, fabrica_sessoes, empresa_teste):
    estranha = criar_empresa_com_admin(fabrica_sessoes, "Estranha", "dono@estranha.pt")
    assert cliente.post(f"/api/empresas/{estranha}/selecionar").status_code == 404
    assert cliente.post("/api/empresas/999999/selecionar").status_code == 404
    assert cliente.get("/api/auth/eu").json()["empresa"] == "Empresa de Teste"


def test_papel_e_por_empresa(cliente, novo_cliente, fabrica_sessoes, empresa_teste):
    outra = criar_empresa_com_admin(fabrica_sessoes, "Gabinete", "g@gabinete.pt")
    pessoa = novo_cliente("g@gabinete.pt")
    _dar_acesso(cliente, "g@gabinete.pt", "membro")
    pessoa.post(f"/api/empresas/{empresa_teste}/selecionar")
    assert pessoa.get("/api/auth/eu").json()["papel"] == "membro"
    assert pessoa.get("/api/equipa").status_code == 403         # membro na empresa de teste
    pessoa.post(f"/api/empresas/{outra}/selecionar")
    assert pessoa.get("/api/auth/eu").json()["papel"] == "admin"
    assert pessoa.get("/api/equipa").status_code == 200         # admin na sua


def test_desativar_o_acesso_so_afeta_essa_empresa(cliente, novo_cliente, fabrica_sessoes, empresa_teste):
    outra = criar_empresa_com_admin(fabrica_sessoes, "Gabinete", "g@gabinete.pt")
    pessoa = novo_cliente("g@gabinete.pt")
    membro = _dar_acesso(cliente, "g@gabinete.pt")["membro"]
    pessoa.post(f"/api/empresas/{empresa_teste}/selecionar")
    assert cliente.patch(f"/api/equipa/{membro['id']}", json={"ativo": False}).status_code == 200
    # A sessão na empresa de teste termina, mas a conta continua a entrar na sua empresa
    assert pessoa.get("/api/auth/eu").status_code == 401
    outra_sessao = entrar(novo_cliente(), "g@gabinete.pt")
    assert outra_sessao.get("/api/auth/eu").json()["empresa"] == "Gabinete"
    assert [e["id"] for e in outra_sessao.get("/api/empresas").json()["empresas"]] == [outra]
    assert outra_sessao.post(f"/api/empresas/{empresa_teste}/selecionar").status_code == 404


def test_admin_de_uma_empresa_nao_repoe_a_palavra_passe_de_quem_tem_outras(cliente, novo_cliente, fabrica_sessoes,
                                                                          empresa_teste):
    criar_empresa_com_admin(fabrica_sessoes, "Gabinete", "g@gabinete.pt")
    membro = _dar_acesso(cliente, "g@gabinete.pt")["membro"]
    r = cliente.post(f"/api/equipa/{membro['id']}/link")
    assert r.status_code == 403 and "outras empresas" in r.json()["detail"]
    r = cliente.patch(f"/api/equipa/{membro['id']}", json={"desligar_dois_passos": True})
    assert r.status_code == 403
    # A pessoa continua a entrar com a palavra-passe de sempre
    entrar(novo_cliente(), "g@gabinete.pt", PALAVRA_PASSE_TESTE)


def test_gabinete_cria_empresas_cliente(cliente, fabrica_sessoes, empresa_teste):
    assert cliente.post("/api/empresas", json={"nome": "Cliente A", "nif": "503504564"}).status_code == 403
    with fabrica_sessoes() as db:
        db.get(Empresa, empresa_teste).plano = "gabinete"
        db.commit()
    assert cliente.post("/api/empresas", json={"nome": "Cliente A", "nif": "123"}).status_code == 400
    r = cliente.post("/api/empresas", json={"nome": "Cliente A, Lda.", "nif": "503504564"})
    assert r.status_code == 201
    eu = cliente.get("/api/auth/eu").json()
    assert eu["empresa"] == "Cliente A, Lda." and eu["papel"] == "admin" and eu["n_empresas"] == 2
    assert cliente.get("/api/faturas").json() == []
    assert cliente.get("/api/contabilidade/resumo").status_code == 200   # plano de contas criado na primeira visita


def test_limite_do_plano_conta_acessos_desta_empresa(cliente, fabrica_sessoes, empresa_teste):
    with fabrica_sessoes() as db:
        db.get(Empresa, empresa_teste).plano = "essencial"
        db.commit()
    criar_empresa_com_admin(fabrica_sessoes, "Gabinete", "g@gabinete.pt")
    _dar_acesso(cliente, "g@gabinete.pt")
    r = cliente.post("/api/equipa", json={"nome": "Eva", "email": "eva@teste.pt"})
    assert r.status_code == 403 and "2 utilizadores" in r.json()["detail"]
