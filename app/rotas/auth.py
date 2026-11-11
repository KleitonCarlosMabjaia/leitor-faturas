"""
Entrar, sair e definir a palavra-passe (a partir de um link de convite).

A sessão vai num cookie:
- HttpOnly: o JavaScript da página não o consegue ler (protege contra roubo por XSS);
- SameSite=Lax: o navegador não o envia em pedidos POST/PUT/DELETE vindos de
  outros sites (protege contra CSRF);
- Secure em HTTPS: nunca viaja sem encriptação.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import config
from app.db import get_db
from app.dependencias import NOME_COOKIE, utilizador_atual
from app.modelos.esquemas import SessaoSaida
from app.modelos.tabelas import Utilizador
from app.servicos import autenticacao
from app.servicos.autenticacao import ErroAutenticacao

router = APIRouter(prefix="/api/auth", tags=["autenticação"])


class Credenciais(BaseModel):
    email: str
    palavra_passe: str


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
    )


def _abrir_sessao(request: Request, response: Response, db: Session, utilizador: Utilizador) -> None:
    token = autenticacao.criar_sessao(db, utilizador)
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


@router.post("/entrar", response_model=SessaoSaida)
def entrar(credenciais: Credenciais, request: Request, response: Response, db: Session = Depends(get_db)):
    _sem_demo()
    try:
        utilizador = autenticacao.entrar(db, credenciais.email, credenciais.palavra_passe)
    except ErroAutenticacao as erro:
        raise HTTPException(status_code=401, detail=str(erro))
    _abrir_sessao(request, response, db, utilizador)
    return _descrever(utilizador)


@router.post("/sair", status_code=204)
def sair(request: Request, response: Response, db: Session = Depends(get_db)):
    autenticacao.terminar_sessao(db, request.cookies.get(NOME_COOKIE))
    db.commit()
    response.delete_cookie(NOME_COOKIE, path="/")


@router.get("/eu", response_model=SessaoSaida)
def quem_sou_eu(utilizador: Utilizador = Depends(utilizador_atual)):
    return _descrever(utilizador)


# O token vai no corpo do pedido (POST) e não no endereço, para não ficar em registos
@router.post("/link")
def consultar_link(dados: Token, db: Session = Depends(get_db)):
    """Diz a quem pertence um link de convite, para a página o mostrar."""
    _sem_demo()
    try:
        utilizador = autenticacao.consultar_link(db, dados.token)
    except ErroAutenticacao as erro:
        raise HTTPException(status_code=400, detail=str(erro))
    return {"nome": utilizador.nome, "email": utilizador.email, "empresa": utilizador.empresa.nome}


@router.post("/definir-palavra-passe", response_model=SessaoSaida)
def definir_palavra_passe(dados: NovaPalavraPasse, request: Request, response: Response, db: Session = Depends(get_db)):
    """Aceita o convite (ou repõe a palavra-passe) e entra logo na aplicação."""
    _sem_demo()
    try:
        utilizador = autenticacao.definir_palavra_passe(db, dados.token, dados.palavra_passe)
    except ErroAutenticacao as erro:
        raise HTTPException(status_code=400, detail=str(erro))
    _abrir_sessao(request, response, db, utilizador)
    return _descrever(utilizador)
