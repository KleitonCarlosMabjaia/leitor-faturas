"""
Fotografias de faturas (câmara do telemóvel) convertidas em PDF.

O resto do processo (QR da AT, IA, revisão, armazenamento) trabalha com PDFs, por isso a foto
é convertida aqui: orientação corrigida pelos dados EXIF, cores em RGB, tamanho limitado e uma
página PDF com a resolução certa para o QR continuar legível.
"""

import io

from PIL import Image, ImageOps, UnidentifiedImageError

FORMATOS = {"JPEG", "PNG", "WEBP"}
LADO_MAXIMO = 2400          # px: suficiente para ler o QR e o texto, sem PDFs enormes
ASSINATURAS = (b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"RIFF")


class ImagemInvalida(ValueError):
    pass


def parece_imagem(conteudo: bytes) -> bool:
    return conteudo.startswith(ASSINATURAS)


def para_pdf(conteudo: bytes) -> bytes:
    try:
        imagem = Image.open(io.BytesIO(conteudo))
        if imagem.format not in FORMATOS:
            raise ImagemInvalida("Formato de imagem não suportado. Use JPEG, PNG ou WebP.")
        imagem.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ImagemInvalida("A imagem está danificada ou não é suportada.")
    imagem = ImageOps.exif_transpose(imagem)           # fotos tiradas com o telemóvel de lado
    if imagem.mode not in ("RGB", "L"):
        imagem = imagem.convert("RGB")
    imagem.thumbnail((LADO_MAXIMO, LADO_MAXIMO))
    saida = io.BytesIO()
    # 200 ppp: uma folha A4 fotografada fica com um tamanho de página realista
    imagem.save(saida, "PDF", resolution=200)
    return saida.getvalue()
