"""
Chaves da API pública (/api/v1), uma ou mais por empresa, criadas pelos administradores.

- A chave tem o formato "dobra_" + 40 caracteres aleatórios; só o hash SHA-256 fica guardado e a
  chave completa é mostrada uma única vez. Os primeiros caracteres (prefixo) identificam-na na lista.
- Permissão "leitura" (só consultar) ou "escrita" (também enviar movimentos bancários, etc.).
- Revogar é imediato e definitivo: o programa que a usava deixa de ter acesso.

As funções não fazem commit: quem as chama decide quando gravar.
"""

import hashlib
import secrets
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modelos.tabelas import PERMISSAO_ESCRITA, PERMISSAO_LEITURA, ChaveApi

PREFIXO = "dobra_"
PERMISSOES = (PERMISSAO_LEITURA, PERMISSAO_ESCRITA)
MAX_CHAVES_ATIVAS = 10
# O "último uso" grava-se no máximo uma vez por minuto (não escrever na base de dados a cada pedido)
INTERVALO_ULTIMO_USO = timedelta(minutes=1)


class ErroChave(Exception):
    """Mensagem pronta a mostrar."""


def _hash(chave: str) -> str:
    return hashlib.sha256(chave.encode()).hexdigest()


def listar(db: Session, empresa_id: int) -> list[ChaveApi]:
    return list(db.scalars(select(ChaveApi).where(ChaveApi.empresa_id == empresa_id, ChaveApi.revogada_em.is_(None))
                           .order_by(ChaveApi.criada_em.desc(), ChaveApi.id.desc())))


def criar(db: Session, empresa_id: int, nome: str, permissao: str, utilizador_id: int | None) -> tuple[ChaveApi, str]:
    """Cria a chave e devolve-a com o texto completo (a única vez em que existe fora do programa)."""
    nome = " ".join((nome or "").split())
    if not nome:
        raise ErroChave("Dê um nome à chave (ex.: o programa que a vai usar).")
    if permissao not in PERMISSOES:
        raise ErroChave("Permissão desconhecida.")
    if len(listar(db, empresa_id)) >= MAX_CHAVES_ATIVAS:
        raise ErroChave(f"Já tem {MAX_CHAVES_ATIVAS} chaves ativas: revogue as que já não usa.")
    texto = PREFIXO + secrets.token_urlsafe(30)
    chave = ChaveApi(empresa_id=empresa_id, nome=nome[:80], prefixo=texto[:12], hash_chave=_hash(texto),
                     permissao=permissao, criada_por_id=utilizador_id, criada_em=datetime.now())
    db.add(chave)
    db.flush()
    return chave, texto


def autenticar(db: Session, texto: str | None, agora: datetime | None = None) -> ChaveApi | None:
    """A chave ativa correspondente ao texto recebido, ou None."""
    if not texto or not texto.startswith(PREFIXO) or len(texto) > 100:
        return None
    chave = db.scalar(select(ChaveApi).where(ChaveApi.hash_chave == _hash(texto)))
    if chave is None or chave.revogada_em is not None:
        return None
    agora = agora or datetime.now()
    if chave.ultimo_uso is None or agora - chave.ultimo_uso >= INTERVALO_ULTIMO_USO:
        chave.ultimo_uso = agora
    return chave


def revogar(db: Session, empresa_id: int, chave_id: int) -> ChaveApi:
    chave = db.scalar(select(ChaveApi).where(ChaveApi.id == chave_id, ChaveApi.empresa_id == empresa_id,
                                             ChaveApi.revogada_em.is_(None)))
    if chave is None:
        raise ErroChave("Chave não encontrada.")
    chave.revogada_em = datetime.now()
    return chave


def chave_dict(chave: ChaveApi) -> dict:
    return {"id": chave.id, "nome": chave.nome, "prefixo": chave.prefixo, "permissao": chave.permissao,
            "criada_em": chave.criada_em, "ultimo_uso": chave.ultimo_uso}
