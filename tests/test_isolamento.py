"""
Testes de isolamento entre empresas: a empresa B nunca vê nem altera nada da empresa A.

É o teste mais importante de uma aplicação com vários clientes: um erro aqui
seria uma fuga de dados.
"""

import io

import pytest
from openpyxl import load_workbook

from app.servicos import extracao_ia
from tests.conftest import criar_empresa_com_admin, criar_pdf_com_qr, criar_pdf_com_texto, dados_exemplo

PDF = criar_pdf_com_texto([
    "Bebidas do Norte, Lda. - NIF 500000000",
    "Fatura FT 2026/123 - Data 2026-10-05",
    "Total sem IVA 100,00 EUR  IVA 23% 23,00 EUR  Total 123,00 EUR",
])


@pytest.fixture
def empresas(cliente, fabrica_sessoes, novo_cliente, monkeypatch):
    """Empresa A (cliente com sessão) com uma fatura; empresa B com outro administrador."""
    monkeypatch.setattr(extracao_ia, "extrair_dados", lambda texto: dados_exemplo())
    fatura = cliente.post("/api/faturas", files={"ficheiro": ("a.pdf", PDF, "application/pdf")}).json()
    criar_empresa_com_admin(fabrica_sessoes, "Empresa B", "admin@empresa-b.pt")
    return cliente, novo_cliente("admin@empresa-b.pt"), fatura["id"]


def test_b_nao_ve_as_faturas_de_a(empresas):
    _, b, id_a = empresas
    assert b.get("/api/faturas").json() == []
    assert b.get("/api/fornecedores").json() == []
    assert b.get(f"/api/faturas/{id_a}").status_code == 404  # 404 e não 403: nem revela que existe
    assert b.get(f"/api/faturas/{id_a}/pdf").status_code == 404


def test_b_nao_altera_as_faturas_de_a(empresas):
    a, b, id_a = empresas
    edicao = dados_exemplo().model_dump(exclude={"confianca"}) | {"fornecedor": "Alterado por B"}
    assert b.put(f"/api/faturas/{id_a}", json=edicao).status_code == 404
    assert b.patch(f"/api/faturas/{id_a}/pagamento", json={"paga": True}).status_code == 404
    assert b.post(f"/api/faturas/{id_a}/extrair").status_code == 404
    assert b.delete(f"/api/faturas/{id_a}").status_code == 404

    # A fatura de A ficou exatamente como estava
    fatura = a.get(f"/api/faturas/{id_a}").json()
    assert fatura["fornecedor"] == "Bebidas do Norte, Lda."
    assert fatura["paga"] is False


def test_dashboard_e_exportacao_so_de_b(empresas):
    _, b, _ = empresas
    assert b.get("/api/dashboard").json()["faturas_mes"] == 0
    folha = load_workbook(io.BytesIO(b.get("/api/faturas/exportar").content)).active
    assert folha.max_row == 1  # só o cabeçalho


def test_a_mesma_fatura_pode_existir_em_duas_empresas(empresas):
    """Os duplicados só se procuram dentro da mesma empresa."""
    _, b, _ = empresas
    assert b.post("/api/faturas", files={"ficheiro": ("a.pdf", PDF, "application/pdf")}).status_code == 201


def test_cache_de_fornecedores_e_de_cada_empresa(empresas, monkeypatch):
    a, b, id_a = empresas
    # A confirma o nome do fornecedor 500000000 (fica na cache de A)
    edicao = dados_exemplo().model_dump(exclude={"confianca"}) | {"confirmar": True}
    assert a.put(f"/api/faturas/{id_a}", json=edicao).status_code == 200

    # B carrega uma fatura (só QR) do mesmo NIF: não pode receber o nome guardado por A
    def ia_falha(texto):
        raise extracao_ia.ErroExtracao("sem IA")

    monkeypatch.setattr(extracao_ia, "extrair_dados", ia_falha)
    fatura_b = b.post("/api/faturas", files={"ficheiro": ("q.pdf", criar_pdf_com_qr(), "application/pdf")}).json()
    assert fatura_b["origem"] == "qr"
    assert fatura_b["fornecedor"] == ""


def test_pdfs_em_pastas_separadas(empresas, tmp_path):
    _, b, _ = empresas
    b.post("/api/faturas", files={"ficheiro": ("b.pdf", PDF, "application/pdf")})
    pastas = sorted(p.name for p in (tmp_path / "uploads").iterdir() if p.is_dir())
    assert pastas == ["empresa-1", "empresa-2"]


def test_equipa_de_outra_empresa(empresas):
    a, b, _ = empresas
    id_admin_a = a.get("/api/equipa").json()[0]["id"]
    assert b.patch(f"/api/equipa/{id_admin_a}", json={"ativo": False}).status_code == 404
    assert b.post(f"/api/equipa/{id_admin_a}/link").status_code == 404
    assert [m["email"] for m in b.get("/api/equipa").json()] == ["admin@empresa-b.pt"]
