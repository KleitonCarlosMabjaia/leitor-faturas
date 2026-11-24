"""Demonstração dos fluxos de caixa (NCRF 2, método direto) e códigos de fluxo nas linhas de caixa e bancos."""

import io
from datetime import date

from openpyxl import load_workbook

from app.modelos.tabelas import Empresa, ExtratoBancario, MovimentoBancario
from app.servicos.contabilidade import fluxos_caixa, lancamentos, plano_snc
from app.servicos.contabilidade.lancamentos import LinhaNova

PERIODO = {"de": "2026-01-01", "ate": "2026-12-31"}


def _lancar(db, e, data: date, descricao: str, linhas: list[LinhaNova], origem: str = "manual"):
    return lancamentos.criar(db, e, "OD", data, descricao, linhas, origem=origem)


def _preparar(fabrica_sessoes, empresa_id: int) -> dict:
    """Um ano de movimentos típicos; devolve as subcontas de terceiros criadas."""
    with fabrica_sessoes() as db:
        e = db.get(Empresa, empresa_id)
        plano_snc.garantir_plano(db, e.id)
        cliente = plano_snc.subconta_terceiro(db, e.id, "2111", "234567899", "Cliente Exemplo").codigo
        fornecedor = plano_snc.subconta_terceiro(db, e.id, "2211", "507842545", "Fornecedor Exemplo").codigo
        investimento = plano_snc.subconta_terceiro(db, e.id, "2711", "123456789", "Máquinas, Lda.").codigo
        _lancar(db, e, date(2026, 1, 1), "Saldos de abertura",
                [LinhaNova("121", debito=5000), LinhaNova("511", credito=5000)], origem="abertura")
        _lancar(db, e, date(2026, 2, 1), "Recebimento de cliente",
                [LinhaNova("121", debito=1230), LinhaNova(cliente, credito=1230)])
        _lancar(db, e, date(2026, 2, 5), "Pagamento a fornecedor",
                [LinhaNova(fornecedor, debito=615), LinhaNova("121", credito=615)])
        _lancar(db, e, date(2026, 2, 28), "Salários",
                [LinhaNova("2312", debito=800), LinhaNova("121", credito=800)])
        _lancar(db, e, date(2026, 3, 10), "Pagamento da máquina",
                [LinhaNova(investimento, debito=2460), LinhaNova("121", credito=2460)])
        _lancar(db, e, date(2026, 4, 1), "Empréstimo bancário",
                [LinhaNova("121", debito=10000), LinhaNova("25111", credito=10000)])
        _lancar(db, e, date(2026, 5, 1), "Juros do empréstimo",
                [LinhaNova("6911", debito=50), LinhaNova("121", credito=50)])
        _lancar(db, e, date(2026, 5, 2), "Aumento de capital",
                [LinhaNova("121", debito=2000), LinhaNova("511", credito=2000)])
        _lancar(db, e, date(2026, 6, 1), "Levantamento para a caixa",
                [LinhaNova("111", debito=300), LinhaNova("121", credito=300)])
        db.commit()
        return {"cliente": cliente, "fornecedor": fornecedor, "investimento": investimento}


def _valores(d: dict) -> dict[str, float]:
    return {ln["codigo"]: ln["valor"] for s in d["seccoes"] for ln in s["linhas"]}


def test_codigos_automaticos_pela_contrapartida():
    def codigo(contra: str, entrada: bool) -> str | None:
        linhas = [("121", 100.0, 0.0), (contra, 0.0, 100.0)] if entrada else [("121", 0.0, 100.0), (contra, 100.0, 0.0)]
        return fluxos_caixa.automatico(linhas, 0, {})
    assert codigo("21110001", True) == "OP01" and codigo("711", True) == "OP01"
    assert codigo("22110001", False) == "OP02" and codigo("6221", False) == "OP02"
    assert codigo("2311", False) == "OP03" and codigo("245", False) == "OP03"
    assert codigo("241", False) == "OP04" and codigo("2421", False) == "OP03" and codigo("2422", False) == "OP05"
    assert codigo("27110001", False) == "IP01" and codigo("433", True) == "IR01" and codigo("44", False) == "IP02"
    assert codigo("25111", True) == "FR01" and codigo("25111", False) == "FP01" and codigo("6911", False) == "FP02"
    assert codigo("511", True) == "FR02" and codigo("265", False) == "FP03" and codigo("7911", True) == "IR06"
    # Transferência entre caixa e bancos: não é um fluxo
    assert fluxos_caixa.automatico([("111", 300.0, 0.0), ("121", 0.0, 300.0)], 0, {}) is None
    # Código memorizado para o terceiro tem prioridade
    assert fluxos_caixa.automatico([("121", 0.0, 50.0), ("22110001", 50.0, 0.0)], 0, {"22110001": "OP05"}) == "OP05"


def test_demonstracao_dos_fluxos(cliente, fabrica_sessoes, empresa_teste):
    _preparar(fabrica_sessoes, empresa_teste)
    d = cliente.get("/api/contabilidade/fluxos-caixa", params=PERIODO).json()
    v = _valores(d)
    assert v["OP01"] == 1230 and v["OP02"] == -615 and v["OP03"] == -800
    assert v["IP01"] == -2460 and v["FR01"] == 10000 and v["FP02"] == -50 and v["FR02"] == 2000
    assert d["caixa_gerada_operacoes"] == 1230 - 615 - 800
    totais = {s["atividade"]: s["total"] for s in d["seccoes"]}
    assert totais == {"operacionais": -185, "investimento": -2460, "financiamento": 11950}
    assert d["variacao"] == 9305
    # Os saldos de abertura entram na caixa do início; a transferência para a caixa não é fluxo
    assert d["caixa_inicio"] == 5000 and d["caixa_fim"] == 14305 and d["diferenca"] == 0
    # Período comparativo (o ano anterior): vazio
    assert d["anteriores"]["variacao"] == 0 and d["anterior_de"] == "2025-01-01"


def test_codigo_editavel_e_memorizado_para_o_terceiro(cliente, fabrica_sessoes, empresa_teste):
    contas = _preparar(fabrica_sessoes, empresa_teste)
    lancs = cliente.get("/api/contabilidade/lancamentos", params=PERIODO).json()
    pagamento = next(la for la in lancs if la["descricao"] == "Pagamento a fornecedor")
    detalhe = cliente.get(f"/api/contabilidade/lancamentos/{pagamento['id']}").json()
    banco = next(ln for ln in detalhe["linhas"] if ln["caixa"])
    assert banco["fluxo"] == "OP02"
    assert not next(ln for ln in detalhe["linhas"] if not ln["caixa"])["fluxo"]

    r = cliente.patch(f"/api/contabilidade/linhas/{banco['id']}/fluxo", json={"fluxo": "OP05"})
    assert r.status_code == 200 and r.json()["fluxo"] == "OP05"
    assert _valores(cliente.get("/api/contabilidade/fluxos-caixa", params=PERIODO).json())["OP05"] == -615
    # O próximo pagamento ao mesmo fornecedor já vem com o código escolhido
    with fabrica_sessoes() as db:
        la = _lancar(db, db.get(Empresa, empresa_teste), date(2026, 7, 1), "Outro pagamento",
                     [LinhaNova(contas["fornecedor"], debito=100), LinhaNova("121", credito=100)])
        assert next(ln for ln in la.linhas if ln.debito == 0).fluxo_caixa == "OP05"
        db.commit()

    # Erros: código desconhecido, linha que não é de caixa, transferência
    assert cliente.patch(f"/api/contabilidade/linhas/{banco['id']}/fluxo", json={"fluxo": "XX99"}).status_code == 400
    outra = next(ln for ln in detalhe["linhas"] if not ln["caixa"])
    assert cliente.patch(f"/api/contabilidade/linhas/{outra['id']}/fluxo", json={"fluxo": "OP01"}).status_code == 400
    transf = next(la for la in lancs if la["descricao"] == "Levantamento para a caixa")
    linha_transf = cliente.get(f"/api/contabilidade/lancamentos/{transf['id']}").json()["linhas"][0]
    assert linha_transf["fluxo"] is None
    r = cliente.patch(f"/api/contabilidade/linhas/{linha_transf['id']}/fluxo", json={"fluxo": "OP05"})
    assert r.status_code == 400 and "transferência" in r.json()["detail"]


def test_lancamento_manual_com_codigo_e_estorno(cliente, fabrica_sessoes, empresa_teste):
    _preparar(fabrica_sessoes, empresa_teste)
    r = cliente.post("/api/contabilidade/lancamentos", json={
        "diario": "BAN", "data": "2026-08-01", "descricao": "Venda de equipamento usado",
        "linhas": [{"conta": "121", "debito": 400, "fluxo": "IR01"}, {"conta": "7911", "credito": 400}]})
    assert r.status_code == 201, r.text
    assert next(ln for ln in r.json()["linhas"] if ln["caixa"])["fluxo"] == "IR01"
    assert _valores(cliente.get("/api/contabilidade/fluxos-caixa", params=PERIODO).json())["IR01"] == 400
    # O estorno leva o mesmo código: a rubrica volta a zero
    assert cliente.post(f"/api/contabilidade/lancamentos/{r.json()['id']}/estornar").status_code in (200, 201)
    d = cliente.get("/api/contabilidade/fluxos-caixa", params=PERIODO).json()
    assert _valores(d)["IR01"] == 0 and d["diferenca"] == 0


def test_codigo_na_reconciliacao_bancaria(cliente, fabrica_sessoes, empresa_teste):
    contas = _preparar(fabrica_sessoes, empresa_teste)
    with fabrica_sessoes() as db:
        extrato = ExtratoBancario(empresa_id=empresa_teste, conta_codigo="121", nome_ficheiro="extrato.csv",
                                  formato="csv")
        db.add(extrato)
        db.flush()
        mov = MovimentoBancario(empresa_id=empresa_teste, extrato_id=extrato.id, conta_codigo="121",
                                data=date(2026, 9, 3),
                                descricao="TRF CLIENTE EXEMPLO ADIANTAMENTO", montante=500.0, estado="pendente",
                                referencia="teste:1")
        db.add(mov)
        db.commit()
        mov_id = mov.id
    r = cliente.post(f"/api/contabilidade/banco/movimentos/{mov_id}/lancar",
                     json={"conta": contas["cliente"], "fluxo": "OP05"})
    assert r.status_code == 200, r.text
    ligado = r.json()["lancamento"]
    assert ligado["fluxo"] == "OP05" and ligado["transferencia"] is False
    # Mudar na reconciliação (a linha do banco) e ver memorizado para o cliente
    r = cliente.patch(f"/api/contabilidade/linhas/{ligado['linha_id']}/fluxo", json={"fluxo": "OP01"})
    assert r.status_code == 200
    with fabrica_sessoes() as db:
        assert fluxos_caixa.regras(db, empresa_teste)[contas["cliente"]] == "OP01"


def test_exportacao_excel(cliente, fabrica_sessoes, empresa_teste):
    _preparar(fabrica_sessoes, empresa_teste)
    r = cliente.get("/api/contabilidade/fluxos-caixa/exportar", params=PERIODO)
    assert r.status_code == 200 and "spreadsheetml" in r.headers["content-type"]
    folha = load_workbook(io.BytesIO(r.content)).active
    linhas = {row[0].strip(): row[1] for row in folha.iter_rows(min_row=5, values_only=True) if row and row[0]}
    assert linhas["Recebimentos de clientes"] == 1230
    assert linhas["Caixa e seus equivalentes no fim do período"] == 14305
    assert len(cliente.get("/api/contabilidade/fluxos-caixa/rubricas").json()) == len(fluxos_caixa.RUBRICAS)
