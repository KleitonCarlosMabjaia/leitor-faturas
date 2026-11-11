"""
Autenticação: palavras-passe, sessões e links de convite.

Regras de segurança aplicadas:
- Palavras-passe guardadas com Argon2 (lento de propósito: dificulta ataques de força bruta).
- Tokens de sessão e de convite gerados com `secrets` e guardados só como hash SHA-256:
  quem conseguir ler a base de dados não fica com tokens utilizáveis.
- Mensagem de erro igual para "email não existe" e "palavra-passe errada", e o mesmo
  trabalho de hash nos dois casos, para não revelar que emails têm conta.
- Bloqueio temporário depois de várias tentativas falhadas.
"""

import hashlib
import secrets
from datetime import datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, joinedload

from app import config
from app.modelos.tabelas import Sessao, TokenAcesso, Utilizador

_hasher = PasswordHasher()
# Hash de uma palavra-passe qualquer, usado quando o email não existe (ver entrar())
_HASH_FALSO = _hasher.hash("palavra-passe-que-ninguem-usa")


class ErroAutenticacao(Exception):
    """Erro com mensagem já pronta para mostrar ao utilizador."""


def agora() -> datetime:
    return datetime.now()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def normalizar_email(email: str) -> str:
    return email.strip().lower()


def hash_palavra_passe(palavra_passe: str) -> str:
    return _hasher.hash(palavra_passe)


def validar_palavra_passe_nova(palavra_passe: str) -> None:
    if len(palavra_passe) < config.MIN_PALAVRA_PASSE:
        raise ErroAutenticacao(f"A palavra-passe tem de ter pelo menos {config.MIN_PALAVRA_PASSE} caracteres.")
    if palavra_passe.isdigit() or palavra_passe.isalpha():
        raise ErroAutenticacao("Use letras e números (ou outros símbolos) na palavra-passe.")


# ---------- Entrar ----------

def entrar(db: Session, email: str, palavra_passe: str) -> Utilizador:
    """Confirma as credenciais. Lança ErroAutenticacao com uma mensagem genérica se falharem."""
    erro_generico = ErroAutenticacao("Email ou palavra-passe incorretos.")
    utilizador = db.scalar(
        select(Utilizador).options(joinedload(Utilizador.empresa)).where(Utilizador.email == normalizar_email(email))
    )

    if utilizador is None or utilizador.hash_palavra_passe is None:
        # Faz o mesmo trabalho que uma verificação real: o tempo de resposta não denuncia o email
        _verificar(_HASH_FALSO, palavra_passe)
        raise erro_generico

    if utilizador.bloqueado_ate and utilizador.bloqueado_ate > agora():
        raise ErroAutenticacao(
            "Demasiadas tentativas falhadas. Tente novamente daqui a alguns minutos."
        )

    if not _verificar(utilizador.hash_palavra_passe, palavra_passe):
        utilizador.tentativas_falhadas += 1
        if utilizador.tentativas_falhadas >= config.MAX_TENTATIVAS_LOGIN:
            utilizador.bloqueado_ate = agora() + timedelta(minutes=config.BLOQUEIO_MINUTOS)
            utilizador.tentativas_falhadas = 0
        db.commit()
        raise erro_generico

    if not utilizador.ativo or not utilizador.empresa.ativa:
        raise ErroAutenticacao("Esta conta está desativada. Fale com o administrador da sua empresa.")

    # Se o Argon2 tiver passado a usar parâmetros mais fortes, atualiza o hash guardado
    if _hasher.check_needs_rehash(utilizador.hash_palavra_passe):
        utilizador.hash_palavra_passe = hash_palavra_passe(palavra_passe)

    utilizador.tentativas_falhadas = 0
    utilizador.bloqueado_ate = None
    utilizador.ultimo_acesso = agora()
    return utilizador


def _verificar(hash_guardado: str, palavra_passe: str) -> bool:
    try:
        return _hasher.verify(hash_guardado, palavra_passe)
    except (VerificationError, InvalidHashError):
        return False


# ---------- Sessões ----------

def criar_sessao(db: Session, utilizador: Utilizador) -> str:
    """Cria a sessão e devolve o token (só existe em claro no cookie do navegador)."""
    token = secrets.token_urlsafe(32)
    db.add(Sessao(
        hash_token=_hash_token(token),
        utilizador_id=utilizador.id,
        criada_em=agora(),
        expira_em=agora() + timedelta(days=config.DURACAO_SESSAO_DIAS),
    ))
    # Aproveita para limpar sessões expiradas (de qualquer utilizador)
    db.execute(delete(Sessao).where(Sessao.expira_em < agora()))
    return token


def utilizador_da_sessao(db: Session, token: str | None) -> Utilizador | None:
    if not token:
        return None
    sessao = db.scalar(
        select(Sessao)
        .options(joinedload(Sessao.utilizador).joinedload(Utilizador.empresa))
        .where(Sessao.hash_token == _hash_token(token))
    )
    if sessao is None or sessao.expira_em < agora():
        return None
    utilizador = sessao.utilizador
    # Desativar uma conta ou empresa corta o acesso logo, mesmo com sessões abertas
    if not utilizador.ativo or not utilizador.empresa.ativa:
        return None
    return utilizador


def terminar_sessao(db: Session, token: str | None) -> None:
    if token:
        db.execute(delete(Sessao).where(Sessao.hash_token == _hash_token(token)))


def terminar_todas_as_sessoes(db: Session, utilizador_id: int) -> None:
    db.execute(delete(Sessao).where(Sessao.utilizador_id == utilizador_id))


# ---------- Convites e reposição ----------

def criar_link_acesso(db: Session, utilizador: Utilizador) -> str:
    """
    Cria um link de uso único para o utilizador definir a palavra-passe.
    Os links anteriores deixam de funcionar. O token vai depois do "#": essa parte
    do endereço não é enviada ao servidor, por isso não fica em registos nem proxies.
    """
    db.execute(delete(TokenAcesso).where(TokenAcesso.utilizador_id == utilizador.id))
    token = secrets.token_urlsafe(32)
    db.add(TokenAcesso(
        hash_token=_hash_token(token),
        utilizador_id=utilizador.id,
        expira_em=agora() + timedelta(hours=config.VALIDADE_CONVITE_HORAS),
    ))
    return f"{config.URL_PUBLICO}/definir-palavra-passe#{token}"


def _token_valido(db: Session, token: str) -> TokenAcesso:
    registo = db.scalar(
        select(TokenAcesso)
        .options(joinedload(TokenAcesso.utilizador).joinedload(Utilizador.empresa))
        .where(TokenAcesso.hash_token == _hash_token(token))
    )
    if registo is None or registo.usado_em is not None or registo.expira_em < agora():
        raise ErroAutenticacao("Este link já não é válido. Peça um novo ao administrador da sua empresa.")
    if not registo.utilizador.ativo:
        raise ErroAutenticacao("Esta conta está desativada. Fale com o administrador da sua empresa.")
    return registo


def consultar_link(db: Session, token: str) -> Utilizador:
    """Para a página mostrar a quem pertence o link antes de definir a palavra-passe."""
    return _token_valido(db, token).utilizador


def definir_palavra_passe(db: Session, token: str, palavra_passe: str) -> Utilizador:
    registo = _token_valido(db, token)
    validar_palavra_passe_nova(palavra_passe)

    utilizador = registo.utilizador
    utilizador.hash_palavra_passe = hash_palavra_passe(palavra_passe)
    utilizador.tentativas_falhadas = 0
    utilizador.bloqueado_ate = None
    registo.usado_em = agora()
    # Depois de mudar a palavra-passe, as sessões antigas deixam de valer
    terminar_todas_as_sessoes(db, utilizador.id)
    return utilizador
