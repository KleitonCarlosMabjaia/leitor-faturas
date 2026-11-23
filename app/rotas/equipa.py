"""
Gestão da equipa de uma empresa (só para administradores da empresa).

Ainda não há envio de emails: ao convidar, a API devolve o link e o
administrador envia-o à pessoa (por email, WhatsApp...). Ligar um serviço
de email (ex.: SMTP, Resend, Postmark) é o próximo passo natural.
"""

from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import config
from app.db import get_db
from app.dependencias import admin_atual
from app.modelos.esquemas import MembroSaida
from app.modelos.tabelas import PAPEL_ADMIN, PAPEL_MEMBRO, Utilizador
from app.servicos import atividade, autenticacao, email, subscricoes

router = APIRouter(prefix="/api/equipa", tags=["equipa"])

Papel = Literal["admin", "membro"]


class Convite(BaseModel):
    nome: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=254)
    papel: Papel = PAPEL_MEMBRO

    @field_validator("email")
    @classmethod
    def validar_email(cls, valor: str) -> str:
        valor = autenticacao.normalizar_email(valor)
        utilizador, _, dominio = valor.partition("@")
        if not utilizador or "." not in dominio or " " in valor:
            raise ValueError("Email inválido.")
        return valor

    @field_validator("nome")
    @classmethod
    def limpar_nome(cls, valor: str) -> str:
        return valor.strip()


class Alteracao(BaseModel):
    ativo: bool | None = None
    papel: Papel | None = None
    # Para quem perdeu o telemóvel e os códigos de recuperação: o administrador desliga e a pessoa volta a ligar
    desligar_dois_passos: bool | None = None


def _membro(utilizador: Utilizador) -> MembroSaida:
    return MembroSaida(
        id=utilizador.id, nome=utilizador.nome, email=utilizador.email, papel=utilizador.papel,
        ativo=utilizador.ativo, ultimo_acesso=utilizador.ultimo_acesso,
        pendente=utilizador.hash_palavra_passe is None, dois_passos=bool(utilizador.totp_ativo),
    )


def _verificar_limite(db: Session, admin: Utilizador) -> None:
    """O plano limita o número de utilizadores ativos (incluindo convites por aceitar)."""
    plano = subscricoes.plano(admin.empresa)
    if plano.max_utilizadores is None:
        return
    ativos = db.scalar(select(func.count()).select_from(Utilizador).where(
        Utilizador.empresa_id == admin.empresa_id, Utilizador.ativo,
    ))
    if ativos >= plano.max_utilizadores:
        raise HTTPException(status_code=403, detail=(
            f"O plano {plano.nome} permite {plano.max_utilizadores} utilizadores. Desative alguém ou mude de plano "
            "na página Subscrição."))


def _membro_da_empresa(db: Session, admin: Utilizador, id_utilizador: int) -> Utilizador:
    utilizador = db.get(Utilizador, id_utilizador)
    if utilizador is None or utilizador.empresa_id != admin.empresa_id:
        raise HTTPException(status_code=404, detail="Utilizador não encontrado.")
    return utilizador


@router.get("", response_model=list[MembroSaida])
def listar_equipa(db: Session = Depends(get_db), admin: Utilizador = Depends(admin_atual)):
    membros = db.scalars(
        select(Utilizador).where(Utilizador.empresa_id == admin.empresa_id).order_by(Utilizador.nome)
    )
    return [_membro(m) for m in membros]


@router.post("", status_code=201)
def convidar(convite: Convite, tarefas: BackgroundTasks, db: Session = Depends(get_db),
             admin: Utilizador = Depends(admin_atual)):
    """Cria a conta (ainda sem palavra-passe) e devolve o link de convite."""
    if db.scalar(select(Utilizador).where(Utilizador.email == convite.email)):
        raise HTTPException(status_code=409, detail="Já existe uma conta com este email.")
    _verificar_limite(db, admin)
    novo = Utilizador(empresa_id=admin.empresa_id, nome=convite.nome, email=convite.email, papel=convite.papel)
    db.add(novo)
    db.flush()
    link = autenticacao.criar_link_acesso(db, novo)
    db.commit()
    enviado = email.configurado()
    if enviado:
        tarefas.add_task(email.enviar, email.convite(novo.email, novo.nome, admin.empresa.nome, admin.nome, link))
    return {"membro": _membro(novo), "link": link, "email_enviado": enviado}


@router.post("/{id_utilizador}/link")
def novo_link(id_utilizador: int, tarefas: BackgroundTasks, db: Session = Depends(get_db),
              admin: Utilizador = Depends(admin_atual)):
    """Gera um link novo: serve para reenviar um convite ou repor uma palavra-passe esquecida."""
    utilizador = _membro_da_empresa(db, admin, id_utilizador)
    if not utilizador.ativo:
        raise HTTPException(status_code=400, detail="Ative a conta antes de gerar um link.")
    pendente = utilizador.hash_palavra_passe is None
    minutos = None if pendente else config.VALIDADE_REPOSICAO_MINUTOS * 24  # reposição pedida pelo admin: 24 h
    link = autenticacao.criar_link_acesso(db, utilizador, minutos=minutos)
    db.commit()
    enviado = email.configurado()
    if enviado:
        mensagem = (email.convite(utilizador.email, utilizador.nome, admin.empresa.nome, admin.nome, link) if pendente
                    else email.reposicao(utilizador.email, utilizador.nome, link, minutos))
        tarefas.add_task(email.enviar, mensagem)
    return {"link": link, "email_enviado": enviado}


@router.patch("/{id_utilizador}", response_model=MembroSaida)
def alterar(
    id_utilizador: int, alteracao: Alteracao, db: Session = Depends(get_db), admin: Utilizador = Depends(admin_atual)
):
    """Ativa/desativa uma conta ou muda o papel."""
    utilizador = _membro_da_empresa(db, admin, id_utilizador)
    if utilizador.id == admin.id:
        raise HTTPException(status_code=400, detail="Não pode alterar a sua própria conta.")

    deixa_de_ser_admin = utilizador.papel == PAPEL_ADMIN and (
        alteracao.ativo is False or (alteracao.papel and alteracao.papel != PAPEL_ADMIN)
    )
    if deixa_de_ser_admin:
        admins_ativos = db.scalar(
            select(func.count()).select_from(Utilizador).where(
                Utilizador.empresa_id == admin.empresa_id, Utilizador.papel == PAPEL_ADMIN, Utilizador.ativo,
            )
        )
        if admins_ativos <= 1:
            raise HTTPException(status_code=400, detail="A empresa tem de ter pelo menos um administrador ativo.")

    if alteracao.ativo and not utilizador.ativo:
        _verificar_limite(db, admin)
    if alteracao.papel:
        utilizador.papel = alteracao.papel
    if alteracao.desligar_dois_passos:
        utilizador.totp_ativo = False
        utilizador.totp_segredo = None
        utilizador.totp_ultimo_passo = None
        utilizador.codigos_recuperacao = None
        autenticacao.terminar_todas_as_sessoes(db, utilizador.id)
    if alteracao.ativo is not None:
        utilizador.ativo = alteracao.ativo
        if not alteracao.ativo:
            # Desativar corta o acesso imediatamente
            autenticacao.terminar_todas_as_sessoes(db, utilizador.id)
    db.commit()
    return _membro(utilizador)


@router.get("/atividade")
def registo_de_atividade(
    antes_de: int | None = None, db: Session = Depends(get_db), admin: Utilizador = Depends(admin_atual)
):
    """Últimas 200 ações da empresa (as mais recentes primeiro). antes_de: para carregar mais."""
    return atividade.listar(db, admin.empresa_id, antes_de=antes_de)
