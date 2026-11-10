"""
Testes do endpoint de carregamento.

A chamada à IA é substituída por uma função falsa (monkeypatch), por isso os
testes correm sem Internet, sem chave da API e sem gastar créditos.
"""

from app.servicos import extracao_ia
from tests.conftest import criar_pdf_com_qr, criar_pdf_com_texto, criar_pdf_sem_texto, dados_exemplo

TEXTO_FATURA = [
    "Bebidas do Norte, Lda. - NIF 500000000",
    "Fatura FT 2026/123 - Data 2026-10-05",
    "Total sem IVA 100,00 EUR  IVA 23% 23,00 EUR  Total 123,00 EUR",
]


def enviar(cliente, conteudo: bytes, nome="fatura.pdf"):
    return cliente.post("/api/faturas", files={"ficheiro": (nome, conteudo, "application/pdf")})


def test_carregar_fatura_extrai_dados(cliente, monkeypatch):
    textos_recebidos = []

    def ia_falsa(texto):
        textos_recebidos.append(texto)
        return dados_exemplo()

    monkeypatch.setattr(extracao_ia, "extrair_dados", ia_falsa)

    resposta = enviar(cliente, criar_pdf_com_texto(TEXTO_FATURA))

    assert resposta.status_code == 201
    corpo = resposta.json()
    assert corpo["estado_extracao"] == "concluida"
    assert corpo["estado"] == "por rever"
    assert corpo["origem"] == "ia"
    assert corpo["fornecedor"] == "Bebidas do Norte, Lda."
    assert corpo["total_iva"] == 23.0
    assert corpo["alertas"] == []
    # O texto do PDF chegou à IA
    assert "FT 2026/123" in textos_recebidos[0]

    # A fatura ficou na base de dados e o fornecedor foi criado
    assert [f["id"] for f in cliente.get("/api/faturas").json()] == [corpo["id"]]
    assert cliente.get(f"/api/faturas/{corpo['id']}").json()["total"] == 123.0
    assert cliente.get("/api/fornecedores").json()[0]["nome"] == "Bebidas do Norte, Lda."
    # O PDF pode ser aberto
    assert cliente.get(f"/api/faturas/{corpo['id']}/pdf").content.startswith(b"%PDF")


def test_totais_errados_geram_alerta(cliente, monkeypatch):
    monkeypatch.setattr(extracao_ia, "extrair_dados", lambda texto: dados_exemplo(total=150.0))

    corpo = enviar(cliente, criar_pdf_com_texto(TEXTO_FATURA)).json()

    assert corpo["totais_batem"] is False
    assert corpo["alertas"]


def test_pdf_digitalizado_da_mensagem_clara(cliente, monkeypatch):
    def ia_nao_deve_ser_chamada(texto):
        raise AssertionError("A IA não deve ser chamada sem texto")

    monkeypatch.setattr(extracao_ia, "extrair_dados", ia_nao_deve_ser_chamada)

    resposta = enviar(cliente, criar_pdf_sem_texto())

    assert resposta.status_code == 201  # o PDF fica guardado
    corpo = resposta.json()
    assert corpo["estado_extracao"] == "sem_texto"
    assert "digitalização" in corpo["erro"]


def test_erro_da_ia_fica_registado(cliente, monkeypatch):
    def ia_com_erro(texto):
        raise extracao_ia.ErroExtracao("Sem ligação ao serviço de IA.")

    monkeypatch.setattr(extracao_ia, "extrair_dados", ia_com_erro)

    corpo = enviar(cliente, criar_pdf_com_texto(TEXTO_FATURA)).json()

    assert corpo["estado_extracao"] == "erro"
    assert corpo["erro"] == "Sem ligação ao serviço de IA."


def test_fatura_duplicada_e_recusada(cliente, monkeypatch, tmp_path):
    monkeypatch.setattr(extracao_ia, "extrair_dados", lambda texto: dados_exemplo())

    assert enviar(cliente, criar_pdf_com_texto(TEXTO_FATURA)).status_code == 201
    resposta = enviar(cliente, criar_pdf_com_texto(TEXTO_FATURA), nome="copia.pdf")

    assert resposta.status_code == 409
    assert "já foi carregada" in resposta.json()["detail"]
    assert len(cliente.get("/api/faturas").json()) == 1
    # O PDF repetido não fica esquecido na pasta
    assert len(list((tmp_path / "uploads").glob("*.pdf"))) == 1


def test_duplicado_pelo_atcud(cliente, monkeypatch):
    monkeypatch.setattr(extracao_ia, "extrair_dados", lambda texto: dados_exemplo())
    assert enviar(cliente, criar_pdf_com_qr()).status_code == 201
    assert enviar(cliente, criar_pdf_com_qr(), nome="digitalizada-outra-vez.pdf").status_code == 409


def test_rejeita_ficheiro_que_nao_e_pdf(cliente):
    resposta = enviar(cliente, b"isto nao e um pdf", nome="fatura.txt")
    assert resposta.status_code == 400
    assert resposta.json()["detail"] == "Só são aceites ficheiros PDF."


def test_rejeita_pdf_falso(cliente):
    resposta = enviar(cliente, b"isto nao e um pdf", nome="fatura.pdf")
    assert resposta.status_code == 400


def test_rejeita_ficheiro_vazio(cliente):
    assert enviar(cliente, b"").status_code == 400


def test_apagar_fatura(cliente, monkeypatch, tmp_path):
    monkeypatch.setattr(extracao_ia, "extrair_dados", lambda texto: dados_exemplo())
    id_fatura = enviar(cliente, criar_pdf_com_texto(TEXTO_FATURA)).json()["id"]

    assert cliente.delete(f"/api/faturas/{id_fatura}").status_code == 204
    assert cliente.get("/api/faturas").json() == []
    assert list((tmp_path / "uploads").glob("*.pdf")) == []
    assert cliente.delete(f"/api/faturas/{id_fatura}").status_code == 404


def test_id_invalido(cliente):
    assert cliente.get("/api/faturas/nao-e-um-id").status_code == 400
