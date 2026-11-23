"""
Subscrição da empresa (planos, pagamento no Stripe) e registo online de empresas novas.

- GET  /api/subscricao             estado, plano, limites e planos disponíveis
- POST /api/subscricao/checkout    página de pagamento do Stripe para um plano (administradores)
- POST /api/subscricao/portal      portal do cliente do Stripe (cartão, faturas, cancelar)
- POST /api/stripe/webhook         eventos do Stripe (assinados), sem sessão
- POST /api/registo                cria uma empresa nova em período experimental e entra
"""

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import config
from app.db import get_db
from app.dependencias import admin_atual, utilizador_atual
from app.modelos.esquemas import SessaoSaida
from app.modelos.tabelas import PAPEL_ADMIN, AcessoEmpresa, Empresa, Utilizador
from app.servicos import atividade, autenticacao, email, limites, subscricoes, validacao
from app.servicos.autenticacao import ErroAutenticacao
from app.servicos.subscricoes import ErroSubscricao

router = APIRouter(tags=["subscrição"])
logger = logging.getLogger(__name__)


def utilizadores_ativos(db: Session, empresa_id: int) -> int:
    return db.scalar(select(func.count()).select_from(AcessoEmpresa).where(
        AcessoEmpresa.empresa_id == empresa_id, AcessoEmpresa.ativo,
    )) or 0


@router.get("/api/subscricao")
def estado(db: Session = Depends(get_db), utilizador: Utilizador = Depends(utilizador_atual)):
    dados = subscricoes.descrever(utilizador.empresa, utilizadores_ativos(db, utilizador.empresa_id))
    dados["pode_gerir"] = utilizador.papel == PAPEL_ADMIN and not config.MODO_DEMO
    return dados


class EscolhaPlano(BaseModel):
    plano: str


@router.post("/api/subscricao/checkout")
def checkout(escolha: EscolhaPlano, admin: Utilizador = Depends(admin_atual)):
    try:
        return {"url": subscricoes.sessao_checkout(admin.empresa, escolha.plano, admin.email)}
    except ErroSubscricao as erro:
        raise HTTPException(status_code=400, detail=str(erro))


@router.post("/api/subscricao/portal")
def portal(admin: Utilizador = Depends(admin_atual)):
    try:
        return {"url": subscricoes.sessao_portal(admin.empresa)}
    except ErroSubscricao as erro:
        raise HTTPException(status_code=400, detail=str(erro))


@router.post("/api/stripe/webhook", include_in_schema=False)
async def webhook(request: Request, db: Session = Depends(get_db)):
    corpo = await request.body()
    try:
        evento = subscricoes.verificar_assinatura(corpo, request.headers.get("stripe-signature"))
    except ErroSubscricao as erro:
        raise HTTPException(status_code=400, detail=str(erro))

    def obter_empresa(identificador):
        try:
            return db.get(Empresa, int(identificador)) if identificador else None
        except (TypeError, ValueError):
            return None

    def empresa_por_cliente(cliente):
        return db.scalar(select(Empresa).where(Empresa.stripe_cliente_id == cliente)) if cliente else None

    empresa = subscricoes.aplicar_evento(evento, obter_empresa, empresa_por_cliente)
    if empresa is not None:
        atividade.registar(db, None, f"Subscrição: {evento.get('type')}", f"estado {empresa.estado_subscricao}, "
                           f"plano {empresa.plano}", empresa_id=empresa.id)
        db.commit()
    return {"recebido": True}


# ---------- Registo online ----------

class Registo(BaseModel):
    empresa: str = Field(min_length=2, max_length=200)
    nif: str = Field(max_length=12)
    nome: str = Field(min_length=2, max_length=120)
    email: str = Field(min_length=3, max_length=254)
    palavra_passe: str = Field(max_length=200)
    plano: str = "contabilidade"
    aceito_termos: bool

    @field_validator("email")
    @classmethod
    def validar_email(cls, valor: str) -> str:
        valor = autenticacao.normalizar_email(valor)
        utilizador, _, dominio = valor.partition("@")
        if not utilizador or "." not in dominio or " " in valor:
            raise ValueError("Email inválido.")
        return valor


@router.post("/api/registo", response_model=SessaoSaida, status_code=201)
def registar(dados: Registo, request: Request, response: Response, tarefas: BackgroundTasks,
             db: Session = Depends(get_db)):
    from app.rotas.auth import _abrir_sessao, _descrever

    if config.MODO_DEMO or not config.REGISTO_ABERTO:
        raise HTTPException(status_code=403, detail="O registo online não está disponível. Fale connosco.")
    limites.verificar(limites.login, request.client.host if request.client else "desconhecido")
    if not dados.aceito_termos:
        raise HTTPException(status_code=400, detail="Para criar a conta, aceite os termos de utilização.")
    nif = "".join(c for c in dados.nif if c.isdigit())
    if not validacao.nif_valido(nif):
        raise HTTPException(status_code=400, detail="O NIF da empresa não é válido.")
    if dados.plano not in subscricoes.PLANOS_COM_CHECKOUT:
        raise HTTPException(status_code=400, detail="Escolha o plano Essencial ou Contabilidade.")
    try:
        autenticacao.validar_palavra_passe_nova(dados.palavra_passe)
    except ErroAutenticacao as erro:
        raise HTTPException(status_code=400, detail=str(erro))
    if db.scalar(select(Utilizador).where(Utilizador.email == dados.email)):
        raise HTTPException(status_code=409,
                            detail="Já existe uma conta com este email. Entre ou recupere a palavra-passe.")

    empresa = Empresa(nome=dados.empresa.strip(), nif=nif)
    subscricoes.nova_empresa_experimental(empresa, dados.plano)
    db.add(empresa)
    db.flush()
    admin = Utilizador(
        empresa_id=empresa.id, nome=dados.nome.strip(), email=dados.email, papel=PAPEL_ADMIN,
        hash_palavra_passe=autenticacao.hash_palavra_passe(dados.palavra_passe),
    )
    db.add(admin)
    db.flush()
    db.refresh(admin)
    detalhe = f"plano {subscricoes.PLANOS[empresa.plano].nome}, {config.DIAS_EXPERIMENTAIS} dias experimentais"
    atividade.registar(db, admin, "Criou a conta da empresa", detalhe, request.client.host if request.client else None)
    _abrir_sessao(request, response, db, admin)
    if email.configurado():
        tarefas.add_task(email.enviar, email.boas_vindas(admin.email, admin.nome, empresa.nome,
                                                         config.DIAS_EXPERIMENTAIS))
    return _descrever(admin)
