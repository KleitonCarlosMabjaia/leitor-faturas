"""Diferimentos, acréscimos, balanço, saldos de abertura, autoliquidação do IVA e inventário para a AT."""

from tests.test_contabilidade import criar_fatura, linhas_por_conta
from tests.test_fecho import lancar, saldos


def test_diferimento_de_seguro_anual(cliente):
    lancar(cliente, "2025-10-01", "Seguro anual", [{"conta": "6263", "debito": 1200},
                                                   {"conta": "121", "credito": 1200}])
    r = cliente.post("/api/contabilidade/periodizacao/diferimentos", json={
        "tipo": "gasto", "descricao": "Seguro multirriscos", "conta": "6263", "valor": 1200, "inicio": "2025-10-01",
        "meses": 12})
    assert r.status_code == 201, r.text
    assert r.json()["mensal"] == 100 and r.json()["fim"] == "2026-09-30"
    assert saldos(cliente, "2025-01-01", "2025-12-31")["281"] == 1200

    la = cliente.post("/api/contabilidade/periodizacao/reconhecer", json={"ate": "2025-12-15"})
    assert la.status_code == 201, la.text
    assert la.json()["data"] == "2025-12-31"
    assert linhas_por_conta(la.json()) == {"6263": (300.0, 0.0), "281": (0.0, 300.0)}
    s = saldos(cliente, "2025-01-01", "2025-12-31")
    assert (s["6263"], s["281"]) == (300, 900)  # só os 3 meses de 2025 ficam em gasto
    assert cliente.post("/api/contabilidade/periodizacao/reconhecer", json={"ate": "2025-12-31"}).status_code == 409

    # Até ao fim: os 9 meses que faltam
    cliente.post("/api/contabilidade/periodizacao/reconhecer", json={"ate": "2027-01-31"})
    d = cliente.get("/api/contabilidade/periodizacao").json()["diferimentos"][0]
    assert (d["meses_reconhecidos"], d["reconhecido"], d["por_reconhecer"]) == (12, 1200, 0)


def test_diferimento_reparte_os_centimos_no_ultimo_mes(cliente):
    cliente.post("/api/contabilidade/periodizacao/diferimentos", json={
        "tipo": "rendimento", "descricao": "Contrato de assistência", "conta": "721", "valor": 1000,
        "inicio": "2025-01-01", "meses": 3})
    a = cliente.post("/api/contabilidade/periodizacao/reconhecer", json={"ate": "2025-02-28"}).json()
    assert linhas_por_conta(a) == {"282": (666.66, 0.0), "721": (0.0, 666.66)}
    b = cliente.post("/api/contabilidade/periodizacao/reconhecer", json={"ate": "2025-03-31"}).json()
    assert linhas_por_conta(b)["721"] == (0.0, 333.34)


def test_acrescimo_de_rendimento_com_reversao(cliente):
    r = cliente.post("/api/contabilidade/periodizacao/acrescimos", json={
        "tipo": "rendimento", "data": "2025-12-31", "descricao": "Juros de dezembro a receber",
        "linhas": [{"conta": "7911", "valor": 40}]})
    assert r.status_code == 201, r.text
    assert linhas_por_conta(r.json()) == {"2721": (40.0, 0.0), "7911": (0.0, 40.0)}
    lista = cliente.get("/api/contabilidade/periodizacao").json()["acrescimos"]
    assert lista[0]["reversao"] == "2026-01-01" and lista[0]["tipo"] == "rendimento"
    assert saldos(cliente, "2025-01-01", "2026-12-31").get("2721", 0) == 0
    erro = cliente.post("/api/contabilidade/periodizacao/acrescimos", json={
        "tipo": "gasto", "data": "2025-12-31", "descricao": "X", "linhas": [{"conta": "7911", "valor": 1}]})
    assert erro.status_code == 409 and "classe 6" in erro.json()["detail"]


def test_balanco_equilibrado(cliente):
    lancar(cliente, "2025-01-02", "Capital", [{"conta": "121", "debito": 5000}, {"conta": "511", "credito": 5000}])
    lancar(cliente, "2025-06-01", "Serviços 2025", [{"conta": "121", "debito": 500}, {"conta": "721", "credito": 500}])
    lancar(cliente, "2026-02-01", "Computador", [{"conta": "435", "debito": 2000}, {"conta": "2781", "credito": 2000}])
    lancar(cliente, "2026-03-01", "Serviços 2026", [{"conta": "121", "debito": 1000},
                                                    {"conta": "721", "credito": 1000}])
    lancar(cliente, "2026-03-02", "Seguro", [{"conta": "281", "debito": 300}, {"conta": "121", "credito": 300}])
    b = cliente.get("/api/contabilidade/balanco", params={"ate": "2026-06-30"}).json()
    rubricas = {(s["chave"], r["rubrica"]): r["valor"] for s in b["secoes"] for r in s["rubricas"]}
    assert rubricas[("ativo_nao_corrente", "Ativos fixos tangíveis")] == 2000
    assert rubricas[("ativo_corrente", "Caixa e depósitos bancários")] == 6200
    assert rubricas[("ativo_corrente", "Diferimentos")] == 300
    assert rubricas[("capital_proprio", "Resultados transitados")] == 500       # lucro de 2025 por transferir
    assert rubricas[("capital_proprio", "Resultado líquido do período")] == 1000
    assert rubricas[("passivo_corrente", "Outras dívidas a pagar")] == 2000
    assert (b["total_ativo"], b["total_capital_proprio_passivo"], b["diferenca"]) == (8500, 8500, 0)


def test_saldos_de_abertura_por_ficheiro(cliente):
    csv = ("Conta;Nome;Débito;Crédito\n1;Meios financeiros;7000;0\n12;Depósitos;7000;0\n121;Banco;7000;0\n"
           "22110001;Fornecedor Antigo, Lda.;0;2000\n511;Capital;0;5000\n").encode()
    r = cliente.post("/api/contabilidade/abertura/importar", data={"data": "2026-01-01"},
                     files={"ficheiro": ("balancete.csv", csv, "text/csv")})
    assert r.status_code == 201, r.text
    assert linhas_por_conta(r.json()) == {"121": (7000.0, 0.0), "22110001": (0.0, 2000.0), "511": (0.0, 5000.0)}
    contas = {c["codigo"]: c for c in cliente.get("/api/contabilidade/contas").json()}
    assert contas["22110001"]["nome"] == "Fornecedor Antigo, Lda." and contas["22110001"]["movimento"]
    assert cliente.get("/api/contabilidade/abertura").json()["lancamento"]["origem"] == "abertura"
    # Só uma abertura; e só contas de balanço
    assert cliente.post("/api/contabilidade/abertura", json={
        "data": "2026-01-01", "saldos": [{"conta": "121", "debito": 1}, {"conta": "511", "credito": 1}]}
    ).status_code == 409
    cliente.post(f"/api/contabilidade/lancamentos/{r.json()['id']}/estornar")
    erro = cliente.post("/api/contabilidade/abertura", json={
        "data": "2026-01-01", "saldos": [{"conta": "121", "debito": 1}, {"conta": "6262", "credito": 1}]})
    assert erro.status_code == 409 and "classes 1 a 5" in erro.json()["detail"]


def test_servicos_intracomunitarios_autoliquidados(cliente, fabrica_sessoes, empresa_teste):
    id_fatura = criar_fatura(fabrica_sessoes, empresa_teste, fornecedor="Google Ireland Limited",
                             nif_fornecedor="IE6388047V", numero_fatura="5123", iva=[], valor_sem_iva=100.0,
                             total=100.0, data_emissao="2026-02-10")
    proposta = cliente.get(f"/api/contabilidade/faturas/{id_fatura}").json()["proposta"]
    assert proposta["classificacao"]["regime_iva"] == "intracomunitario_servicos"
    r = cliente.post(f"/api/contabilidade/faturas/{id_fatura}/contabilizar", json={"conta_codigo": "6222"})
    assert r.status_code == 201, r.text
    linhas = linhas_por_conta(r.json())
    assert linhas["6222"] == (100.0, 0.0) and linhas["24323"] == (23.0, 0.0) and linhas["24335"] == (0.0, 23.0)
    assert [c for c, (_, cr) in linhas.items() if cr == 100.0][0].startswith("2211")

    mapa = cliente.get("/api/contabilidade/iva", params={"de": "2026-02-01", "ate": "2026-02-28"}).json()
    servicos = next(x for x in mapa["autoliquidado"] if x["campo_base"] == "16")
    assert (servicos["base"], servicos["imposto"]) == (100, 23)
    assert mapa["saldo"] == 0  # liquidado e deduzido na mesma declaração


def test_autoliquidacao_na_construcao_civil(cliente, fabrica_sessoes, empresa_teste):
    id_fatura = criar_fatura(fabrica_sessoes, empresa_teste, fornecedor="Obras Já, Lda.", iva=[],
                             valor_sem_iva=1000.0, total=1000.0, data_emissao="2026-02-10")
    r = cliente.post(f"/api/contabilidade/faturas/{id_fatura}/contabilizar",
                     json={"conta_codigo": "6226", "regime_iva": "autoliquidacao"})
    assert r.status_code == 201, r.text
    mapa = cliente.get("/api/contabilidade/iva", params={"de": "2026-02-01", "ate": "2026-02-28"}).json()
    normal = next(x for x in mapa["liquidado"] if x["campo_base"] == "3")
    assert (normal["base"], normal["imposto"]) == (1000, 230)
    assert mapa["quadro_06a"]["base"] == 1000
    # Fatura com IVA do fornecedor não admite autoliquidação
    outra = criar_fatura(fabrica_sessoes, empresa_teste, numero_fatura="FT 9/9", data_emissao="2026-02-11")
    erro = cliente.post(f"/api/contabilidade/faturas/{outra}/contabilizar", json={"regime_iva": "autoliquidacao"})
    assert erro.status_code == 409


def test_ficheiro_do_inventario_para_a_at(cliente):
    inv = cliente.post("/api/contabilidade/inventarios", json={"data": "2025-12-31", "linhas": [
        {"codigo": "A1", "descricao": "Café; lote 3", "quantidade": 12.5, "custo_unitario": 4, "unidade": "KG"}]})
    assert inv.status_code == 201, inv.text
    r = cliente.get(f"/api/contabilidade/inventarios/{inv.json()['id']}/at")
    assert r.status_code == 200 and "inventario_" in r.headers["content-disposition"]
    linhas = r.content.decode("utf-8").splitlines()
    assert linhas[0].split(";") == ["ProductCategory", "ProductCode", "ProductDescription", "ProductNumberCode",
                                    "ClosingStockQuantity", "UnitOfMeasure"]
    assert linhas[1] == "M;A1;Café lote 3;A1;12,5;KG"
