"""
Armazenamento dos ficheiros PDF em uploads/.

Os dados das faturas estão na base de dados; aqui só se gerem os ficheiros.
Cada empresa tem a sua pasta: uploads/empresa-<n>/<id>__<nome>.pdf.
"""

import re
import unicodedata
import uuid
from pathlib import Path

from app import config

PADRAO_ID = re.compile(r"[a-f0-9]{12}")


def nome_seguro(nome: str) -> str:
    """Remove caracteres problemáticos do nome original do ficheiro."""
    base = Path(nome).stem
    # "Março" passa a "Marco": tira os acentos em vez de cortar as letras
    base = unicodedata.normalize("NFKD", base).encode("ascii", "ignore").decode()
    base = re.sub(r"[^A-Za-z0-9_-]+", "-", base).strip("-")
    return base[:60] or "fatura"


def id_valido(id_fatura: str) -> bool:
    return bool(PADRAO_ID.fullmatch(id_fatura))


def _pasta() -> Path:
    # Lido a cada chamada para os testes poderem trocar a pasta por uma temporária
    config.UPLOAD_DIR.mkdir(exist_ok=True)
    return config.UPLOAD_DIR


def guardar_pdf(conteudo: bytes, nome_original: str, empresa_id: int) -> tuple[str, str]:
    """Guarda o PDF na pasta da empresa e devolve (id da fatura, caminho relativo a uploads/)."""
    id_fatura = uuid.uuid4().hex[:12]
    pasta_empresa = _pasta() / f"empresa-{int(empresa_id)}"
    pasta_empresa.mkdir(exist_ok=True)
    ficheiro = f"{pasta_empresa.name}/{id_fatura}__{nome_seguro(nome_original)}.pdf"
    (_pasta() / ficheiro).write_bytes(conteudo)
    return id_fatura, ficheiro


def caminho_pdf(ficheiro: str) -> Path | None:
    """Devolve o caminho do PDF, recusando qualquer caminho que saia da pasta uploads/."""
    base = _pasta().resolve()
    caminho = (base / ficheiro).resolve()
    if not caminho.is_relative_to(base):  # impede "../../outro-ficheiro"
        return None
    return caminho if caminho.is_file() else None


def apagar_pdf(ficheiro: str) -> None:
    caminho = caminho_pdf(ficheiro)
    if caminho:
        caminho.unlink()
