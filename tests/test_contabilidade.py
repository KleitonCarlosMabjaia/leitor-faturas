"""Contabilidade: partidas dobradas, IVA (art. 21.º do CIVA), estornos, pagamentos, apuramento e relatórios."""

import secrets
from datetime import date

import pytest

from app.modelos.tabelas import ESTADO_CONFIRMADA, Fatura
from app.servicos import repositorio
from app.servicos.contabilidade import classificacao, obrigacoes
from tests.conftest import criar_empresa_com_admin, dados_exemplo, entrar


def criar_fatura(fabrica_sessoes, empresa_id: int, **alteracoes) -> str:
    """Fatura confirmada, já lida, gravada diretamente na base de dados."""
    tipo = alteracoes.pop("tipo_documento", "FT")
    paga = alteracoes.pop("paga", False)
    with fabrica_sessoes() as db:
        fatura = Fatura(
            id=secrets.token_hex(6), empresa_id=empresa_id, ficheiro="inexistente.pdf", nome_original="f.pdf",
            tamanho_kb=1, estado_extracao="concluida", estado=ESTADO_CONFIRMADA, tipo_documento=tipo,
            atcud="ABCD-1", paga=paga,
        )
        repositorio.aplicar_dados(db, fatura, dados_exemplo(**alteracoes))
        db.add(fatura)
        db.commit()
        return fatura.id


def linhas_por_conta(lancamento: dict) -> dict[str, tuple[float, float]]:
    resultado: dict[str, tuple[float, float]] = {}
    for ln in lancamento["linhas"]:
        d, c = resultado.get(ln["conta"], (0.0, 0.0))
        resultado[ln["conta"]] = (round(d + ln["debito"], 2), round(c + ln["credito"], 2))
    return resultado


def test_plano_snc_e_balancete_vazio(cliente):
    contas = {c["codigo"]: c for c in cliente.get("/api/contabilidade/contas").json()}
    assert contas["6242"]["nome"] == "Combustíveis" and contas["6242"]["movimento"]
    assert not contas["62"]["movimento"]          # agregadora
    assert not contas["2211"]["movimento"]        # só se lança nas subcontas de cada fornecedor
    balancete = cliente.get("/api/contabilidade/balancete").json()
    assert balancete["linhas"] == [] and balancete["equilibrado"]


def test_contabilizar_fatura_de_materias_primas(cliente, fabrica_sessoes, empresa_teste):
    id_fatura = criar_fatura(
        fabrica_sessoes, empresa_teste, fornecedor="Talho Central, Lda.",
        iva=[{"taxa": 6, "base": 100.0, "valor": 6.0}], valor_sem_iva=100.0, total=106.0,
    )
    proposta = cliente.get(f"/api/contabilidade/faturas/{id_fatura}").json()["proposta"]
    assert proposta["classificacao"]["conta"] == "312"
    assert proposta["classificacao"]["origem"] == "palavras"

    resposta = cliente.post(f"/api/contabilidade/faturas/{id_fatura}/contabilizar", json={})
    assert resposta.status_code == 201, resposta.text
    lancamento = resposta.json()
    assert lancamento["referencia"].startswith("COM 2026/1")
    contas = linhas_por_conta(lancamento)
    assert contas["312"] == (100.0, 0.0)
    assert contas["243211"] == (6.0, 0.0)          # IVA dedutível de existências à taxa reduzida
    assert contas["22110001"] == (0.0, 106.0)      # subconta criada para o fornecedor

    # Contabilizar duas vezes não é possível
    assert cliente.post(f"/api/contabilidade/faturas/{id_fatura}/contabilizar", json={}).status_code == 409

    # Segunda fatura do mesmo fornecedor: a classificação foi aprendida e a subconta reutilizada
    outra = criar_fatura(fabrica_sessoes, empresa_teste, fornecedor="Talho Central, Lda.", numero_fatura="FT 2026/124")
    proposta = cliente.get(f"/api/contabilidade/faturas/{outra}").json()["proposta"]
    assert proposta["classificacao"]["origem"] == "regra"
    assert any(ln["conta"] == "22110001" for ln in proposta["linhas"])

    balancete = cliente.get("/api/contabilidade/balancete", params={"de": "2026-01-01", "ate": "2026-12-31"}).json()
    assert balancete["equilibrado"]
    por_codigo = {ln["codigo"]: ln for ln in balancete["linhas"]}
    assert por_codigo["3"]["saldo_devedor"] == 100.0
    assert por_codigo["2211"]["saldo_credor"] == 106.0


def test_gasoleo_so_deduz_metade_do_iva(cliente, fabrica_sessoes, empresa_teste):
    id_fatura = criar_fatura(fabrica_sessoes, empresa_teste, fornecedor="Galp Energia, S.A.")
    lancamento = cliente.post(f"/api/contabilidade/faturas/{id_fatura}/contabilizar", json={}).json()
    contas = linhas_por_conta(lancamento)
    assert contas["6242"] == (111.5, 0.0)          # 100 de base + 11,50 de IVA não dedutível
    assert contas["24323"] == (11.5, 0.0)          # outros bens e serviços (campo 24)
    assert any("art. 21.º" in nota for nota in lancamento["notas"])

    mapa = cliente.get("/api/contabilidade/iva", params={"de": "2026-10-01", "ate": "2026-12-31"}).json()
    assert {ln["campo"]: ln["valor"] for ln in mapa["dedutivel"]}["24"] == 11.5
    assert mapa["iva_nao_dedutivel"] == 11.5


def test_escolha_do_utilizador_prevalece_e_e_validada(cliente, fabrica_sessoes, empresa_teste):
    id_fatura = criar_fatura(fabrica_sessoes, empresa_teste, fornecedor="Fornecedor Desconhecido")
    erro = cliente.post(f"/api/contabilidade/faturas/{id_fatura}/contabilizar", json={"conta_codigo": "12"})
    assert erro.status_code == 409 and "não serve" in erro.json()["detail"]
    lancamento = cliente.post(
        f"/api/contabilidade/faturas/{id_fatura}/contabilizar",
        json={"conta_codigo": "435", "perfil_iva": "total"},
    ).json()
    contas = linhas_por_conta(lancamento)
    assert contas["435"] == (100.0, 0.0)
    assert contas["24322"] == (23.0, 0.0)          # IVA de investimentos (campo 20)
    assert contas["27110001"] == (0.0, 123.0)      # fornecedor de investimentos


def test_nota_de_credito_inverte_o_lancamento(cliente, fabrica_sessoes, empresa_teste):
    id_fatura = criar_fatura(fabrica_sessoes, empresa_teste, fornecedor="Peixaria do Mar", tipo_documento="NC")
    contas = linhas_por_conta(cliente.post(f"/api/contabilidade/faturas/{id_fatura}/contabilizar", json={}).json())
    assert contas["312"] == (0.0, 100.0)
    assert contas["22110001"] == (123.0, 0.0)


def test_lancamento_manual_valida_partidas_dobradas(cliente):
    base = {"diario": "OD", "data": "2026-10-10", "descricao": "Renda de outubro"}
    desequilibrado = cliente.post("/api/contabilidade/lancamentos", json={**base, "linhas": [
        {"conta": "6261", "debito": 800}, {"conta": "12", "credito": 700}]})
    assert desequilibrado.status_code == 409 and "equilibrado" in desequilibrado.json()["detail"]

    agregadora = cliente.post("/api/contabilidade/lancamentos", json={**base, "linhas": [
        {"conta": "62", "debito": 800}, {"conta": "12", "credito": 800}]})
    assert agregadora.status_code == 409 and "agregadora" in agregadora.json()["detail"]

    ok = cliente.post("/api/contabilidade/lancamentos", json={**base, "linhas": [
        {"conta": "6261", "debito": 800}, {"conta": "12", "credito": 800}]})
    assert ok.status_code == 201
    assert ok.json()["referencia"] == "OD 2026/1"
    segundo = cliente.post("/api/contabilidade/lancamentos", json={**base, "linhas": [
        {"conta": "6261", "debito": 800}, {"conta": "12", "credito": 800}]}).json()
    assert segundo["referencia"] == "OD 2026/2"    # numeração sequencial por diário e ano


def test_periodo_fechado_bloqueia_lancamentos(cliente):
    assert cliente.put("/api/contabilidade/configuracao",
                       json={"regime_iva": "mensal", "fechado_ate": "2026-09-30"}).status_code == 200
    resposta = cliente.post("/api/contabilidade/lancamentos", json={
        "diario": "OD", "data": "2026-09-15", "descricao": "Fora de prazo",
        "linhas": [{"conta": "6261", "debito": 10}, {"conta": "11", "credito": 10}]})
    assert resposta.status_code == 409 and "fechado" in resposta.json()["detail"]


def test_estorno_anula_e_desbloqueia_a_fatura(cliente, fabrica_sessoes, empresa_teste):
    id_fatura = criar_fatura(fabrica_sessoes, empresa_teste, fornecedor="Talho Central, Lda.")
    lancamento = cliente.post(f"/api/contabilidade/faturas/{id_fatura}/contabilizar", json={}).json()

    # Contabilizada: não pode ser alterada nem apagada
    assert cliente.delete(f"/api/faturas/{id_fatura}").status_code == 409

    estorno = cliente.post(f"/api/contabilidade/lancamentos/{lancamento['id']}/estornar")
    assert estorno.status_code == 201
    assert estorno.json()["estorno_de_id"] == lancamento["id"]
    assert cliente.post(f"/api/contabilidade/lancamentos/{lancamento['id']}/estornar").status_code == 409

    balancete = cliente.get("/api/contabilidade/balancete", params={"de": "2026-01-01", "ate": "2026-12-31"}).json()
    assert all(ln["saldo_devedor"] == 0 and ln["saldo_credor"] == 0 for ln in balancete["linhas"])
    assert cliente.delete(f"/api/faturas/{id_fatura}").status_code == 204


def test_pagamento_gera_lancamento_no_banco(cliente, fabrica_sessoes, empresa_teste):
    id_fatura = criar_fatura(fabrica_sessoes, empresa_teste, fornecedor="Talho Central, Lda.")
    cliente.post(f"/api/contabilidade/faturas/{id_fatura}/contabilizar", json={})
    assert cliente.patch(f"/api/faturas/{id_fatura}/pagamento", json={"paga": True}).status_code == 200

    dados = cliente.get(f"/api/contabilidade/faturas/{id_fatura}").json()
    pagamento = next(la for la in dados["lancamentos"] if la["origem"] == "pagamento")
    assert pagamento["diario"] == "BAN"
    assert linhas_por_conta(pagamento) == {"22110001": (123.0, 0.0), "12": (0.0, 123.0)}

    correntes = cliente.get("/api/contabilidade/fornecedores").json()
    assert correntes["total"] == 0

    # Desmarcar como paga estorna o pagamento
    cliente.patch(f"/api/faturas/{id_fatura}/pagamento", json={"paga": False})
    assert cliente.get("/api/contabilidade/fornecedores").json()["total"] == 123.0


def test_fatura_ja_paga_regista_pagamento_ao_contabilizar(cliente, fabrica_sessoes, empresa_teste):
    id_fatura = criar_fatura(fabrica_sessoes, empresa_teste, fornecedor="Talho Central, Lda.", paga=True)
    cliente.post(f"/api/contabilidade/faturas/{id_fatura}/contabilizar", json={})
    origens = [la["origem"] for la in cliente.get(f"/api/contabilidade/faturas/{id_fatura}").json()["lancamentos"]]
    assert origens == ["fatura", "pagamento"]


def test_apuramento_do_iva(cliente, fabrica_sessoes, empresa_teste):
    id_fatura = criar_fatura(fabrica_sessoes, empresa_teste, fornecedor="Talho Central, Lda.")  # 23 € dedutível
    cliente.post(f"/api/contabilidade/faturas/{id_fatura}/contabilizar", json={})
    cliente.post("/api/contabilidade/lancamentos", json={
        "diario": "VEN", "data": "2026-10-20", "descricao": "Vendas de outubro",
        "linhas": [{"conta": "12", "debito": 1230}, {"conta": "711", "credito": 1000},
                   {"conta": "24333", "credito": 230}]})

    resposta = cliente.post("/api/contabilidade/iva/apurar", json={"de": "2026-10-01", "ate": "2026-12-31"})
    assert resposta.status_code == 201, resposta.text
    contas = linhas_por_conta(resposta.json())
    assert contas["2436"] == (0.0, 207.0)          # 230 liquidado - 23 dedutível

    saldos = cliente.get("/api/contabilidade/balancete", params={"de": "2026-01-01", "ate": "2026-12-31"}).json()
    por_codigo = {ln["codigo"]: ln for ln in saldos["linhas"]}
    assert por_codigo["2435"]["saldo_devedor"] == 0 and por_codigo["2435"]["saldo_credor"] == 0
    assert por_codigo["2432"]["saldo_devedor"] == 0

    # Já apurado: não há nada de novo para apurar
    repetido = cliente.post("/api/contabilidade/iva/apurar", json={"de": "2026-10-01", "ate": "2026-12-31"})
    assert repetido.status_code == 409

    resultados = cliente.get("/api/contabilidade/resultados", params={"de": "2026-01-01", "ate": "2026-12-31"}).json()
    assert resultados["resultado"] == 1000.0 - 100.0


def test_contabilidade_isolada_entre_empresas(cliente, fabrica_sessoes, empresa_teste, novo_cliente):
    id_fatura = criar_fatura(fabrica_sessoes, empresa_teste, fornecedor="Talho Central, Lda.")
    lancamento = cliente.post(f"/api/contabilidade/faturas/{id_fatura}/contabilizar", json={}).json()

    criar_empresa_com_admin(fabrica_sessoes, "Outra", "admin@outra.pt")
    outro = novo_cliente("admin@outra.pt")
    assert outro.get(f"/api/contabilidade/lancamentos/{lancamento['id']}").status_code == 404
    assert outro.post(f"/api/contabilidade/lancamentos/{lancamento['id']}/estornar").status_code == 404
    assert outro.get(f"/api/contabilidade/faturas/{id_fatura}").status_code == 404
    assert outro.get("/api/contabilidade/lancamentos", params={"de": "2026-01-01"}).json() == []
    assert outro.get("/api/contabilidade/balancete").json()["linhas"] == []


def test_resumo_e_alertas(cliente, fabrica_sessoes, empresa_teste):
    criar_fatura(fabrica_sessoes, empresa_teste, fornecedor="Talho Central, Lda.")
    resumo = cliente.get("/api/contabilidade/resumo").json()
    assert resumo["por_contabilizar"]["quantidade"] == 1
    assert any("por contabilizar" in a["titulo"] for a in resumo["alertas"])

    resultado = cliente.post("/api/contabilidade/contabilizar-lote",
                             json={"ids": [p["fatura"]["id"] for p in cliente.get(
                                 "/api/contabilidade/por-contabilizar").json()]}).json()
    assert resultado["contabilizadas"] == 1
    assert cliente.get("/api/contabilidade/resumo").json()["por_contabilizar"]["quantidade"] == 0


def test_criar_subconta(cliente):
    assert cliente.post("/api/contabilidade/contas", json={"codigo": "121", "nome": "Banco A"}).status_code == 201
    contas = {c["codigo"]: c for c in cliente.get("/api/contabilidade/contas").json()}
    assert not contas["12"]["movimento"] and contas["121"]["movimento"]
    assert cliente.post("/api/contabilidade/contas", json={"codigo": "999", "nome": "X"}).status_code == 422


def test_assistente_sem_ia_explica_porque(cliente, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "CHAVE_API_DEFINIDA", False)
    resposta = cliente.post("/api/contabilidade/assistente", json={"pergunta": "Quanto IVA vou pagar?"})
    assert resposta.status_code == 503
    assert "ANTHROPIC_API_KEY" in resposta.json()["detail"]


@pytest.mark.parametrize("nome, conta, perfil", [
    ("Galp Energia, S.A.", "6242", "gasoleo_50"),
    ("Posto de Gasolina Silva", "6242", "nao_dedutivel"),
    ("EDP Comercial", "6241", "total"),
    ("Vodafone Portugal", "6262", "total"),
    ("Restaurante O Marinheiro", "6266", "representacao"),
    ("Via Verde Portugal", "6251", "deslocacoes"),
    ("Securitas Vigilância", "6223", "total"),
    ("Distribuidora de Bebidas Rio Douro", "311", "total"),
    ("Hortícolas Vale do Tejo", "312", "total"),
])
def test_classificacao_por_palavras_chave(nome, conta, perfil):
    c = classificacao.por_palavras_chave(nome)
    assert (c.conta_codigo, c.perfil_iva) == (conta, perfil)


def test_calendario_fiscal():
    assert obrigacoes.pascoa(2026) == date(2026, 4, 5)
    assert obrigacoes.dia_util(date(2026, 10, 5)) == date(2026, 10, 6)    # feriado (segunda) -> terça
    assert obrigacoes.dia_util(date(2026, 6, 4)) == date(2026, 6, 5)      # Corpo de Deus 2026
    proximas = obrigacoes.proximas(date(2026, 11, 1), "trimestral", 30)
    dp = next(o for o in proximas if o["chave"] == "iva_dp")
    assert dp["prazo"] == date(2026, 11, 20) and dp["periodo"] == "3.º trimestre de 2026"
    assert obrigacoes.periodo_iva(date(2026, 11, 18), "trimestral")[:2] == (date(2026, 7, 1), date(2026, 9, 30))
    assert obrigacoes.periodo_iva(date(2026, 1, 10), "mensal")[:2] == (date(2025, 12, 1), date(2025, 12, 31))


def test_utilizador_sem_sessao_nao_acede(cliente_anonimo):
    assert cliente_anonimo.get("/api/contabilidade/resumo").status_code == 401


def test_membro_nao_fecha_periodos(cliente_anonimo, fabrica_sessoes, empresa_teste):
    from app.modelos.tabelas import PAPEL_MEMBRO, Utilizador
    from app.servicos.autenticacao import hash_palavra_passe
    from tests.conftest import PALAVRA_PASSE_TESTE
    with fabrica_sessoes() as db:
        db.add(Utilizador(empresa_id=empresa_teste, nome="Membro", email="membro@teste.pt", papel=PAPEL_MEMBRO,
                          hash_palavra_passe=hash_palavra_passe(PALAVRA_PASSE_TESTE)))
        db.commit()
    membro = entrar(cliente_anonimo, "membro@teste.pt")
    assert membro.put("/api/contabilidade/configuracao", json={"regime_iva": "mensal"}).status_code == 403
