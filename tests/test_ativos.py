"""Ativos fixos: depreciações por quotas constantes (duodécimos), abate, alienação e comparação com a contabilidade."""

from tests.conftest import criar_empresa_com_admin


def registar(cliente, **dados):
    corpo = {"descricao": "Computador portátil", "conta_ativo": "435", "data_aquisicao": "2026-03-15",
             "valor_aquisicao": 1200, "taxa": 33.33, **dados}
    resposta = cliente.post("/api/contabilidade/ativos", json=corpo)
    assert resposta.status_code == 201, resposta.text
    return resposta.json()


def depreciar(cliente, ate, esperado=201):
    resposta = cliente.post("/api/contabilidade/ativos/depreciar", json={"ate": ate})
    assert resposta.status_code == esperado, resposta.text
    return resposta.json()


def linhas(lancamento) -> dict[str, tuple[float, float]]:
    r: dict[str, tuple[float, float]] = {}
    for ln in lancamento["linhas"]:
        d, c = r.get(ln["conta"], (0.0, 0.0))
        r[ln["conta"]] = (round(d + ln["debito"], 2), round(c + ln["credito"], 2))
    return r


def ativo(cliente, codigo, ano=2026):
    lista = cliente.get("/api/contabilidade/ativos", params={"ano": ano}).json()["ativos"]
    return next(a for a in lista if a["codigo"] == codigo)


def test_duodecimos_desde_o_mes_de_entrada_e_sem_repetir_meses(cliente):
    a = registar(cliente)
    assert a["codigo"] == "AF0001" and a["depreciacao_mensal"] == 33.33

    # Março (mês da entrada em funcionamento) a junho: 4 meses
    la = depreciar(cliente, "2026-06-30")
    assert linhas(la) == {"6425": (133.32, 0.0), "4385": (0.0, 133.32)}
    assert la["data"] == "2026-06-30"
    depreciar(cliente, "2026-06-15", esperado=409)  # o mês de junho já está feito

    depreciar(cliente, "2026-12-31")  # julho a dezembro: 6 meses
    a = ativo(cliente, "AF0001")
    assert (a["depreciacao_ano"], a["acumulada"], a["valor_liquido"]) == (333.30, 333.30, 866.70)
    assert a["depreciado_ate"] == "2026-12-31"


def test_nunca_deprecia_mais_do_que_o_valor(cliente):
    registar(cliente, descricao="Programa de faturação", conta_ativo="443", data_aquisicao="2026-01-10",
             valor_aquisicao=120, taxa=100)
    la = depreciar(cliente, "2027-06-30")
    assert linhas(la)["6433"] == (120.0, 0.0)  # 12 meses, não 18
    assert ativo(cliente, "AF0001", 2026)["depreciado_ate"] == "2026-12-31"
    depreciar(cliente, "2027-12-31", esperado=409)


def test_estornar_devolve_os_meses(cliente):
    registar(cliente)
    la = depreciar(cliente, "2026-06-30")
    assert cliente.post(f"/api/contabilidade/lancamentos/{la['id']}/estornar").status_code == 201
    assert ativo(cliente, "AF0001")["acumulada"] == 0
    assert linhas(depreciar(cliente, "2026-06-30"))["6425"] == (133.32, 0.0)


def test_abate_deprecia_ate_a_saida_e_estorno_repoe(cliente):
    registar(cliente)
    depreciar(cliente, "2026-12-31")
    r = cliente.post("/api/contabilidade/ativos/1/saida", json={"data": "2027-02-15"})
    assert r.status_code == 201, r.text
    # Janeiro e fevereiro de 2027 foram depreciados antes do abate: 333,30 + 66,66
    assert linhas(r.json()) == {"4385": (399.96, 0.0), "6873": (800.04, 0.0), "435": (0.0, 1200.0)}
    assert ativo(cliente, "AF0001", 2027)["estado"] == "abatido"
    assert cliente.post("/api/contabilidade/ativos/1/saida", json={"data": "2027-03-01"}).status_code == 409

    assert cliente.post(f"/api/contabilidade/lancamentos/{r.json()['id']}/estornar").status_code == 201
    assert ativo(cliente, "AF0001", 2027)["estado"] == "em_uso"


def test_alienacao_com_iva_e_ganho(cliente):
    registar(cliente)
    depreciar(cliente, "2026-12-31")
    r = cliente.post("/api/contabilidade/ativos/1/saida",
                     json={"data": "2026-12-31", "valor_venda": 1000, "taxa_iva": 23})
    assert r.status_code == 201, r.text
    # Valor líquido 866,70; vendido por 1000 + 230 de IVA: ganho de 133,30
    assert linhas(r.json()) == {"121": (1230.0, 0.0), "4385": (333.3, 0.0), "435": (0.0, 1200.0),
                                "24333": (0.0, 230.0), "7871": (0.0, 133.3)}
    assert ativo(cliente, "AF0001")["estado"] == "alienado"


def test_compara_a_ficha_com_a_contabilidade_e_valida(cliente):
    registar(cliente)
    diferencas = cliente.get("/api/contabilidade/ativos").json()["diferencas"]
    assert diferencas == [{"conta": "435", "nome": "Equipamento administrativo", "contabilidade": 0.0,
                           "registados": 1200.0}]
    cliente.post("/api/contabilidade/lancamentos", json={
        "diario": "COM", "data": "2026-03-15", "descricao": "Compra do computador",
        "linhas": [{"conta": "435", "debito": 1200}, {"conta": "121", "credito": 1200}]})
    assert cliente.get("/api/contabilidade/ativos").json()["diferencas"] == []

    invalido = {"descricao": "Terreno", "conta_ativo": "431", "data_aquisicao": "2026-01-01",
                "valor_aquisicao": 1, "taxa": 1}
    assert cliente.post("/api/contabilidade/ativos", json=invalido).status_code == 422
    sem_taxa = {**invalido, "conta_ativo": "435", "taxa": 0}
    assert cliente.post("/api/contabilidade/ativos", json=sem_taxa).status_code == 422


def test_ativos_isolados_entre_empresas(cliente, fabrica_sessoes, novo_cliente):
    registar(cliente)
    criar_empresa_com_admin(fabrica_sessoes, "Outra", "admin@outra.pt")
    outro = novo_cliente("admin@outra.pt")
    assert outro.post("/api/contabilidade/ativos/1/saida", json={"data": "2026-12-31"}).status_code == 404
    assert outro.get("/api/contabilidade/ativos").json()["ativos"] == []
    assert outro.post("/api/contabilidade/ativos/depreciar", json={"ate": "2026-12-31"}).status_code == 409
