"""Testes do dashboard, da exportação, dos pagamentos, dos dados de demonstração e da migração."""

import csv
import io
from datetime import date

import pytest
from openpyxl import load_workbook
from sqlalchemy import create_engine, inspect, text

from app import demo
from app.db import migrar
from app.modelos.tabelas import Fatura
from app.servicos import estatisticas, extracao_ia, qr
from tests.conftest import criar_pdf_com_texto, dados_exemplo

HOJE = date(2026, 11, 11)  # data fixa: os testes dão sempre o mesmo resultado


@pytest.fixture
def com_demo(fabrica_sessoes, empresa_teste):
    """30 faturas de demonstração na empresa de teste (a do cliente com sessão iniciada)."""
    with fabrica_sessoes() as db:
        demo.criar_dados_demo(db, empresa_id=empresa_teste, hoje=HOJE)
    return fabrica_sessoes


# ---------- Dados de demonstração ----------

def test_demo_cria_30_faturas_coerentes(com_demo):
    with com_demo() as db:
        faturas = db.query(Fatura).all()
        assert len(faturas) == 30
        assert all(f.totais_batem and f.nif_valido for f in faturas)
        assert sum(f.estado == "por rever" for f in faturas) == 4
        assert len({f.fornecedor_id for f in faturas}) == 7


def test_pdf_de_demonstracao_tem_qr_legivel(com_demo):
    with com_demo() as db:
        fatura = db.query(Fatura).first()
        conteudo = (demo.armazenamento.caminho_pdf(fatura.ficheiro)).read_bytes()
    dados_qr = qr.ler_qr(conteudo)
    assert dados_qr["total"] == fatura.total
    assert dados_qr["numero_fatura"] == fatura.numero_fatura


# ---------- Dashboard ----------

def test_dashboard_bate_com_as_faturas(com_demo, empresa_teste):
    with com_demo() as db:
        resumo = estatisticas.resumo(db, empresa_teste, "2026-10", hoje=HOJE)
        outubro = [f for f in db.query(Fatura).all() if f.data_emissao.strftime("%Y-%m") == "2026-10"]

    assert resumo["total_mes"] == round(sum(f.total for f in outubro), 2)
    assert resumo["faturas_mes"] == len(outubro)
    assert round(sum(l["total"] for l in resumo["por_fornecedor"]), 2) == resumo["total_mes"]
    assert len(resumo["evolucao"]) == 12
    assert resumo["evolucao"][-1]["mes"] == "2026-10"
    assert resumo["por_pagar"]["vencidas"] == 2  # as duas deixadas por pagar de propósito


def test_dashboard_vazio(cliente):
    corpo = cliente.get("/api/dashboard").json()
    assert corpo["total_mes"] == 0
    assert corpo["por_fornecedor"] == []
    assert corpo["por_pagar"]["quantidade"] == 0


def test_dashboard_mes_invalido(cliente):
    assert cliente.get("/api/dashboard?mes=2026-1").status_code == 422


# ---------- Pagamentos ----------

def test_marcar_como_paga(cliente, monkeypatch):
    monkeypatch.setattr(extracao_ia, "extrair_dados", lambda texto: dados_exemplo())
    pdf = criar_pdf_com_texto(["Bebidas do Norte, Lda. - NIF 500000000", "Fatura FT 2026/123 de 2026-10-05",
                               "Total sem IVA 100,00 IVA 23,00 Total 123,00 EUR"])
    id_fatura = cliente.post("/api/faturas", files={"ficheiro": ("f.pdf", pdf, "application/pdf")}).json()["id"]

    assert cliente.get("/api/dashboard").json()["por_pagar"]["quantidade"] == 1
    corpo = cliente.patch(f"/api/faturas/{id_fatura}/pagamento", json={"paga": True}).json()
    assert corpo["paga"] is True
    assert cliente.get("/api/dashboard").json()["por_pagar"]["quantidade"] == 0


# ---------- Exportação ----------

def test_exportar_csv(cliente, com_demo):
    resposta = cliente.get("/api/faturas/exportar?formato=csv&mes=2026-10")

    assert resposta.status_code == 200
    assert "attachment" in resposta.headers["content-disposition"]
    texto = resposta.content.decode("utf-8")
    assert texto.startswith("﻿")  # BOM para o Excel reconhecer os acentos
    linhas = list(csv.reader(io.StringIO(texto.lstrip("﻿")), delimiter=";"))
    assert linhas[0][:3] == ["Data de emissão", "Fornecedor", "NIF"]
    assert "Base 6%" in linhas[0] and "Total" in linhas[0]
    assert all(linha[0].endswith("/10/2026") for linha in linhas[1:])
    assert "," in linhas[1][-1] and "." not in linhas[1][-1]  # vírgula decimal


def test_exportar_excel(cliente, com_demo):
    resposta = cliente.get("/api/faturas/exportar?formato=xlsx")

    folha = load_workbook(io.BytesIO(resposta.content)).active
    cabecalho = [c.value for c in folha[1]]
    assert cabecalho[0] == "Data de emissão"
    assert folha.max_row == 30 + 2  # cabeçalho + 30 faturas + totais
    ultima = [c.value for c in folha[folha.max_row]]
    assert ultima[0] == "Total"
    assert ultima[-1].startswith("=SUBTOTAL(9,")
    assert folha.freeze_panes == "A2"


def test_exportacao_neutraliza_formulas(cliente, monkeypatch):
    # Um fornecedor "malicioso" cujo nome é uma fórmula
    monkeypatch.setattr(extracao_ia, "extrair_dados", lambda texto: dados_exemplo(fornecedor="=HYPERLINK(\"x\")"))
    pdf = criar_pdf_com_texto(["Bebidas do Norte, Lda. - NIF 500000000", "Fatura FT 2026/123 de 2026-10-05",
                               "Total sem IVA 100,00 IVA 23,00 Total 123,00 EUR"])
    cliente.post("/api/faturas", files={"ficheiro": ("f.pdf", pdf, "application/pdf")})

    folha = load_workbook(io.BytesIO(cliente.get("/api/faturas/exportar").content)).active
    assert folha["B2"].value == "'=HYPERLINK(\"x\")"
    assert folha["B2"].data_type == "s"  # texto, não fórmula


# ---------- Migração ----------

def test_migracao_acrescenta_coluna_paga(tmp_path):
    motor = create_engine(f"sqlite:///{tmp_path / 'antiga.db'}")
    with motor.begin() as ligacao:
        ligacao.execute(text("CREATE TABLE faturas (id VARCHAR(12) PRIMARY KEY)"))
        ligacao.execute(text("INSERT INTO faturas (id) VALUES ('abc')"))

    migrar(motor)
    migrar(motor)  # correr duas vezes não pode dar erro

    assert "paga" in {c["name"] for c in inspect(motor).get_columns("faturas")}
    with motor.connect() as ligacao:
        assert ligacao.execute(text("SELECT paga FROM faturas")).scalar() == 0
    motor.dispose()
