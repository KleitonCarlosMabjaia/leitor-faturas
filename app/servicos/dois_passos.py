"""
Verificação em dois passos com códigos temporários (TOTP, RFC 6238).

Funciona com qualquer app autenticadora (Google Authenticator, Microsoft Authenticator,
1Password, Authy...): o segredo é partilhado uma vez por código QR e, a partir daí,
a app e o servidor calculam o mesmo código de 6 dígitos a cada 30 segundos.

- Aceita-se o código do passo atual e do anterior/seguinte (relógios ligeiramente desacertados).
- Cada código só serve uma vez: guarda-se o último passo usado.
- Códigos de recuperação de uso único para quem perder o telemóvel (guardados só como hash).
"""

import base64
import hashlib
import hmac
import io
import secrets
import struct
import time
from urllib.parse import quote

from app.modelos.tabelas import Utilizador

PASSO_SEGUNDOS = 30
DIGITOS = 6
EMISSOR = "Dobra"
N_CODIGOS_RECUPERACAO = 8


def novo_segredo() -> str:
    """20 bytes aleatórios em base32 (o formato das apps autenticadoras)."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _chave(segredo: str) -> bytes:
    return base64.b32decode(segredo + "=" * (-len(segredo) % 8))


def codigo(segredo: str, passo: int) -> str:
    """HOTP (RFC 4226) do passo indicado."""
    resumo = hmac.new(_chave(segredo), struct.pack(">Q", passo), hashlib.sha1).digest()
    deslocamento = resumo[-1] & 0x0F
    valor = struct.unpack(">I", resumo[deslocamento:deslocamento + 4])[0] & 0x7FFFFFFF
    return str(valor % 10**DIGITOS).zfill(DIGITOS)


def passo_atual(agora: float | None = None) -> int:
    return int((time.time() if agora is None else agora) // PASSO_SEGUNDOS)


def _limpar(texto: str) -> str:
    return "".join(c for c in (texto or "") if c.isalnum()).upper()


def verificar_codigo(
    segredo: str, introduzido: str, ultimo_passo: int | None, agora: float | None = None
) -> int | None:
    """Devolve o passo do código, se for válido e ainda não usado; senão None."""
    introduzido = _limpar(introduzido)
    if len(introduzido) != DIGITOS or not introduzido.isdigit():
        return None
    atual = passo_atual(agora)
    for passo in (atual - 1, atual, atual + 1):
        if ultimo_passo is not None and passo <= ultimo_passo:
            continue
        if hmac.compare_digest(codigo(segredo, passo), introduzido):
            return passo
    return None


def uri_aprovisionamento(segredo: str, email: str) -> str:
    rotulo = quote(f"{EMISSOR}:{email}")
    return f"otpauth://totp/{rotulo}?secret={segredo}&issuer={EMISSOR}&algorithm=SHA1&digits={DIGITOS}&period={PASSO_SEGUNDOS}"


def qr_png_base64(texto: str) -> str:
    """Código QR em PNG (data URI) para a app autenticadora ler."""
    import zxingcpp
    from PIL import Image

    codigo_qr = zxingcpp.create_barcode(texto, zxingcpp.BarcodeFormat.QRCode)
    imagem = memoryview(codigo_qr.to_image(scale=6, add_quiet_zones=True))
    png = Image.frombytes("L", (imagem.shape[1], imagem.shape[0]), imagem.tobytes())
    saida = io.BytesIO()
    png.save(saida, "PNG")
    return "data:image/png;base64," + base64.b64encode(saida.getvalue()).decode()


def _hash(codigo_recuperacao: str) -> str:
    return hashlib.sha256(_limpar(codigo_recuperacao).encode()).hexdigest()


def novos_codigos_recuperacao() -> tuple[list[str], list[str]]:
    """Devolve (códigos em claro para mostrar uma vez, hashes para guardar)."""
    alfabeto = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # sem 0/O e 1/I, que se confundem
    codigos = []
    for _ in range(N_CODIGOS_RECUPERACAO):
        bruto = "".join(secrets.choice(alfabeto) for _ in range(10))
        codigos.append(f"{bruto[:5]}-{bruto[5:]}")
    return codigos, [_hash(c) for c in codigos]


def usar_codigo(utilizador: Utilizador, introduzido: str, agora: float | None = None) -> bool:
    """
    Confirma o segundo passo com um código da app ou de recuperação.
    Um código de recuperação usado deixa de valer; um código da app não pode ser reutilizado.
    """
    if not utilizador.totp_ativo or not utilizador.totp_segredo:
        return True
    passo = verificar_codigo(utilizador.totp_segredo, introduzido, utilizador.totp_ultimo_passo, agora)
    if passo is not None:
        utilizador.totp_ultimo_passo = passo
        return True
    resumo = _hash(introduzido)
    guardados = list(utilizador.codigos_recuperacao or [])
    for i, guardado in enumerate(guardados):
        if hmac.compare_digest(guardado, resumo):
            del guardados[i]
            utilizador.codigos_recuperacao = guardados  # nova lista: o SQLAlchemy deteta a alteração
            return True
    return False
