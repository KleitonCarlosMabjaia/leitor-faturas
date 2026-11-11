"""
Números do dashboard.

As somas são feitas em Python e não em SQL: um restaurante tem centenas de
faturas por ano, não milhões, e assim o código funciona igual em SQLite e
PostgreSQL (as funções de datas do SQL são diferentes em cada um).
"""

from collections import defaultdict
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modelos.tabelas import Fatura
from app.servicos.repositorio import intervalo_do_mes

MAX_FORNECEDORES_GRAFICO = 8  # os restantes são agrupados em "Outros"


def _chave_mes(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _meses_anteriores(mes: str, quantidade: int) -> list[str]:
    """'2026-03', 3 -> ['2026-01', '2026-02', '2026-03']"""
    ano, numero = (int(p) for p in mes.split("-"))
    meses = []
    for _ in range(quantidade):
        meses.append(f"{ano:04d}-{numero:02d}")
        numero -= 1
        if numero == 0:
            ano, numero = ano - 1, 12
    return meses[::-1]


def _nome(fatura: Fatura) -> str:
    return fatura.nome_fornecedor or (f"NIF {fatura.nif_fornecedor}" if fatura.nif_fornecedor else "Sem fornecedor")


def resumo(db: Session, empresa_id: int, mes: str | None = None, hoje: date | None = None) -> dict:
    hoje = hoje or date.today()

    # Só contam as faturas desta empresa lidas com sucesso (as que têm data e total)
    faturas = list(db.scalars(
        select(Fatura).where(
            Fatura.empresa_id == empresa_id,
            Fatura.estado_extracao == "concluida",
            Fatura.data_emissao.is_not(None),
            Fatura.total.is_not(None),
        )
    ))

    meses_disponiveis = sorted({_chave_mes(f.data_emissao) for f in faturas}, reverse=True)
    # Por omissão: o mês mais recente com faturas (ou o mês atual, se não houver nenhuma)
    mes = mes or (meses_disponiveis[0] if meses_disponiveis else _chave_mes(hoje))
    inicio, fim = intervalo_do_mes(mes)

    do_mes = [f for f in faturas if inicio <= f.data_emissao < fim]

    # Gasto por fornecedor no mês (maiores primeiro, os pequenos juntos em "Outros")
    por_fornecedor: dict[str, float] = defaultdict(float)
    for f in do_mes:
        por_fornecedor[_nome(f)] += f.total
    ordenados = sorted(por_fornecedor.items(), key=lambda item: item[1], reverse=True)
    if len(ordenados) > MAX_FORNECEDORES_GRAFICO:
        principais = ordenados[: MAX_FORNECEDORES_GRAFICO - 1]
        outros = sum(valor for _, valor in ordenados[MAX_FORNECEDORES_GRAFICO - 1:])
        ordenados = principais + [("Outros", outros)]

    # Evolução dos últimos 12 meses (meses sem faturas aparecem a zero)
    meses_grafico = _meses_anteriores(mes, 12)
    por_mes: dict[str, float] = defaultdict(float)
    for f in faturas:
        por_mes[_chave_mes(f.data_emissao)] += f.total

    # IVA suportado desde 1 de janeiro até ao fim do mês escolhido
    inicio_ano = date(inicio.year, 1, 1)
    iva_ano = sum(f.total_iva or 0 for f in faturas if inicio_ano <= f.data_emissao < fim)

    # Faturas por pagar (todas, independentemente do mês), as que vencem primeiro no topo
    por_pagar = sorted(
        (f for f in faturas if not f.paga),
        key=lambda f: (f.data_vencimento or date.max, f.data_emissao),
    )
    vencidas = [f for f in por_pagar if f.data_vencimento and f.data_vencimento < hoje]

    return {
        "mes": mes,
        "meses_disponiveis": meses_disponiveis,
        "total_mes": round(sum(f.total for f in do_mes), 2),
        "faturas_mes": len(do_mes),
        "iva_mes": round(sum(f.total_iva or 0 for f in do_mes), 2),
        "iva_ano": round(iva_ano, 2),
        "ano": inicio.year,
        "por_fornecedor": [{"fornecedor": nome, "total": round(valor, 2)} for nome, valor in ordenados],
        "evolucao": [{"mes": m, "total": round(por_mes.get(m, 0.0), 2)} for m in meses_grafico],
        "por_pagar": {
            "quantidade": len(por_pagar),
            "total": round(sum(f.total for f in por_pagar), 2),
            "vencidas": len(vencidas),
            "total_vencidas": round(sum(f.total for f in vencidas), 2),
            "faturas": [
                {
                    "id": f.id,
                    "fornecedor": _nome(f),
                    "numero_fatura": f.numero_fatura,
                    "data_vencimento": f.data_vencimento,
                    "total": f.total,
                    "vencida": bool(f.data_vencimento and f.data_vencimento < hoje),
                }
                for f in por_pagar
            ],
        },
    }
