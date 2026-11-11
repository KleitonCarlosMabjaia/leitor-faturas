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

@pytest.mark.parametrize("caminho", ["/", "/dashboard", "REVISAO"])
def test_paginas_sem_erros_de_javascript(page: Page, api, erros_js, caminho):
    if caminho == "REVISAO":
        caminho = f"/faturas/{_uma_por_rever(api)['id']}"
    page.goto(caminho)
    page.wait_for_load_state("networkidle")
    assert erros_js == []


@pytest.mark.parametrize("caminho", ["/", "/dashboard", "REVISAO"])
def test_telemovel_sem_deslocamento_horizontal(page: Page, api, caminho):
    if caminho == "REVISAO":
        caminho = f"/faturas/{_uma_por_rever(api)['id']}"
    page.set_viewport_size({"width": 375, "height": 800})
    page.goto(caminho)
    page.wait_for_load_state("networkidle")
    largura = page.evaluate("document.documentElement.scrollWidth")
    assert largura <= 375, f"A página tem {largura}px de largura num ecrã de 375px"
