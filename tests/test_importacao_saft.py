"""Migração por SAF-T (PT) da contabilidade: exportar de uma empresa e importar noutra dá o mesmo balancete."""

from datetime import date

import pytest

from app import demo
from app.modelos.tabelas import Empresa
from app.servicos.contabilidade import importacao_saft, relatorios, saft
from tests.conftest import criar_empresa_com_admin, entrar

HOJE = date(2026, 11, 11)


@pytest.fixture
def saft_da_demonstracao(fabrica_sessoes, empresa_teste) -> bytes:
    """O SAF-T de um ano completo de contabilidade (a demonstração), como viria de outro programa."""
    with fabrica_sessoes() as db:
        demo.criar_dados_demo(db, empresa_id=empresa_teste, hoje=HOJE)
        empresa = db.get(Empresa, empresa_teste)
        empresa.nif, empresa.morada = "507842545", "Rua A, 1"
        empresa.codigo_postal, empresa.localidade = "1000-001", "Lisboa"
        db.commit()
        return saft.gerar(db, empresa, date(2026, 1, 1), HOJE)


def _saldos(db, empresa_id: int) -> dict[str, float]:
    linhas = relatorios.balancete(db, empresa_id, date(2026, 1, 1), HOJE)["linhas"]
    return {ln["codigo"]: round(ln["saldo_devedor"] - ln["saldo_credor"], 2) for ln in linhas
            if ln.get("movimento") and (ln["saldo_devedor"] or ln["saldo_credor"])}


def test_migracao_completa_reproduz_o_balancete(fabrica_sessoes, empresa_teste, saft_da_demonstracao):
    nova = criar_empresa_com_admin(fabrica_sessoes, "Empresa Migrada", "migrada@teste.pt")
    with fabrica_sessoes() as db:
        destino = db.get(Empresa, nova)
        previa = importacao_saft.importar(db, destino, saft_da_demonstracao, simular=True)
        assert previa["problemas"] == [] and previa["importado"] is False
        assert previa["lancamentos"] > 50 and previa["contas_novas"] > 0
        assert any("→" in d for d in previa["diarios"])
        db.rollback()

        resultado = importacao_saft.importar(db, destino, saft_da_demonstracao, simular=False)
        db.commit()
        assert resultado["importado"] is True
        origem, migrada = _saldos(db, empresa_teste), _saldos(db, nova)
        assert migrada == origem   # o mesmo balancete, conta a conta, ao cêntimo

        # Importar o mesmo período outra vez é recusado
        de_novo = importacao_saft.importar(db, destino, saft_da_demonstracao, simular=True)
        assert any("já foi importado" in p for p in de_novo["problemas"])


def test_empresa_com_lancamentos_no_periodo_nao_duplica(fabrica_sessoes, empresa_teste, saft_da_demonstracao):
    """Importar o SAF-T para a própria empresa (ou outra já com movimentos) duplicaria tudo: é recusado."""
    with fabrica_sessoes() as db:
        empresa = db.get(Empresa, empresa_teste)
        previa = importacao_saft.importar(db, empresa, saft_da_demonstracao, simular=False)
        assert previa["importado"] is False
        assert any("duplicaria" in p for p in previa["problemas"])


def test_nif_diferente_e_ficheiros_invalidos(fabrica_sessoes, empresa_teste, saft_da_demonstracao):
    nova = criar_empresa_com_admin(fabrica_sessoes, "Outra", "outra@teste.pt")
    with fabrica_sessoes() as db:
        destino = db.get(Empresa, nova)
        destino.nif = "503504564"
        previa = importacao_saft.importar(db, destino, saft_da_demonstracao, simular=True)
        assert any("NIF 507842545" in p for p in previa["problemas"])
        invalidos = (
            (b"nao e xml", "XML"),
            (b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><x/>', "válido"),
            (b"<AuditFile><Header><TaxAccountingBasis>F</TaxAccountingBasis></Header></AuditFile>", "faturação"),
        )
        for conteudo, mensagem in invalidos:
            with pytest.raises(importacao_saft.ErroImportacao, match=mensagem):
                importacao_saft.importar(db, destino, conteudo, simular=True)


def test_rota_de_importacao(cliente_anonimo, fabrica_sessoes, empresa_teste, saft_da_demonstracao):
    criar_empresa_com_admin(fabrica_sessoes, "Destino", "destino@teste.pt")
    cliente = entrar(cliente_anonimo, "destino@teste.pt")
    ficheiro = {"ficheiro": ("saft.xml", saft_da_demonstracao, "application/xml")}
    r = cliente.post("/api/contabilidade/importar-saft", files=ficheiro, data={"simular": "true"})
    assert r.status_code == 200 and r.json()["importado"] is False
    r = cliente.post("/api/contabilidade/importar-saft", files=ficheiro, data={"simular": "false"})
    assert r.status_code == 200 and r.json()["importado"] is True
    assert cliente.get("/api/contabilidade/balancete?de=2026-01-01&ate=2026-11-11").json()["linhas"]


def test_diarios_de_outros_programas():
    assert importacao_saft.diario_do_dobra("VEN") == "VEN"
    assert importacao_saft.diario_do_dobra("VENDAS") == "VEN"
    assert importacao_saft.diario_do_dobra("Compras") == "COM"
    assert importacao_saft.diario_do_dobra("BANCOS-CGD") == "BAN"
    assert importacao_saft.diario_do_dobra("51") == "OD"
