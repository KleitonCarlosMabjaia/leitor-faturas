"""Endpoints dos fornecedores (usados, por exemplo, no filtro da lista)."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencias import utilizador_atual
from app.modelos.esquemas import FornecedorSaida
from app.modelos.tabelas import Utilizador
from app.servicos import repositorio

router = APIRouter(prefix="/api/fornecedores", tags=["fornecedores"])


@router.get("", response_model=list[FornecedorSaida])
def listar_fornecedores(db: Session = Depends(get_db), utilizador: Utilizador = Depends(utilizador_atual)):
    return repositorio.listar_fornecedores(db, utilizador.empresa_id)
