"""
Exportação das faturas para CSV e Excel.

Os dois formatos têm as mesmas colunas. As colunas de IVA são criadas para
cada taxa que aparece nas faturas exportadas (ex.: Base 6%, IVA 6%, Base 23%...),
que é como os contabilistas costumam pedir os dados.
"""

import csv
import io
from datetime import date

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from app.modelos.tabelas import Fatura

FORMATO_EUROS = '#,##0.00 "€"'
FORMATO_DATA = "dd/mm/yyyy"


def _seguro(texto: str | None) -> str:
    """
    Evita "CSV injection": os textos vêm de PDFs de terceiros e, se começarem
    por = + - @, o Excel executa-os como fórmulas. Um apóstrofo à frente
    faz o Excel tratá-los como texto simples.
    """
    texto = texto or ""
    return "'" + texto if texto[:1] in ("=", "+", "-", "@", "\t", "\r") else texto


def _texto_taxa(taxa: float) -> str:
    return f"{taxa:g}%"  # 23.0 -> "23%", 5.5 -> "5.5%"


def _taxas(faturas: list[Fatura]) -> list[float]:
    return sorted({linha["taxa"] for f in faturas for linha in (f.linhas_iva or [])})


def _linhas(faturas: list[Fatura]) -> tuple[list[str], list[list]]:
    """Cabeçalho e linhas com valores "crus" (datas como date, valores como float)."""
    taxas = _taxas(faturas)
    cabecalho = [
        "Data de emissão", "Fornecedor", "NIF", "Número", "ATCUD", "Vencimento",
        "Estado", "Paga", "Valor sem IVA",
    ]
    for taxa in taxas:
        cabecalho += [f"Base {_texto_taxa(taxa)}", f"IVA {_texto_taxa(taxa)}"]
    cabecalho += ["Total IVA", "Total"]

    linhas = []
    for f in faturas:
        por_taxa = {}
        for linha in f.linhas_iva or []:
            base, valor = por_taxa.get(linha["taxa"], (0.0, 0.0))
            por_taxa[linha["taxa"]] = (base + linha["base"], valor + linha["valor"])

        linha = [
            f.data_emissao, _seguro(f.nome_fornecedor), _seguro(f.nif_fornecedor), _seguro(f.numero_fatura),
            _seguro(f.atcud if f.atcud != "0" else ""), f.data_vencimento,
            f.estado, "Sim" if f.paga else "Não", f.valor_sem_iva,
        ]
        for taxa in taxas:
            base, valor = por_taxa.get(taxa, (None, None))
            linha += [base, valor]
        linha += [f.total_iva, f.total]
        linhas.append(linha)
    return cabecalho, linhas


def gerar_csv(faturas: list[Fatura]) -> bytes:
    """
    CSV preparado para o Excel em português: separador ";", vírgula decimal,
    datas DD/MM/AAAA e BOM no início (sem ele o Excel estraga os acentos).
    """
    cabecalho, linhas = _linhas(faturas)
    saida = io.StringIO()
    escritor = csv.writer(saida, delimiter=";")
    escritor.writerow(cabecalho)
    for linha in linhas:
        escritor.writerow([_celula_csv(valor) for valor in linha])
    return ("﻿" + saida.getvalue()).encode("utf-8")


def _celula_csv(valor) -> str:
    if valor is None:
        return ""
    if isinstance(valor, date):
        return valor.strftime("%d/%m/%Y")
    if isinstance(valor, float):
        return f"{valor:.2f}".replace(".", ",")
    return str(valor)


def gerar_xlsx(faturas: list[Fatura]) -> bytes:
    cabecalho, linhas = _linhas(faturas)
    livro = Workbook()
    folha = livro.active
    folha.title = "Faturas"

    folha.append(cabecalho)
    for linha in linhas:
        folha.append(linha)

    colunas_euros = range(cabecalho.index("Valor sem IVA") + 1, len(cabecalho) + 1)
    colunas_datas = (cabecalho.index("Data de emissão") + 1, cabecalho.index("Vencimento") + 1)

    # Linha de totais, com fórmulas (o Excel recalcula se o utilizador filtrar ou editar)
    if linhas:
        ultima = len(linhas) + 1
        totais = ["Total"] + [None] * (len(cabecalho) - 1)
        for coluna in colunas_euros:
            letra = get_column_letter(coluna)
            totais[coluna - 1] = f"=SUBTOTAL(9,{letra}2:{letra}{ultima})"
        folha.append(totais)
        for celula in folha[folha.max_row]:
            celula.font = Font(bold=True)

    # Formatos
    for linha in folha.iter_rows(min_row=2):
        for coluna in colunas_euros:
            linha[coluna - 1].number_format = FORMATO_EUROS
        for coluna in colunas_datas:
            linha[coluna - 1].number_format = FORMATO_DATA

    for celula in folha[1]:
        celula.font = Font(bold=True, color="FFFFFF")
        celula.fill = PatternFill("solid", fgColor="1F7A5A")
        celula.alignment = Alignment(vertical="center", wrap_text=True)

    larguras = {"Fornecedor": 32, "Número": 20, "ATCUD": 18, "NIF": 12}
    for indice, titulo in enumerate(cabecalho, start=1):
        folha.column_dimensions[get_column_letter(indice)].width = larguras.get(titulo, 14)

    folha.freeze_panes = "A2"  # cabeçalho sempre visível
    if linhas:
        folha.auto_filter.ref = f"A1:{get_column_letter(len(cabecalho))}{len(linhas) + 1}"

    saida = io.BytesIO()
    livro.save(saida)
    return saida.getvalue()
