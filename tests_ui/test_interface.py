"""Testes da interface no navegador (Playwright). Ver tests_ui/conftest.py."""

import re

import pytest
from playwright.sync_api import Page, expect


def _valor(texto: str) -> float:
    """'1 234,56 €' -> 1234.56"""
    return float(re.sub(r"[^\d,]", "", texto).replace(",", "."))


def _uma_por_rever(api) -> dict:
    return next(f for f in api("/api/faturas") if f["estado"] == "por rever")


# ---------- Lista ----------

def test_contadores_e_filtro_por_estado(page: Page, api):
    faturas = api("/api/faturas")
    por_rever = sum(f["estado"] == "por rever" for f in faturas)
    page.goto("/")

    expect(page.locator("#linhas tr")).to_have_count(len(faturas))
    expect(page.locator("#n-todas")).to_have_text(str(len(faturas)))
    expect(page.locator("#n-por-rever")).to_have_text(str(por_rever))

    botao = page.get_by_role("button", name=re.compile("^Por rever"))
    botao.click()
    expect(page.locator("#linhas tr")).to_have_count(por_rever)
    expect(botao).to_have_attribute("aria-pressed", "true")


def test_pesquisa_e_lista_vazia(page: Page):
    page.goto("/")
    page.get_by_label("Pesquisar").fill("talho")
    nomes = page.locator("#linhas .titulo")
    expect(nomes.first).to_be_visible()
    for nome in nomes.all_text_contents():
        assert "Talho" in nome

    page.get_by_label("Pesquisar").fill("não existe nenhuma assim")
    expect(page.get_by_text("Nenhuma fatura encontrada")).to_be_visible()
    page.locator("#vazio-limpar").click()
    expect(page.get_by_label("Pesquisar")).to_have_value("")
    expect(page.locator("#linhas tr").first).to_be_visible()


def test_tecla_barra_vai_para_a_pesquisa(page: Page):
    page.goto("/")
    expect(page.locator("#linhas tr").first).to_be_visible()
    page.keyboard.press("/")
    expect(page.get_by_label("Pesquisar")).to_be_focused()


def test_ordenar_por_total(page: Page):
    page.goto("/")
    expect(page.locator("#linhas tr").first).to_be_visible()
    cabecalho = page.locator("th[data-campo=total]")

    cabecalho.get_by_role("button").click()
    expect(cabecalho).to_have_attribute("aria-sort", "ascending")
    valores = [_valor(t) for t in page.locator("#linhas .valor-total").all_text_contents()]
    assert valores == sorted(valores)

    cabecalho.get_by_role("button").click()
    expect(cabecalho).to_have_attribute("aria-sort", "descending")
    valores = [_valor(t) for t in page.locator("#linhas .valor-total").all_text_contents()]
    assert valores == sorted(valores, reverse=True)


def test_apagar_pede_confirmacao(page: Page, api):
    total = len(api("/api/faturas"))
    page.goto("/")
    linhas = page.locator("#linhas tr")
    expect(linhas).to_have_count(total)

    # Cancelar: a janela fecha e nada é apagado (o foco começa em "Cancelar")
    linhas.first.get_by_role("button", name=re.compile("^Apagar")).click()
    janela = page.get_by_role("dialog")
    expect(janela).to_be_visible()
    expect(janela.get_by_role("button", name="Cancelar")).to_be_focused()
    page.keyboard.press("Escape")
    expect(janela).to_be_hidden()
    expect(linhas).to_have_count(total)

    # Confirmar: a fatura desaparece e aparece a notificação
    linhas.first.get_by_role("button", name=re.compile("^Apagar")).click()
    janela.get_by_role("button", name="Apagar").click()
    expect(page.get_by_role("status").filter(has_text="Fatura apagada.")).to_be_visible()
    expect(linhas).to_have_count(total - 1)


def test_tema_escuro_fica_guardado(page: Page):
    page.emulate_media(color_scheme="light")
    page.goto("/")
    page.get_by_role("button", name="Mudar para o modo escuro").click()
    expect(page.locator("html")).to_have_attribute("data-tema", "escuro")

    page.reload()
    expect(page.locator("html")).to_have_attribute("data-tema", "escuro")
    expect(page.get_by_role("button", name="Mudar para o modo claro")).to_be_visible()


# ---------- Revisão ----------

def test_revisao_assinala_campos_e_atalhos(page: Page, api):
    fatura = _uma_por_rever(api)
    page.goto(f"/faturas/{fatura['id']}")
    nome = page.get_by_label("Nome")
    expect(nome).to_have_value(fatura["fornecedor"])
    estado = page.locator("#estado-gravacao")

    # Campo obrigatório vazio + Ctrl+Enter: fica assinalado e nada é gravado
    nome.fill("")
    expect(estado).to_have_text("Alterações por guardar")
    page.keyboard.press("Control+Enter")
    expect(nome).to_have_attribute("aria-invalid", "true")
    expect(page.get_by_text("Campo obrigatório.")).to_be_visible()

    # Ao corrigir, o aviso desaparece; Ctrl+S guarda sem confirmar
    nome.fill(fatura["fornecedor"])
    expect(page.get_by_text("Campo obrigatório.")).to_be_hidden()
    page.keyboard.press("Control+s")
    expect(estado).to_have_text("Tudo guardado")
    expect(page.get_by_role("status").filter(has_text="Alterações guardadas.")).to_be_visible()


def test_conferencia_dos_totais_em_tempo_real(page: Page, api):
    fatura = _uma_por_rever(api)
    page.goto(f"/faturas/{fatura['id']}")
    conferencia = page.locator("#conferencia")
    expect(conferencia).to_have_class(re.compile(r"\bok\b"))

    page.get_by_label("Total (€)").fill(str(fatura["total"] + 10))
    expect(conferencia).to_have_class(re.compile(r"\berro\b"))
    expect(conferencia).to_contain_text("difere")


# ---------- Dashboard ----------

def test_dashboard_mostra_indicadores_e_graficos(page: Page):
    page.goto("/dashboard")
    expect(page.locator("#total-mes")).not_to_have_text("—")
    # O Chart.js vem de uma CDN: se não houver Internet, os valores aparecem nas tabelas
    page.wait_for_function("typeof Chart !== 'undefined' && !!Chart.getChart('grafico-evolucao')")
    page.get_by_text("Ver os valores em tabela").last.click()
    expect(page.locator("#tabela-evolucao tr")).to_have_count(12)


# ---------- Em todas as páginas ----------

CONTABILIDADE = [f"/contabilidade#{s}" for s in (
    "resumo", "por-contabilizar", "lancamentos", "balancete", "extrato", "iva", "resultados", "fornecedores",
    "obrigacoes", "plano", "assistente", "bancos", "vendas", "inventarios", "ativos", "fecho", "salarios",
    "periodizacao", "balanco", "centros")]


@pytest.mark.parametrize("caminho", ["/", "/dashboard", "/conta", "/subscricao", "REVISAO", *CONTABILIDADE])
def test_paginas_sem_erros_de_javascript(page: Page, api, erros_js, caminho):
    if caminho == "REVISAO":
        caminho = f"/faturas/{_uma_por_rever(api)['id']}"
    page.goto(caminho)
    page.wait_for_load_state("networkidle")
    assert erros_js == []


@pytest.mark.parametrize("caminho", ["/", "/dashboard", "/conta", "/subscricao", "REVISAO", "/contabilidade#resumo",
                                     "/contabilidade#balancete", "/contabilidade#por-contabilizar",
                                     "/contabilidade#salarios", "/contabilidade#balanco"])
def test_telemovel_sem_deslocamento_horizontal(page: Page, api, caminho):
    if caminho == "REVISAO":
        caminho = f"/faturas/{_uma_por_rever(api)['id']}"
    page.set_viewport_size({"width": 375, "height": 800})
    page.goto(caminho)
    page.wait_for_load_state("networkidle")
    largura = page.evaluate("document.documentElement.scrollWidth")
    assert largura <= 375, f"A página tem {largura}px de largura num ecrã de 375px"


# ---------- Contabilidade ----------

def test_balancete_equilibrado_e_extrato(page: Page):
    page.goto("/contabilidade#balancete")
    expect(page.get_by_text("Débito = Crédito")).to_be_visible()
    page.locator("tr", has=page.get_by_role("cell", name="Depósitos à ordem", exact=True)).click()
    expect(page).to_have_url(re.compile(r"#extrato:12$"))
    expect(page.locator("#sec-extrato")).to_contain_text("Saldo anterior")


def test_contabilizar_fatura_pela_interface(page: Page, api):
    pendentes = api("/api/contabilidade/por-contabilizar")
    if not pendentes:
        pytest.skip("Sem faturas por contabilizar na demonstração")
    page.goto("/contabilidade#por-contabilizar")
    propostas = page.locator(".proposta")
    expect(propostas).to_have_count(len(pendentes))
    # Centro de custo (a demonstração tem SALA e TAKE): escolhe-se no cartão e vai para o gasto
    primeira = propostas.first
    centro = primeira.locator(".seletor-centro-proposta")
    expect(centro).to_be_visible()
    centro.select_option(label="TAKE · Take-away e entregas")
    primeira.get_by_role("button", name="Contabilizar").click()
    expect(propostas).to_have_count(len(pendentes) - 1)
    expect(page.locator(".notificacao.ok")).to_contain_text("Lançado: COM")
    expect(page.locator(".notificacao.ok")).to_contain_text("no centro TAKE")


def test_lancamento_desequilibrado_e_recusado(page: Page):
    page.goto("/contabilidade#lancamentos")
    page.get_by_role("button", name="Novo lançamento").click()
    page.locator("#lanc-descricao").fill("Teste")
    linhas = page.locator("#lanc-linhas tr")
    linhas.nth(0).locator("input").nth(0).fill("6233")
    linhas.nth(0).locator("input").nth(1).fill("10")
    linhas.nth(1).locator("input").nth(0).fill("121")
    linhas.nth(1).locator("input").nth(2).fill("9")
    expect(page.locator("#lanc-diferenca")).to_have_text("Diferença 1,00 €")
    page.locator("#lanc-gravar").click()
    expect(page.locator("#lanc-erro")).to_contain_text("não está equilibrado")


def test_saft_verificado_e_descarregado(page: Page):
    page.goto("/contabilidade#obrigacoes")
    expect(page.get_by_text("Pronto a exportar")).to_be_visible()
    with page.expect_download() as info:
        page.locator("#saft-descarregar").click()
    assert re.match(r"SAFT-PT-contabilidade-\d{9}-\d{4}-01-01-a-\d{4}-\d{2}-\d{2}\.xml$", info.value.suggested_filename)


def test_reconciliacao_bancaria(page: Page, tmp_path):
    page.goto("/contabilidade#bancos")
    cartoes = page.locator(".proposta-banco")
    expect(cartoes.first).to_be_visible()
    antes = cartoes.count()
    cartoes.filter(has_text="IMPOSTO SELO").first.get_by_role("button", name="Lançar").first.click()
    expect(page.get_by_text("Lançado em 6812 e reconciliado.")).to_be_visible()
    expect(cartoes).to_have_count(antes - 1)

    extrato = tmp_path / "extrato.csv"
    extrato.write_text("Data;Descrição;Montante\n02-01-2026;JUROS CREDORES;0,12\n", encoding="cp1252")
    page.set_input_files("#banco-ficheiro", str(extrato))
    page.get_by_role("button", name="Importar extrato").click()
    expect(page.get_by_text("1 movimento importado")).to_be_visible()


def test_vendas_registar_e_receber(page: Page):
    page.goto("/contabilidade#vendas")
    expect(page.get_by_role("heading", name="Faturas de clientes")).to_be_visible()
    page.get_by_role("button", name="Registar venda").click()
    page.locator("#venda-numero").fill("Z/900")
    page.locator("#venda-nif").fill("500000000")
    page.locator("#venda-cliente").fill("Cliente de Teste, Lda.")
    page.locator("#janela-temporaria .editor-linhas input[type=number]").fill("100")
    expect(page.locator("#janela-temporaria")).to_contain_text("Total 123,00")
    page.get_by_role("button", name="Registar e contabilizar").click()
    expect(page.get_by_text("Venda FT Z/900 registada e contabilizada")).to_be_visible()

    linha = page.locator("tr", has_text="FT Z/900")
    linha.get_by_role("button", name="Receber").click()
    page.get_by_role("button", name="Registar recebimento").click()
    expect(page.get_by_text("Recebimento lançado")).to_be_visible()
    expect(page.locator("tr", has_text="FT Z/900")).to_contain_text("Recebida")


def test_inventario_importado_e_apuramento(page: Page, tmp_path):
    page.goto("/contabilidade#inventarios")
    expect(page.get_by_role("heading", name="Contagens de inventário")).to_be_visible()
    contagem = tmp_path / "contagem.csv"
    contagem.write_text("Código;Descrição;Quantidade;Custo\nB01;Vinho Verde;10;3,85\n", encoding="cp1252")
    page.set_input_files("#inv-ficheiro", str(contagem))
    page.locator("#inv-import-data").fill("2030-12-31")
    page.get_by_role("button", name="Importar contagem").click()
    expect(page.get_by_text("1 artigo importado")).to_be_visible()
    expect(page.locator("#detalhe-inventario")).to_contain_text("Custo das vendas e consumos")


def test_registar_ativo_e_lancar_depreciacoes(page: Page):
    page.goto("/contabilidade#ativos")
    expect(page.get_by_role("heading", name=re.compile("Mapa de depreciações"))).to_be_visible()
    page.get_by_role("button", name="Registar ativo").click()
    page.locator("#ativo-referencia").select_option(value="4")  # computadores e equipamento informático
    page.locator("#ativo-descricao").fill("Portátil de teste")
    page.locator("#ativo-valor").fill("1200")
    page.locator("#ativo-aquisicao").fill("2026-01-10")
    expect(page.locator("#janela-temporaria")).to_contain_text("Depreciação mensal: 33,33")
    page.get_by_role("button", name="Registar ativo").last.click()
    expect(page.get_by_text(re.compile(r"registado \(depreciação mensal"))).to_be_visible()
    page.get_by_role("button", name=re.compile("Lançar depreciações até")).click()
    expect(page.get_by_text(re.compile("Depreciações lançadas"))).to_be_visible()
    expect(page.locator("tr", has_text="Portátil de teste")).not_to_contain_text("0,00 €  0,00 €")


def test_fecho_do_exercicio_mostra_verificacoes_e_estimativa(page: Page):
    page.goto("/contabilidade#fecho")
    expect(page.get_by_role("heading", name="Fecho do exercício", level=1)).to_be_visible()
    expect(page.locator(".lista-alertas li")).to_have_count(9)
    irc = page.locator("table.dr tr", has_text="IRC").first
    antes = irc.inner_text()
    page.locator("#fecho-pme").uncheck()
    expect(irc).not_to_have_text(antes)
    expect(page.get_by_role("button", name=re.compile("Fechar o exercício"))).to_be_disabled()  # demonstração


def test_processar_salarios_do_mes(page: Page):
    page.goto("/contabilidade#salarios")
    expect(page.get_by_role("heading", name="Pessoal")).to_be_visible()
    expect(page.locator("#sec-salarios")).not_to_contain_text("null")
    page.locator("tr", has_text="Maria Fernandes").get_by_role("button", name="Variáveis").click()
    page.locator("#var-outros").fill("100")
    page.get_by_role("button", name="Aplicar").click()
    expect(page.locator("tr", has_text="Maria Fernandes (cozinheira) base")).to_contain_text("outros 100,00 €")
    page.get_by_role("button", name=re.compile("^Processar ")).click()
    expect(page.get_by_text(re.compile("processados e lançados"))).to_be_visible()
    page.get_by_role("button", name=re.compile("Pagar salários")).click()
    page.get_by_role("button", name="Registar pagamento").click()
    expect(page.get_by_text(re.compile("Pagamento lançado"))).to_be_visible()


def test_ficha_com_tabelas_de_retencao_de_irs(page: Page):
    page.goto("/contabilidade#salarios")
    linha = page.locator("#sec-salarios tr", has_text="João Pereira").first
    expect(linha).to_contain_text("Tabela I")
    linha.get_by_role("button", name="Editar").click()
    expect(page.locator("#func-irs-situacao")).to_be_visible()
    expect(page.locator("#func-irs")).to_be_hidden()
    expect(page.locator("#func-irs-conjuge")).to_be_hidden()
    page.locator("#func-irs-situacao").select_option("casado_unico")
    expect(page.locator("#func-irs-conjuge")).to_be_visible()
    page.locator("#func-irs-dependentes").fill("2")
    page.get_by_role("button", name="Guardar").click()
    expect(page.get_by_text("Ficha atualizada.")).to_be_visible()
    expect(page.locator("#sec-salarios tr", has_text="João Pereira").first).to_contain_text("Tabela III · 2 dep.")
    # Taxa manual (por exemplo, residentes nas regiões autónomas)
    page.locator("#sec-salarios tr", has_text="João Pereira").first.get_by_role("button", name="Editar").click()
    page.locator("#func-irs-modo").select_option("manual")
    expect(page.locator("#func-irs-situacao")).to_be_hidden()
    page.locator("#func-irs").fill("7")
    page.get_by_role("button", name="Guardar").click()
    expect(page.locator("#sec-salarios tr", has_text="João Pereira").first).to_contain_text("7% (manual)")


def test_acrescimo_e_balanco(page: Page):
    page.goto("/contabilidade#periodizacao")
    expect(page.get_by_role("heading", name="Diferimentos", exact=True)).to_be_visible()
    page.get_by_role("button", name="Novo acréscimo").click()
    page.locator("#acr-descricao").fill("Eletricidade por faturar")
    page.locator("#acr-valor").fill("210")
    page.get_by_role("button", name="Lançar", exact=True).click()
    expect(page.get_by_text(re.compile("Acréscimo lançado"))).to_be_visible()
    expect(page.locator("tr", has_text="Eletricidade por faturar")).to_be_visible()
    page.goto("/contabilidade#balanco")
    expect(page.get_by_role("heading", name=re.compile("Balanço em"))).to_be_visible()
    totais = page.locator("#sec-balanco tr.total td.num").all_inner_texts()
    assert len(totais) == 2 and totais[0] == totais[1]


def test_registar_pagamento_de_fatura_nos_fornecedores(page: Page):
    page.goto("/contabilidade#fornecedores")
    expect(page.get_by_role("heading", name="Faturas por pagar")).to_be_visible()
    linhas = page.locator("#sec-fornecedores tbody tr", has=page.get_by_role("button", name="Registar pagamento"))
    antes = linhas.count()
    assert antes > 0
    linhas.first.get_by_role("button", name="Registar pagamento").click()
    page.locator("#pag-fat-conta").select_option(value="111")
    page.get_by_role("button", name="Registar pagamento").last.click()
    expect(page.get_by_text(re.compile("Pagamento lançado"))).to_be_visible()
    expect(linhas).to_have_count(antes - 1)


# ---------- Pesquisa global, impressão e conta ----------

def test_pesquisa_global_com_ctrl_k(page: Page, erros_js):
    page.goto("/contabilidade#resumo")
    page.wait_for_load_state("networkidle")
    page.keyboard.press("Control+k")
    campo = page.locator("#paleta-campo")
    expect(campo).to_be_focused()
    campo.fill("balancet")
    expect(page.get_by_role("option", name="Balancete")).to_be_visible()
    page.keyboard.press("Enter")
    expect(page.get_by_role("heading", name="Balancete", level=1)).to_be_visible()
    assert page.url.endswith("#balancete")
    # Também encontra faturas pelo fornecedor, e o botão da barra lateral abre a mesma pesquisa
    page.get_by_role("button", name="Pesquisar").click()
    page.locator("#paleta-campo").fill("talho")
    expect(page.locator(".paleta-item").first).to_contain_text("Talho")
    page.keyboard.press("Escape")
    expect(page.locator("#paleta-campo")).to_have_count(0)
    assert erros_js == []


def test_impressao_dos_relatorios(page: Page):
    page.goto("/contabilidade#balancete")
    expect(page.get_by_role("heading", name="Balancete analítico")).to_be_visible()
    expect(page.get_by_role("button", name="Imprimir")).to_be_visible()
    page.evaluate("prepararImpressao()")
    page.emulate_media(media="print")
    cabecalho = page.locator("#cabecalho-impressao")
    expect(cabecalho).to_be_visible()
    expect(cabecalho).to_contain_text("Balancete")
    expect(cabecalho).to_contain_text("Período:")
    expect(page.locator("#barra")).to_be_hidden()
    expect(page.get_by_role("button", name="Imprimir")).to_be_hidden()


def test_conta_na_demonstracao_fica_so_de_leitura(page: Page):
    page.goto("/conta")
    expect(page.get_by_role("heading", name="A minha conta")).to_be_visible()
    expect(page.get_by_role("heading", name="Verificação em dois passos")).to_be_visible()
    expect(page.get_by_role("button", name="Ligar a verificação em dois passos")).to_be_disabled()
    expect(page.get_by_label("Alertas de prazos fiscais")).to_be_disabled()


def test_subscricao_mostra_o_plano_e_os_planos(page: Page):
    page.goto("/subscricao")
    expect(page.get_by_role("heading", name="Contabilidade", level=2)).to_be_visible()
    expect(page.locator(".plano-cartao")).to_have_count(3)
    # Na demonstração não há pagamentos online: os botões ficam desativados e a aplicação sem limites
    expect(page.get_by_role("button", name="Plano atual")).to_be_disabled()
    expect(page.get_by_text("Os pagamentos online ainda não estão ativos")).to_be_visible()
    page.locator("#conta-botao").or_(page.locator(".selo-demo")).first.wait_for()


# ---------- App instalável e sem rede ----------

def test_service_worker_e_funcionamento_sem_rede(page: Page, context):
    page.goto("/contabilidade#balancete")
    expect(page.get_by_role("heading", name="Balancete analítico")).to_be_visible()
    # O service worker fica ativo e passa a controlar a página
    # (wait_for_function avalia texto como código, o que a política de segurança da página bloqueia)
    for _ in range(60):
        if page.evaluate("() => !!(navigator.serviceWorker && navigator.serviceWorker.controller)"):
            break
        page.wait_for_timeout(250)
    else:
        raise AssertionError("O service worker não ficou a controlar a página")
    assert page.locator('link[rel="manifest"]').count() == 1
    page.reload()
    expect(page.get_by_role("heading", name="Balancete analítico")).to_be_visible()

    # Sem rede: a página e os dados já vistos continuam disponíveis, com o aviso no topo
    context.set_offline(True)
    page.reload()
    expect(page.get_by_role("heading", name="Balancete analítico")).to_be_visible(timeout=15000)
    expect(page.get_by_text("Sem ligação à Internet")).to_be_visible()
    # Uma página nunca aberta neste dispositivo mostra a página "Sem ligação"
    page.goto("/conta")
    expect(page.get_by_role("heading", name="Sem ligação à Internet")).to_be_visible()
    context.set_offline(False)


def test_botao_da_camera_no_telemovel(browser, base_url):
    contexto = browser.new_context(base_url=base_url, viewport={"width": 390, "height": 844},
                                   has_touch=True, is_mobile=True)
    pagina = contexto.new_page()
    pagina.goto("/")
    botao = pagina.get_by_text("Fotografar fatura")
    expect(botao).to_be_visible()
    assert pagina.locator("#camera").get_attribute("capture") == "environment"
    contexto.close()


def test_migrar_de_outro_programa_mostra_a_previa(page: Page, tmp_path):
    page.goto("/contabilidade#plano")
    expect(page.get_by_role("heading", name="Migrar de outro programa")).to_be_visible()
    falso = tmp_path / "saft.xml"
    falso.write_text('<?xml version="1.0"?><AuditFile><Header><TaxAccountingBasis>F</TaxAccountingBasis>'
                     '</Header></AuditFile>', encoding="utf-8")
    page.locator("#migracao-ficheiro").set_input_files(str(falso))
    page.get_by_role("button", name="Analisar o ficheiro").click()
    expect(page.get_by_text("Este SAF-T é de faturação")).to_be_visible()


def test_centros_de_custo_na_demonstracao(page: Page):
    page.goto("/contabilidade#centros")
    expect(page.get_by_role("heading", name="Resultado por centro de custo")).to_be_visible()
    tabela = page.locator(".tabela-centros")
    expect(tabela).to_contain_text("SALA")
    expect(tabela).to_contain_text("TAKE")
    # Nos lançamentos, as linhas de gastos e rendimentos têm o seletor do centro
    page.goto("/contabilidade#lancamentos")
    expect(page.locator(".seletor-centro").first).to_be_attached()


def test_area_de_gestao_e_seletor_de_areas(page: Page, erros_js):
    page.goto("/gestao#painel")
    expect(page.get_by_role("heading", name="Painel de gestão")).to_be_visible()
    expect(page.locator(".indicadores.seis .indicador")).to_have_count(6)
    # O seletor mostra a Gestão ativa e os grupos da Gestão
    expect(page.locator('.seletor-area a[data-area="gestao"]')).to_have_attribute("aria-current", "true")
    expect(page.locator(".lateral-nav a[data-chave='tesouraria']")).to_be_visible()
    expect(page.locator(".lateral-nav a[data-chave='balancete']")).to_be_hidden()
    # Mudar para a Contabilidade troca os grupos da barra lateral
    page.locator('.seletor-area a[data-area="contabilidade"]').click()
    expect(page.locator(".lateral-nav a[data-chave='balancete']")).to_be_visible()
    expect(page.locator(".lateral-nav a[data-chave='tesouraria']")).to_be_hidden()
    for secao, titulo in (("artigos", "Artigos"), ("clientes", "Clientes"), ("tesouraria", "Saldo previsto por semana"),
                          ("orcamento", "Orçado e real")):
        page.goto(f"/gestao#{secao}")
        expect(page.get_by_role("heading", name=titulo, exact=True)).to_be_visible()
    assert erros_js == []


def test_declaracoes_de_remuneracoes(page: Page):
    page.goto("/contabilidade#salarios")
    page.get_by_role("button", name="Declarações").first.click()
    janela = page.locator("#janela-temporaria")
    expect(janela).to_contain_text("RC4008")
    expect(janela.get_by_role("link", name="Descarregar o ficheiro")).to_be_visible()
    expect(janela).to_contain_text("declaração mensal de remunerações")
