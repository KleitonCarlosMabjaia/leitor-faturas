"""
A minha conta: palavra-passe, verificação em dois passos e sessões abertas.

Ações sensíveis pedem a palavra-passe (ou um código) outra vez, mesmo com a sessão
iniciada: quem encontrar um computador desbloqueado não consegue desligar as proteções.
"""

import re

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import config
from app.db import get_db
from app.dependencias import NOME_COOKIE, utilizador_atual
from app.modelos.tabelas import Utilizador
from app.servicos import atividade, autenticacao, dois_passos, email
from app.servicos.autenticacao import ErroAutenticacao

router = APIRouter(prefix="/api/conta", tags=["conta"])


class MudancaPalavraPasse(BaseModel):
    atual: str = Field(max_length=200)
    nova: str = Field(max_length=200)


class Confirmacao(BaseModel):
    palavra_passe: str = Field(max_length=200)


class Codigo(BaseModel):
    codigo: str = Field(max_length=20)


class Preferencias(BaseModel):
    alertas_email: bool


class Desativacao(BaseModel):
    palavra_passe: str = Field(max_length=200)
    codigo: str = Field(max_length=20)


def ip_do_pedido(request: Request) -> str | None:
    return request.client.host if request.client else None


def descrever_dispositivo(agente: str | None) -> str:
    """'Chrome em Windows' a partir do User-Agent (sem bibliotecas: só os casos comuns)."""
    a = agente or ""
    if not a:
        return "Dispositivo desconhecido"
    navegador = next((nome for chave, nome in (
        ("Edg/", "Edge"), ("OPR/", "Opera"), ("Firefox/", "Firefox"), ("Chrome/", "Chrome"), ("Safari/", "Safari"),
    ) if chave in a), "Navegador")
    sistema = next((nome for padrao, nome in (
        (r"iPhone|iPad", "iPhone/iPad"), (r"Android", "Android"), (r"Windows", "Windows"),
        (r"Mac OS X|Macintosh", "macOS"), (r"Linux", "Linux"),
    ) if re.search(padrao, a)), "sistema desconhecido")
    return f"{navegador} em {sistema}"


def _sem_demo() -> None:
    if config.MODO_DEMO:
        raise HTTPException(status_code=403, detail="Na demonstração a conta não pode ser alterada.")


def _erro(erro: ErroAutenticacao) -> HTTPException:
    return HTTPException(status_code=400, detail=str(erro))


@router.get("")
def obter(request: Request, db: Session = Depends(get_db), utilizador: Utilizador = Depends(utilizador_atual)):
    atual = autenticacao.hash_da_sessao(request.cookies.get(NOME_COOKIE))
    sessoes = [] if config.MODO_DEMO else autenticacao.listar_sessoes(db, utilizador.id)
    return {
        "nome": utilizador.nome,
        "email": utilizador.email,
        "papel": utilizador.papel,
        "empresa": utilizador.empresa.nome,
        "modo_demo": config.MODO_DEMO,
        "dois_passos": utilizador.totp_ativo,
        "alertas_email": utilizador.alertas_email,
        "email_configurado": email.configurado(),
        "codigos_restantes": len(utilizador.codigos_recuperacao or []) if utilizador.totp_ativo else 0,
        "sessoes": [
            {
                "id": s.hash_token[:16],
                "atual": s.hash_token == atual,
                "dispositivo": descrever_dispositivo(s.agente),
                "ip": s.ip,
                "criada_em": s.criada_em.isoformat(timespec="minutes"),
                "ultimo_uso": (s.ultimo_uso or s.criada_em).isoformat(timespec="minutes"),
            }
            for s in sessoes
        ],
    }


@router.put("/preferencias", status_code=204)
def preferencias(dados: Preferencias, db: Session = Depends(get_db),
                 utilizador: Utilizador = Depends(utilizador_atual)):
    _sem_demo()
    utilizador.alertas_email = dados.alertas_email
    db.commit()


@router.post("/palavra-passe", status_code=204)
def mudar_palavra_passe(dados: MudancaPalavraPasse, request: Request, db: Session = Depends(get_db),
                        utilizador: Utilizador = Depends(utilizador_atual)):
    _sem_demo()
    try:
        autenticacao.mudar_palavra_passe(db, utilizador, dados.atual, dados.nova, request.cookies.get(NOME_COOKIE))
    except ErroAutenticacao as erro:
        raise _erro(erro)
    atividade.registar(db, utilizador, "Mudou a palavra-passe", "As outras sessões foram terminadas",
                       ip_do_pedido(request))
    db.commit()


@router.post("/dois-passos/iniciar")
def iniciar_dois_passos(dados: Confirmacao, db: Session = Depends(get_db),
                        utilizador: Utilizador = Depends(utilizador_atual)):
    """Gera o segredo e o código QR. Só fica ativo depois de confirmado com um código (ativar)."""
    _sem_demo()
    if utilizador.totp_ativo:
        raise HTTPException(status_code=400, detail="A verificação em dois passos já está ligada.")
    try:
        autenticacao.confirmar_palavra_passe(utilizador, dados.palavra_passe)
    except ErroAutenticacao as erro:
        raise _erro(erro)
    utilizador.totp_segredo = dois_passos.novo_segredo()
    utilizador.totp_ultimo_passo = None
    db.commit()
    uri = dois_passos.uri_aprovisionamento(utilizador.totp_segredo, utilizador.email)
    return {"segredo": utilizador.totp_segredo, "uri": uri, "qr": dois_passos.qr_png_base64(uri)}


@router.post("/dois-passos/ativar")
def ativar_dois_passos(dados: Codigo, request: Request, db: Session = Depends(get_db),
                       utilizador: Utilizador = Depends(utilizador_atual)):
    _sem_demo()
    if utilizador.totp_ativo or not utilizador.totp_segredo:
        raise HTTPException(status_code=400, detail="Comece por gerar o código QR.")
    passo = dois_passos.verificar_codigo(utilizador.totp_segredo, dados.codigo, None)
    if passo is None:
        raise HTTPException(status_code=400, detail="Código incorreto. Confirme a hora do telemóvel e tente de novo.")
    codigos, hashes = dois_passos.novos_codigos_recuperacao()
    utilizador.totp_ativo = True
    utilizador.totp_ultimo_passo = passo
    utilizador.codigos_recuperacao = hashes
    atividade.registar(db, utilizador, "Ligou a verificação em dois passos", ip=ip_do_pedido(request))
    db.commit()
    return {"codigos_recuperacao": codigos}


@router.post("/dois-passos/desativar", status_code=204)
def desativar_dois_passos(dados: Desativacao, request: Request, db: Session = Depends(get_db),
                          utilizador: Utilizador = Depends(utilizador_atual)):
    _sem_demo()
    if not utilizador.totp_ativo:
        return
    try:
        autenticacao.confirmar_palavra_passe(utilizador, dados.palavra_passe)
    except ErroAutenticacao as erro:
        raise _erro(erro)
    if not dois_passos.usar_codigo(utilizador, dados.codigo):
        raise HTTPException(status_code=400, detail="Código de verificação incorreto ou já usado.")
    utilizador.totp_ativo = False
    utilizador.totp_segredo = None
    utilizador.totp_ultimo_passo = None
    utilizador.codigos_recuperacao = None
    atividade.registar(db, utilizador, "Desligou a verificação em dois passos", ip=ip_do_pedido(request))
    db.commit()


@router.post("/dois-passos/codigos")
def novos_codigos(dados: Confirmacao, request: Request, db: Session = Depends(get_db),
                  utilizador: Utilizador = Depends(utilizador_atual)):
    """Substitui os códigos de recuperação (os antigos deixam de valer)."""
    _sem_demo()
    if not utilizador.totp_ativo:
        raise HTTPException(status_code=400, detail="A verificação em dois passos não está ligada.")
    try:
        autenticacao.confirmar_palavra_passe(utilizador, dados.palavra_passe)
    except ErroAutenticacao as erro:
        raise _erro(erro)
    codigos, hashes = dois_passos.novos_codigos_recuperacao()
    utilizador.codigos_recuperacao = hashes
    atividade.registar(db, utilizador, "Gerou novos códigos de recuperação", ip=ip_do_pedido(request))
    db.commit()
    return {"codigos_recuperacao": codigos}


@router.post("/sessoes/terminar-outras")
def terminar_outras(request: Request, db: Session = Depends(get_db),
                    utilizador: Utilizador = Depends(utilizador_atual)):
    _sem_demo()
    n = autenticacao.terminar_outras_sessoes(db, utilizador.id, request.cookies.get(NOME_COOKIE))
    atividade.registar(db, utilizador, "Terminou as outras sessões", f"{n} sessão(ões)", ip_do_pedido(request))
    db.commit()
    return {"terminadas": n}


@router.post("/sessoes/{id_sessao}/terminar", status_code=204)
def terminar_sessao(id_sessao: str, request: Request, db: Session = Depends(get_db),
                    utilizador: Utilizador = Depends(utilizador_atual)):
    _sem_demo()
    if not re.fullmatch(r"[0-9a-f]{16}", id_sessao):
        raise HTTPException(status_code=400, detail="Sessão inválida.")
    for s in autenticacao.listar_sessoes(db, utilizador.id):
        if s.hash_token.startswith(id_sessao):
            db.delete(s)
            atividade.registar(db, utilizador, "Terminou uma sessão", descrever_dispositivo(s.agente),
                               ip_do_pedido(request))
            db.commit()
            return
    raise HTTPException(status_code=404, detail="Sessão não encontrada.")
