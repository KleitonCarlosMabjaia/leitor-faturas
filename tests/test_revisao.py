"""Testes da revisão (editar/confirmar), da cache de fornecedores e dos filtros."""

from app.servicos import extracao_ia
from tests.conftest import criar_pdf_com_qr, criar_pdf_com_texto, dados_exemplo, qr_com_numero


def pdf_texto(referencia: str = "") -> bytes:
    """PDF com texto suficiente para não ser tratado como digitalização."""
    return criar_pdf_com_texto([
        "Bebidas do Norte, Lda. - NIF 500000000",
        f"Fatura de teste {referencia} - Data 2026-10-05",
        "Total sem IVA 100,00 EUR  IVA 23% 23,00 EUR  Total 123,00 EUR",
    ])


def enviar(cliente, conteudo: bytes, nome="fatura.pdf"):
    return cliente.post("/api/faturas", files={"ficheiro": (nome, conteudo, "application/pdf")})


def edicao(**alteracoes) -> dict:
    corpo = dados_exemplo().model_dump(exclude={"confianca"})
    corpo.update(alteracoes)
    return corpo


def ia_proibida(texto):
    raise AssertionError("A IA não devia ter sido chamada")


def ia_sem_creditos(texto):
    raise extracao_ia.ErroExtracao("A conta da Anthropic não tem créditos.")


# ---------- Editar e confirmar ----------

def test_confirmar_fatura(cliente, monkeypatch):
    monkeypatch.setattr(extracao_ia, "extrair_dados", lambda texto: dados_exemplo(total=150.0))
    id_fatura = enviar(cliente, pdf_texto()).json()["id"]

    # O utilizador corrige o total e confirma
    resposta = cliente.put(f"/api/faturas/{id_fatura}", json=edicao(total=123.0, confirmar=True))

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["estado"] == "confirmada"
    assert corpo["total"] == 123.0
    assert corpo["totais_batem"] is True
    assert corpo["alertas"] == []


def test_guardar_sem_confirmar_fica_por_rever(cliente, monkeypatch):
    monkeypatch.setattr(extracao_ia, "extrair_dados", lambda texto: dados_exemplo())
    id_fatura = enviar(cliente, pdf_texto()).json()["id"]

    corpo = cliente.put(f"/api/faturas/{id_fatura}", json=edicao(fornecedor="")).json()

    assert corpo["estado"] == "por rever"
    assert "Falta o nome do fornecedor." in corpo["alertas"]


def test_nao_confirma_sem_fornecedor(cliente, monkeypatch):
    monkeypatch.setattr(extracao_ia, "extrair_dados", lambda texto: dados_exemplo())
    id_fatura = enviar(cliente, pdf_texto()).json()["id"]

    resposta = cliente.put(f"/api/faturas/{id_fatura}", json=edicao(fornecedor=" ", confirmar=True))

    assert resposta.status_code == 422
    assert "fornecedor" in resposta.json()["detail"]


def test_edicao_com_data_invalida_e_recusada(cliente, monkeypatch):
    monkeypatch.setattr(extracao_ia, "extrair_dados", lambda texto: dados_exemplo())
    id_fatura = enviar(cliente, pdf_texto()).json()["id"]

    assert cliente.put(f"/api/faturas/{id_fatura}", json=edicao(data_emissao="2026-02-30")).status_code == 422


def test_edicao_nao_pode_criar_duplicado(cliente, monkeypatch):
    numeros = iter(["FT 1", "FT 2"])
    monkeypatch.setattr(extracao_ia, "extrair_dados", lambda texto: dados_exemplo(numero_fatura=next(numeros)))
    enviar(cliente, pdf_texto())
    id_segunda = enviar(cliente, pdf_texto()).json()["id"]

    resposta = cliente.put(f"/api/faturas/{id_segunda}", json=edicao(numero_fatura="FT 1"))

    assert resposta.status_code == 409


# ---------- Cache de fornecedores ----------

def test_fornecedor_confirmado_dispensa_a_ia(cliente, monkeypatch):
    # 1.ª fatura: só QR (a IA falha), o nome fica por preencher
    monkeypatch.setattr(extracao_ia, "extrair_dados", ia_sem_creditos)
    primeira = enviar(cliente, criar_pdf_com_qr(qr_com_numero("FT 2026/1", "AAAA-1"))).json()
    assert primeira["origem"] == "qr"
    assert primeira["fornecedor"] == ""

    # O utilizador escreve o nome e confirma -> fica guardado na cache
    cliente.put(f"/api/faturas/{primeira['id']}", json=edicao(
        fornecedor="Bebidas do Norte, Lda.", numero_fatura="FT 2026/1", confirmar=True,
    ))

    # 2.ª fatura do mesmo NIF: o nome vem da cache e a IA nem é chamada
    monkeypatch.setattr(extracao_ia, "extrair_dados", ia_proibida)
    segunda = enviar(cliente, criar_pdf_com_qr(qr_com_numero("FT 2026/2", "AAAA-2"))).json()

    assert segunda["origem"] == "qr+cache"
    assert segunda["fornecedor"] == "Bebidas do Norte, Lda."
    assert segunda["confianca"] == "alta"
    assert segunda["alertas"] == []


def test_ler_novamente(cliente, monkeypatch):
    monkeypatch.setattr(extracao_ia, "extrair_dados", ia_sem_creditos)
    id_fatura = enviar(cliente, pdf_texto()).json()["id"]

    # Depois de haver créditos, "Ler" funciona e a fatura não é vista como duplicada de si própria
    monkeypatch.setattr(extracao_ia, "extrair_dados", lambda texto: dados_exemplo())
    resposta = cliente.post(f"/api/faturas/{id_fatura}/extrair")

    assert resposta.status_code == 200
    assert resposta.json()["estado_extracao"] == "concluida"


# ---------- Filtros ----------

def test_filtros(cliente, monkeypatch):
    faturas = iter([
        dados_exemplo(numero_fatura="FT 1", data_emissao="2026-09-10"),
        dados_exemplo(numero_fatura="FT 2", data_emissao="2026-10-05"),
        dados_exemplo(numero_fatura="FT 3", data_emissao="2026-10-20",
                      fornecedor="Talho Central", nif_fornecedor="123456789"),
    ])
    monkeypatch.setattr(extracao_ia, "extrair_dados", lambda texto: next(faturas))
    ids = [enviar(cliente, pdf_texto(str(i))).json()["id"] for i in range(3)]
    cliente.put(f"/api/faturas/{ids[0]}", json=edicao(numero_fatura="FT 1", data_emissao="2026-09-10", confirmar=True))

    def numeros(consulta):
        return sorted(f["numero_fatura"] for f in cliente.get(f"/api/faturas?{consulta}").json())

    talho = next(f for f in cliente.get("/api/fornecedores").json() if f["nome"] == "Talho Central")

    assert numeros("mes=2026-10") == ["FT 2", "FT 3"]
    assert numeros(f"fornecedor_id={talho['id']}") == ["FT 3"]
    assert numeros("estado=confirmada") == ["FT 1"]
    assert numeros("estado=por rever&mes=2026-10") == ["FT 2", "FT 3"]
    assert cliente.get("/api/faturas?mes=2026-13").status_code == 422
