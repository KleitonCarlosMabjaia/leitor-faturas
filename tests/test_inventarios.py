"""Inventários: contagens, inventário inicial e apuramento do CMVMC (sistema de inventário periódico)."""

import io

from openpyxl import Workbook

from tests.conftest import criar_empresa_com_admin
from tests.test_contabilidade import criar_fatura


def contagem(cliente, data, linhas, esperado=201):
    resposta = cliente.post("/api/contabilidade/inventarios", json={"data": data, "linhas": linhas})
    assert resposta.status_code == esperado, resposta.text
    return resposta.json()


def saldos(cliente, ate="2026-12-31") -> dict[str, float]:
    linhas = cliente.get("/api/contabilidade/balancete", params={"de": "2026-01-01", "ate": ate}).json()["linhas"]
    return {ln["codigo"]: round(ln["saldo_devedor"] - ln["saldo_credor"], 2) for ln in linhas}


def preparar_compras(cliente, fabrica_sessoes, empresa_teste):
    """Compras do trimestre: 100 € de bebidas (mercadorias), 200 € de carne (matérias) e uma devolução de 10 €."""
    for fornecedor, nif, numero, base in (("Bebidas do Norte, Lda.", "500000000", "FT 1", 100.0),
                                          ("Talho Central, Lda.", "508000009", "FT 2", 200.0)):
        id_fatura = criar_fatura(fabrica_sessoes, empresa_teste, fornecedor=fornecedor, numero_fatura=numero,
                                 nif_fornecedor=nif,
                                 data_emissao="2026-02-10", valor_sem_iva=base, total=base * 1.06,
                                 iva=[{"taxa": 6, "base": base, "valor": round(base * 0.06, 2)}])
        assert cliente.post(f"/api/contabilidade/faturas/{id_fatura}/contabilizar", json={}).status_code == 201
    r = cliente.post("/api/contabilidade/lancamentos", json={
        "diario": "COM", "data": "2026-03-05", "descricao": "Devolução de bebidas",
        "linhas": [{"conta": "121", "debito": 10}, {"conta": "3171", "credito": 10}]})
    assert r.status_code == 201, r.text


def test_inventario_inicial_e_apuramento_do_cmvmc(cliente, fabrica_sessoes, empresa_teste):
    inicial = contagem(cliente, "2026-01-01", [
        {"codigo": "B1", "descricao": "Vinho tinto", "quantidade": 10, "custo_unitario": 5, "categoria": "M"},
        {"codigo": "C1", "descricao": "Carne", "quantidade": 20, "custo_unitario": 2, "categoria": "P"},
    ])
    assert (inicial["mercadorias"], inicial["materias"]) == (50.0, 40.0)
    r = cliente.post(f"/api/contabilidade/inventarios/{inicial['id']}/inicial")
    assert r.status_code == 201, r.text
    preparar_compras(cliente, fabrica_sessoes, empresa_teste)

    final = contagem(cliente, "2026-03-31", [
        {"codigo": "B1", "descricao": "Vinho tinto", "quantidade": 12, "custo_unitario": 5, "categoria": "M"},
        {"codigo": "C1", "descricao": "Carne", "quantidade": 15, "custo_unitario": 2, "categoria": "P"},
    ])
    preview = cliente.get(f"/api/contabilidade/inventarios/{final['id']}").json()["apuramento"]
    cat = {c["categoria"]: c for c in preview["categorias"]}
    # Mercadorias: 50 + (100 - 10) - 60 = 80 ; Matérias: 40 + 200 - 30 = 210
    conta = lambda c: (cat[c]["inicial"], cat[c]["compras_liquidas"], cat[c]["final"], cat[c]["cmvmc"])  # noqa: E731
    assert conta("M") == (50, 90, 60, 80)
    assert conta("P") == (40, 200, 30, 210)

    r = cliente.post(f"/api/contabilidade/inventarios/{final['id']}/apurar")
    assert r.status_code == 201, r.text
    s = saldos(cliente)
    assert s.get("311", 0) == 0 and s.get("312", 0) == 0 and s.get("3171", 0) == 0  # compras a zero
    assert (s["321"], s["331"]) == (60.0, 30.0)                                       # inventário final
    assert (s["611"], s["612"]) == (80.0, 210.0)                                      # CMVMC
    assert cliente.get("/api/contabilidade/balancete").json()["equilibrado"]
    resultados = cliente.get("/api/contabilidade/resultados", params={"de": "2026-01-01", "ate": "2026-12-31"}).json()
    assert next(r for r in resultados["rubricas"] if r["rubrica"] == "Compras e CMVMC")["valor"] == 290.0

    # Lançado: não se apura duas vezes, nem se altera ou apaga sem estornar
    url = f"/api/contabilidade/inventarios/{final['id']}"
    assert cliente.post(f"{url}/apurar").status_code == 409
    assert cliente.delete(url).status_code == 409
    alterar = {"data": "2026-03-31", "linhas": [{"descricao": "X", "quantidade": 1, "custo_unitario": 1}]}
    assert cliente.put(url, json=alterar).status_code == 409
    lancamento_id = r.json()["id"]
    assert cliente.post(f"/api/contabilidade/lancamentos/{lancamento_id}/estornar").status_code == 201
    assert cliente.put(url, json=alterar).status_code == 200


def test_inventarios_lancam_se_por_ordem(cliente):
    primeiro = contagem(cliente, "2026-03-31", [{"descricao": "A", "quantidade": 1, "custo_unitario": 10}])
    segundo = contagem(cliente, "2026-06-30", [{"descricao": "A", "quantidade": 2, "custo_unitario": 10}])
    r = cliente.post(f"/api/contabilidade/inventarios/{segundo['id']}/apurar")
    assert r.status_code == 409 and "31/03/2026" in r.json()["detail"]
    preview = cliente.get(f"/api/contabilidade/inventarios/{segundo['id']}").json()["apuramento"]
    assert any("ainda não foi lançado" in a for a in preview["avisos"])
    # Duas contagens na mesma data: recusado
    contagem(cliente, "2026-03-31", [{"descricao": "B", "quantidade": 1, "custo_unitario": 1}], esperado=422)
    assert primeiro["total"] == 10.0


def test_importa_contagem_de_csv_e_excel(cliente):
    csv = ("Código;Descrição;Família;Qtd;Preço unitário\n"
           "V1;Vinho verde;Bebidas;24;3,20\n"
           "M1;Farinha;Matérias-primas;10;0,85\n"
           ";Total;;;\n").encode("cp1252")
    r = cliente.post("/api/contabilidade/inventarios/importar", data={"data": "2026-06-30"},
                     files={"ficheiro": ("contagem.csv", csv, "text/csv")})
    assert r.status_code == 201, r.text
    assert (r.json()["mercadorias"], r.json()["materias"], r.json()["artigos"]) == (76.8, 8.5, 2)

    livro = Workbook()
    folha = livro.active
    folha.append(["Referência", "Designação", "Quantidade", "Valor total"])
    folha.append(["A1", "Azeite 5 L", 4, 60.0])
    saida = io.BytesIO()
    livro.save(saida)
    r = cliente.post("/api/contabilidade/inventarios/importar", data={"data": "2026-07-31", "categoria": "P"},
                     files={"ficheiro": ("contagem.xlsx", saida.getvalue(), "application/octet-stream")})
    assert r.status_code == 201, r.text
    assert (r.json()["materias"], r.json()["mercadorias"]) == (60.0, 0.0)

    r = cliente.post("/api/contabilidade/inventarios/importar", data={"data": "2026-08-31"},
                     files={"ficheiro": ("x.csv", b"a;b\n1;2\n", "text/csv")})
    assert r.status_code == 422 and "colunas" in r.json()["detail"]


def test_inventarios_isolados_entre_empresas(cliente, fabrica_sessoes, novo_cliente):
    inv = contagem(cliente, "2026-03-31", [{"descricao": "A", "quantidade": 1, "custo_unitario": 10}])
    criar_empresa_com_admin(fabrica_sessoes, "Outra", "admin@outra.pt")
    outro = novo_cliente("admin@outra.pt")
    assert outro.get(f"/api/contabilidade/inventarios/{inv['id']}").status_code == 404
    assert outro.post(f"/api/contabilidade/inventarios/{inv['id']}/apurar").status_code == 404
    assert outro.get("/api/contabilidade/inventarios").json()["inventarios"] == []
