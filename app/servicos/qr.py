"""
Leitura do código QR das faturas portuguesas (Portaria n.º 195/2020).

Desde 2022 as faturas certificadas têm um QR com os dados fiscais em texto:
    A:500000000*B:999999990*C:PT*D:FT*...*O:123.00*...
Cada campo é "código:valor" e os campos estão separados por "*".

Ler o QR é gratuito e determinístico (dá sempre o mesmo resultado), por isso
é usado antes da IA. O QR não traz o nome do fornecedor nem a data de
vencimento: esses dois campos continuam a vir da IA (ou da cache de
fornecedores, na Fase 3).
"""

import logging
from datetime import datetime

import pypdfium2 as pdfium
import zxingcpp

logger = logging.getLogger(__name__)

PAGINAS_A_PROCURAR = 3  # o QR está quase sempre na primeira ou na última página
ESCALA_RENDER = 3       # 3 x 72 ppp = 216 ppp: nítido o suficiente para QR pequenos

# O QR traz a base e o valor do IVA, mas não a taxa. A taxa deduz-se da posição
# do campo (reduzida, intermédia, normal) e da região fiscal.
# Atualizar esta tabela se as taxas mudarem no Orçamento do Estado.
TAXAS_POR_REGIAO = {
    "PT": {"reduzida": 6, "intermedia": 13, "normal": 23},     # Continente
    "PT-AC": {"reduzida": 4, "intermedia": 9, "normal": 16},   # Açores
    "PT-MA": {"reduzida": 4, "intermedia": 12, "normal": 22},  # Madeira
}

# Grupos de IVA no QR: I = 1.º espaço fiscal, J = Açores, K = Madeira.
# Em cada grupo: 1 = região, 2 = base isenta, 3/4 = reduzida, 5/6 = intermédia, 7/8 = normal.
GRUPOS_IVA = ("I", "J", "K")
CAMPOS_TAXA = (("reduzida", "3", "4"), ("intermedia", "5", "6"), ("normal", "7", "8"))


class QRInvalido(ValueError):
    """O texto não é um QR de fatura válido da AT."""


def _numero(campos: dict, codigo: str) -> float:
    valor = campos.get(codigo)
    return round(float(valor), 2) if valor else 0.0


def interpretar(texto: str) -> dict:
    """
    Converte o texto do QR num dicionário com os dados da fatura.
    Função pura (sem ficheiros nem rede), por isso é fácil de testar.
    """
    campos = {}
    for parte in texto.strip().split("*"):
        codigo, separador, valor = parte.partition(":")
        if separador:
            campos[codigo.strip()] = valor.strip()

    # Campos obrigatórios em qualquer QR da AT
    for obrigatorio in ("A", "D", "F", "G", "O"):
        if obrigatorio not in campos:
            raise QRInvalido(f"Falta o campo {obrigatorio} no QR.")

    try:
        data_emissao = datetime.strptime(campos["F"], "%Y%m%d").date().isoformat()
        linhas_iva = []
        base_total = _numero(campos, "L")  # L = valor não sujeito a IVA

        for grupo in GRUPOS_IVA:
            regiao = campos.get(f"{grupo}1")
            if not regiao or regiao == "0":
                continue
            taxas = TAXAS_POR_REGIAO.get(regiao)
            if taxas is None:
                raise QRInvalido(f"Região fiscal desconhecida no QR: {regiao}")

            isenta = _numero(campos, f"{grupo}2")
            if isenta:
                linhas_iva.append({"taxa": 0.0, "base": isenta, "valor": 0.0})
                base_total += isenta

            for nome_taxa, campo_base, campo_iva in CAMPOS_TAXA:
                base = _numero(campos, f"{grupo}{campo_base}")
                if base:
                    valor = _numero(campos, f"{grupo}{campo_iva}")
                    linhas_iva.append({"taxa": float(taxas[nome_taxa]), "base": base, "valor": valor})
                    base_total += base

        return {
            "nif_fornecedor": campos["A"],
            "nif_cliente": campos.get("B"),
            "tipo_documento": campos["D"],
            "numero_fatura": campos["G"],
            "atcud": campos.get("H"),
            "data_emissao": data_emissao,
            "valor_sem_iva": round(base_total, 2),
            "iva": linhas_iva,
            "total": _numero(campos, "O"),
        }
    except ValueError as erro:
        # Datas ou números mal formatados
        raise QRInvalido(f"QR com valores inválidos: {erro}") from erro


def _parece_qr_da_at(texto: str) -> bool:
    return texto.startswith("A:") and "*B:" in texto


def ler_qr(conteudo: bytes) -> dict | None:
    """
    Procura o QR da AT nas primeiras páginas do PDF.
    Nunca lança exceções: se não houver QR (ou não for legível) devolve None,
    e a fatura segue o caminho normal pela IA.
    """
    try:
        pdf = pdfium.PdfDocument(conteudo)
    except pdfium.PdfiumError as erro:
        logger.info("Não foi possível abrir o PDF para procurar o QR: %s", erro)
        return None

    try:
        total_paginas = len(pdf)
        if total_paginas == 0:
            return None
        # Primeiras páginas e a última (onde costuma estar o rodapé fiscal)
        indices = sorted(set(range(min(PAGINAS_A_PROCURAR, total_paginas))) | {total_paginas - 1})

        for indice in indices:
            imagem = pdf[indice].render(scale=ESCALA_RENDER, grayscale=True).to_pil()
            for codigo in zxingcpp.read_barcodes(imagem, formats=zxingcpp.BarcodeFormat.QRCode):
                if not _parece_qr_da_at(codigo.text):
                    continue  # outros QR (site do fornecedor, pagamentos...) são ignorados
                try:
                    return interpretar(codigo.text)
                except QRInvalido as erro:
                    logger.warning("QR da AT encontrado mas inválido: %s", erro)
    except Exception:
        logger.exception("Erro inesperado ao procurar o QR")
    finally:
        pdf.close()

    return None
