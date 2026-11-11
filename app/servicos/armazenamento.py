"""
Armazenamento dos ficheiros (PDFs das faturas e cópias de segurança).

Os dados das faturas estão na base de dados; aqui só se gerem os ficheiros.
Há duas implementações com a mesma interface:
- Local: pasta uploads/ (desenvolvimento e testes);
- S3: bucket compatível com S3, como o Cloudflare R2 (produção). O disco dos
  contentores nos alojamentos apaga-se a cada nova versão; o bucket não.

O resto da aplicação só usa as funções deste módulo (guardar_pdf, ler_pdf,
apagar_pdf) e não sabe onde os ficheiros ficam.

Cada empresa tem a sua "pasta": empresa-<n>/<id>__<nome>.pdf.
"""

import logging
import os
import re
import unicodedata
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from app import config

logger = logging.getLogger(__name__)

PADRAO_ID = re.compile(r"[a-f0-9]{12}")

# Chaves aceites para PDFs. As faturas anteriores à Fase 5 não têm a pasta da empresa.
PADRAO_CHAVE_PDF = re.compile(r"(empresa-\d+/)?[a-f0-9]{12}__[A-Za-z0-9_-]{1,60}\.pdf")


class Armazenamento(Protocol):
    def guardar(self, chave: str, conteudo: bytes, tipo: str) -> None: ...
    def ler(self, chave: str) -> bytes | None: ...
    def apagar(self, chave: str) -> None: ...
    def listar(self, prefixo: str) -> list[str]: ...


class ArmazenamentoLocal:
    """Ficheiros numa pasta do disco."""

    def __init__(self, pasta: Path | None = None):
        self._pasta_fixa = pasta

    def _pasta(self) -> Path:
        # Sem pasta fixa, lê config.UPLOAD_DIR a cada chamada (os testes trocam-na por uma temporária)
        pasta = self._pasta_fixa or config.UPLOAD_DIR
        pasta.mkdir(parents=True, exist_ok=True)
        return pasta

    def _caminho(self, chave: str) -> Path | None:
        """Recusa qualquer chave que saia da pasta (por exemplo "../../outro-ficheiro")."""
        base = self._pasta().resolve()
        caminho = (base / chave).resolve()
        return caminho if caminho.is_relative_to(base) else None

    def guardar(self, chave: str, conteudo: bytes, tipo: str) -> None:
        caminho = self._caminho(chave)
        if caminho is None:
            raise ValueError("Caminho inválido.")
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_bytes(conteudo)

    def ler(self, chave: str) -> bytes | None:
        caminho = self._caminho(chave)
        return caminho.read_bytes() if caminho and caminho.is_file() else None

    def apagar(self, chave: str) -> None:
        caminho = self._caminho(chave)
        if caminho and caminho.is_file():
            caminho.unlink()

    def listar(self, prefixo: str) -> list[str]:
        base = self._pasta()
        pasta = base / prefixo
        if not pasta.is_dir():
            return []
        return sorted(p.relative_to(base).as_posix() for p in pasta.rglob("*") if p.is_file())


class ArmazenamentoS3:
    """Ficheiros num bucket S3 / Cloudflare R2. O bucket é privado: os PDFs passam sempre pela API."""

    def __init__(self, cliente, bucket: str):
        self.cliente = cliente
        self.bucket = bucket

    def guardar(self, chave: str, conteudo: bytes, tipo: str) -> None:
        self.cliente.put_object(Bucket=self.bucket, Key=chave, Body=conteudo, ContentType=tipo)

    def ler(self, chave: str) -> bytes | None:
        try:
            resposta = self.cliente.get_object(Bucket=self.bucket, Key=chave)
        except self.cliente.exceptions.NoSuchKey:
            return None
        return resposta["Body"].read()

    def apagar(self, chave: str) -> None:
        self.cliente.delete_object(Bucket=self.bucket, Key=chave)

    def listar(self, prefixo: str) -> list[str]:
        chaves = []
        paginador = self.cliente.get_paginator("list_objects_v2")
        for pagina in paginador.paginate(Bucket=self.bucket, Prefix=prefixo):
            chaves.extend(objeto["Key"] for objeto in pagina.get("Contents", []))
        return sorted(chaves)


@lru_cache(maxsize=1)
def _cliente_s3():
    # Importado só aqui: em desenvolvimento e nos testes o boto3 nem é carregado
    import boto3

    return boto3.client(
        "s3",
        endpoint_url=config.S3_ENDPOINT_URL,
        region_name=config.S3_REGIAO,
        aws_access_key_id=os.getenv("S3_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("S3_SECRET_ACCESS_KEY"),
    )


_local = ArmazenamentoLocal()


def atual() -> Armazenamento:
    """Implementação em uso: S3 se houver bucket configurado, senão a pasta local."""
    if config.S3_BUCKET:
        return ArmazenamentoS3(_cliente_s3(), config.S3_BUCKET)
    return _local


# ---------- PDFs das faturas ----------

def nome_seguro(nome: str) -> str:
    """Remove caracteres problemáticos do nome original do ficheiro."""
    base = Path(nome).stem
    # "Março" passa a "Marco": tira os acentos em vez de cortar as letras
    base = unicodedata.normalize("NFKD", base).encode("ascii", "ignore").decode()
    base = re.sub(r"[^A-Za-z0-9_-]+", "-", base).strip("-")
    return base[:60] or "fatura"


def id_valido(id_fatura: str) -> bool:
    return bool(PADRAO_ID.fullmatch(id_fatura))


def chave_pdf_valida(chave: str) -> bool:
    return bool(PADRAO_CHAVE_PDF.fullmatch(chave))


def guardar_pdf(conteudo: bytes, nome_original: str, empresa_id: int) -> tuple[str, str]:
    """Guarda o PDF na pasta da empresa e devolve (id da fatura, chave do ficheiro)."""
    id_fatura = uuid.uuid4().hex[:12]
    chave = f"empresa-{int(empresa_id)}/{id_fatura}__{nome_seguro(nome_original)}.pdf"
    atual().guardar(chave, conteudo, "application/pdf")
    return id_fatura, chave


def ler_pdf(chave: str) -> bytes | None:
    """Conteúdo do PDF, ou None se não existir. Chaves fora do formato esperado são recusadas."""
    if not chave_pdf_valida(chave):
        return None
    return atual().ler(chave)


def apagar_pdf(chave: str) -> None:
    """
    Apaga o PDF. Chama-se depois de a base de dados confirmar a operação, por isso uma
    falha aqui não deve virar erro para o utilizador: fica só um ficheiro órfão, registado.
    """
    if not chave_pdf_valida(chave):
        return
    try:
        atual().apagar(chave)
    except Exception:
        logger.exception("Não foi possível apagar o ficheiro %s", chave)
