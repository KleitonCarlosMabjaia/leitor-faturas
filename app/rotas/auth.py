"""
Entrar, sair e definir a palavra-passe (a partir de um link de convite).

A sessão vai num cookie:
- HttpOnly: o JavaScript da página não o consegue ler (protege contra roubo por XSS);
- SameSite=Lax: o navegador não o envia em pedidos POST/PUT/DELETE vindos de
  outros sites (protege contra CSRF);
- Secure em HTTPS: nunca viaja sem encriptação.
"""

from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.db import get_db
from app.dependencias import NOME_COOKIE, utilizador_atual
from app.modelos.esquemas import SessaoSaida
from app.modelos.tabelas import Utilizador
from app.servicos import atividade, autenticacao, email, limites, subscricoes
from app.servicos.autenticacao import CodigoNecessario, ErroAutenticacao

router = APIRouter(prefix="/api/auth", tags=["autenticação"])


class Credenciais(BaseModel):
    email: str
    palavra_passe: str
    codigo: str | None = None  # verificação em dois passos (app autenticadora ou código de recuperação)


class PedidoRecuperacao(BaseModel):
    email: str


class Token(BaseModel):
    token: str


class NovaPalavraPasse(BaseModel):
    token: str
    palavra_passe: str


def _descrever(utilizador: Utilizador) -> SessaoSaida:
    return SessaoSaida(
        nome=utilizador.nome,
        email=utilizador.email,
        papel=utilizador.papel,
        empresa=utilizador.empresa.nome,
        modo_demo=config.MODO_DEMO,
        plano=subscricoes.plano(utilizador.empresa).chave,
        funcionalidades=sorted(subscricoes.plano(utilizador.empresa).funcionalidades),
        faturacao_ativa=subscricoes.faturacao_ativa(),
        dias_experimentais=(subscricoes.dias_experimentais(utilizador.empresa)
                            if subscricoes.faturacao_ativa() else None),
        aviso_subscricao=subscricoes.so_leitura(utilizador.empresa),
    )


def _abrir_sessao(request: Request, response: Response, db: Session, utilizador: Utilizador) -> None:
    token = autenticacao.criar_sessao(
        db, utilizador, request.client.host if request.client else None, request.headers.get("user-agent"),
    )
    db.commit()
    response.set_cookie(
        NOME_COOKIE,
        token,
        max_age=config.DURACAO_SESSAO_DIAS * 24 * 3600,
        httponly=True,
        samesite="lax",
        # Em produção o pedido chega por HTTPS; localmente (http://127.0.0.1) não
        secure=request.url.scheme == "https",
        path="/",
    )


def _sem_demo() -> None:
    if config.MODO_DEMO:
        raise HTTPException(status_code=400, detail="Na demonstração não é preciso entrar.")


def _limitar(request: Request) -> None:
    # Por IP: o bloqueio da conta não trava quem experimenta muitos emails diferentes
    limites.verificar(limites.login, request.client.host if request.client else "desconhecido")


@router.post("/entrar", response_model=SessaoSaida)
def entrar(credenciais: Credenciais, request: Request, response: Response, tarefas: BackgroundTasks,
           db: Session = Depends(get_db)):
    _sem_demo()
    _limitar(request)
    ip = request.client.host if request.client else None
    try:
        utilizador = autenticacao.entrar(db, credenciais.email, credenciais.palavra_passe, credenciais.codigo)
    except CodigoNecessario as erro:
        # A palavra-passe está certa: a página pede o código e volta a enviar tudo
        raise HTTPException(status_code=401, detail=str(erro), headers={"X-Dois-Passos": "necessario"})
    except ErroAutenticacao as erro:
        endereco = autenticacao.normalizar_email(credenciais.email)
        conhecido = db.scalar(select(Utilizador).where(Utilizador.email == endereco))
        if conhecido is not None:
            atividade.registar(db, conhecido, "Tentativa de entrada falhada", str(erro), ip)
            db.commit()
        raise HTTPException(status_code=401, detail=str(erro))
    detalhe = "com verificação em dois passos" if utilizador.totp_ativo else None
    atividade.registar(db, utilizador, "Entrou na aplicação", detalhe, ip)
    agente = request.headers.get("user-agent")
    if email.configurado() and not autenticacao.dispositivo_conhecido(db, utilizador.id, agente):
        from app.rotas.conta import descrever_dispositivo

        quando = datetime.now().strftime("%d/%m/%Y às %H:%M")
        mensagem = email.nova_sessao(utilizador.email, utilizador.nome, descrever_dispositivo(agente), ip, quando)
        tarefas.add_task(email.enviar, mensagem)
    _abrir_sessao(request, response, db, utilizador)
    return _descrever(utilizador)


@router.post("/sair", status_code=204)
def sair(request: Request, response: Response, db: Session = Depends(get_db)):
    utilizador = autenticacao.utilizador_da_sessao(db, request.cookies.get(NOME_COOKIE))
    if utilizador is not None and not config.MODO_DEMO:
        atividade.registar(db, utilizador, "Saiu da aplicação", ip=request.client.host if request.client else None)
    autenticacao.terminar_sessao(db, request.cookies.get(NOME_COOKIE))
    db.commit()
    response.delete_cookie(NOME_COOKIE, path="/")


@router.get("/opcoes")
def opcoes():
    """O que a página de entrada pode oferecer (a recuperação por email só existe com o envio configurado)."""
    return {"recuperacao_por_email": email.configurado() and not config.MODO_DEMO,
            "registo_aberto": config.REGISTO_ABERTO and not config.MODO_DEMO}


@router.post("/recuperar", status_code=204)
def recuperar(dados: PedidoRecuperacao, request: Request, tarefas: BackgroundTasks, db: Session = Depends(get_db)):
    """
    "Esqueci-me da palavra-passe": envia um link de uso único, válido 60 minutos.
    A resposta é sempre a mesma, exista ou não a conta: não se revela que emails estão registados.
    """
    _sem_demo()
    _limitar(request)
    limites.verificar(limites.login, f"recuperar:{autenticacao.normalizar_email(dados.email)}")
    if not email.configurado():
        raise HTTPException(status_code=400,
                            detail="A recuperação por email não está disponível. Peça um link ao administrador.")
    endereco = autenticacao.normalizar_email(dados.email)
    utilizador = db.scalar(select(Utilizador).where(Utilizador.email == endereco))
    if (utilizador is None or not utilizador.ativo or not utilizador.empresa.ativa
            or utilizador.hash_palavra_passe is None):
        return
    minutos = config.VALIDADE_REPOSICAO_MINUTOS
    link = autenticacao.criar_link_acesso(db, utilizador, minutos=minutos)
    ip = request.client.host if request.client else None
    atividade.registar(db, utilizador, "Pediu a recuperação da palavra-passe", ip=ip)
    db.commit()
    tarefas.add_task(email.enviar, email.reposicao(utilizador.email, utilizador.nome, link, minutos))


@router.get("/eu", response_model=SessaoSaida)
def quem_sou_eu(utilizador: Utilizador = Depends(utilizador_atual)):
    return _descrever(utilizador)


# O token vai no corpo do pedido (POST) e não no endereço, para não ficar em registos
@router.post("/link")
def consultar_link(dados: Token, request: Request, db: Session = Depends(get_db)):
    """Diz a quem pertence um link de convite, para a página o mostrar."""
    _sem_demo()
    _limitar(request)
    try:
        utilizador = autenticacao.consultar_link(db, dados.token)
    except ErroAutenticacao as erro:
        raise HTTPException(status_code=400, detail=str(erro))
    return {"nome": utilizador.nome, "email": utilizador.email, "empresa": utilizador.empresa.nome}


@router.post("/definir-palavra-passe", response_model=SessaoSaida)
def definir_palavra_passe(dados: NovaPalavraPasse, request: Request, response: Response, db: Session = Depends(get_db)):
    """Aceita o convite (ou repõe a palavra-passe) e entra logo na aplicação."""
    _sem_demo()
    _limitar(request)
    try:
        utilizador = autenticacao.definir_palavra_passe(db, dados.token, dados.palavra_passe)
    except ErroAutenticacao as erro:
        raise HTTPException(status_code=400, detail=str(erro))
    atividade.registar(db, utilizador, "Definiu a palavra-passe", "a partir de um link de convite ou reposição",
                       request.client.host if request.client else None)
    _abrir_sessao(request, response, db, utilizador)
    return _descrever(utilizador)
