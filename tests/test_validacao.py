"""Testes da validação do NIF e da verificação dos totais."""

import pytest

from app.servicos.validacao import nif_valido, totais_batem, verificar
from tests.conftest import dados_exemplo


@pytest.mark.parametrize("nif", ["500000000", "123456789", "999999990", "450000001"])
def test_nif_valido(nif):
    assert nif_valido(nif)


@pytest.mark.parametrize(
    "nif, motivo",
    [
        ("123456780", "dígito de controlo errado"),
        ("12345678", "só 8 dígitos"),
        ("1234567890", "10 dígitos"),
        ("12345678A", "tem uma letra"),
        ("", "vazio"),
        ("400000000", "prefixo 4 não atribuído (só 45)"),
        ("000000000", "prefixo 0 não atribuído"),
    ],
)
def test_nif_invalido(nif, motivo):
    assert not nif_valido(nif), motivo


def test_totais_certos():
    assert totais_batem(dados_exemplo())


def test_totais_com_varias_taxas():
    dados = dados_exemplo(
        valor_sem_iva=150.00,
        iva=[
            {"taxa": 23, "base": 100.00, "valor": 23.00},
            {"taxa": 6, "base": 50.00, "valor": 3.00},
        ],
        total=176.00,
    )
    assert totais_batem(dados)


@pytest.mark.parametrize("total, esperado", [(123.02, True), (122.98, True), (123.03, False), (120.00, False)])
def test_tolerancia_de_dois_centimos(total, esperado):
    assert totais_batem(dados_exemplo(total=total)) is esperado


def test_verificar_gera_alertas():
    resultado = verificar(dados_exemplo(nif_fornecedor="123456780", total=130.00, confianca="baixa"))
    assert resultado["nif_valido"] is False
    assert resultado["totais_batem"] is False
    assert resultado["diferenca_totais"] == 7.00
    assert len(resultado["alertas"]) == 3


def test_nif_com_prefixo_pt_e_limpo():
    assert dados_exemplo(nif_fornecedor="PT 500 000 000").nif_fornecedor == "500000000"


def test_data_invalida_e_rejeitada():
    with pytest.raises(ValueError):
        dados_exemplo(data_emissao="05/10/2026")
