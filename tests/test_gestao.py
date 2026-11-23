"""Área de gestão: artigos e stock (ligados às compras, vendas e inventários), clientes, tesouraria e orçamento."""

from datetime import date, timedelta

from app.modelos.tabelas import ESTADO_CONFIRMADA, Fatura, FaturaVenda
from tests.conftest import criar_empresa_com_admin, entrar


def _artigo(cliente, codigo="A1", **extra) -> dict:
    r = cliente.post("/api/gestao/artigos", json={"codigo": codigo, "nome": f"Artigo {codigo}", "tipo": "M",
                                                  "preco_venda": 10, "stock_minimo": 5, **extra})
    assert r.status_code == 201, r.text
    return r.json()


def _fatura(fabrica_sessoes, empresa_id: int, valor: float = 100.0, paga: bool = False,
            vencimento: date | None = None) -> str:
    with fabrica_sessoes() as db:
        f = Fatura(id=f"f{int(valor * 100):06d}"[:12], empresa_id=empresa_id, ficheiro="x.pdf", nome_original="x.pdf",
                   tamanho_kb=1, estado_extracao="concluida", estado=ESTADO_CONFIRMADA, paga=paga,
                   nome_fornecedor="Fornecedor", nif_fornecedor="507842545", numero_fatura="FT 1/1",
                   data_emissao=date.today(), data_vencimento=vencimento or date.today() + timedelta(days=10),
                   valor_sem_iva=valor, total_iva=round(valor * 0.23, 2), total=round(valor * 1.23, 2))
        db.add(f)
        db.commit()
        return f.id


def _venda(fabrica_sessoes, empresa_id: int, total: float = 123.0, vencimento: date | None = None,
           numero: str = "FT A/1") -> int:
    with fabrica_sessoes() as db:
        v = FaturaVenda(empresa_id=empresa_id, numero=numero, tipo="FT", data=date.today(),
                        data_vencimento=vencimento, cliente_nif="503504564", cliente_nome="Cliente Bom, Lda.",
                        linhas=[], valor_sem_iva=round(total / 1.23, 2), total_iva=round(total - total / 1.23, 2),
                        total=total)
        db.add(v)
        db.commit()
        return v.id


def test_artigos_e_custo_medio(cliente):
    a = _artigo(cliente, "vinho 1")
    assert a["codigo"] == "VINHO 1"
    assert cliente.post("/api/gestao/artigos", json={"codigo": "VINHO 1", "nome": "x"}).status_code == 400
    for qtd, custo in ((10, 4.0), (10, 6.0)):
        r = cliente.post("/api/gestao/movimentos", json={"artigo_id": a["id"], "tipo": "entrada", "quantidade": qtd,
                                                         "custo_unitario": custo})
        assert r.status_code == 201, r.text
    art = next(x for x in cliente.get("/api/gestao/artigos").json()["artigos"] if x["id"] == a["id"])
    assert art["quantidade"] == 20 and art["custo_medio"] == 5.0 and art["valor"] == 100.0
    # Saída ao custo médio; não pode sair mais do que há
    r = cliente.post("/api/gestao/movimentos", json={"artigo_id": a["id"], "tipo": "saida", "quantidade": 17})
    assert r.status_code == 201 and r.json()["valor"] == -85.0
    r = cliente.post("/api/gestao/movimentos", json={"artigo_id": a["id"], "tipo": "saida", "quantidade": 4})
    assert r.status_code == 400 and "insuficiente" in r.json()["detail"]
    # Acerto de contagem: 3 em stock, contaram-se 2
    r = cliente.post("/api/gestao/movimentos", json={"artigo_id": a["id"], "tipo": "ajuste", "quantidade": 2})
    assert r.status_code == 201 and r.json()["quantidade"] == -1
    art = next(x for x in cliente.get("/api/gestao/artigos").json()["artigos"] if x["id"] == a["id"])
    assert art["quantidade"] == 2 and art["abaixo_minimo"] is True


def test_servicos_sem_stock(cliente):
    s = _artigo(cliente, "S1", tipo="S")
    r = cliente.post("/api/gestao/movimentos", json={"artigo_id": s["id"], "tipo": "entrada", "quantidade": 1,
                                                     "custo_unitario": 1})
    assert r.status_code == 400


def test_entrada_por_fatura_nao_passa_do_valor(cliente, fabrica_sessoes, empresa_teste):
    a = _artigo(cliente)
    fatura_id = _fatura(fabrica_sessoes, empresa_teste, 100.0)
    origens = cliente.get("/api/gestao/stock/origens").json()
    assert any(f["id"] == fatura_id and f["por_dar_entrada"] == 100.0 for f in origens["faturas"])
    r = cliente.post("/api/gestao/stock/entrada-fatura", json={
        "fatura_id": fatura_id, "linhas": [{"artigo_id": a["id"], "quantidade": 20, "valor_unitario": 4}]})
    assert r.status_code == 201, r.text
    r = cliente.post("/api/gestao/stock/entrada-fatura", json={
        "fatura_id": fatura_id, "linhas": [{"artigo_id": a["id"], "quantidade": 10, "valor_unitario": 4}]})
    assert r.status_code == 400 and "20,00 €" in r.json()["detail"]
    origens = cliente.get("/api/gestao/stock/origens").json()
    assert next(f for f in origens["faturas"] if f["id"] == fatura_id)["por_dar_entrada"] == 20.0


def test_saida_por_venda_e_margens(cliente, fabrica_sessoes, empresa_teste):
    a = _artigo(cliente)
    cliente.post("/api/gestao/movimentos", json={"artigo_id": a["id"], "tipo": "entrada", "quantidade": 10,
                                                 "custo_unitario": 4})
    venda_id = _venda(fabrica_sessoes, empresa_teste)
    r = cliente.post("/api/gestao/stock/saida-venda", json={
        "venda_id": venda_id, "linhas": [{"artigo_id": a["id"], "quantidade": 3, "valor_unitario": 10}]})
    assert r.status_code == 201, r.text
    # Só uma vez por fatura
    r = cliente.post("/api/gestao/stock/saida-venda", json={
        "venda_id": venda_id, "linhas": [{"artigo_id": a["id"], "quantidade": 1}]})
    assert r.status_code == 400
    m = cliente.get("/api/gestao/margens").json()
    assert m["vendas"] == 30 and m["custo"] == 12 and m["margem"] == 18 and m["margem_percentual"] == 60.0


def test_stock_passa_para_inventario_da_contabilidade(cliente):
    a = _artigo(cliente)
    cliente.post("/api/gestao/movimentos", json={"artigo_id": a["id"], "tipo": "entrada", "quantidade": 8,
                                                 "custo_unitario": 2.5, "data": "2026-03-01"})
    r = cliente.post("/api/gestao/stock/inventario", json={"data": "2026-03-31"})
    assert r.status_code == 201, r.text
    assert r.json()["valor"] == 20.0
    inventarios = cliente.get("/api/contabilidade/inventarios").json()
    assert any(i["data"] == "2026-03-31" for i in inventarios["inventarios"])
    # Repetir a mesma data não duplica
    assert cliente.post("/api/gestao/stock/inventario", json={"data": "2026-03-31"}).status_code == 400


def test_clientes_e_tesouraria(cliente, fabrica_sessoes, empresa_teste):
    eid = empresa_teste
    hoje = date.today()
    _venda(fabrica_sessoes, eid, 246.0, vencimento=hoje - timedelta(days=12), numero="FT A/1")
    _venda(fabrica_sessoes, eid, 123.0, vencimento=hoje + timedelta(days=20), numero="FT A/2")
    _fatura(fabrica_sessoes, eid, 50.0, vencimento=hoje + timedelta(days=3))
    clientes = cliente.get("/api/gestao/clientes").json()
    c = clientes[0]
    assert c["nif"] == "503504564" and c["em_divida"] == 369.0 and c["vencido"] == 246.0 and c["dias_atraso"] == 12
    docs = cliente.get("/api/gestao/clientes/503504564").json()
    assert len(docs) == 2

    t = cliente.get("/api/gestao/tesouraria").json()
    assert t["recebimentos_em_atraso"] == 246.0
    tipos = {m["tipo"] for m in t["movimentos"]}
    assert {"recebimento", "pagamento"} <= tipos
    assert t["saldo_final"] == round(t["saldo_inicial"] + 369.0 - 61.5, 2)
    painel = cliente.get("/api/gestao/painel").json()
    assert painel["receber"]["vencido"] == 246.0
    assert any("vencidas" in a["texto"] for a in painel["alertas"])


def test_orcamento_vs_real(cliente):
    ano = date.today().year
    cliente.post("/api/contabilidade/lancamentos", json={
        "diario": "OD", "data": f"{ano}-01-15", "descricao": "Venda", "linhas": [
            {"conta": "111", "debito": 1000}, {"conta": "721", "credito": 1000}]})
    valores = {"vendas": [800.0] * 12, "fse": [100.0] * 12}
    r = cliente.put("/api/gestao/orcamento", json={"ano": ano, "valores": valores})
    assert r.status_code == 200, r.text
    vendas = next(x for x in r.json()["rubricas"] if x["chave"] == "vendas")
    assert vendas["real"][0] == 1000 and vendas["orcado"][0] == 800
    assert cliente.put("/api/gestao/orcamento", json={"ano": ano, "valores": {"x": [0] * 12}}).status_code == 400
    assert cliente.put("/api/gestao/orcamento", json={"ano": ano, "valores": {"fse": [1] * 11}}).status_code == 400
    proposta = cliente.get(f"/api/gestao/orcamento/proposta?ano={ano + 1}&crescimento=10").json()
    assert proposta["vendas"][0] == 1100.0


def test_gestao_isolada_entre_empresas(cliente, novo_cliente, fabrica_sessoes):
    a = _artigo(cliente)
    criar_empresa_com_admin(fabrica_sessoes, "Outra", "outra@teste.pt")
    outra = entrar(novo_cliente(), "outra@teste.pt")
    assert outra.get("/api/gestao/artigos").json()["artigos"] == []
    r = outra.post("/api/gestao/movimentos", json={"artigo_id": a["id"], "tipo": "entrada", "quantidade": 1,
                                                   "custo_unitario": 1})
    assert r.status_code == 404
    assert outra.put(f"/api/gestao/artigos/{a['id']}", json={"codigo": "X", "nome": "X"}).status_code == 404
    assert outra.get("/api/gestao/clientes").json() == []


def test_pagina_gestao(cliente):
    r = cliente.get("/gestao")
    assert r.status_code == 200 and "gestao.js" in r.text
