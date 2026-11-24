"""
API pública do Dobra (versão 1), para ligar outros programas: ERP, BI, faturação, bancos.

Autenticação: cabeçalho `Authorization: Bearer dobra_...` com uma chave criada pelo administrador
na página Equipa. A chave pertence a uma empresa e tem permissão de leitura ou de escrita. O
cookie de sessão nunca é aceite aqui (sem risco de CSRF; pode ser chamada de qualquer origem).

Regras
- Limite de pedidos por chave e por minuto (LIMITE_API_POR_MINUTO, 300 por omissão): 429 com Retry-After.
- Listas paginadas: `pagina` (1, 2, ...) e `por_pagina` (até 200). A resposta traz `total`.
- Montantes em euros com sinal quando faz sentido (movimentos bancários: + entra, - sai).
- Datas em AAAA-MM-DD. Erros sempre em JSON: {"detail": "mensagem"}.
- Com a subscrição em "só leitura", as escritas respondem 402, como na aplicação.

O contrato está em /api/v1/openapi.json e a documentação legível em /documentacao-api.
"""

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.openapi.utils import get_openapi
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app import config
from app.db import get_db
from app.modelos.tabelas import (
    LANCAMENTO_ATIVO,
    PERMISSAO_ESCRITA,
    ChaveApi,
    Conta,
    Empresa,
    Fatura,
    FaturaVenda,
    Lancamento,
    LinhaLancamento,
    MovimentoBancario,
)
from app.servicos import atividade, chaves_api, limites, subscricoes
from app.servicos.contabilidade import banco, extratos, lancamentos, plano_snc, relatorios

PREFIXO = "/api/v1"
_portador = HTTPBearer(auto_error=False, description="Chave da API (criada na página Equipa)")


class Contexto:
    def __init__(self, chave: ChaveApi, empresa: Empresa):
        self.chave = chave
        self.empresa = empresa


def contexto(credenciais: HTTPAuthorizationCredentials | None = Depends(_portador),
             db: Session = Depends(get_db)) -> Contexto:
    chave = chaves_api.autenticar(db, credenciais.credentials if credenciais else None)
    if chave is None:
        raise HTTPException(status_code=401, detail="Chave da API em falta, inválida ou revogada.",
                            headers={"WWW-Authenticate": "Bearer"})
    limites.verificar(limites.api, f"chave-{chave.id}")
    empresa = db.get(Empresa, chave.empresa_id)
    if empresa is None or not empresa.ativa:
        raise HTTPException(status_code=403, detail="A empresa desta chave está desativada.")
    db.commit()   # último uso
    return Contexto(chave, empresa)


def _funcionalidade(ctx: Contexto, funcionalidade: str) -> None:
    if not subscricoes.tem_funcionalidade(ctx.empresa, funcionalidade):
        raise HTTPException(status_code=403, detail="O plano da empresa não inclui estes dados.")


def com_compras(ctx: Contexto = Depends(contexto)) -> Contexto:
    _funcionalidade(ctx, subscricoes.FUNC_COMPRAS)
    return ctx


def com_contabilidade(ctx: Contexto = Depends(contexto)) -> Contexto:
    _funcionalidade(ctx, subscricoes.FUNC_CONTABILIDADE)
    return ctx


def para_escrita(ctx: Contexto = Depends(com_contabilidade)) -> Contexto:
    if ctx.chave.permissao != PERMISSAO_ESCRITA:
        raise HTTPException(status_code=403, detail="Esta chave só tem permissão de leitura.")
    motivo = subscricoes.so_leitura(ctx.empresa)
    if motivo:
        raise HTTPException(status_code=402, detail=motivo)
    return ctx


router = APIRouter(prefix=PREFIXO, tags=["API pública v1"])


class Paginacao(BaseModel):
    pagina: int
    por_pagina: int
    total: int


def _paginar(db: Session, consulta, pagina: int, por_pagina: int) -> tuple[list, Paginacao]:
    total = db.scalar(select(func.count()).select_from(consulta.order_by(None).subquery()))
    itens = list(db.scalars(consulta.offset((pagina - 1) * por_pagina).limit(por_pagina)).unique())
    return itens, Paginacao(pagina=pagina, por_pagina=por_pagina, total=total or 0)


Pagina = Query(1, ge=1, description="Página (começa em 1)")
PorPagina = Query(50, ge=1, le=200, description="Resultados por página (até 200)")


# ---------- Empresa ----------

@router.get("/empresa", summary="Empresa da chave")
def empresa(ctx: Contexto = Depends(contexto)):
    """A empresa a que a chave dá acesso e a permissão da chave."""
    e = ctx.empresa
    return {"id": e.id, "nome": e.nome, "nif": e.nif, "regime_iva": e.regime_iva, "plano": e.plano,
            "permissao": ctx.chave.permissao}


# ---------- Faturas de compra ----------

def _fatura(f: Fatura) -> dict:
    return {
        "id": f.id, "fornecedor": f.nome_fornecedor, "nif_fornecedor": f.nif_fornecedor,
        "numero": f.numero_fatura, "atcud": f.atcud, "tipo_documento": f.tipo_documento,
        "data_emissao": f.data_emissao, "data_vencimento": f.data_vencimento,
        "valor_sem_iva": f.valor_sem_iva, "total_iva": f.total_iva, "total": f.total, "moeda": f.moeda,
        "linhas_iva": f.linhas_iva or [], "estado": (f.estado or "").replace(" ", "_"), "paga": f.paga,
        "estado_leitura": f.estado_extracao, "carregada_em": f.carregada_em,
    }


@router.get("/faturas", summary="Faturas de compra")
def listar_faturas(de: date | None = Query(None, description="Data de emissão desde"),
                   ate: date | None = Query(None, description="Data de emissão até"),
                   estado: Literal["por_rever", "confirmada"] | None = None,
                   paga: bool | None = None,
                   nif: str | None = Query(None, max_length=20, description="NIF do fornecedor"),
                   pagina: int = Pagina, por_pagina: int = PorPagina,
                   ctx: Contexto = Depends(com_compras), db: Session = Depends(get_db)):
    """Faturas de fornecedores lidas pela aplicação, das mais recentes para as mais antigas."""
    consulta = select(Fatura).where(Fatura.empresa_id == ctx.empresa.id)
    if de:
        consulta = consulta.where(Fatura.data_emissao >= de)
    if ate:
        consulta = consulta.where(Fatura.data_emissao <= ate)
    if estado:
        consulta = consulta.where(Fatura.estado == ("por rever" if estado == "por_rever" else estado))
    if paga is not None:
        consulta = consulta.where(Fatura.paga.is_(paga))
    if nif:
        consulta = consulta.where(Fatura.nif_fornecedor == nif)
    consulta = consulta.order_by(Fatura.data_emissao.desc(), Fatura.carregada_em.desc())
    itens, pag = _paginar(db, consulta, pagina, por_pagina)
    return {"dados": [_fatura(f) for f in itens], **pag.model_dump()}


@router.get("/faturas/{fatura_id}", summary="Uma fatura de compra")
def obter_fatura(fatura_id: str, ctx: Contexto = Depends(com_compras), db: Session = Depends(get_db)):
    f = db.scalar(select(Fatura).where(Fatura.id == fatura_id, Fatura.empresa_id == ctx.empresa.id))
    if f is None:
        raise HTTPException(status_code=404, detail="Fatura não encontrada.")
    return _fatura(f)


# ---------- Vendas ----------

def _venda(v: FaturaVenda) -> dict:
    return {
        "id": v.id, "numero": v.numero, "tipo": v.tipo, "atcud": v.atcud, "data": v.data,
        "data_vencimento": v.data_vencimento, "cliente_nif": v.cliente_nif, "cliente_nome": v.cliente_nome,
        "valor_sem_iva": v.valor_sem_iva, "total_iva": v.total_iva, "total": v.total, "estado": v.estado,
        "valor_recebido": v.valor_recebido, "recebida": v.recebida, "origem": v.origem,
    }


@router.get("/vendas", summary="Faturas de venda")
def listar_vendas(de: date | None = None, ate: date | None = None, recebida: bool | None = None,
                  pagina: int = Pagina, por_pagina: int = PorPagina,
                  ctx: Contexto = Depends(com_contabilidade), db: Session = Depends(get_db)):
    """Faturas emitidas a clientes (importadas do SAF-T de faturação ou registadas à mão)."""
    consulta = select(FaturaVenda).where(FaturaVenda.empresa_id == ctx.empresa.id)
    if de:
        consulta = consulta.where(FaturaVenda.data >= de)
    if ate:
        consulta = consulta.where(FaturaVenda.data <= ate)
    if recebida is not None:
        consulta = consulta.where(FaturaVenda.recebida.is_(recebida))
    itens, pag = _paginar(db, consulta.order_by(FaturaVenda.data.desc(), FaturaVenda.id.desc()), pagina, por_pagina)
    return {"dados": [_venda(v) for v in itens], **pag.model_dump()}


# ---------- Contabilidade ----------

@router.get("/contas", summary="Plano de contas (SNC)")
def listar_contas(ctx: Contexto = Depends(com_contabilidade), db: Session = Depends(get_db)):
    plano_snc.garantir_plano(db, ctx.empresa.id)
    db.commit()
    contas = db.scalars(select(Conta).where(Conta.empresa_id == ctx.empresa.id).order_by(Conta.codigo))
    return [{"codigo": c.codigo, "nome": c.nome, "movimento": c.movimento} for c in contas]


def _lancamento(la: Lancamento) -> dict:
    return {
        "id": la.id, "referencia": f"{la.diario} {la.ano}/{la.numero}", "diario": la.diario, "data": la.data,
        "descricao": la.descricao, "documento": la.documento, "origem": la.origem, "estado": la.estado,
        "fatura_id": la.fatura_id, "venda_id": la.venda_id, "estorno_de_id": la.estorno_de_id,
        "linhas": [{"conta": ln.conta.codigo, "debito": ln.debito, "credito": ln.credito, "descricao": ln.descricao,
                    "tipo_iva": ln.tipo_iva, "taxa_iva": ln.taxa_iva, "centro_custo_id": ln.centro_custo_id}
                   for ln in la.linhas],
    }


@router.get("/lancamentos", summary="Lançamentos contabilísticos")
def listar_lancamentos(de: date | None = None, ate: date | None = None,
                       diario: str | None = Query(None, pattern=r"^[A-Z]{2,4}$", description="Ex.: COM, VEN, BAN, OD"),
                       incluir_estornados: bool = Query(False, description="Incluir os lançamentos estornados"),
                       pagina: int = Pagina, por_pagina: int = PorPagina,
                       ctx: Contexto = Depends(com_contabilidade), db: Session = Depends(get_db)):
    """Lançamentos (partidas dobradas) com as linhas, dos mais antigos para os mais recentes."""
    consulta = (select(Lancamento)
                .options(selectinload(Lancamento.linhas).joinedload(LinhaLancamento.conta))
                .where(Lancamento.empresa_id == ctx.empresa.id))
    if de:
        consulta = consulta.where(Lancamento.data >= de)
    if ate:
        consulta = consulta.where(Lancamento.data <= ate)
    if diario:
        consulta = consulta.where(Lancamento.diario == diario)
    if not incluir_estornados:
        consulta = consulta.where(Lancamento.estado == LANCAMENTO_ATIVO)
    itens, pag = _paginar(db, consulta.order_by(Lancamento.data, Lancamento.id), pagina, por_pagina)
    return {"dados": [_lancamento(la) for la in itens], **pag.model_dump()}


@router.get("/balancete", summary="Balancete")
def obter_balancete(de: date = Query(..., description="Início do período"), ate: date = Query(..., description="Fim"),
                    ctx: Contexto = Depends(com_contabilidade), db: Session = Depends(get_db)):
    """Balancete analítico do período: movimentos, acumulados e saldos por conta."""
    if de > ate:
        raise HTTPException(status_code=422, detail="A data de início é posterior à data de fim.")
    resultado = relatorios.balancete(db, ctx.empresa.id, de, ate)
    db.commit()
    return resultado


# ---------- Bancos ----------

def _movimento(m: MovimentoBancario) -> dict:
    return {"id": m.id, "conta": m.conta_codigo, "data": m.data, "descricao": m.descricao, "montante": m.montante,
            "saldo": m.saldo, "estado": m.estado, "referencia": m.referencia,
            "lancamento_id": m.linha.lancamento_id if m.linha else None}


@router.get("/bancos/movimentos", summary="Movimentos bancários")
def listar_movimentos(conta: str | None = Query(None, pattern=r"^1[123]\d+$", description="Conta SNC (ex.: 121)"),
                      de: date | None = None, ate: date | None = None,
                      estado: Literal["pendente", "reconciliado", "ignorado"] | None = None,
                      pagina: int = Pagina, por_pagina: int = PorPagina,
                      ctx: Contexto = Depends(com_contabilidade), db: Session = Depends(get_db)):
    """Movimentos importados (ficheiro, open banking ou API) e o estado da reconciliação."""
    consulta = (select(MovimentoBancario).options(joinedload(MovimentoBancario.linha))
                .where(MovimentoBancario.empresa_id == ctx.empresa.id))
    if conta:
        consulta = consulta.where(MovimentoBancario.conta_codigo == conta)
    if de:
        consulta = consulta.where(MovimentoBancario.data >= de)
    if ate:
        consulta = consulta.where(MovimentoBancario.data <= ate)
    if estado:
        consulta = consulta.where(MovimentoBancario.estado == estado)
    consulta = consulta.order_by(MovimentoBancario.data.desc(), MovimentoBancario.id.desc())
    itens, pag = _paginar(db, consulta, pagina, por_pagina)
    return {"dados": [_movimento(m) for m in itens], **pag.model_dump()}


class MovimentoEntrada(BaseModel):
    data: date
    descricao: str = Field(min_length=1, max_length=300)
    montante: float = Field(description="Com sinal: positivo entra na conta, negativo sai")
    saldo: float | None = Field(None, description="Saldo depois do movimento (opcional)")
    referencia: str | None = Field(None, max_length=50,
                                   description="Identificador único no banco: reenviar o mesmo não duplica")


class MovimentosEntrada(BaseModel):
    conta: str = Field("121", pattern=r"^1[123]\d+$", description="Conta SNC de bancos ou caixa")
    movimentos: list[MovimentoEntrada] = Field(min_length=1, max_length=1000)
    saldo_final: float | None = Field(None, description="Saldo da conta no fim (para o mapa de reconciliação)")
    origem: str = Field("API", min_length=1, max_length=60, description="Nome que aparece na lista de importações")


@router.post("/bancos/movimentos", status_code=201, summary="Enviar movimentos bancários")
def enviar_movimentos(dados: MovimentosEntrada, request: Request, ctx: Contexto = Depends(para_escrita),
                      db: Session = Depends(get_db)):
    """
    Grava movimentos bancários para reconciliar (ex.: vindos de outro agregador bancário).
    Movimentos já enviados (mesma referência, ou mesma data, valor e descrição) são ignorados.
    Precisa de uma chave com permissão de escrita.
    """
    if any(abs(m.montante) < 0.005 for m in dados.movimentos):
        raise HTTPException(status_code=422, detail="Há movimentos com montante zero.")
    lidos = [extratos.MovimentoLido(m.data, " ".join(m.descricao.split()), round(m.montante, 2), m.saldo,
                                    f"api:{m.referencia}" if m.referencia else None) for m in dados.movimentos]
    try:
        plano_snc.garantir_plano(db, ctx.empresa.id)
        banco.verificar_conta(db, ctx.empresa.id, dados.conta)
        lido = extratos.ExtratoLido(formato="api", movimentos=lidos, saldo_final=dados.saldo_final)
        r = banco.gravar(db, ctx.empresa, dados.conta, lido, f"{dados.origem} (API: {ctx.chave.nome})",
                         dados.saldo_final)
    except lancamentos.ErroContabilistico as erro:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(erro))
    atividade.registar(db, None, f"API: enviou {r.importados} movimento(s) bancário(s) para a conta {dados.conta}",
                       detalhe=f"Chave {ctx.chave.nome} ({ctx.chave.prefixo}…)", empresa_id=ctx.empresa.id,
                       ip=request.client.host if request.client else None)
    db.commit()
    return {"extrato_id": r.extrato.id, "importados": r.importados, "duplicados": r.duplicados}


# ---------- Contrato (OpenAPI) ----------

def contrato() -> dict:
    """Só as rotas da API pública, publicado mesmo em produção (o resto da aplicação não é API pública)."""
    spec = get_openapi(
        title="API do Dobra", version="1.0.0", routes=router.routes,
        description=(__doc__ or "").strip().split("\n\n", 1)[1] if __doc__ else None,
        servers=[{"url": config.URL_PUBLICO}],
    )
    return spec
