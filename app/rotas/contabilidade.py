"""
Endpoints da contabilidade (SNC).

Tudo é por empresa: cada rota recebe o utilizador e usa utilizador.empresa_id.
Os erros contabilísticos (lançamento desequilibrado, período fechado...) chegam ao
utilizador como 409 com a explicação em português.
"""

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.db import get_db
from app.dependencias import utilizador_atual
from app.modelos.tabelas import PAPEL_ADMIN, Conta, Empresa, Fatura, Lancamento, Utilizador
from app.servicos import armazenamento, exportacao, repositorio
from app.servicos.contabilidade import (
    assistente,
    classificacao,
    ia,
    iva,
    lancamentos,
    obrigacoes,
    plano_snc,
    relatorios,
    verificacoes,
)

router = APIRouter(prefix="/api/contabilidade", tags=["contabilidade"])


# ---------- Utilitários ----------

def _empresa(db: Session, utilizador: Utilizador) -> Empresa:
    """A empresa de quem faz o pedido, com o plano de contas criado (na primeira visita)."""
    empresa = db.get(Empresa, utilizador.empresa_id)
    plano_snc.garantir_plano(db, empresa.id)
    db.commit()
    return empresa


def _periodo(de: date | None, ate: date | None) -> tuple[date, date]:
    hoje = date.today()
    ate = ate or hoje
    de = de or date(ate.year, 1, 1)
    if de > ate:
        raise HTTPException(status_code=422, detail="A data inicial é posterior à data final.")
    return de, ate


def _fatura(db: Session, utilizador: Utilizador, id_fatura: str) -> Fatura:
    if not armazenamento.id_valido(id_fatura):
        raise HTTPException(status_code=400, detail="Identificador inválido.")
    fatura = repositorio.obter_fatura(db, utilizador.empresa_id, id_fatura)
    if fatura is None:
        raise HTTPException(status_code=404, detail="Fatura não encontrada.")
    return fatura


def _erro(erro: Exception) -> HTTPException:
    return HTTPException(status_code=409, detail=str(erro))


def lancamento_dict(la: Lancamento) -> dict:
    return {
        "id": la.id,
        "referencia": f"{la.diario} {la.ano}/{la.numero}",
        "diario": la.diario,
        "diario_nome": lancamentos.DIARIOS.get(la.diario, la.diario),
        "data": la.data,
        "descricao": la.descricao,
        "documento": la.documento,
        "origem": la.origem,
        "estado": la.estado,
        "fatura_id": la.fatura_id,
        "estorno_de_id": la.estorno_de_id,
        "notas": la.notas or [],
        "criado_por": la.criado_por.nome if la.criado_por else None,
        "criado_em": la.criado_em,
        "total": round(sum(ln.debito for ln in la.linhas), 2),
        "linhas": [{
            "conta": ln.conta.codigo, "nome": ln.conta.nome, "debito": ln.debito, "credito": ln.credito,
            "descricao": ln.descricao, "tipo_iva": ln.tipo_iva, "taxa_iva": ln.taxa_iva,
        } for ln in la.linhas],
    }


def _proposta(db: Session, empresa: Empresa, fatura: Fatura, usar_ia: bool = False) -> dict:
    """Classificação sugerida e pré-visualização das linhas, sem gravar nada."""
    c = classificacao.classificar(db, empresa.id, fatura, usar_ia=usar_ia)
    linhas = lancamentos.linhas_fatura(db, empresa.id, fatura, c.conta_codigo, c.perfil_iva)
    contas = plano_snc.contas_por_codigo(db, empresa.id)
    return {
        "fatura": {
            "id": fatura.id, "fornecedor": fatura.nome_fornecedor, "nif": fatura.nif_fornecedor,
            "documento": lancamentos.documento_fatura(fatura), "data_emissao": fatura.data_emissao,
            "total": fatura.total, "total_iva": fatura.total_iva, "paga": fatura.paga,
        },
        "classificacao": {
            "conta": c.conta_codigo, "conta_nome": contas[c.conta_codigo].nome if c.conta_codigo in contas else "",
            "perfil_iva": c.perfil_iva, "perfil_nome": iva.PERFIS[c.perfil_iva].nome, "origem": c.origem,
            "confianca": c.confianca, "explicacao": c.explicacao, "alertas": c.alertas,
        },
        "linhas": [{
            "conta": ln.conta_codigo, "nome": contas[ln.conta_codigo].nome if ln.conta_codigo in contas else "",
            "debito": round(ln.debito, 2), "credito": round(ln.credito, 2), "descricao": ln.descricao,
        } for ln in linhas],
    }


# ---------- Resumo ----------

@router.get("/resumo")
def resumo(db: Session = Depends(get_db), utilizador: Utilizador = Depends(utilizador_atual)):
    """Painel: indicadores, alertas e próximos prazos."""
    empresa = _empresa(db, utilizador)
    hoje = date.today()
    pendentes = verificacoes.faturas_por_contabilizar(db, empresa.id)

    # IVA do período em curso (mês ou trimestre), até hoje
    if empresa.regime_iva == "mensal":
        inicio_periodo = date(hoje.year, hoje.month, 1)
    else:
        inicio_periodo = date(hoje.year, (hoje.month - 1) // 3 * 3 + 1, 1)
    mapa = relatorios.mapa_iva(db, empresa.id, inicio_periodo, hoje)
    resultados = relatorios.demonstracao_resultados(db, empresa.id, date(hoje.year, 1, 1), hoje)
    correntes = relatorios.contas_correntes(db, empresa.id, hoje, hoje)
    alertas = verificacoes.alertas(db, empresa, hoje)
    db.commit()  # o plano de contas pode ter sido criado agora
    return {
        "empresa": {"nome": empresa.nome, "regime_iva": empresa.regime_iva, "fechado_ate": empresa.fechado_ate},
        "por_contabilizar": {"quantidade": len(pendentes), "total": round(sum(f.total for f in pendentes), 2)},
        "iva_periodo": {"de": inicio_periodo, "ate": hoje, "dedutivel": mapa["total_dedutivel"],
                        "liquidado": mapa["total_liquidado"], "saldo": mapa["saldo"],
                        "nao_dedutivel": mapa["iva_nao_dedutivel"]},
        "resultado_ano": {"valor": resultados["resultado"], "rubricas": resultados["rubricas"]},
        "divida_fornecedores": {"total": correntes["total"], "antiguidade": correntes["antiguidade"]},
        "alertas": alertas,
        "obrigacoes": obrigacoes.proximas(hoje, empresa.regime_iva, 60)[:6],
        "aviso_obrigacoes": obrigacoes.AVISO,
        "ia_disponivel": config.CHAVE_API_DEFINIDA,
    }


# ---------- Faturas por contabilizar ----------

@router.get("/por-contabilizar")
def por_contabilizar(db: Session = Depends(get_db), utilizador: Utilizador = Depends(utilizador_atual)):
    empresa = _empresa(db, utilizador)
    propostas = [_proposta(db, empresa, f) for f in verificacoes.faturas_por_contabilizar(db, empresa.id)]
    db.rollback()  # as subcontas de fornecedor criadas na pré-visualização não ficam gravadas
    return propostas


@router.get("/faturas/{id_fatura}")
def contabilidade_da_fatura(
    id_fatura: str,
    ia_sugestao: bool = Query(False, alias="ia"),
    db: Session = Depends(get_db),
    utilizador: Utilizador = Depends(utilizador_atual),
):
    """Lançamentos já feitos para a fatura ou, se ainda não foi contabilizada, a proposta."""
    empresa = _empresa(db, utilizador)
    fatura = _fatura(db, utilizador, id_fatura)
    feitos = list(db.scalars(
        select(Lancamento).where(Lancamento.empresa_id == empresa.id, Lancamento.fatura_id == fatura.id)
        .order_by(Lancamento.data, Lancamento.id)
    ))
    compra = lancamentos.lancamento_ativo(db, empresa.id, fatura.id, "fatura")
    resposta = {"contabilizada": compra is not None, "lancamentos": [lancamento_dict(la) for la in feitos],
                "proposta": None, "erro": None}
    if compra is None:
        try:
            lancamentos.verificar_fatura_contabilizavel(fatura)
            resposta["proposta"] = _proposta(db, empresa, fatura, usar_ia=ia_sugestao)
        except (lancamentos.ErroContabilistico, ia.ErroIA) as erro:
            resposta["erro"] = str(erro)
    db.rollback()
    return resposta


class Contabilizacao(BaseModel):
    conta_codigo: str | None = None
    perfil_iva: str | None = None


@router.post("/faturas/{id_fatura}/contabilizar", status_code=201)
def contabilizar(
    id_fatura: str,
    escolha: Contabilizacao,
    db: Session = Depends(get_db),
    utilizador: Utilizador = Depends(utilizador_atual),
):
    empresa = _empresa(db, utilizador)
    fatura = _fatura(db, utilizador, id_fatura)
    try:
        lancamento = lancamentos.contabilizar_fatura(
            db, empresa, fatura, conta_codigo=escolha.conta_codigo, perfil_iva=escolha.perfil_iva,
            utilizador_id=utilizador.id,
        )
    except lancamentos.ErroContabilistico as erro:
        db.rollback()
        raise _erro(erro)
    db.commit()
    return lancamento_dict(lancamento)


class Lote(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=200)


@router.post("/contabilizar-lote")
def contabilizar_lote(lote: Lote, db: Session = Depends(get_db), utilizador: Utilizador = Depends(utilizador_atual)):
    """Contabiliza várias faturas com a classificação sugerida. Cada uma tem o seu resultado."""
    empresa = _empresa(db, utilizador)
    db.commit()
    resultados = []
    for id_fatura in lote.ids:
        try:
            fatura = _fatura(db, utilizador, id_fatura)
            lancamento = lancamentos.contabilizar_fatura(db, empresa, fatura, utilizador_id=utilizador.id)
            db.commit()
            resultados.append({"id": id_fatura, "ok": True,
                               "referencia": f"{lancamento.diario} {lancamento.ano}/{lancamento.numero}"})
        except (HTTPException, lancamentos.ErroContabilistico) as erro:
            db.rollback()
            resultados.append({"id": id_fatura, "ok": False, "erro": getattr(erro, "detail", None) or str(erro)})
    return {"resultados": resultados, "contabilizadas": sum(r["ok"] for r in resultados)}


class Pagamento(BaseModel):
    data: date | None = None
    conta: Literal["11", "12", "13"] = "12"


@router.post("/faturas/{id_fatura}/pagamento", status_code=201)
def registar_pagamento(
    id_fatura: str,
    pagamento: Pagamento,
    db: Session = Depends(get_db),
    utilizador: Utilizador = Depends(utilizador_atual),
):
    empresa = _empresa(db, utilizador)
    fatura = _fatura(db, utilizador, id_fatura)
    try:
        lancamento = lancamentos.registar_pagamento(
            db, empresa, fatura, pagamento.data or date.today(), pagamento.conta, utilizador_id=utilizador.id
        )
    except lancamentos.ErroContabilistico as erro:
        db.rollback()
        raise _erro(erro)
    fatura.paga = True
    db.commit()
    return lancamento_dict(lancamento)


# ---------- Lançamentos ----------

@router.get("/lancamentos")
def listar_lancamentos(
    de: date | None = None,
    ate: date | None = None,
    diario: str | None = None,
    pesquisa: str | None = Query(None, max_length=100),
    db: Session = Depends(get_db),
    utilizador: Utilizador = Depends(utilizador_atual),
):
    de, ate = _periodo(de, ate)
    return [lancamento_dict(la) for la in relatorios.diario(db, utilizador.empresa_id, de, ate, diario, pesquisa)]


@router.get("/lancamentos/{lancamento_id}")
def obter_lancamento(lancamento_id: int, db: Session = Depends(get_db),
                     utilizador: Utilizador = Depends(utilizador_atual)):
    lancamento = lancamentos.obter(db, utilizador.empresa_id, lancamento_id)
    if lancamento is None:
        raise HTTPException(status_code=404, detail="Lançamento não encontrado.")
    return lancamento_dict(lancamento)


class LinhaEntrada(BaseModel):
    conta: str = Field(min_length=1, max_length=20)
    debito: float = Field(0, ge=0)
    credito: float = Field(0, ge=0)
    descricao: str | None = Field(None, max_length=200)


class LancamentoEntrada(BaseModel):
    diario: Literal["COM", "VEN", "BAN", "CX", "OD"]
    data: date
    descricao: str = Field(min_length=1, max_length=300)
    documento: str | None = Field(None, max_length=80)
    linhas: list[LinhaEntrada] = Field(min_length=2, max_length=100)
    origem: Literal["manual", "assistente"] = "manual"


@router.post("/lancamentos", status_code=201)
def criar_lancamento(entrada: LancamentoEntrada, db: Session = Depends(get_db),
                     utilizador: Utilizador = Depends(utilizador_atual)):
    empresa = _empresa(db, utilizador)
    linhas = [lancamentos.LinhaNova(ln.conta, debito=ln.debito, credito=ln.credito, descricao=ln.descricao)
              for ln in entrada.linhas]
    try:
        lancamento = lancamentos.criar(
            db, empresa, entrada.diario, entrada.data, entrada.descricao, linhas,
            documento=entrada.documento, origem=entrada.origem, utilizador_id=utilizador.id,
        )
    except lancamentos.ErroContabilistico as erro:
        db.rollback()
        raise _erro(erro)
    db.commit()
    return lancamento_dict(lancamento)


@router.post("/lancamentos/{lancamento_id}/estornar", status_code=201)
def estornar_lancamento(lancamento_id: int, db: Session = Depends(get_db),
                        utilizador: Utilizador = Depends(utilizador_atual)):
    empresa = _empresa(db, utilizador)
    lancamento = lancamentos.obter(db, empresa.id, lancamento_id)
    if lancamento is None:
        raise HTTPException(status_code=404, detail="Lançamento não encontrado.")
    try:
        estorno = lancamentos.estornar(db, empresa, lancamento, utilizador.id)
    except lancamentos.ErroContabilistico as erro:
        db.rollback()
        raise _erro(erro)
    # Estornar o lançamento de um pagamento volta a pôr a fatura "por pagar"
    if lancamento.origem == "pagamento" and lancamento.fatura_id:
        fatura = repositorio.obter_fatura(db, empresa.id, lancamento.fatura_id)
        if fatura:
            fatura.paga = False
    db.commit()
    return lancamento_dict(estorno)


# ---------- Relatórios ----------

@router.get("/balancete")
def obter_balancete(de: date | None = None, ate: date | None = None, db: Session = Depends(get_db),
                    utilizador: Utilizador = Depends(utilizador_atual)):
    de, ate = _periodo(de, ate)
    dados = relatorios.balancete(db, utilizador.empresa_id, de, ate)
    db.commit()
    return dados


@router.get("/balancete/exportar")
def exportar_balancete(de: date | None = None, ate: date | None = None, db: Session = Depends(get_db),
                       utilizador: Utilizador = Depends(utilizador_atual)):
    de, ate = _periodo(de, ate)
    dados = relatorios.balancete(db, utilizador.empresa_id, de, ate)
    conteudo = exportacao.gerar_balancete_xlsx(dados, utilizador.empresa.nome)
    return Response(
        content=conteudo,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="balancete-{de}-a-{ate}.xlsx"'},
    )


@router.get("/extrato")
def obter_extrato(conta: str = Query(..., min_length=1, max_length=20, pattern=r"^\d+$"),
                  de: date | None = None, ate: date | None = None, db: Session = Depends(get_db),
                  utilizador: Utilizador = Depends(utilizador_atual)):
    de, ate = _periodo(de, ate)
    return relatorios.extrato(db, utilizador.empresa_id, conta, de, ate)


@router.get("/iva")
def obter_iva(de: date | None = None, ate: date | None = None, db: Session = Depends(get_db),
              utilizador: Utilizador = Depends(utilizador_atual)):
    empresa = _empresa(db, utilizador)
    if de is None and ate is None:
        de, ate, descricao = obrigacoes.periodo_iva(date.today(), empresa.regime_iva)
    else:
        de, ate = _periodo(de, ate)
        descricao = f"{de:%d/%m/%Y} a {ate:%d/%m/%Y}"
    mapa = relatorios.mapa_iva(db, empresa.id, de, ate)
    apuramentos = db.scalars(select(Lancamento).where(
        Lancamento.empresa_id == empresa.id, Lancamento.origem == "apuramento", Lancamento.estado == "ativo",
        Lancamento.estorno_de_id.is_(None), Lancamento.data >= de, Lancamento.data <= ate,
    )).all()
    db.commit()
    return {**mapa, "descricao": descricao, "apuramentos": [lancamento_dict(la) for la in apuramentos]}


class Apuramento(BaseModel):
    de: date
    ate: date


@router.post("/iva/apurar", status_code=201)
def apurar_iva(periodo: Apuramento, db: Session = Depends(get_db), utilizador: Utilizador = Depends(utilizador_atual)):
    empresa = _empresa(db, utilizador)
    try:
        lancamento = lancamentos.apurar_iva(
            db, empresa, periodo.ate, f"{periodo.de:%d/%m/%Y} a {periodo.ate:%d/%m/%Y}", utilizador.id
        )
    except lancamentos.ErroContabilistico as erro:
        db.rollback()
        raise _erro(erro)
    db.commit()
    return lancamento_dict(lancamento)


@router.get("/resultados")
def obter_resultados(de: date | None = None, ate: date | None = None, db: Session = Depends(get_db),
                     utilizador: Utilizador = Depends(utilizador_atual)):
    de, ate = _periodo(de, ate)
    dados = relatorios.demonstracao_resultados(db, utilizador.empresa_id, de, ate)
    db.commit()
    return dados


@router.get("/fornecedores")
def obter_contas_correntes(ate: date | None = None, db: Session = Depends(get_db),
                           utilizador: Utilizador = Depends(utilizador_atual)):
    return relatorios.contas_correntes(db, utilizador.empresa_id, ate or date.today())


@router.get("/obrigacoes")
def obter_obrigacoes(db: Session = Depends(get_db), utilizador: Utilizador = Depends(utilizador_atual)):
    empresa = _empresa(db, utilizador)
    db.commit()
    return {"aviso": obrigacoes.AVISO, "regime_iva": empresa.regime_iva,
            "obrigacoes": obrigacoes.proximas(date.today(), empresa.regime_iva, 120)}


# ---------- Plano de contas e configuração ----------

@router.get("/contas")
def listar_contas(db: Session = Depends(get_db), utilizador: Utilizador = Depends(utilizador_atual)):
    _empresa(db, utilizador)
    db.commit()
    contas = db.scalars(select(Conta).where(Conta.empresa_id == utilizador.empresa_id).order_by(Conta.codigo))
    return [{"codigo": c.codigo, "nome": c.nome, "movimento": c.movimento, "nif": c.nif} for c in contas]


class ContaNova(BaseModel):
    codigo: str = Field(min_length=2, max_length=20, pattern=r"^\d+$")
    nome: str = Field(min_length=1, max_length=200)


@router.post("/contas", status_code=201)
def criar_conta(nova: ContaNova, db: Session = Depends(get_db), utilizador: Utilizador = Depends(utilizador_atual)):
    """Cria uma subconta. A conta-mãe passa a agregadora, se ainda não tiver movimentos."""
    empresa = _empresa(db, utilizador)
    contas = plano_snc.contas_por_codigo(db, empresa.id)
    if nova.codigo in contas:
        raise HTTPException(status_code=409, detail=f"A conta {nova.codigo} já existe.")
    mae = next((contas[nova.codigo[:n]] for n in range(len(nova.codigo) - 1, 0, -1) if nova.codigo[:n] in contas), None)
    if mae is None:
        raise HTTPException(status_code=422, detail="O código tem de começar pelo código de uma conta existente.")
    if mae.movimento:
        if relatorios.movimentos_por_conta(db, empresa.id).get(mae.codigo):
            raise HTTPException(
                status_code=409,
                detail=f"A conta {mae.codigo} já tem movimentos: não pode passar a ter subcontas.",
            )
        mae.movimento = False
    conta = Conta(empresa_id=empresa.id, codigo=nova.codigo, nome=nova.nome.strip(), movimento=True)
    db.add(conta)
    db.commit()
    return {"codigo": conta.codigo, "nome": conta.nome, "movimento": True, "nif": None}


@router.get("/perfis-iva")
def perfis_iva():
    return [{"chave": p.chave, "nome": p.nome, "dedutivel": p.dedutivel, "explicacao": p.explicacao}
            for p in iva.PERFIS.values()]


class Configuracao(BaseModel):
    regime_iva: Literal["mensal", "trimestral"]
    fechado_ate: date | None = None


@router.put("/configuracao")
def configurar(conf: Configuracao, db: Session = Depends(get_db), utilizador: Utilizador = Depends(utilizador_atual)):
    if config.MODO_DEMO:
        raise HTTPException(status_code=403, detail="A configuração não pode ser alterada na demonstração.")
    if utilizador.papel != PAPEL_ADMIN:
        raise HTTPException(status_code=403, detail="Só os administradores podem fechar períodos ou mudar o regime.")
    empresa = _empresa(db, utilizador)
    empresa.regime_iva = conf.regime_iva
    empresa.fechado_ate = conf.fechado_ate
    db.commit()
    return {"regime_iva": empresa.regime_iva, "fechado_ate": empresa.fechado_ate}


# ---------- Assistente com IA ----------

class MensagemHistorico(BaseModel):
    papel: Literal["utilizador", "assistente"]
    texto: str = Field(max_length=20000)


class Pergunta(BaseModel):
    pergunta: str = Field(min_length=1, max_length=2000)
    historico: list[MensagemHistorico] = Field(default_factory=list, max_length=40)


@router.post("/assistente")
def perguntar(pergunta: Pergunta, db: Session = Depends(get_db), utilizador: Utilizador = Depends(utilizador_atual)):
    empresa = _empresa(db, utilizador)
    try:
        resposta = assistente.perguntar(
            db, empresa, pergunta.pergunta.strip(), [m.model_dump() for m in pergunta.historico]
        )
    except ia.ErroIA as erro:
        db.rollback()
        raise HTTPException(status_code=503, detail=str(erro))
    db.rollback()  # o assistente só lê: nada do que fez fica gravado
    return resposta
