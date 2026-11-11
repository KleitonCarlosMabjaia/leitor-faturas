"""
Dependências do FastAPI para saber quem está a fazer o pedido.

Cada rota protegida recebe o utilizador com `Depends(utilizador_atual)` e passa
`utilizador.empresa_id` ao repositório. Assim é impossível esquecer o filtro da
empresa numa rota: sem utilizador não há empresa_id e a consulta não se faz.
"""

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app import config
from app.db import get_db
from app.modelos.tabelas import PAPEL_ADMIN, Utilizador
from app.servicos import autenticacao

NOME_COOKIE = "sessao"
EMAIL_DEMO = "demo@demonstracao.pt"


def _utilizador_demo(db: Session) -> Utilizador:
    """Na demonstração pública não há login: todos entram na empresa de demonstração."""
    utilizador = db.scalar(
        select(Utilizador).options(joinedload(Utilizador.empresa)).where(Utilizador.email == EMAIL_DEMO)
    )
    if utilizador is None:
        raise HTTPException(status_code=503, detail="A demonstração ainda está a ser preparada. Tente daqui a pouco.")
    return utilizador


def utilizador_opcional(request: Request, db: Session = Depends(get_db)) -> Utilizador | None:
    if config.MODO_DEMO:
        return _utilizador_demo(db)
    return autenticacao.utilizador_da_sessao(db, request.cookies.get(NOME_COOKIE))


def utilizador_atual(utilizador: Utilizador | None = Depends(utilizador_opcional)) -> Utilizador:
    if utilizador is None:
        raise HTTPException(status_code=401, detail="A sua sessão terminou. Entre novamente.")
    return utilizador


def admin_atual(utilizador: Utilizador = Depends(utilizador_atual)) -> Utilizador:
    if config.MODO_DEMO:
        raise HTTPException(status_code=403, detail="A gestão da equipa não está disponível na demonstração.")
    if utilizador.papel != PAPEL_ADMIN:
        raise HTTPException(status_code=403, detail="Só os administradores da empresa podem fazer isto.")
    return utilizador
