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
from sqlalchemy.orm import Session, joinedload

from app import config
from app.db import get_db
from app.dependencias import admin_atual
from app.modelos.esquemas import MembroSaida
from app.modelos.tabelas import PAPEL_ADMIN, PAPEL_MEMBRO, AcessoEmpresa, Utilizador
from app.servicos import atividade, autenticacao, chaves_api, email, empresas, subscricoes

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


def _membro(acesso: AcessoEmpresa) -> MembroSaida:
    """Uma pessoa tal como aparece na equipa DESTA empresa (papel e estado do acesso a ela)."""
    u = acesso.utilizador
    return MembroSaida(
        id=u.id, nome=u.nome, email=u.email, papel=acesso.papel, ativo=acesso.ativo and u.ativo,
        ultimo_acesso=u.ultimo_acesso, pendente=u.hash_palavra_passe is None, dois_passos=bool(u.totp_ativo),
    )


def _ativos_da_empresa(db: Session, empresa_id: int, *condicoes) -> int:
    return db.scalar(select(func.count()).select_from(AcessoEmpresa).where(
        AcessoEmpresa.empresa_id == empresa_id, AcessoEmpresa.ativo, *condicoes,
    )) or 0


def _verificar_limite(db: Session, admin: Utilizador) -> None:
    """O plano limita o número de utilizadores ativos (incluindo convites por aceitar)."""
    plano = subscricoes.plano(admin.empresa)
    if plano.max_utilizadores is None:
        return
    if _ativos_da_empresa(db, admin.empresa_id) >= plano.max_utilizadores:
        raise HTTPException(status_code=403, detail=(
            f"O plano {plano.nome} permite {plano.max_utilizadores} utilizadores. Desative alguém ou mude de plano "
            "na página Subscrição."))


def _membro_da_empresa(db: Session, admin: Utilizador, id_utilizador: int) -> AcessoEmpresa:
    acesso = empresas.membro(db, admin.empresa_id, id_utilizador)
    if acesso is None:
        raise HTTPException(status_code=404, detail="Utilizador não encontrado.")
    return acesso


def _so_desta_empresa(db: Session, acesso: AcessoEmpresa, acao: str) -> None:
    """
    Quem tem acesso a outras empresas não pode ter a palavra-passe ou a verificação em dois passos
    mudadas pelo administrador desta: isso daria acesso às outras empresas.
    """
    if empresas.noutras_empresas(db, acesso.utilizador_id, acesso.empresa_id):
        raise HTTPException(status_code=403, detail=(
            f"{acesso.utilizador.nome} tem acesso a outras empresas: não pode {acao} a partir desta. "
            "A pessoa pode recuperar a palavra-passe pelo email ou usar os códigos de recuperação."))


@router.get("", response_model=list[MembroSaida])
def listar_equipa(db: Session = Depends(get_db), admin: Utilizador = Depends(admin_atual)):
    acessos = db.scalars(
        select(AcessoEmpresa).options(joinedload(AcessoEmpresa.utilizador))
        .join(Utilizador, Utilizador.id == AcessoEmpresa.utilizador_id)
        .where(AcessoEmpresa.empresa_id == admin.empresa_id).order_by(Utilizador.nome)
    )
    return [_membro(a) for a in acessos]


@router.post("", status_code=201)
def convidar(convite: Convite, tarefas: BackgroundTasks, db: Session = Depends(get_db),
             admin: Utilizador = Depends(admin_atual)):
    """
    Convida alguém para esta empresa. Se a pessoa já tem conta no Dobra (por exemplo, o contabilista
    de um gabinete), ganha acesso a esta empresa com a mesma conta, sem novo convite.
    """
    existente = db.scalar(select(Utilizador).where(Utilizador.email == convite.email))
    if existente is not None:
        if empresas.membro(db, admin.empresa_id, existente.id) is not None:
            raise HTTPException(status_code=409, detail="Esta pessoa já faz parte da equipa desta empresa.")
        _verificar_limite(db, admin)
        acesso = AcessoEmpresa(utilizador_id=existente.id, empresa_id=admin.empresa_id, papel=convite.papel)
        db.add(acesso)
        db.commit()
        db.refresh(acesso)
        enviado = email.configurado() and existente.hash_palavra_passe is not None
        if enviado:
            tarefas.add_task(email.enviar, email.acesso_adicionado(existente.email, existente.nome,
                                                                   admin.empresa.nome, admin.nome))
        return {"membro": _membro(acesso), "link": None, "email_enviado": enviado, "conta_existente": True}

    _verificar_limite(db, admin)
    novo = Utilizador(empresa_id=admin.empresa_id, nome=convite.nome, email=convite.email, papel=convite.papel)
    db.add(novo)
    db.flush()
    link = autenticacao.criar_link_acesso(db, novo)
    db.commit()
    acesso = empresas.membro(db, admin.empresa_id, novo.id)
    enviado = email.configurado()
    if enviado:
        tarefas.add_task(email.enviar, email.convite(novo.email, novo.nome, admin.empresa.nome, admin.nome, link))
    return {"membro": _membro(acesso), "link": link, "email_enviado": enviado, "conta_existente": False}


@router.post("/{id_utilizador}/link")
def novo_link(id_utilizador: int, tarefas: BackgroundTasks, db: Session = Depends(get_db),
              admin: Utilizador = Depends(admin_atual)):
    """Gera um link novo: serve para reenviar um convite ou repor uma palavra-passe esquecida."""
    acesso = _membro_da_empresa(db, admin, id_utilizador)
    utilizador = acesso.utilizador
    if not acesso.ativo or not utilizador.ativo:
        raise HTTPException(status_code=400, detail="Ative a conta antes de gerar um link.")
    pendente = utilizador.hash_palavra_passe is None
    if not pendente:
        _so_desta_empresa(db, acesso, "repor a palavra-passe")
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
    """Ativa/desativa o acesso da pessoa a esta empresa, muda o papel nela ou desliga a verificação em dois passos."""
    acesso = _membro_da_empresa(db, admin, id_utilizador)
    utilizador = acesso.utilizador
    if utilizador.id == admin.id:
        raise HTTPException(status_code=400, detail="Não pode alterar a sua própria conta.")

    deixa_de_ser_admin = acesso.papel == PAPEL_ADMIN and (
        alteracao.ativo is False or (alteracao.papel and alteracao.papel != PAPEL_ADMIN)
    )
    if deixa_de_ser_admin and _ativos_da_empresa(db, admin.empresa_id, AcessoEmpresa.papel == PAPEL_ADMIN) <= 1:
        raise HTTPException(status_code=400, detail="A empresa tem de ter pelo menos um administrador ativo.")

    if alteracao.ativo and not acesso.ativo:
        _verificar_limite(db, admin)
    if alteracao.papel:
        acesso.papel = alteracao.papel
    if alteracao.desligar_dois_passos:
        _so_desta_empresa(db, acesso, "desligar a verificação em dois passos")
        utilizador.totp_ativo = False
        utilizador.totp_segredo = None
        utilizador.totp_ultimo_passo = None
        utilizador.codigos_recuperacao = None
        autenticacao.terminar_todas_as_sessoes(db, utilizador.id)
    if alteracao.ativo is not None:
        acesso.ativo = alteracao.ativo
        if not alteracao.ativo:
            # Desativar corta o acesso a esta empresa imediatamente (as sessões noutras empresas continuam)
            autenticacao.terminar_sessoes_na_empresa(db, utilizador.id, admin.empresa_id)
    db.commit()
    return _membro(acesso)


@router.get("/atividade")
def registo_de_atividade(
    antes_de: int | None = None, db: Session = Depends(get_db), admin: Utilizador = Depends(admin_atual)
):
    """Últimas 200 ações da empresa (as mais recentes primeiro). antes_de: para carregar mais."""
    return atividade.listar(db, admin.empresa_id, antes_de=antes_de)


# ---------- Chaves da API pública ----------

class ChaveNova(BaseModel):
    nome: str = Field(min_length=1, max_length=80)
    permissao: Literal["leitura", "escrita"] = "leitura"


@router.get("/chaves-api")
def listar_chaves(db: Session = Depends(get_db), admin: Utilizador = Depends(admin_atual)):
    """Chaves ativas da API pública desta empresa (nunca a chave completa)."""
    return [chaves_api.chave_dict(c) for c in chaves_api.listar(db, admin.empresa_id)]


@router.post("/chaves-api", status_code=201)
def criar_chave(dados: ChaveNova, db: Session = Depends(get_db), admin: Utilizador = Depends(admin_atual)):
    """Cria uma chave. A resposta traz a chave completa: é a única vez que aparece."""
    try:
        chave, texto = chaves_api.criar(db, admin.empresa_id, dados.nome, dados.permissao, admin.id)
    except chaves_api.ErroChave as erro:
        raise HTTPException(status_code=400, detail=str(erro))
    db.commit()
    return {**chaves_api.chave_dict(chave), "chave": texto}


@router.delete("/chaves-api/{chave_id}", status_code=204)
def revogar_chave(chave_id: int, db: Session = Depends(get_db), admin: Utilizador = Depends(admin_atual)):
    try:
        chaves_api.revogar(db, admin.empresa_id, chave_id)
    except chaves_api.ErroChave as erro:
        raise HTTPException(status_code=404, detail=str(erro))
    db.commit()
