"""
Extração de texto de PDFs.

Primeiro tenta o pypdf (rápido e leve). Se o texto vier vazio ou mal
formatado, tenta o pdfplumber, que respeita melhor a posição das palavras.
"""

import io
import logging

import pdfplumber
from pypdf import PdfReader

logger = logging.getLogger(__name__)

# Abaixo deste número de letras e dígitos consideramos que o PDF não tem texto
MINIMO_CARACTERES = 40


class PDFSemTexto(Exception):
    """O PDF não tem texto selecionável (provavelmente é uma digitalização)."""


class PDFIlegivel(Exception):
    """O PDF está corrompido ou protegido e não pode ser aberto."""


def _caracteres_uteis(texto: str) -> int:
    return sum(c.isalnum() for c in texto)


def _parece_mal_formatado(texto: str) -> bool:
    """
    Deteta texto com as palavras coladas, um problema comum do pypdf
    em PDFs gerados por alguns programas de faturação.
    """
    palavras = texto.split()
    if not palavras:
        return True
    return sum(len(p) for p in palavras) / len(palavras) > 15


def _texto_pypdf(conteudo: bytes) -> str:
    leitor = PdfReader(io.BytesIO(conteudo))
    return "\n".join(pagina.extract_text() or "" for pagina in leitor.pages)


def _texto_pdfplumber(conteudo: bytes) -> str:
    with pdfplumber.open(io.BytesIO(conteudo)) as pdf:
        return "\n".join(pagina.extract_text() or "" for pagina in pdf.pages)


def extrair_texto(conteudo: bytes) -> str:
    """Devolve o texto do PDF ou lança PDFSemTexto / PDFIlegivel."""
    abriu = False  # passa a True se pelo menos uma biblioteca abrir o ficheiro
    texto = ""

    try:
        texto = _texto_pypdf(conteudo)
        abriu = True
    except Exception as erro:  # PDFs estragados podem lançar vários tipos de erro
        logger.warning("pypdf não conseguiu ler o PDF: %s", erro)

    if _caracteres_uteis(texto) < MINIMO_CARACTERES or _parece_mal_formatado(texto):
        try:
            alternativo = _texto_pdfplumber(conteudo)
            abriu = True
            # Fica com o resultado que tiver mais conteúdo útil
            if _caracteres_uteis(alternativo) > _caracteres_uteis(texto):
                texto = alternativo
        except Exception as erro:
            logger.warning("pdfplumber não conseguiu ler o PDF: %s", erro)

    if not abriu:
        raise PDFIlegivel("Não foi possível abrir o PDF. Pode estar corrompido ou protegido por palavra-passe.")

    if _caracteres_uteis(texto) < MINIMO_CARACTERES:
        raise PDFSemTexto(
            "Este PDF não tem texto selecionável: parece ser uma digitalização ou fotografia. "
            "Para já só são lidas faturas em PDF digital."
        )
    return texto.strip()
