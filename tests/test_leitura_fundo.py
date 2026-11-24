"""Leitura das faturas em segundo plano (lotes grandes): guardar já, ler depois, acompanhar o estado."""

from app.servicos import extracao_ia, leitura_fundo
from tests.conftest import criar_empresa_com_admin, criar_pdf_com_qr, criar_pdf_com_texto, dados_exemplo
from tests.test_carregamento import TEXTO_FATURA


def enviar_fundo(cliente, conteudo: bytes, nome="fatura.pdf"):
    return cliente.post("/api/faturas", params={"fundo": "true"},
                        files={"ficheiro": (nome, conteudo, "application/pdf")})


def estados(cliente, ids):
    return {e["id"]: e for e in cliente.get("/api/faturas/estado", params={"ids": ",".join(ids)}).json()}


def test_lote_guardado_ja_e_lido_depois(cliente, fabrica_sessoes, monkeypatch):
    numeros = iter(["FT 2026/1", "FT 2026/2"])
    monkeypatch.setattr(extracao_ia, "extrair_dados", lambda texto: dados_exemplo(numero_fatura=next(numeros)))
    r1 = enviar_fundo(cliente, criar_pdf_com_texto(TEXTO_FATURA))
    r2 = enviar_fundo(cliente, criar_pdf_com_texto(TEXTO_FATURA + ["Segunda fatura"]), "segunda.pdf")
    assert r1.status_code == 201 and r1.json()["estado_extracao"] == "pendente"
    ids = [r1.json()["id"], r2.json()["id"]]
    assert {e["estado_extracao"] for e in estados(cliente, ids).values()} == {"pendente"}
    # Na lista aparecem logo, por ler
    assert {f["id"] for f in cliente.get("/api/faturas").json()} == set(ids)

    with fabrica_sessoes() as db:
        assert leitura_fundo.processar_pendentes(db) == {"concluida": 2}
    depois = estados(cliente, ids)
    assert all(e["estado_extracao"] == "concluida" for e in depois.values())
    assert depois[ids[0]]["fornecedor"] == "Bebidas do Norte, Lda." and depois[ids[0]]["total"] == 123.0


def test_repetida_e_apagada_e_comunicada(cliente, fabrica_sessoes):
    original = cliente.post("/api/faturas", files={"ficheiro": ("a.pdf", criar_pdf_com_qr(), "application/pdf")})
    assert original.json()["estado_extracao"] == "concluida"
    copia = enviar_fundo(cliente, criar_pdf_com_qr(), "copia.pdf").json()
    with fabrica_sessoes() as db:
        assert leitura_fundo.processar_pendentes(db) == {"repetida": 1}
    e = estados(cliente, [copia["id"]])[copia["id"]]
    assert e["estado_extracao"] == "repetida" and e["erro"]
    assert [f["id"] for f in cliente.get("/api/faturas").json()] == [original.json()["id"]]


def test_erro_inesperado_nao_para_a_fila(cliente, fabrica_sessoes, monkeypatch):
    monkeypatch.setattr(extracao_ia, "extrair_dados", lambda texto: dados_exemplo())
    ma = enviar_fundo(cliente, criar_pdf_com_texto(TEXTO_FATURA)).json()
    boa = enviar_fundo(cliente, criar_pdf_com_qr(), "boa.pdf").json()
    from app.servicos import processamento
    original = processamento.processar

    def falha_na_primeira(db, fatura, conteudo):
        if fatura.id == ma["id"]:
            raise RuntimeError("PDF estranho")
        return original(db, fatura, conteudo)

    monkeypatch.setattr(processamento, "processar", falha_na_primeira)
    with fabrica_sessoes() as db:
        assert leitura_fundo.processar_pendentes(db) == {"erro": 1, "concluida": 1}
    e = estados(cliente, [ma["id"], boa["id"]])
    assert e[ma["id"]]["estado_extracao"] == "erro" and "Ler novamente" in e[ma["id"]]["erro"]
    assert e[boa["id"]]["estado_extracao"] == "concluida"


def test_estado_so_da_propria_empresa(cliente, novo_cliente, fabrica_sessoes):
    f = enviar_fundo(cliente, criar_pdf_com_qr()).json()
    criar_empresa_com_admin(fabrica_sessoes, "Outra", "outra@teste.pt")
    outra = novo_cliente("outra@teste.pt")
    assert outra.get("/api/faturas/estado", params={"ids": f["id"]}).json()[0]["estado_extracao"] == "desconhecida"
    assert cliente.get("/api/faturas/estado", params={"ids": "../../etc,xx"}).json() == []
