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


def _centros_das_linhas(lancamento: dict) -> dict[str, int | None]:
    return {ln["conta"]: ln["centro_id"] for ln in lancamento["linhas"]}


def test_centro_ao_contabilizar_fica_memorizado_para_o_fornecedor(cliente, fabrica_sessoes, empresa_teste):
    from tests.test_contabilidade import criar_fatura

    loja = cliente.post("/api/contabilidade/centros", json={"codigo": "LOJA", "nome": "Loja"}).json()
    obra = cliente.post("/api/contabilidade/centros", json={"codigo": "OBRA", "nome": "Obra"}).json()
    f1, f2, f3, f4 = (criar_fatura(fabrica_sessoes, empresa_teste, numero_fatura=f"FT 2026/{n}") for n in range(1, 5))

    # Sem nada memorizado, a proposta não sugere centro
    assert cliente.get(f"/api/contabilidade/faturas/{f1}").json()["proposta"]["centro"] is None
    la = cliente.post(f"/api/contabilidade/faturas/{f1}/contabilizar", json={"centro_custo_id": loja["id"]}).json()
    centros_la = _centros_das_linhas(la)
    gasto = next(c for c in centros_la if c.startswith(("6", "31")))
    assert centros_la[gasto] == loja["id"]
    assert all(v is None for c, v in centros_la.items() if c != gasto)   # IVA e fornecedor sem centro

    # A fatura seguinte do mesmo fornecedor sugere a loja; sem escolha, usa-a
    propostas = {p["fatura"]["id"]: p for p in cliente.get("/api/contabilidade/por-contabilizar").json()}
    assert propostas[f2]["centro"] == {"id": loja["id"], "codigo": "LOJA", "nome": "Loja"}
    la2 = cliente.post(f"/api/contabilidade/faturas/{f2}/contabilizar", json={}).json()
    assert _centros_das_linhas(la2)[gasto] == loja["id"]

    # Em lote: o escolhido no cartão prevalece e passa a ser o memorizado; null tira o centro
    r = cliente.post("/api/contabilidade/contabilizar-lote", json={"ids": [f3, f4],
                                                                   "centros": {f3: obra["id"], f4: None}}).json()
    assert r["contabilizadas"] == 2
    la3, la4 = (cliente.get(f"/api/contabilidade/faturas/{f}").json()["lancamentos"][0] for f in (f3, f4))
    assert _centros_das_linhas(la3)[gasto] == obra["id"]
    assert _centros_das_linhas(la4)[gasto] is None


def test_centro_desativado_nao_se_atribui_nem_se_sugere(cliente, fabrica_sessoes, empresa_teste):
    from tests.test_contabilidade import criar_fatura

    centro = cliente.post("/api/contabilidade/centros", json={"codigo": "VELHO", "nome": "Antigo"}).json()
    f1, f2 = (criar_fatura(fabrica_sessoes, empresa_teste, numero_fatura=f"FT 2026/{n}") for n in (1, 2))
    assert cliente.post(f"/api/contabilidade/faturas/{f1}/contabilizar",
                        json={"centro_custo_id": centro["id"]}).status_code == 201
    cliente.patch(f"/api/contabilidade/centros/{centro['id']}", json={"ativo": False})
    assert cliente.get(f"/api/contabilidade/faturas/{f2}").json()["proposta"]["centro"] is None
    r = cliente.post(f"/api/contabilidade/faturas/{f2}/contabilizar", json={"centro_custo_id": centro["id"]})
    assert r.status_code == 409 and "desativado" in r.json()["detail"]
    assert cliente.post(f"/api/contabilidade/faturas/{f2}/contabilizar", json={"centro_custo_id": 999999}
                        ).status_code == 409
