"""
Endpoints da contabilidade (SNC).

Tudo é por empresa: cada rota recebe o utilizador e usa utilizador.empresa_id.
Os erros contabilísticos (lançamento desequilibrado, período fechado...) chegam ao
utilizador como 409 com a explicação em português.
"""

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app import config
from app.db import get_db
from app.dependencias import utilizador_atual
from app.modelos.tabelas import (
    MOVIMENTO_PENDENTE,
    PAPEL_ADMIN,
    VENDA_NORMAL,
    Conta,
    Empresa,
    ExtratoBancario,
    Fatura,
    FaturaVenda,
    Inventario,
    Lancamento,
    LinhaLancamento,
    MovimentoBancario,
    Utilizador,
)
from app.servicos import armazenamento, exportacao, limites, repositorio, validacao
from app.servicos.contabilidade import (
    assistente,
    banco,
    classificacao,
    extratos,
    ia,
    inventarios,
    iva,
    lancamentos,
    obrigacoes,
    plano_snc,
    relatorios,
    saft,
    vendas,
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
        "empresa": _dados_empresa(empresa),
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
    conta: str = Field("121", pattern=r"^1[123]\d+$", description="Conta de caixa ou bancos (ex.: 111, 121)")


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
    # 3 dígitos no mínimo: as contas de 1.º grau (2 dígitos) são sempre agregadoras no SAF-T
    codigo: str = Field(min_length=3, max_length=20, pattern=r"^\d+$")
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


def _dados_empresa(empresa: Empresa) -> dict:
    return {
        "nome": empresa.nome, "nif": empresa.nif, "regime_iva": empresa.regime_iva,
        "fechado_ate": empresa.fechado_ate, "morada": empresa.morada, "codigo_postal": empresa.codigo_postal,
        "localidade": empresa.localidade,
    }


class Configuracao(BaseModel):
    regime_iva: Literal["mensal", "trimestral"]
    fechado_ate: date | None = None
    # Dados da sede (para o SAF-T). None: não mexer; texto vazio: apagar
    nif: str | None = Field(None, max_length=9)
    morada: str | None = Field(None, max_length=210)
    codigo_postal: str | None = Field(None, max_length=8)
    localidade: str | None = Field(None, max_length=50)


@router.put("/configuracao")
def configurar(conf: Configuracao, db: Session = Depends(get_db), utilizador: Utilizador = Depends(utilizador_atual)):
    if config.MODO_DEMO:
        raise HTTPException(status_code=403, detail="A configuração não pode ser alterada na demonstração.")
    if utilizador.papel != PAPEL_ADMIN:
        raise HTTPException(status_code=403, detail="Só os administradores podem fechar períodos ou mudar o regime.")
    if conf.nif and not validacao.nif_valido(conf.nif.strip()):
        raise HTTPException(status_code=422, detail="O NIF da empresa não é válido.")
    if conf.codigo_postal and not saft.PADRAO_CODIGO_POSTAL.match(conf.codigo_postal.strip()):
        raise HTTPException(status_code=422, detail="O código postal tem de ter o formato 0000-000.")
    empresa = _empresa(db, utilizador)
    empresa.regime_iva = conf.regime_iva
    empresa.fechado_ate = conf.fechado_ate
    for campo in ("nif", "morada", "codigo_postal", "localidade"):
        valor = getattr(conf, campo)
        if valor is not None:
            setattr(empresa, campo, valor.strip() or None)
    db.commit()
    return _dados_empresa(empresa)


# ---------- SAF-T (PT) da contabilidade ----------

def _periodo_saft(ano: int | None, de: date | None, ate: date | None) -> tuple[date, date]:
    """Com `ano`: o exercício inteiro (o ano em curso vai só até hoje). Sem `ano`: de/ate."""
    if ano is None:
        return _periodo(de, ate)
    fim = date(ano, 12, 31)
    if ano == date.today().year:
        fim = date.today()
    return date(ano, 1, 1), fim


@router.get("/saft/verificar")
def verificar_saft(
    ano: int | None = Query(None, ge=2000, le=9999),
    de: date | None = None,
    ate: date | None = None,
    db: Session = Depends(get_db),
    utilizador: Utilizador = Depends(utilizador_atual),
):
    """O que falta para gerar o SAF-T do período (lista vazia: pronto a exportar)."""
    empresa = _empresa(db, utilizador)
    de, ate = _periodo_saft(ano, de, ate)
    lancamentos_periodo = db.scalar(select(func.count(Lancamento.id)).where(
        Lancamento.empresa_id == empresa.id, Lancamento.data >= de, Lancamento.data <= ate
    ))
    return {"de": de, "ate": ate, "problemas": saft.verificar(db, empresa, de, ate),
            "lancamentos": lancamentos_periodo, "empresa": _dados_empresa(empresa)}


@router.get("/saft")
def exportar_saft(
    ano: int | None = Query(None, ge=2000, le=9999),
    de: date | None = None,
    ate: date | None = None,
    db: Session = Depends(get_db),
    utilizador: Utilizador = Depends(utilizador_atual),
):
    """Ficheiro SAF-T (PT) 1.04_01 da contabilidade (exercício inteiro com `ano`, ou de/ate no mesmo ano)."""
    empresa = _empresa(db, utilizador)
    de, ate = _periodo_saft(ano, de, ate)
    try:
        conteudo = saft.gerar(db, empresa, de, ate)
    except saft.ErroSAFT as erro:
        raise HTTPException(status_code=409, detail=" ".join(erro.problemas))
    return Response(
        content=conteudo,
        media_type="application/xml; charset=windows-1252",
        headers={"Content-Disposition": f'attachment; filename="{saft.nome_ficheiro(empresa, de, ate)}"'},
    )


# ---------- Vendas (faturas de clientes) ----------

def _venda(db: Session, utilizador: Utilizador, venda_id: int) -> FaturaVenda:
    venda = db.scalar(select(FaturaVenda).where(
        FaturaVenda.id == venda_id, FaturaVenda.empresa_id == utilizador.empresa_id
    ))
    if venda is None:
        raise HTTPException(status_code=404, detail="Venda não encontrada.")
    return venda


@router.get("/vendas")
def listar_vendas(
    estado: Literal["por_receber", "recebidas", "anuladas", "por_contabilizar", "todas"] = "todas",
    pesquisa: str | None = Query(None, max_length=100),
    de: date | None = None,
    ate: date | None = None,
    db: Session = Depends(get_db),
    utilizador: Utilizador = Depends(utilizador_atual),
):
    """Vendas do período (mais recentes primeiro) e os totais para os indicadores."""
    empresa = _empresa(db, utilizador)
    de, ate = _periodo(de, ate)
    hoje = date.today()
    consulta = (select(FaturaVenda)
                .where(FaturaVenda.empresa_id == empresa.id, FaturaVenda.data >= de, FaturaVenda.data <= ate)
                .order_by(FaturaVenda.data.desc(), FaturaVenda.id.desc()))
    if pesquisa:
        termo = f"%{pesquisa.strip()}%"
        consulta = consulta.where(FaturaVenda.numero.ilike(termo) | FaturaVenda.cliente_nome.ilike(termo)
                                  | FaturaVenda.cliente_nif.ilike(termo))
    todas = list(db.scalars(consulta))
    sem_lancamento = {v.id for v in vendas.por_contabilizar(db, empresa.id)}
    abertas = vendas.por_receber(db, empresa.id)
    filtros = {
        "por_receber": lambda v: v.id in {a.id for a in abertas},
        "recebidas": lambda v: v.recebida,
        "anuladas": lambda v: v.estado != VENDA_NORMAL,
        "por_contabilizar": lambda v: v.id in sem_lancamento,
        "todas": lambda v: True,
    }
    validas = [v for v in todas if v.estado == VENDA_NORMAL]
    sinal = lambda v: -1 if v.tipo == "NC" else 1  # noqa: E731
    vencidas = [v for v in abertas if v.data_vencimento and v.data_vencimento < hoje]
    return {
        "vendas": [{**vendas.resumo_venda(v, hoje), "contabilizada": v.id not in sem_lancamento}
                   for v in todas if filtros[estado](v)][:1000],
        "totais": {
            "vendas_sem_iva": round(sum(sinal(v) * v.valor_sem_iva for v in validas), 2),
            "iva_liquidado": round(sum(sinal(v) * v.total_iva for v in validas), 2),
            "documentos": len(validas),
            "por_receber": round(sum(v.total - v.valor_recebido for v in abertas), 2),
            "por_receber_quantidade": len(abertas),
            "vencidas": round(sum(v.total - v.valor_recebido for v in vencidas), 2),
            "vencidas_quantidade": len(vencidas),
            "por_contabilizar": len(sem_lancamento),
        },
    }


@router.post("/vendas/importar-saft", status_code=201)
def importar_saft_faturacao(
    ficheiro: UploadFile = File(...),
    db: Session = Depends(get_db),
    utilizador: Utilizador = Depends(utilizador_atual),
):
    """Importa as faturas, notas de crédito e recibos do SAF-T de faturação do programa certificado."""
    limites.verificar(limites.envios, f"utilizador-{utilizador.id}")
    conteudo = ficheiro.file.read()
    if len(conteudo) > config.TAMANHO_MAXIMO * 5:
        raise HTTPException(status_code=413, detail="O ficheiro tem mais de 50 MB.")
    empresa = _empresa(db, utilizador)
    try:
        r = vendas.importar_saft(db, empresa, conteudo, utilizador.id)
    except (vendas.ErroVenda, lancamentos.ErroContabilistico) as erro:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(erro))
    db.commit()
    return {
        "importadas": r.importadas, "duplicadas": r.duplicadas, "anuladas": r.anuladas, "ignoradas": r.ignoradas,
        "recebimentos": r.recebimentos, "nao_contabilizadas": r.nao_contabilizadas, "avisos": r.avisos[:50],
        "de": r.periodo[0], "ate": r.periodo[1],
    }


class LinhaVendaEntrada(BaseModel):
    taxa: float = Field(ge=0, le=100)
    base: float = Field(gt=0)
    conta: Literal["711", "712", "721"] = "711"
    isencao: str | None = Field(None, max_length=10)


class VendaEntrada(BaseModel):
    tipo: Literal["FT", "FS", "FR", "NC", "ND"] = "FT"
    numero: str = Field(min_length=1, max_length=60)
    data: date
    data_vencimento: date | None = None
    cliente_nif: str | None = Field(None, max_length=20)
    cliente_nome: str | None = Field(None, max_length=200)
    atcud: str | None = Field(None, max_length=100)
    meio_pagamento: Literal["NU", "TB", "CC", "CD", "MB", "CH", "OU"] | None = None
    linhas: list[LinhaVendaEntrada] = Field(min_length=1, max_length=50)


@router.post("/vendas", status_code=201)
def registar_venda(entrada: VendaEntrada, db: Session = Depends(get_db),
                   utilizador: Utilizador = Depends(utilizador_atual)):
    """Regista uma venda emitida noutro programa (ou no Portal das Finanças) e contabiliza-a."""
    empresa = _empresa(db, utilizador)
    try:
        venda = vendas.registar(
            db, empresa, numero=entrada.numero, tipo=entrada.tipo, data=entrada.data,
            cliente_nif=entrada.cliente_nif, cliente_nome=entrada.cliente_nome, atcud=entrada.atcud,
            data_vencimento=entrada.data_vencimento, meio_pagamento=entrada.meio_pagamento,
            linhas=[vendas.LinhaVenda(ln.taxa, ln.base, ln.conta, ln.isencao) for ln in entrada.linhas],
            utilizador_id=utilizador.id,
        )
    except vendas.ErroVenda as erro:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(erro))
    except lancamentos.ErroContabilistico as erro:
        db.rollback()
        raise _erro(erro)
    db.commit()
    return vendas.resumo_venda(venda)


@router.get("/vendas/clientes")
def contas_correntes_clientes(db: Session = Depends(get_db), utilizador: Utilizador = Depends(utilizador_atual)):
    return relatorios.contas_correntes_clientes(db, utilizador.empresa_id, date.today())


@router.get("/vendas/{venda_id}")
def obter_venda(venda_id: int, db: Session = Depends(get_db), utilizador: Utilizador = Depends(utilizador_atual)):
    venda = _venda(db, utilizador, venda_id)
    feitos = db.scalars(select(Lancamento).where(
        Lancamento.empresa_id == utilizador.empresa_id, Lancamento.venda_id == venda.id
    ).order_by(Lancamento.data, Lancamento.id)).all()
    return {**vendas.resumo_venda(venda), "lancamentos": [lancamento_dict(la) for la in feitos]}


@router.post("/vendas/{venda_id}/contabilizar", status_code=201)
def contabilizar_venda(venda_id: int, db: Session = Depends(get_db),
                       utilizador: Utilizador = Depends(utilizador_atual)):
    empresa = _empresa(db, utilizador)
    venda = _venda(db, utilizador, venda_id)
    try:
        lancamento = vendas.contabilizar(db, empresa, venda, utilizador.id)
    except lancamentos.ErroContabilistico as erro:
        db.rollback()
        raise _erro(erro)
    db.commit()
    return lancamento_dict(lancamento)


class Recebimento(BaseModel):
    data: date | None = None
    conta: str = Field("121", pattern=r"^1[123]\d+$")
    valor: float | None = Field(None, gt=0)


@router.post("/vendas/{venda_id}/receber", status_code=201)
def receber_venda(venda_id: int, dados: Recebimento, db: Session = Depends(get_db),
                  utilizador: Utilizador = Depends(utilizador_atual)):
    """Regista o recebimento (total, ou parcial com `valor`)."""
    empresa = _empresa(db, utilizador)
    venda = _venda(db, utilizador, venda_id)
    try:
        lancamento = vendas.receber(db, empresa, venda, dados.data or date.today(), dados.conta, dados.valor,
                                    utilizador.id)
    except lancamentos.ErroContabilistico as erro:
        db.rollback()
        raise _erro(erro)
    db.commit()
    return lancamento_dict(lancamento)


# ---------- Inventários ----------

def _inventario(db: Session, utilizador: Utilizador, inventario_id: int) -> Inventario:
    inv = inventarios.obter(db, utilizador.empresa_id, inventario_id)
    if inv is None:
        raise HTTPException(status_code=404, detail="Inventário não encontrado.")
    return inv


class LinhaInventarioEntrada(BaseModel):
    codigo: str = Field("", max_length=60)
    descricao: str = Field(min_length=1, max_length=200)
    categoria: Literal["M", "P"] = "M"
    unidade: str = Field("UN", max_length=10)
    quantidade: float = Field(ge=0)
    custo_unitario: float = Field(ge=0)


class InventarioEntrada(BaseModel):
    data: date
    descricao: str = Field("Inventário", max_length=200)
    linhas: list[LinhaInventarioEntrada] = Field(min_length=1, max_length=20000)


def _linhas_inventario(linhas: list[LinhaInventarioEntrada]) -> list:
    return [inventarios.LinhaNovaInventario(ln.codigo, ln.descricao, ln.quantidade, ln.custo_unitario, ln.categoria,
                                            ln.unidade) for ln in linhas]


@router.get("/inventarios")
def listar_inventarios(db: Session = Depends(get_db), utilizador: Utilizador = Depends(utilizador_atual)):
    lista = db.scalars(select(Inventario).options(selectinload(Inventario.linhas))
                       .where(Inventario.empresa_id == utilizador.empresa_id).order_by(Inventario.data.desc())).all()
    saldos = relatorios.saldos_por_conta(db, utilizador.empresa_id)
    return {
        "inventarios": [inventarios.resumo(db, i) for i in lista],
        "compras_por_apurar": round(sum(v for c, v in saldos.items() if c.startswith("31")), 2),
        "saldo_mercadorias": round(saldos.get("321", 0.0), 2),
        "saldo_materias": round(saldos.get("331", 0.0), 2),
    }


@router.post("/inventarios", status_code=201)
def criar_inventario(entrada: InventarioEntrada, db: Session = Depends(get_db),
                     utilizador: Utilizador = Depends(utilizador_atual)):
    empresa = _empresa(db, utilizador)
    try:
        inv = inventarios.criar(db, empresa, entrada.data, entrada.descricao, _linhas_inventario(entrada.linhas),
                                utilizador.id)
    except inventarios.ErroInventario as erro:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(erro))
    db.commit()
    return inventarios.resumo(db, inv)


@router.post("/inventarios/importar", status_code=201)
def importar_inventario(
    ficheiro: UploadFile = File(...),
    data: date = Form(...),
    descricao: str = Form("Inventário", max_length=200),
    categoria: Literal["M", "P"] = Form("M"),
    db: Session = Depends(get_db),
    utilizador: Utilizador = Depends(utilizador_atual),
):
    """Cria a contagem a partir de um CSV ou Excel (código, descrição, quantidade, custo unitário ou valor)."""
    conteudo = ficheiro.file.read()
    if len(conteudo) > config.TAMANHO_MAXIMO:
        raise HTTPException(status_code=413, detail="O ficheiro tem mais de 10 MB.")
    empresa = _empresa(db, utilizador)
    try:
        linhas = inventarios.ler_ficheiro(conteudo, ficheiro.filename or "inventario.csv", categoria)
        inv = inventarios.criar(db, empresa, data, descricao, linhas, utilizador.id)
    except inventarios.ErroInventario as erro:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(erro))
    db.commit()
    return inventarios.resumo(db, inv)


@router.get("/inventarios/{inventario_id}")
def obter_inventario(inventario_id: int, db: Session = Depends(get_db),
                     utilizador: Utilizador = Depends(utilizador_atual)):
    inv = _inventario(db, utilizador, inventario_id)
    return {
        **inventarios.resumo(db, inv),
        "linhas": [{"codigo": ln.codigo, "descricao": ln.descricao, "categoria": ln.categoria, "unidade": ln.unidade,
                    "quantidade": ln.quantidade, "custo_unitario": ln.custo_unitario, "valor": ln.valor}
                   for ln in inv.linhas],
        "apuramento": None if inv.inicial else inventarios.apuramento(db, utilizador.empresa_id, inv),
    }


@router.put("/inventarios/{inventario_id}")
def alterar_inventario(inventario_id: int, entrada: InventarioEntrada, db: Session = Depends(get_db),
                       utilizador: Utilizador = Depends(utilizador_atual)):
    inv = _inventario(db, utilizador, inventario_id)
    try:
        inventarios.substituir_linhas(db, inv, _linhas_inventario(entrada.linhas))
    except inventarios.ErroInventario as erro:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(erro))
    inv.descricao = entrada.descricao.strip() or inv.descricao
    db.commit()
    return inventarios.resumo(db, inv)


@router.delete("/inventarios/{inventario_id}", status_code=204)
def apagar_inventario(inventario_id: int, db: Session = Depends(get_db),
                      utilizador: Utilizador = Depends(utilizador_atual)):
    inv = _inventario(db, utilizador, inventario_id)
    if inventarios.lancamento_ativo(db, inv):
        raise HTTPException(status_code=409, detail="O inventário já foi lançado: estorne o lançamento primeiro.")
    db.delete(inv)
    db.commit()


def _lancar_inventario(db: Session, utilizador: Utilizador, inventario_id: int, acao) -> dict:
    empresa = _empresa(db, utilizador)
    inv = _inventario(db, utilizador, inventario_id)
    try:
        lancamento = acao(empresa, inv)
    except (inventarios.ErroInventario, lancamentos.ErroContabilistico) as erro:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(erro))
    db.commit()
    return lancamento_dict(lancamento)


@router.post("/inventarios/{inventario_id}/apurar", status_code=201)
def apurar_cmvmc(inventario_id: int, db: Session = Depends(get_db),
                 utilizador: Utilizador = Depends(utilizador_atual)):
    """Lança o apuramento do CMVMC na data da contagem."""
    return _lancar_inventario(db, utilizador, inventario_id,
                              lambda e, inv: inventarios.apurar(db, e, inv, utilizador.id))


@router.post("/inventarios/{inventario_id}/inicial", status_code=201)
def inventario_inicial(inventario_id: int, db: Session = Depends(get_db),
                       utilizador: Utilizador = Depends(utilizador_atual)):
    """Usa a contagem como inventário de abertura (contrapartida em resultados transitados)."""
    return _lancar_inventario(db, utilizador, inventario_id,
                              lambda e, inv: inventarios.registar_inicial(db, e, inv, utilizador.id))


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


# ---------- Bancos: extratos e reconciliação ----------

def _movimento_dict(mov: MovimentoBancario, sugs: list[dict] | None = None) -> dict:
    ligado = None
    if mov.linha_id and mov.linha:
        la = mov.linha.lancamento
        ligado = {"lancamento_id": la.id, "referencia": f"{la.diario} {la.ano}/{la.numero}", "descricao": la.descricao}
    return {
        "id": mov.id, "conta": mov.conta_codigo, "data": mov.data, "descricao": mov.descricao,
        "montante": mov.montante, "saldo": mov.saldo, "estado": mov.estado, "lancamento": ligado,
        "sugestoes": sugs,
    }


def _movimento(db: Session, utilizador: Utilizador, movimento_id: int) -> MovimentoBancario:
    mov = db.scalar(select(MovimentoBancario).where(
        MovimentoBancario.id == movimento_id, MovimentoBancario.empresa_id == utilizador.empresa_id
    ))
    if mov is None:
        raise HTTPException(status_code=404, detail="Movimento não encontrado.")
    return mov


@router.get("/banco/contas")
def listar_contas_bancarias(db: Session = Depends(get_db), utilizador: Utilizador = Depends(utilizador_atual)):
    """Contas de bancos e caixa, com o saldo contabilístico e os movimentos por reconciliar."""
    empresa = _empresa(db, utilizador)
    saldos = relatorios.saldos_por_conta(db, empresa.id)
    pendentes = dict(db.execute(
        select(MovimentoBancario.conta_codigo, func.count(MovimentoBancario.id))
        .where(MovimentoBancario.empresa_id == empresa.id, MovimentoBancario.estado == MOVIMENTO_PENDENTE)
        .group_by(MovimentoBancario.conta_codigo)
    ).all())
    return [{"codigo": c.codigo, "nome": c.nome, "saldo": saldos.get(c.codigo, 0.0),
             "por_reconciliar": pendentes.get(c.codigo, 0)} for c in banco.contas_bancarias(db, empresa.id)]


@router.post("/banco/importar", status_code=201)
def importar_extrato(
    ficheiro: UploadFile = File(...),
    conta: str = Form("121", pattern=r"^1[123]\d+$"),
    saldo_final: str | None = Form(None),
    db: Session = Depends(get_db),
    utilizador: Utilizador = Depends(utilizador_atual),
):
    """Importa um extrato (CSV, Excel ou OFX). Movimentos já importados são ignorados."""
    limites.verificar(limites.envios, f"utilizador-{utilizador.id}")
    conteudo = ficheiro.file.read()
    if len(conteudo) > config.TAMANHO_MAXIMO:
        raise HTTPException(status_code=413, detail="O ficheiro tem mais de 10 MB.")
    saldo = None
    if saldo_final and saldo_final.strip():
        saldo = extratos.ler_numero(saldo_final)
        if saldo is None:
            raise HTTPException(status_code=422, detail="O saldo final não é um número válido.")
    empresa = _empresa(db, utilizador)
    try:
        resultado = banco.importar(db, empresa, conta, conteudo, ficheiro.filename or "extrato", saldo, utilizador.id)
    except (extratos.ErroExtrato, lancamentos.ErroContabilistico, ia.ErroIA) as erro:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(erro))
    db.commit()
    e = resultado.extrato
    return {
        "extrato_id": e.id, "formato": e.formato, "importados": resultado.importados,
        "duplicados": resultado.duplicados, "data_inicio": e.data_inicio, "data_fim": e.data_fim,
        "saldo_final": e.saldo_final, "mapeado_por_ia": resultado.lido.mapeado_por_ia,
    }


@router.get("/banco/movimentos")
def listar_movimentos(
    conta: str = Query("121", pattern=r"^1[123]\d+$"),
    estado: Literal["pendente", "reconciliado", "ignorado"] | None = None,
    db: Session = Depends(get_db),
    utilizador: Utilizador = Depends(utilizador_atual),
):
    """Movimentos do banco (mais recentes primeiro); os pendentes trazem as sugestões de reconciliação."""
    empresa = _empresa(db, utilizador)
    consulta = (
        select(MovimentoBancario)
        .options(joinedload(MovimentoBancario.linha).joinedload(LinhaLancamento.lancamento))
        .where(MovimentoBancario.empresa_id == empresa.id, MovimentoBancario.conta_codigo == conta)
        .order_by(MovimentoBancario.data.desc(), MovimentoBancario.id.desc())
        .limit(1000)
    )
    if estado:
        consulta = consulta.where(MovimentoBancario.estado == estado)
    movimentos = list(db.scalars(consulta))
    # Os candidatos carregam-se uma só vez para todos os movimentos pendentes
    linhas = banco.linhas_por_reconciliar(db, empresa.id, conta)
    faturas = banco.faturas_por_pagar(db, empresa.id)
    abertas = vendas.por_receber(db, empresa.id)
    resultado = [
        _movimento_dict(m, banco.sugestoes(db, empresa.id, m, linhas, faturas, abertas)
                        if m.estado == MOVIMENTO_PENDENTE else None)
        for m in movimentos
    ]
    seguras = sum(1 for r in resultado if r["sugestoes"] and banco.sugestao_segura(r["sugestoes"]))
    return {"movimentos": resultado, "sugestoes_seguras": seguras}


class Associacao(BaseModel):
    linha_id: int


class PagamentoFatura(BaseModel):
    fatura_id: str = Field(min_length=1, max_length=12)


class RecebimentoVenda(BaseModel):
    venda_id: int


class LancamentoBanco(BaseModel):
    conta: str = Field(min_length=3, max_length=20, pattern=r"^\d+$")
    descricao: str | None = Field(None, max_length=300)


def _acao_banco(db: Session, utilizador: Utilizador, movimento_id: int, acao) -> dict:
    empresa = _empresa(db, utilizador)
    mov = _movimento(db, utilizador, movimento_id)
    try:
        acao(empresa, mov)
    except lancamentos.ErroContabilistico as erro:
        db.rollback()
        raise _erro(erro)
    db.commit()
    db.refresh(mov)
    return _movimento_dict(mov)


@router.post("/banco/movimentos/{movimento_id}/associar")
def associar_movimento(movimento_id: int, dados: Associacao, db: Session = Depends(get_db),
                       utilizador: Utilizador = Depends(utilizador_atual)):
    return _acao_banco(db, utilizador, movimento_id,
                       lambda e, m: banco.associar(db, e.id, m, dados.linha_id, utilizador.id))


@router.post("/banco/movimentos/{movimento_id}/pagar-fatura")
def pagar_fatura_movimento(movimento_id: int, dados: PagamentoFatura, db: Session = Depends(get_db),
                           utilizador: Utilizador = Depends(utilizador_atual)):
    return _acao_banco(db, utilizador, movimento_id,
                       lambda e, m: banco.pagar_fatura(db, e, m, dados.fatura_id, utilizador.id))


@router.post("/banco/movimentos/{movimento_id}/receber-venda")
def receber_venda_movimento(movimento_id: int, dados: RecebimentoVenda, db: Session = Depends(get_db),
                            utilizador: Utilizador = Depends(utilizador_atual)):
    return _acao_banco(db, utilizador, movimento_id,
                       lambda e, m: banco.receber_venda(db, e, m, dados.venda_id, utilizador.id))


@router.post("/banco/movimentos/{movimento_id}/lancar")
def lancar_movimento(movimento_id: int, dados: LancamentoBanco, db: Session = Depends(get_db),
                     utilizador: Utilizador = Depends(utilizador_atual)):
    return _acao_banco(db, utilizador, movimento_id,
                       lambda e, m: banco.lancar(db, e, m, dados.conta, dados.descricao, utilizador.id))


@router.post("/banco/movimentos/{movimento_id}/ignorar")
def ignorar_movimento(movimento_id: int, db: Session = Depends(get_db),
                      utilizador: Utilizador = Depends(utilizador_atual)):
    return _acao_banco(db, utilizador, movimento_id, lambda e, m: banco.ignorar(m, utilizador.id))


@router.post("/banco/movimentos/{movimento_id}/desfazer")
def desfazer_movimento(movimento_id: int, db: Session = Depends(get_db),
                       utilizador: Utilizador = Depends(utilizador_atual)):
    """Volta a pôr o movimento por reconciliar (o lançamento, se foi criado, fica e pode ser estornado)."""
    return _acao_banco(db, utilizador, movimento_id, lambda e, m: banco.desfazer(m))


class ContaBanco(BaseModel):
    conta: str = Field("121", pattern=r"^1[123]\d+$")


@router.post("/banco/reconciliar-automaticamente")
def reconciliar_automaticamente(dados: ContaBanco, db: Session = Depends(get_db),
                                utilizador: Utilizador = Depends(utilizador_atual)):
    empresa = _empresa(db, utilizador)
    try:
        contagem = banco.reconciliar_automaticamente(db, empresa, dados.conta, utilizador.id)
    except lancamentos.ErroContabilistico as erro:
        db.rollback()
        raise _erro(erro)
    db.commit()
    return {**contagem, "total": sum(contagem.values())}


@router.get("/banco/reconciliacao")
def mapa_reconciliacao(conta: str = Query("121", pattern=r"^1[123]\d+$"), ate: date | None = None,
                       db: Session = Depends(get_db), utilizador: Utilizador = Depends(utilizador_atual)):
    return banco.mapa(db, utilizador.empresa_id, conta, ate)


@router.get("/banco/extratos")
def listar_extratos(conta: str = Query("121", pattern=r"^1[123]\d+$"), db: Session = Depends(get_db),
                    utilizador: Utilizador = Depends(utilizador_atual)):
    extratos_conta = db.execute(
        select(ExtratoBancario, func.count(MovimentoBancario.id))
        .outerjoin(MovimentoBancario, MovimentoBancario.extrato_id == ExtratoBancario.id)
        .where(ExtratoBancario.empresa_id == utilizador.empresa_id, ExtratoBancario.conta_codigo == conta)
        .group_by(ExtratoBancario.id)
        .order_by(ExtratoBancario.importado_em.desc())
    ).all()
    return [{"id": e.id, "nome_ficheiro": e.nome_ficheiro, "formato": e.formato, "data_inicio": e.data_inicio,
             "data_fim": e.data_fim, "saldo_final": e.saldo_final, "importado_em": e.importado_em,
             "movimentos": n} for e, n in extratos_conta]


@router.delete("/banco/extratos/{extrato_id}", status_code=204)
def apagar_extrato(extrato_id: int, db: Session = Depends(get_db),
                   utilizador: Utilizador = Depends(utilizador_atual)):
    """Apaga uma importação feita por engano, se nenhum dos seus movimentos estiver reconciliado."""
    extrato = db.scalar(select(ExtratoBancario).where(
        ExtratoBancario.id == extrato_id, ExtratoBancario.empresa_id == utilizador.empresa_id
    ))
    if extrato is None:
        raise HTTPException(status_code=404, detail="Extrato não encontrado.")
    tratados = db.scalar(select(func.count(MovimentoBancario.id)).where(
        MovimentoBancario.extrato_id == extrato.id, MovimentoBancario.estado != MOVIMENTO_PENDENTE
    ))
    if tratados:
        raise HTTPException(status_code=409,
                            detail="Há movimentos deste extrato já reconciliados: desfaça-os primeiro.")
    db.execute(delete(MovimentoBancario).where(MovimentoBancario.extrato_id == extrato.id))
    db.delete(extrato)
    db.commit()
