"""Contabilidade analítica: centros de custo, atribuição às linhas e relatório por centro."""

from tests.conftest import criar_empresa_com_admin, entrar

DE, ATE = "2026-01-01", "2026-12-31"


def _lancar(cliente, conta_gasto: str, valor: float, conta_rendimento: str | None = None) -> dict:
    linhas = ([{"conta": conta_gasto, "debito": valor}, {"conta": "111", "credito": valor}] if not conta_rendimento
              else [{"conta": "111", "debito": valor}, {"conta": conta_rendimento, "credito": valor}])
    r = cliente.post("/api/contabilidade/lancamentos", json={
        "diario": "OD", "data": "2026-03-10", "descricao": "Teste analítico", "linhas": linhas})
    assert r.status_code == 201, r.text
    return r.json()


def _linha(lancamento: dict, prefixo: str) -> int:
    return next(ln["id"] for ln in lancamento["linhas"] if ln["conta"].startswith(prefixo))


def test_centros_e_relatorio(cliente):
    loja = cliente.post("/api/contabilidade/centros", json={"codigo": "loja 1", "nome": "Loja do Porto"}).json()
    assert loja["codigo"] == "LOJA1"
    obra = cliente.post("/api/contabilidade/centros", json={"codigo": "OBRA", "nome": "Obra Rua Nova"}).json()
    assert cliente.post("/api/contabilidade/centros", json={"codigo": "LOJA1", "nome": "x"}).status_code == 400

    renda = _lancar(cliente, "6261", 1000)
    luz = _lancar(cliente, "6242", 300)
    venda = _lancar(cliente, None, 2500, conta_rendimento="721")
    for la, prefixo, centro in ((renda, "6", loja), (venda, "7", loja), (luz, "6", obra)):
        r = cliente.patch(f"/api/contabilidade/linhas/{_linha(la, prefixo)}/centro", json={"centro_id": centro["id"]})
        assert r.status_code == 200, r.text
    # Só gastos e rendimentos têm centro
    r = cliente.patch(f"/api/contabilidade/linhas/{_linha(renda, '111')}/centro", json={"centro_id": loja["id"]})
    assert r.status_code == 400

    rel = cliente.get(f"/api/contabilidade/centros/relatorio?de={DE}&ate={ATE}").json()
    por_codigo = {c["codigo"]: c for c in rel["centros"]}
    assert por_codigo["LOJA1"]["gastos"] == 1000 and por_codigo["LOJA1"]["rendimentos"] == 2500
    assert por_codigo["LOJA1"]["resultado"] == 1500
    assert por_codigo["OBRA"]["gastos"] == 300
    assert rel["total"]["resultado"] == 1200

    # Retirar o centro: o valor passa para "sem centro"
    cliente.patch(f"/api/contabilidade/linhas/{_linha(luz, '6')}/centro", json={"centro_id": None})
    rel = cliente.get(f"/api/contabilidade/centros/relatorio?de={DE}&ate={ATE}").json()
    sem = next(c for c in rel["centros"] if c["codigo"] is None)
    assert sem["gastos"] == 300


def test_estornos_nao_contam_e_centro_desativado(cliente):
    centro = cliente.post("/api/contabilidade/centros", json={"codigo": "A", "nome": "A"}).json()
    la = _lancar(cliente, "6261", 500)
    cliente.patch(f"/api/contabilidade/linhas/{_linha(la, '6')}/centro", json={"centro_id": centro["id"]})
    cliente.post(f"/api/contabilidade/lancamentos/{la['id']}/estornar")
    rel = cliente.get(f"/api/contabilidade/centros/relatorio?de={DE}&ate={ATE}").json()
    assert rel["total"]["gastos"] == 0
    cliente.patch(f"/api/contabilidade/centros/{centro['id']}", json={"ativo": False})
    outro = _lancar(cliente, "6261", 10)
    r = cliente.patch(f"/api/contabilidade/linhas/{_linha(outro, '6')}/centro", json={"centro_id": centro["id"]})
    assert r.status_code == 400


def test_centros_isolados_entre_empresas(cliente, novo_cliente, fabrica_sessoes):
    criar_empresa_com_admin(fabrica_sessoes, "Outra", "outra@teste.pt")
    outra = entrar(novo_cliente(), "outra@teste.pt")
    centro_outra = outra.post("/api/contabilidade/centros", json={"codigo": "X", "nome": "Da outra"}).json()
    assert cliente.get("/api/contabilidade/centros").json() == []
    la = _lancar(cliente, "6261", 100)
    r = cliente.patch(f"/api/contabilidade/linhas/{_linha(la, '6')}/centro", json={"centro_id": centro_outra["id"]})
    assert r.status_code == 400
    linha_outra = _lancar(outra, "6261", 50)
    r = cliente.patch(f"/api/contabilidade/linhas/{_linha(linha_outra, '6')}/centro", json={"centro_id": None})
    assert r.status_code == 400   # linha de outra empresa: não encontrada
