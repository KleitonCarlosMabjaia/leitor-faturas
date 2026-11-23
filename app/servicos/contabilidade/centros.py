"""
Contabilidade analítica por centros de custo (lojas, obras, departamentos, projetos...).

Cada linha de gasto (classe 6 e compras, 31) ou de rendimento (classe 7) pode ser atribuída a um
centro. A atribuição é informação de gestão: não altera a contabilidade geral, por isso pode ser
feita ou corrigida em lançamentos já gravados, mesmo em períodos fechados.
O relatório mostra, por centro, gastos, rendimentos e resultado, mais o que ficou sem centro.
"""

import re
from collections import defaultdict
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.modelos.tabelas import LANCAMENTO_ATIVO, CentroCusto, Conta, Lancamento, LinhaLancamento

PREFIXOS_ANALITICOS = ("6", "7", "31")
SEM_CENTRO = "Sem centro de custo"


class ErroCentro(Exception):
    pass


def analitica(codigo_conta: str) -> bool:
    return codigo_conta.startswith(PREFIXOS_ANALITICOS)


def listar(db: Session, empresa_id: int) -> list[CentroCusto]:
    consulta = select(CentroCusto).where(CentroCusto.empresa_id == empresa_id).order_by(CentroCusto.codigo)
    return list(db.scalars(consulta))


def obter(db: Session, empresa_id: int, centro_id: int) -> CentroCusto | None:
    centro = db.get(CentroCusto, centro_id)
    return centro if centro is not None and centro.empresa_id == empresa_id else None


def criar(db: Session, empresa_id: int, codigo: str, nome: str) -> CentroCusto:
    codigo = re.sub(r"\s+", "", codigo or "").upper()[:20]
    nome = (nome or "").strip()[:120]
    if not codigo or not nome:
        raise ErroCentro("Indique o código e o nome do centro de custo.")
    if db.scalar(select(CentroCusto.id).where(CentroCusto.empresa_id == empresa_id, CentroCusto.codigo == codigo)):
        raise ErroCentro(f"Já existe um centro de custo com o código {codigo}.")
    centro = CentroCusto(empresa_id=empresa_id, codigo=codigo, nome=nome)
    db.add(centro)
    db.flush()
    return centro


def atribuir(db: Session, empresa_id: int, linha_id: int, centro_id: int | None) -> LinhaLancamento:
    linha = db.scalar(
        select(LinhaLancamento).options(joinedload(LinhaLancamento.conta)).join(Lancamento)
        .where(LinhaLancamento.id == linha_id, Lancamento.empresa_id == empresa_id)
    )
    if linha is None:
        raise ErroCentro("Linha não encontrada.")
    if not analitica(linha.conta.codigo):
        raise ErroCentro("Só as linhas de gastos (classe 6 e compras) e de rendimentos (classe 7) têm centro de custo.")
    if centro_id is not None:
        centro = obter(db, empresa_id, centro_id)
        if centro is None or not centro.ativo:
            raise ErroCentro("Centro de custo não encontrado ou desativado.")
    linha.centro_custo_id = centro_id
    return linha


def atribuir_ao_lancamento(lancamento: Lancamento, centro_id: int | None) -> None:
    """Na contabilização de uma fatura: o centro escolhido vai para as linhas de gasto."""
    for linha in lancamento.linhas:
        if linha.conta and analitica(linha.conta.codigo):
            linha.centro_custo_id = centro_id


def relatorio(db: Session, empresa_id: int, de: date, ate: date) -> dict:
    linhas = db.execute(
        select(LinhaLancamento.debito, LinhaLancamento.credito, Conta.codigo, CentroCusto.codigo, CentroCusto.nome)
        .join(Lancamento, Lancamento.id == LinhaLancamento.lancamento_id)
        .join(Conta, Conta.id == LinhaLancamento.conta_id)
        .outerjoin(CentroCusto, CentroCusto.id == LinhaLancamento.centro_custo_id)
        .where(Lancamento.empresa_id == empresa_id, Lancamento.data >= de, Lancamento.data <= ate,
               # Lançamentos anulados e os respetivos estornos não contam; o apuramento de resultados também não
               Lancamento.estado == LANCAMENTO_ATIVO, Lancamento.estorno_de_id.is_(None),
               Lancamento.origem.not_in(("fecho", "aplicacao")))
    ).all()
    por_centro: dict[tuple[str, str], dict[str, float]] = defaultdict(lambda: {"gastos": 0.0, "rendimentos": 0.0})
    por_conta: dict[tuple[str, str], dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for debito, credito, conta, cod_centro, nome_centro in linhas:
        if not analitica(conta):
            continue
        chave = (cod_centro or "", nome_centro or SEM_CENTRO)
        if conta.startswith("7"):
            por_centro[chave]["rendimentos"] += credito - debito
        else:
            por_centro[chave]["gastos"] += debito - credito
        por_conta[chave][conta[:2]] += (debito - credito) if not conta.startswith("7") else (credito - debito)
    resultado = []
    for (codigo, nome), v in sorted(por_centro.items(), key=lambda x: (x[0][0] == "", x[0][0])):
        gastos, rendimentos = round(v["gastos"], 2), round(v["rendimentos"], 2)
        resultado.append({
            "codigo": codigo or None, "nome": nome, "gastos": gastos, "rendimentos": rendimentos,
            "resultado": round(rendimentos - gastos, 2),
            "por_conta": {k: round(x, 2) for k, x in sorted(por_conta[(codigo, nome)].items()) if round(x, 2)},
        })
    total = {k: round(sum(r[k] for r in resultado), 2) for k in ("gastos", "rendimentos", "resultado")}
    return {"de": de, "ate": ate, "centros": resultado, "total": total}
