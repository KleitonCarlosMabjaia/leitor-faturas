"""Endpoint com os números do dashboard."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.servicos import estatisticas

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("")
def obter_dashboard(
    mes: str | None = Query(None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$", description="Mês, AAAA-MM"),
    db: Session = Depends(get_db),
):
    """Totais do mês, IVA do ano, gasto por fornecedor, evolução mensal e faturas por pagar."""
    return estatisticas.resumo(db, mes)
