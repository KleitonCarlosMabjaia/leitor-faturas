"""Endpoints dos fornecedores (usados, por exemplo, no filtro da lista)."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.modelos.esquemas import FornecedorSaida
from app.servicos import repositorio

router = APIRouter(prefix="/api/fornecedores", tags=["fornecedores"])


@router.get("", response_model=list[FornecedorSaida])
def listar_fornecedores(db: Session = Depends(get_db)):
    return repositorio.listar_fornecedores(db)
