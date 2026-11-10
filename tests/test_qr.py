"""Testes da leitura do QR da AT e da combinação com a IA."""

import pytest

from app.servicos import extracao_ia, qr
from app.servicos.processamento import combinar
from tests.conftest import QR_EXEMPLO, criar_pdf_com_qr, criar_pdf_sem_texto, dados_exemplo


def test_interpretar_qr_simples():
    dados = qr.interpretar(QR_EXEMPLO)
    assert dados["nif_fornecedor"] == "500000000"
    assert dados["numero_fatura"] == "FT 2026/123"
    assert dados["atcud"] == "ABCD1234-123"
    assert dados["data_emissao"] == "2026-10-05"
    assert dados["iva"] == [{"taxa": 23.0, "base": 100.0, "valor": 23.0}]
    assert dados["valor_sem_iva"] == 100.0
    assert dados["total"] == 123.0


def test_interpretar_qr_com_varias_taxas_e_isento():
    texto = (
        "A:500000000*B:999999990*C:PT*D:FT*E:N*F:20261005*G:FT A/1*H:0"
        "*I1:PT*I2:10.00*I3:50.00*I4:3.00*I5:20.00*I6:2.60*I7:100.00*I8:23.00"
        "*N:28.60*O:208.60*Q:ab12*R:9999"
    )
    dados = qr.interpretar(texto)
    assert [linha["taxa"] for linha in dados["iva"]] == [0.0, 6.0, 13.0, 23.0]
    assert dados["valor_sem_iva"] == 180.0
    assert dados["total"] == 208.60


def test_interpretar_qr_madeira():
    texto = "A:500000000*B:999999990*C:PT*D:FT*E:N*F:20261005*G:FT M/1*H:0*I1:PT-MA*I7:100.00*I8:22.00*O:122.00"
    assert qr.interpretar(texto)["iva"] == [{"taxa": 22.0, "base": 100.0, "valor": 22.0}]


@pytest.mark.parametrize(
    "texto",
    [
        "isto não é um QR da AT",
        "A:500000000*B:999999990*D:FT*G:FT 1/1*O:10.00",               # falta a data (F)
        "A:500000000*B:999999990*D:FT*F:2026-10-05*G:FT 1/1*O:10.00",  # data no formato errado
    ],
)
def test_qr_invalido(texto):
    with pytest.raises(qr.QRInvalido):
        qr.interpretar(texto)


def test_ler_qr_de_um_pdf():
    dados = qr.ler_qr(criar_pdf_com_qr())
    assert dados is not None
    assert dados["total"] == 123.0


def test_pdf_sem_qr_devolve_none():
    assert qr.ler_qr(criar_pdf_sem_texto()) is None


def test_combinar_qr_manda_nos_valores():
    # A IA leu mal o total; o QR corrige-o e o nome vem da IA
    dados_ia = dados_exemplo(total=999.0, confianca="media")
    dados = combinar(qr.interpretar(QR_EXEMPLO), dados_ia)
    assert dados.total == 123.0
    assert dados.fornecedor == "Bebidas do Norte, Lda."
    assert dados.data_vencimento == "2026-11-04"
    assert dados.confianca == "alta"


def test_combinar_so_com_qr():
    dados = combinar(qr.interpretar(QR_EXEMPLO), None)
    assert dados.fornecedor == ""
    assert dados.total == 123.0
    assert dados.confianca == "media"


def test_pdf_digitalizado_com_qr_e_lido(cliente, monkeypatch):
    def ia_nao_deve_ser_chamada(texto):
        raise AssertionError("Sem texto, a IA não deve ser chamada")

    monkeypatch.setattr(extracao_ia, "extrair_dados", ia_nao_deve_ser_chamada)

    resposta = cliente.post(
        "/api/faturas", files={"ficheiro": ("digitalizada.pdf", criar_pdf_com_qr(), "application/pdf")}
    )

    corpo = resposta.json()
    assert resposta.status_code == 201
    assert corpo["estado_extracao"] == "concluida"
    assert corpo["origem"] == "qr"
    assert corpo["total"] == 123.0
    assert corpo["atcud"] == "ABCD1234-123"
    assert corpo["totais_batem"] is True
    assert any("digitalizado" in alerta for alerta in corpo["alertas"])
    assert "Falta o nome do fornecedor." in corpo["alertas"]
