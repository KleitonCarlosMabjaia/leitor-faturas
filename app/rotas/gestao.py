"""
Área de gestão: painel do empresário, artigos e stock, clientes, tesouraria e orçamento.

Está em todos os planos. O que depende da contabilidade (gerar o inventário para o apuramento do
CMVMC) exige o plano com contabilidade.
"""

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencias import utilizador_atual
from app.modelos.tabelas import Empresa, FaturaVenda, Utilizador
from app.servicos import repositorio, subscricoes
from app.servicos.gestao import orcamento, painel, stock, tesouraria

router = APIRouter(prefix="/api/gestao", tags=["gestão"])


def _empresa(db: Session, utilizador: Utilizador) -> Empresa:
    return db.get(Empresa, utilizador.empresa_id)


def _artigo(db: Session, utilizador: Utilizador, artigo_id: int):
    a = stock.obter(db, utilizador.empresa_id, artigo_id)
    if a is None:
        raise HTTPException(status_code=404, detail="Artigo não encontrado.")
    return a


@router.get("/painel")
def obter_painel(db: Session = Depends(get_db), utilizador: Utilizador = Depends(utilizador_atual)):
    return painel.painel(db, _empresa(db, utilizador))


# ---------- Artigos e stock ----------

class ArtigoEntrada(BaseModel):
    codigo: str = Field(min_length=1, max_length=60)
    nome: str = Field(min_length=1, max_length=200)
    tipo: Literal["M", "P", "S"] = "M"
    unidade: str = Field("UN", max_length=10)
    preco_venda: float = Field(0, ge=0)
    taxa_iva: float = Field(23, ge=0, le=23)
    stock_minimo: float = Field(0, ge=0)
    ativo: bool = True


@router.get("/artigos")
def listar_artigos(db: Session = Depends(get_db), utilizador: Utilizador = Depends(utilizador_atual)):
    artigos = [stock.artigo_dict(a) for a in stock.listar(db, utilizador.empresa_id)]
    ativos = [a for a in artigos if a["ativo"]]
    return {"artigos": artigos, "valor_total": round(sum(a["valor"] for a in ativos), 2),
            "abaixo_minimo": sum(1 for a in ativos if a["abaixo_minimo"]), "tipos": stock.TIPOS,
            "taxas_iva": stock.TAXAS_IVA}


@router.post("/artigos", status_code=201)
def criar_artigo(dados: ArtigoEntrada, db: Session = Depends(get_db),
                 utilizador: Utilizador = Depends(utilizador_atual)):
    try:
        a = stock.guardar(db, utilizador.empresa_id, None, **dados.model_dump())
    except stock.ErroStock as erro:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(erro))
    db.commit()
    return stock.artigo_dict(a)


@router.put("/artigos/{artigo_id}")
def alterar_artigo(artigo_id: int, dados: ArtigoEntrada, db: Session = Depends(get_db),
                   utilizador: Utilizador = Depends(utilizador_atual)):
    a = _artigo(db, utilizador, artigo_id)
    try:
        stock.guardar(db, utilizador.empresa_id, a, **dados.model_dump())
    except stock.ErroStock as erro:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(erro))
    db.commit()
    return stock.artigo_dict(a)


@router.get("/movimentos")
def listar_movimentos(artigo_id: int | None = None, db: Session = Depends(get_db),
                      utilizador: Utilizador = Depends(utilizador_atual)):
    return [stock.movimento_dict(m) for m in stock.movimentos(db, utilizador.empresa_id, artigo_id)]


class MovimentoEntrada(BaseModel):
    artigo_id: int
    tipo: Literal["entrada", "saida", "ajuste"]
    quantidade: float = Field(ge=0, le=1e9)
    custo_unitario: float | None = Field(None, ge=0, le=1e9)
    data: date | None = None
    documento: str | None = Field(None, max_length=80)


@router.post("/movimentos", status_code=201)
def criar_movimento(dados: MovimentoEntrada, db: Session = Depends(get_db),
                    utilizador: Utilizador = Depends(utilizador_atual)):
    a = _artigo(db, utilizador, dados.artigo_id)
    try:
        m = stock.movimentar(db, utilizador.empresa_id, a, dados.tipo, dados.quantidade, dados.data or date.today(),
                             custo_unitario=dados.custo_unitario, documento=dados.documento,
                             utilizador_id=utilizador.id)
    except stock.ErroStock as erro:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(erro))
    db.commit()
    return stock.movimento_dict(m)


@router.get("/stock/origens")
def origens_stock(db: Session = Depends(get_db), utilizador: Utilizador = Depends(utilizador_atual)):
    """Faturas de fornecedores por dar entrada e vendas por dar saída do stock."""
    return {"faturas": stock.faturas_para_entrada(db, utilizador.empresa_id),
            "vendas": stock.vendas_para_saida(db, utilizador.empresa_id)}


class LinhaStock(BaseModel):
    artigo_id: int
    quantidade: float = Field(gt=0, le=1e9)
    valor_unitario: float | None = Field(None, ge=0, le=1e9)


class EntradaFatura(BaseModel):
    fatura_id: str = Field(max_length=12)
    linhas: list[LinhaStock] = Field(min_length=1, max_length=200)


class SaidaVenda(BaseModel):
    venda_id: int
    linhas: list[LinhaStock] = Field(min_length=1, max_length=200)


def _linhas(linhas: list[LinhaStock]) -> list[stock.LinhaMovimento]:
    return [stock.LinhaMovimento(ln.artigo_id, ln.quantidade, ln.valor_unitario) for ln in linhas]


@router.post("/stock/entrada-fatura", status_code=201)
def entrada_fatura(dados: EntradaFatura, db: Session = Depends(get_db),
                   utilizador: Utilizador = Depends(utilizador_atual)):
    fatura = repositorio.obter_fatura(db, utilizador.empresa_id, dados.fatura_id)
    if fatura is None:
        raise HTTPException(status_code=404, detail="Fatura não encontrada.")
    if any(ln.valor_unitario is None for ln in dados.linhas):
        raise HTTPException(status_code=422, detail="Indique o custo unitário de cada artigo.")
    try:
        movs = stock.entrada_por_fatura(db, _empresa(db, utilizador), fatura, _linhas(dados.linhas), utilizador.id)
    except stock.ErroStock as erro:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(erro))
    db.commit()
    return [stock.movimento_dict(m) for m in movs]


@router.post("/stock/saida-venda", status_code=201)
def saida_venda(dados: SaidaVenda, db: Session = Depends(get_db), utilizador: Utilizador = Depends(utilizador_atual)):
    venda = db.get(FaturaVenda, dados.venda_id)
    if venda is None or venda.empresa_id != utilizador.empresa_id:
        raise HTTPException(status_code=404, detail="Venda não encontrada.")
    try:
        movs = stock.saida_por_venda(db, _empresa(db, utilizador), venda, _linhas(dados.linhas), utilizador.id)
    except stock.ErroStock as erro:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(erro))
    db.commit()
    return [stock.movimento_dict(m) for m in movs]


class InventarioEntrada(BaseModel):
    data: date


@router.post("/stock/inventario", status_code=201)
def gerar_inventario(dados: InventarioEntrada, db: Session = Depends(get_db),
                     utilizador: Utilizador = Depends(utilizador_atual)):
    """Passa o stock numa data para os Inventários da contabilidade (para o apuramento do CMVMC)."""
    empresa = _empresa(db, utilizador)
    if not subscricoes.tem_funcionalidade(empresa, subscricoes.FUNC_CONTABILIDADE):
        raise HTTPException(status_code=403, detail="O inventário da contabilidade faz parte do plano Contabilidade.")
    try:
        inv = stock.criar_inventario(db, empresa, dados.data, utilizador.id)
    except stock.ErroStock as erro:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(erro))
    db.commit()
    return {"id": inv.id, "data": inv.data, "artigos": len(inv.linhas),
            "valor": round(sum(ln.valor for ln in inv.linhas), 2)}


@router.get("/margens")
def margens(de: date | None = None, ate: date | None = None, db: Session = Depends(get_db),
            utilizador: Utilizador = Depends(utilizador_atual)):
    ate = ate or date.today()
    return stock.margens(db, utilizador.empresa_id, de or date(ate.year, 1, 1), ate)


# ---------- Clientes ----------

@router.get("/clientes")
def listar_clientes(db: Session = Depends(get_db), utilizador: Utilizador = Depends(utilizador_atual)):
    return painel.clientes(db, utilizador.empresa_id)


@router.get("/clientes/{nif}")
def documentos_cliente(nif: str, db: Session = Depends(get_db), utilizador: Utilizador = Depends(utilizador_atual)):
    return painel.documentos_cliente(db, utilizador.empresa_id, nif[:20])


# ---------- Tesouraria ----------

@router.get("/tesouraria")
def previsao_tesouraria(semanas: int = Query(13, ge=4, le=26), db: Session = Depends(get_db),
                        utilizador: Utilizador = Depends(utilizador_atual)):
    return tesouraria.previsao(db, _empresa(db, utilizador), semanas=semanas)


# ---------- Orçamento ----------

@router.get("/orcamento")
def obter_orcamento(ano: int | None = Query(None, ge=2000, le=2100), db: Session = Depends(get_db),
                    utilizador: Utilizador = Depends(utilizador_atual)):
    return orcamento.comparar(db, utilizador.empresa_id, ano or date.today().year)


@router.get("/orcamento/proposta")
def proposta_orcamento(ano: int = Query(ge=2000, le=2100), crescimento: float = Query(0, ge=-90, le=500),
                       db: Session = Depends(get_db), utilizador: Utilizador = Depends(utilizador_atual)):
    return orcamento.proposta(db, utilizador.empresa_id, ano, crescimento)


class OrcamentoEntrada(BaseModel):
    ano: int = Field(ge=2000, le=2100)
    valores: dict[str, list[float]]


@router.put("/orcamento")
def guardar_orcamento(dados: OrcamentoEntrada, db: Session = Depends(get_db),
                      utilizador: Utilizador = Depends(utilizador_atual)):
    try:
        orcamento.guardar(db, utilizador.empresa_id, dados.ano, dados.valores)
    except orcamento.ErroOrcamento as erro:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(erro))
    db.commit()
    return orcamento.comparar(db, utilizador.empresa_id, dados.ano)
