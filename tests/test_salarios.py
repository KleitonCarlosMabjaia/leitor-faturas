"""Salários: recibos, lançamento do processamento, pagamentos ao Estado e acréscimo de férias."""

from tests.test_contabilidade import linhas_por_conta

TRABALHADORA = {
    "nome": "Ana Costa", "tipo": "pessoal", "nif": "123456789", "niss": "12345678901", "data_admissao": "2025-03-01",
    "salario_base": 1000, "subsidio_alimentacao": 8, "alimentacao_cartao": True, "taxa_irs": 10,
}
GERENTE = {
    "nome": "Rui Gerente", "tipo": "orgaos_sociais", "data_admissao": "2024-01-01", "salario_base": 2000,
    "subsidio_alimentacao": 7, "alimentacao_cartao": False, "taxa_irs": 20,
}


def registar_pessoas(cliente) -> tuple[dict, dict]:
    a = cliente.post("/api/contabilidade/salarios/funcionarios", json=TRABALHADORA)
    b = cliente.post("/api/contabilidade/salarios/funcionarios", json=GERENTE)
    assert a.status_code == 201 and b.status_code == 201, a.text + b.text
    return a.json(), b.json()


def test_ficha_com_taxas_por_omissao_e_validacoes(cliente):
    a, b = registar_pessoas(cliente)
    assert (a["numero"], a["taxa_ss_trabalhador"], a["taxa_ss_entidade"]) == (1, 11.0, 23.75)
    assert (b["numero"], b["taxa_ss_entidade"]) == (2, 20.3)  # membros dos órgãos sociais
    r = cliente.post("/api/contabilidade/salarios/funcionarios", json={**TRABALHADORA, "nif": "123456780"})
    assert r.status_code == 422 and "NIF" in r.json()["detail"]


def test_processamento_do_mes(cliente):
    registar_pessoas(cliente)
    previa = cliente.get("/api/contabilidade/salarios/2026/3").json()
    assert previa["processado"] is False and len(previa["recibos"]) == 2

    r = cliente.post("/api/contabilidade/salarios/processar", json={"ano": 2026, "mes": 3})
    assert r.status_code == 201, r.text
    p = r.json()
    ana, rui = p["recibos"]
    # Ana: 1 000 € sujeitos (alimentação em cartão abaixo do limite); SS 11% e 23,75%; IRS 10%
    assert (ana["remuneracao_sujeita"], ana["ss_trabalhador"], ana["ss_entidade"]) == (1000, 110, 237.5)
    assert ana["irs"] == 100
    assert (ana["iliquido"], ana["liquido"]) == (1176, 966)
    # Rui: 7 € em numerário, acima do limite de 6,15 €: 0,85 € x 22 dias sujeitos
    assert rui["remuneracao_sujeita"] == 2018.7
    assert (rui["ss_trabalhador"], rui["ss_entidade"], rui["irs"], rui["liquido"]) == (222.06, 409.8, 403.74, 1528.2)

    la = cliente.get(f"/api/contabilidade/lancamentos/{p['lancamento_id']}").json()
    assert la["data"] == "2026-03-31" and la["origem"] == "salarios"
    assert linhas_por_conta(la) == {
        "632": (1176.0, 0.0), "631": (2154.0, 0.0), "635": (647.3, 0.0), "2312": (0.0, 966.0),
        "2311": (0.0, 1528.2), "2421": (0.0, 503.74), "245": (0.0, 979.36),
    }

    # Não se processa duas vezes; depois de estornar, volta-se a processar
    assert cliente.post("/api/contabilidade/salarios/processar", json={"ano": 2026, "mes": 3}).status_code == 409
    assert cliente.post(f"/api/contabilidade/lancamentos/{p['lancamento_id']}/estornar").status_code == 201
    assert cliente.get("/api/contabilidade/salarios/2026/3").json()["processado"] is False
    r = cliente.post("/api/contabilidade/salarios/processar", json={
        "ano": 2026, "mes": 3, "variaveis": [{"funcionario_id": ana["funcionario_id"], "dias": 20, "outros": 50}]})
    assert r.status_code == 201
    assert r.json()["recibos"][0]["remuneracao_sujeita"] == 1050 and r.json()["recibos"][0]["alimentacao"] == 160


def test_subsidios_de_ferias_e_duodecimos(cliente):
    a, b = registar_pessoas(cliente)
    cliente.put(f"/api/contabilidade/salarios/funcionarios/{b['id']}", json={**GERENTE, "duodecimos": True})
    junho = cliente.post("/api/contabilidade/salarios/simular", json={"ano": 2026, "mes": 6}).json()["recibos"]
    assert junho[0]["subsidio_ferias"] == 1000 and junho[0]["remuneracao_sujeita"] == 2000
    assert (junho[1]["subsidio_ferias"], junho[1]["subsidio_natal"]) == (166.67, 166.67)  # duodécimos
    sem = cliente.post("/api/contabilidade/salarios/simular",
                       json={"ano": 2026, "mes": 6, "subsidio_ferias": False}).json()["recibos"]
    assert sem[0]["subsidio_ferias"] == 0


def test_pagamentos_seguranca_social_irs_e_salarios(cliente):
    registar_pessoas(cliente)
    cliente.post("/api/contabilidade/salarios/processar", json={"ano": 2026, "mes": 3})
    ss = cliente.post("/api/contabilidade/salarios/2026/3/pagar", json={"tipo": "seguranca_social"})
    assert ss.status_code == 201, ss.text
    assert ss.json()["data"] == "2026-04-20"  # prazo: dia 20 do mês seguinte
    assert linhas_por_conta(ss.json()) == {"245": (979.36, 0.0), "121": (0.0, 979.36)}
    repetido = cliente.post("/api/contabilidade/salarios/2026/3/pagar", json={"tipo": "seguranca_social"})
    assert repetido.status_code == 409

    sal = cliente.post("/api/contabilidade/salarios/2026/3/pagar",
                       json={"tipo": "salarios", "conta": "111", "data": "2026-03-31"}).json()
    assert sal["diario"] == "CX"
    assert linhas_por_conta(sal) == {"2311": (1528.2, 0.0), "2312": (966.0, 0.0), "111": (0.0, 2494.2)}

    resumo = cliente.get("/api/contabilidade/salarios", params={"ano": 2026}).json()
    marco = resumo["mapa"]["meses"][0]
    assert marco["pagamentos"]["irs"]["pago"] is False and marco["pagamentos"]["salarios"]["pago"]
    # O IRS de março está em atraso (prazo 20/04) e janeiro e fevereiro estão por processar
    assert {a["tipo"] for a in resumo["pagamentos_em_atraso"]} == {"irs"}
    assert [m["mes"] for m in resumo["por_processar"]][:2] == [1, 2]
    titulos = [a["titulo"] for a in cliente.get("/api/contabilidade/resumo").json()["alertas"]]
    assert any(t.startswith("IRS retido de março de 2026") for t in titulos)
    assert any(t.startswith("Salários por processar") for t in titulos)


def test_acrescimo_de_ferias_com_reversao(cliente):
    registar_pessoas(cliente)
    est = cliente.get("/api/contabilidade/salarios/ferias", params={"ano": 2025}).json()
    # Ana entrou em março: 10/12 de (férias + subsídio); Rui está desde 2024
    assert [d["remuneracoes"] for d in est["detalhe"]] == [1666.67, 4000]
    r = cliente.post("/api/contabilidade/salarios/ferias", json={"ano": 2025})
    assert r.status_code == 201, r.text
    la = r.json()
    assert la["data"] == "2025-12-31" and la["origem"] == "acrescimo"
    assert linhas_por_conta(la) == {"631": (4000.0, 0.0), "632": (1666.67, 0.0), "635": (1207.83, 0.0),
                                    "2722": (0.0, 6874.5)}
    reversao = cliente.get("/api/contabilidade/lancamentos", params={"de": "2026-01-01", "ate": "2026-01-01"}).json()
    assert [x["origem"] for x in reversao] == ["acrescimo_reversao"]
    assert linhas_por_conta(reversao[0])["2722"] == (6874.5, 0.0)

    # Estornar o acréscimo estorna também a reversão
    assert cliente.post(f"/api/contabilidade/lancamentos/{la['id']}/estornar").status_code == 201
    estados = {x["origem"]: x["estado"] for x in cliente.get(
        "/api/contabilidade/lancamentos", params={"de": "2025-12-31", "ate": "2026-01-01"}).json()}
    assert estados["acrescimo"] == estados["acrescimo_reversao"] == "estornado"


def test_nao_apaga_quem_tem_salarios_processados(cliente):
    a, _ = registar_pessoas(cliente)
    cliente.post("/api/contabilidade/salarios/processar", json={"ano": 2026, "mes": 3})
    assert cliente.delete(f"/api/contabilidade/salarios/funcionarios/{a['id']}").status_code == 409
