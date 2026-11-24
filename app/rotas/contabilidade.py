"""
Endpoints da contabilidade (SNC).

Tudo é por empresa: cada rota recebe o utilizador e usa utilizador.empresa_id.
Os erros contabilísticos (lançamento desequilibrado, período fechado...) chegam ao
utilizador como 409 com a explicação em português.
"""

from datetime import date, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, joinedload, selectinload

from app import config
from app.db import get_db
from app.dependencias import com_contabilidade, utilizador_atual
from app.modelos.tabelas import (
    MOVIMENTO_PENDENTE,
    PAPEL_ADMIN,
    VENDA_NORMAL,
    AcessoEmpresa,
    AtivoFixo,
    Conta,
    Diferimento,
    Empresa,
    ExtratoBancario,
    Fatura,
    FaturaVenda,
    Funcionario,
    Inventario,
    Lancamento,
    LinhaLancamento,
    MovimentoBancario,
    ProcessamentoSalarial,
    Utilizador,
)
from app.servicos import armazenamento, exportacao, limites, repositorio, validacao
from app.servicos.contabilidade import (
    abertura,
    assistente,
    ativos,
    banco,
    centros,
    classificacao,
    declaracao_iva,
    declaracoes,
    extratos,
    fecho,
    ia,
    importacao_saft,
    inventarios,
    iva,
    lancamentos,
    obrigacoes,
    open_banking,
    periodizacao,
    plano_snc,
    relatorios,
    saft,
    salarios,
    vendas,
    verificacoes,
)

router = APIRouter(prefix="/api/contabilidade", tags=["contabilidade"], dependencies=[Depends(com_contabilidade)])


# ---------- Utilitários ----------

def _empresa(db: Session, utilizador: Utilizador) -> Empresa:
    """A empresa de quem faz o pedido, com o plano de contas criado (na primeira visita)."""
    empresa = db.get(Empresa, utilizador.empresa_id)
    try:
        plano_snc.garantir_plano(db, empresa.id)
        db.commit()
    except (IntegrityError, OperationalError):
        # Dois pedidos na primeira visita (ex.: resumo e primeiros passos em paralelo) criaram o
        # plano ao mesmo tempo. Desfaz-se esta tentativa e repete-se: o plano gravado pelo outro
        # pedido já está lá, por isso a segunda tentativa não insere nada.
        db.rollback()
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
            "id": ln.id, "conta": ln.conta.codigo, "nome": ln.conta.nome, "debito": ln.debito, "credito": ln.credito,
            "descricao": ln.descricao, "tipo_iva": ln.tipo_iva, "taxa_iva": ln.taxa_iva,
            "centro_id": ln.centro_custo_id, "analitica": centros.analitica(ln.conta.codigo),
        } for ln in la.linhas],
    }


def _centro_sugerido(db: Session, empresa_id: int, fatura: Fatura) -> dict | None:
    centro = centros.memorizado(db, empresa_id, fatura.nif_fornecedor)
    return {"id": centro.id, "codigo": centro.codigo, "nome": centro.nome} if centro else None


def _aplicar_centro(db: Session, empresa_id: int, fatura: Fatura, lancamento: Lancamento, centro_id: int | None,
                    escolhido: bool) -> None:
    """
    Centro de custo das linhas de gasto: o escolhido (que fica memorizado para o fornecedor) ou, sem
    escolha, o memorizado da última vez.
    """
    if not escolhido:
        centro = centros.memorizado(db, empresa_id, fatura.nif_fornecedor)
        centro_id = centro.id if centro else None
    elif centro_id is not None:
        centros.validar_para_atribuir(db, empresa_id, centro_id)
    if centro_id is not None:
        centros.atribuir_ao_lancamento(lancamento, centro_id)
    if escolhido:
        centros.memorizar(db, empresa_id, fatura.nif_fornecedor, centro_id)


def _proposta(db: Session, empresa: Empresa, fatura: Fatura, usar_ia: bool = False) -> dict:
    """Classificação sugerida e pré-visualização das linhas, sem gravar nada."""
    c = classificacao.classificar(db, empresa.id, fatura, usar_ia=usar_ia)
    regime = iva.regime_sugerido(fatura.nif_fornecedor, fatura.total_iva)
    linhas = lancamentos.linhas_fatura(db, empresa.id, fatura, c.conta_codigo, c.perfil_iva, regime)
    # Seguros, rendas ou assinaturas que cobrem vários meses: proposta de diferimento
    texto = classificacao.texto_fatura(fatura) if c.conta_codigo.startswith("6") else ""
    diferimento = periodizacao.propor(fatura, c.conta_codigo, periodizacao.valor_na_conta(linhas, c.conta_codigo),
                                      texto, c.periodo)
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
            "regime_iva": regime, "regime_nome": iva.REGIMES[regime],
        },
        "diferimento": periodizacao.proposta_dict(diferimento),
        "centro": _centro_sugerido(db, empresa.id, fatura),
        "linhas": [{
            "conta": ln.conta_codigo, "nome": contas[ln.conta_codigo].nome if ln.conta_codigo in contas else "",
            "debito": round(ln.debito, 2), "credito": round(ln.credito, 2), "descricao": ln.descricao,
        } for ln in linhas],
    }


# ---------- Resumo ----------

# ---------- Centros de custo (contabilidade analítica) ----------

class NovoCentro(BaseModel):
    codigo: str = Field(min_length=1, max_length=20)
    nome: str = Field(min_length=1, max_length=120)


class AlteracaoCentro(BaseModel):
    nome: str | None = Field(None, min_length=1, max_length=120)
    ativo: bool | None = None


class AtribuicaoCentro(BaseModel):
    centro_id: int | None = None


def _centro_dict(c) -> dict:
    return {"id": c.id, "codigo": c.codigo, "nome": c.nome, "ativo": c.ativo}


@router.get("/centros")
def listar_centros(db: Session = Depends(get_db), utilizador: Utilizador = Depends(utilizador_atual)):
    return [_centro_dict(c) for c in centros.listar(db, utilizador.empresa_id)]


@router.post("/centros", status_code=201)
def criar_centro(dados: NovoCentro, db: Session = Depends(get_db), utilizador: Utilizador = Depends(utilizador_atual)):
    try:
        centro = centros.criar(db, utilizador.empresa_id, dados.codigo, dados.nome)
    except centros.ErroCentro as erro:
        raise HTTPException(status_code=400, detail=str(erro))
    db.commit()
    return _centro_dict(centro)


@router.patch("/centros/{centro_id}")
def alterar_centro(centro_id: int, dados: AlteracaoCentro, db: Session = Depends(get_db),
                   utilizador: Utilizador = Depends(utilizador_atual)):
    centro = centros.obter(db, utilizador.empresa_id, centro_id)
    if centro is None:
        raise HTTPException(status_code=404, detail="Centro de custo não encontrado.")
    if dados.nome:
        centro.nome = dados.nome.strip()
    if dados.ativo is not None:
        centro.ativo = dados.ativo
    db.commit()
    return _centro_dict(centro)


@router.get("/centros/relatorio")
def relatorio_centros(de: date | None = None, ate: date | None = None, db: Session = Depends(get_db),
                      utilizador: Utilizador = Depends(utilizador_atual)):
    de, ate = _periodo(de, ate)
    return centros.relatorio(db, utilizador.empresa_id, de, ate)


@router.patch("/linhas/{linha_id}/centro")
def atribuir_centro(linha_id: int, dados: AtribuicaoCentro, db: Session = Depends(get_db),
                    utilizador: Utilizador = Depends(utilizador_atual)):
    """Atribui (ou retira) o centro de custo de uma linha já lançada: não altera a contabilidade geral."""
    try:
        linha = centros.atribuir(db, utilizador.empresa_id, linha_id, dados.centro_id)
    except centros.ErroCentro as erro:
        raise HTTPException(status_code=400, detail=str(erro))
    db.commit()
    return {"id": linha.id, "centro_id": linha.centro_custo_id}


@router.post("/importar-saft")
def importar_saft(
    ficheiro: UploadFile = File(...),
    simular: bool = Form(True),
    com_abertura: bool = Form(True),
    com_movimentos: bool = Form(True),
    db: Session = Depends(get_db),
    utilizador: Utilizador = Depends(utilizador_atual),
):
    """Migração: plano de contas, saldos de abertura e lançamentos de um SAF-T (PT) de outro programa."""
    if utilizador.papel != PAPEL_ADMIN:
        raise HTTPException(status_code=403, detail="Só os administradores podem importar a contabilidade.")
    empresa = _empresa(db, utilizador)
    try:
        resumo = importacao_saft.importar(
            db, empresa, ficheiro.file.read(importacao_saft.TAMANHO_MAXIMO + 1), simular=simular,
            com_abertura=com_abertura, com_movimentos=com_movimentos, utilizador_id=utilizador.id,
        )
    except importacao_saft.ErroImportacao as erro:
        raise HTTPException(status_code=400, detail=str(erro))
    if resumo["importado"]:
        db.commit()
    return resumo


@router.get("/primeiros-passos")
def primeiros_passos(db: Session = Depends(get_db), utilizador: Utilizador = Depends(utilizador_atual)):
    """Lista do que falta configurar numa empresa nova (o Início mostra-a até estar tudo feito)."""
    empresa = _empresa(db, utilizador)

    def existe(modelo, *condicoes) -> bool:
        consulta = select(func.count()).select_from(modelo).where(modelo.empresa_id == empresa.id, *condicoes)
        return db.scalar(consulta) > 0

    passos = [
        ("empresa", "Completar os dados da empresa", "NIF e morada da sede, usados no SAF-T e nos relatórios.",
         bool(empresa.nif and empresa.morada), "#obrigacoes"),
        ("abertura", "Lançar os saldos de abertura", "Importe o balancete do fim do ano anterior (Excel ou CSV).",
         existe(Lancamento, Lancamento.origem == "abertura"), "#plano"),
        ("fatura", "Carregar a primeira fatura", "Arraste os PDFs dos fornecedores: os dados são lidos sozinhos.",
         existe(Fatura), "/"),
        ("contabilizar", "Contabilizar uma fatura", "Confirme a conta e o IVA propostos para cada fornecedor.",
         existe(Lancamento, Lancamento.origem == "fatura"), "#por-contabilizar"),
        ("banco", "Importar um extrato bancário", "CSV, Excel ou OFX do seu banco, para reconciliar os movimentos.",
         existe(ExtratoBancario), "#bancos"),
        ("dois-passos", "Ligar a verificação em dois passos", "Proteja a conta com um código da app autenticadora.",
         bool(utilizador.totp_ativo), "/conta"),
    ]
    if config.MODO_DEMO:
        passos = [p for p in passos if p[0] != "dois-passos"]
    elif utilizador.papel == PAPEL_ADMIN:
        passos.insert(5, ("equipa", "Convidar a equipa", "O contabilista e os colegas que tratam das faturas.",
                          existe(AcessoEmpresa, AcessoEmpresa.utilizador_id != utilizador.id), "/equipa"))
    lista = [{"chave": c, "titulo": t, "texto": x, "feito": f, "destino": d} for c, t, x, f, d in passos]
    return {"empresa_id": empresa.id, "passos": lista, "feitos": sum(p["feito"] for p in lista), "total": len(lista)}


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
    ligado = periodizacao.de_fatura(db, empresa.id, fatura.id)
    resposta = {"contabilizada": compra is not None, "lancamentos": [lancamento_dict(la) for la in feitos],
                "proposta": None, "erro": None,
                "diferimento": periodizacao.diferimento_dict(db, ligado) if ligado else None}
    if compra is None:
        try:
            lancamentos.verificar_fatura_contabilizavel(fatura)
            resposta["proposta"] = _proposta(db, empresa, fatura, usar_ia=ia_sugestao)
        except (lancamentos.ErroContabilistico, ia.ErroIA) as erro:
            resposta["erro"] = str(erro)
    db.rollback()
    return resposta


RegimeIVA = Literal["nacional", "intracomunitario_bens", "intracomunitario_servicos", "autoliquidacao"]


class Diferir(BaseModel):
    inicio: date
    meses: int = Field(ge=2, le=120)


class Contabilizacao(BaseModel):
    conta_codigo: str | None = None
    perfil_iva: str | None = None
    regime_iva: RegimeIVA | None = None
    taxa_autoliquidacao: float = Field(23.0, gt=0, le=100)
    diferimento: Diferir | None = None  # confirmado pelo utilizador: reparte o gasto pelos meses
    centro_custo_id: int | None = None   # contabilidade analítica (opcional)


def _diferir_fatura(db: Session, empresa: Empresa, fatura: Fatura, compra: Lancamento, dados: Diferir,
                    utilizador_id: int):
    conta = next((ln.conta.codigo for ln in compra.linhas if ln.debito and ln.conta.codigo.startswith("6")), None)
    if conta is None:
        raise periodizacao.ErroPeriodizacao("Só se diferem faturas lançadas numa conta de gasto (classe 6).")
    return periodizacao.registar(
        db, empresa, tipo="gasto", descricao=f"{fatura.nome_fornecedor or 'Fornecedor'} - "
        f"{lancamentos.documento_fatura(fatura)}", conta=conta, valor=periodizacao.valor_na_conta(compra.linhas, conta),
        inicio=dados.inicio, meses=dados.meses, data=compra.data, utilizador_id=utilizador_id, fatura_id=fatura.id)


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
            regime_iva=escolha.regime_iva, taxa_autoliquidacao=escolha.taxa_autoliquidacao,
            utilizador_id=utilizador.id,
        )
        if escolha.diferimento:
            _diferir_fatura(db, empresa, fatura, lancamento, escolha.diferimento, utilizador.id)
        _aplicar_centro(db, empresa.id, fatura, lancamento, escolha.centro_custo_id,
                        "centro_custo_id" in escolha.model_fields_set)
    except (lancamentos.ErroContabilistico, periodizacao.ErroPeriodizacao, centros.ErroCentro) as erro:
        db.rollback()
        raise _erro(erro)
    db.commit()
    return lancamento_dict(lancamento)


@router.post("/faturas/{id_fatura}/diferir", status_code=201)
def diferir_fatura(id_fatura: str, dados: Diferir, db: Session = Depends(get_db),
                   utilizador: Utilizador = Depends(utilizador_atual)):
    """Diferimento de uma fatura já contabilizada (ex.: um seguro contabilizado em lote)."""
    empresa = _empresa(db, utilizador)
    fatura = _fatura(db, utilizador, id_fatura)
    compra = lancamentos.lancamento_ativo(db, empresa.id, fatura.id, "fatura")
    if compra is None:
        raise HTTPException(status_code=409, detail="Contabilize a fatura antes de a diferir.")
    try:
        d = _diferir_fatura(db, empresa, fatura, compra, dados, utilizador.id)
    except (lancamentos.ErroContabilistico, periodizacao.ErroPeriodizacao) as erro:
        db.rollback()
        raise _erro(erro)
    db.commit()
    return periodizacao.diferimento_dict(db, d)


class Lote(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=200)
    # Centro escolhido em cada cartão ({id da fatura: centro ou null}); sem entrada, usa-se o memorizado
    centros: dict[str, int | None] = Field(default_factory=dict)


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
            _aplicar_centro(db, empresa.id, fatura, lancamento, lote.centros.get(id_fatura),
                            id_fatura in lote.centros)
            db.commit()
            resultados.append({"id": id_fatura, "ok": True,
                               "referencia": f"{lancamento.diario} {lancamento.ano}/{lancamento.numero}"})
        except (HTTPException, lancamentos.ErroContabilistico, centros.ErroCentro) as erro:
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


def _periodo_iva(empresa: Empresa, de: date | None, ate: date | None) -> tuple[date, date]:
    if de is None and ate is None:
        de, ate, _ = obrigacoes.periodo_iva(date.today(), empresa.regime_iva)
        return de, ate
    return _periodo(de, ate)


@router.get("/iva/declaracao")
def resumo_declaracao_iva(de: date | None = None, ate: date | None = None, db: Session = Depends(get_db),
                          utilizador: Utilizador = Depends(utilizador_atual)):
    """Campos da declaração periódica do período (em euros) e se o ficheiro pode ser gerado."""
    empresa = _empresa(db, utilizador)
    de, ate = _periodo_iva(empresa, de, ate)
    try:
        d = declaracao_iva.calcular(db, empresa, de, ate)
    except declaracao_iva.ErroDeclaracaoIVA as erro:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(erro))
    db.commit()
    return declaracao_iva.resumo(empresa, d)


@router.get("/iva/declaracao.xml")
def ficheiro_declaracao_iva(de: date | None = None, ate: date | None = None, db: Session = Depends(get_db),
                            utilizador: Utilizador = Depends(utilizador_atual)):
    """Ficheiro XML da declaração periódica do IVA, para abrir e submeter no Portal das Finanças."""
    empresa = _empresa(db, utilizador)
    de, ate = _periodo_iva(empresa, de, ate)
    try:
        d = declaracao_iva.calcular(db, empresa, de, ate)
        conteudo = declaracao_iva.gerar_xml(empresa, d)
    except declaracao_iva.ErroDeclaracaoIVA as erro:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(erro))
    db.commit()
    return Response(conteudo, media_type="application/xml",
                    headers={"Content-Disposition":
                             f'attachment; filename="{declaracao_iva.nome_ficheiro(empresa, d)}"'})


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
    """Saldos e antiguidade por fornecedor, e as faturas contabilizadas por pagar (para registar o pagamento)."""
    hoje = date.today()
    dados = relatorios.contas_correntes(db, utilizador.empresa_id, ate or hoje)
    por_pagar = sorted(banco.faturas_por_pagar(db, utilizador.empresa_id),
                       key=lambda f: (f.data_vencimento or f.data_emissao or hoje, f.id))
    dados["faturas_por_pagar"] = [{
        "id": f.id, "fornecedor": f.nome_fornecedor, "nif": f.nif_fornecedor,
        "documento": lancamentos.documento_fatura(f), "data_emissao": f.data_emissao,
        "data_vencimento": f.data_vencimento, "total": f.total,
        "nota_credito": (f.tipo_documento or "").upper() in lancamentos.TIPOS_NOTA_CREDITO,
        "dias_atraso": (hoje - f.data_vencimento).days if f.data_vencimento and f.data_vencimento < hoje else 0,
    } for f in por_pagar]
    # Faturas por pagar que ainda não estão na contabilidade: o pagamento só se regista depois de contabilizar
    pendentes = verificacoes.faturas_por_contabilizar(db, utilizador.empresa_id)
    dados["por_pagar_sem_contabilizar"] = sum(1 for f in pendentes if not f.paga)
    return dados


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
        "localidade": empresa.localidade, "niss": empresa.niss, "servico_financas": empresa.servico_financas,
        "nif_contabilista": empresa.nif_contabilista, "nif_representante": empresa.nif_representante,
        "regiao_sede": empresa.regiao_sede,
    }


class Configuracao(BaseModel):
    regime_iva: Literal["mensal", "trimestral"]
    fechado_ate: date | None = None
    # Dados da sede (para o SAF-T). None: não mexer; texto vazio: apagar
    nif: str | None = Field(None, max_length=9)
    morada: str | None = Field(None, max_length=210)
    codigo_postal: str | None = Field(None, max_length=8)
    localidade: str | None = Field(None, max_length=50)
    niss: str | None = Field(None, max_length=11)
    # Declarações à AT (declaração periódica do IVA e DMR)
    servico_financas: str | None = Field(None, max_length=4)
    nif_contabilista: str | None = Field(None, max_length=9)
    nif_representante: str | None = Field(None, max_length=9)
    regiao_sede: Literal["continente", "acores", "madeira"] | None = None


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
    if conf.niss and not (conf.niss.strip().isdigit() and len(conf.niss.strip()) == 11):
        raise HTTPException(status_code=422, detail="O NISS da empresa tem 11 algarismos.")
    servico = (conf.servico_financas or "").strip()
    if servico and not (servico.isdigit() and len(servico) == 4):
        raise HTTPException(status_code=422, detail="O código do serviço de finanças tem 4 algarismos.")
    for campo, nome in (("nif_contabilista", "do contabilista certificado"),
                        ("nif_representante", "do representante legal")):
        valor = (getattr(conf, campo) or "").strip()
        if valor and not validacao.nif_valido(valor):
            raise HTTPException(status_code=422, detail=f"O NIF {nome} não é válido.")
    empresa = _empresa(db, utilizador)
    empresa.regime_iva = conf.regime_iva
    empresa.fechado_ate = conf.fechado_ate
    for campo in ("nif", "morada", "codigo_postal", "localidade", "niss", "servico_financas", "nif_contabilista",
                  "nif_representante"):
        valor = getattr(conf, campo)
        if valor is not None:
            setattr(empresa, campo, valor.strip() or None)
    if conf.regiao_sede:
        empresa.regiao_sede = conf.regiao_sede
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


@router.get("/inventarios/{inventario_id}/at")
def ficheiro_inventario_at(inventario_id: int, db: Session = Depends(get_db),
                           utilizador: Utilizador = Depends(utilizador_atual)):
    """Ficheiro CSV da comunicação do inventário à AT (Portaria n.º 2/2015)."""
    inv = _inventario(db, utilizador, inventario_id)
    empresa = db.get(Empresa, utilizador.empresa_id)
    return Response(content=inventarios.ficheiro_at(inv), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition":
                             f'attachment; filename="{inventarios.nome_ficheiro_at(empresa, inv)}"'})


@router.post("/inventarios/{inventario_id}/inicial", status_code=201)
def inventario_inicial(inventario_id: int, db: Session = Depends(get_db),
                       utilizador: Utilizador = Depends(utilizador_atual)):
    """Usa a contagem como inventário de abertura (contrapartida em resultados transitados)."""
    return _lancar_inventario(db, utilizador, inventario_id,
                              lambda e, inv: inventarios.registar_inicial(db, e, inv, utilizador.id))


# ---------- Ativos fixos e depreciações ----------

def _ativo(db: Session, utilizador: Utilizador, ativo_id: int) -> AtivoFixo:
    a = ativos.obter(db, utilizador.empresa_id, ativo_id)
    if a is None:
        raise HTTPException(status_code=404, detail="Ativo não encontrado.")
    return a


@router.get("/ativos")
def listar_ativos(ano: int | None = Query(None, ge=2000, le=9999), db: Session = Depends(get_db),
                  utilizador: Utilizador = Depends(utilizador_atual)):
    """Ativos com a depreciação do ano, depreciações por lançar até ao fim do mês passado e diferenças."""
    empresa = _empresa(db, utilizador)
    hoje = date.today()
    ano = ano or hoje.year
    lista = db.scalars(select(AtivoFixo).options(selectinload(AtivoFixo.depreciacoes))
                       .where(AtivoFixo.empresa_id == empresa.id).order_by(AtivoFixo.codigo)).all()
    resumos = [ativos.resumo(db, a, ano) for a in lista]
    fim_mes_passado = date(hoje.year, hoje.month, 1) - timedelta(days=1)
    quotas = ativos.calcular(db, empresa.id, fim_mes_passado)
    em_uso = [r for r in resumos if r["estado"] == "em_uso"]
    return {
        "ano": ano,
        "ativos": resumos,
        "totais": {
            "valor_aquisicao": round(sum(r["valor_aquisicao"] for r in em_uso), 2),
            "acumulada": round(sum(r["acumulada"] for r in em_uso), 2),
            "valor_liquido": round(sum(r["valor_liquido"] for r in em_uso), 2),
            "depreciacao_ano": round(sum(r["depreciacao_ano"] for r in resumos), 2),
        },
        "por_lancar": {"ate": fim_mes_passado, "ativos": len(quotas), "valor": round(sum(q.valor for q in quotas), 2)},
        "diferencas": ativos.diferencas_com_contabilidade(db, empresa.id),
        "taxas_referencia": [{"descricao": d, "conta": c, "taxa": t} for d, c, t in ativos.TAXAS_REFERENCIA],
        "contas": [{"conta": c, "nome": n} for c, n in ativos.NOMES_CONTAS.items()],
    }


class AtivoEntrada(BaseModel):
    descricao: str = Field(min_length=1, max_length=200)
    conta_ativo: Literal["432", "433", "434", "435", "437", "443"]
    data_aquisicao: date
    data_inicio: date | None = None
    valor_aquisicao: float = Field(gt=0)
    valor_residual: float = Field(0, ge=0)
    taxa: float = Field(gt=0, le=100)
    fatura_id: str | None = Field(None, max_length=12)


@router.post("/ativos", status_code=201)
def registar_ativo(entrada: AtivoEntrada, db: Session = Depends(get_db),
                   utilizador: Utilizador = Depends(utilizador_atual)):
    """Regista o ativo na ficha de ativos (a aquisição já está lançada pela fatura ou à mão)."""
    empresa = _empresa(db, utilizador)
    if entrada.fatura_id and repositorio.obter_fatura(db, empresa.id, entrada.fatura_id) is None:
        raise HTTPException(status_code=404, detail="Fatura não encontrada.")
    try:
        ativo = ativos.registar(
            db, empresa, descricao=entrada.descricao, conta_ativo=entrada.conta_ativo,
            data_aquisicao=entrada.data_aquisicao, data_inicio=entrada.data_inicio,
            valor_aquisicao=entrada.valor_aquisicao, valor_residual=entrada.valor_residual, taxa=entrada.taxa,
            fatura_id=entrada.fatura_id,
        )
    except ativos.ErroAtivo as erro:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(erro))
    db.commit()
    return ativos.resumo(db, ativo)


class Depreciacao(BaseModel):
    ate: date


@router.post("/ativos/depreciar", status_code=201)
def lancar_depreciacoes(dados: Depreciacao, db: Session = Depends(get_db),
                        utilizador: Utilizador = Depends(utilizador_atual)):
    """Lança as depreciações de todos os ativos em uso até ao fim do mês de `ate`."""
    empresa = _empresa(db, utilizador)
    try:
        lancamento = ativos.lancar_depreciacoes(db, empresa, dados.ate, utilizador.id)
    except (ativos.ErroAtivo, lancamentos.ErroContabilistico) as erro:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(erro))
    db.commit()
    return lancamento_dict(lancamento)


class SaidaAtivo(BaseModel):
    data: date
    valor_venda: float | None = Field(None, ge=0)   # sem valor: abate; com valor: alienação
    taxa_iva: float = Field(23.0, ge=0, le=100)
    conta: str = Field("121", pattern=r"^1[123]\d+$|^2\d+$")


@router.post("/ativos/{ativo_id}/saida", status_code=201)
def saida_ativo(ativo_id: int, dados: SaidaAtivo, db: Session = Depends(get_db),
                utilizador: Utilizador = Depends(utilizador_atual)):
    """Abate (sem valor de venda) ou alienação (com valor de venda) do ativo."""
    empresa = _empresa(db, utilizador)
    ativo = _ativo(db, utilizador, ativo_id)
    try:
        if dados.valor_venda is None:
            lancamento = ativos.abater(db, empresa, ativo, dados.data, utilizador.id)
        else:
            lancamento = ativos.alienar(db, empresa, ativo, dados.data, dados.valor_venda, dados.taxa_iva,
                                        dados.conta, utilizador.id)
    except (ativos.ErroAtivo, lancamentos.ErroContabilistico) as erro:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(erro))
    db.commit()
    return lancamento_dict(lancamento)


@router.delete("/ativos/{ativo_id}", status_code=204)
def apagar_ativo(ativo_id: int, db: Session = Depends(get_db), utilizador: Utilizador = Depends(utilizador_atual)):
    """Apaga um ativo registado por engano (só sem depreciações lançadas e em uso)."""
    ativo = _ativo(db, utilizador, ativo_id)
    if ativo.estado != "em_uso" or ativos.acumulada(db, ativo):
        raise HTTPException(status_code=409,
                            detail="O ativo já tem depreciações ou saiu: estorne os lançamentos primeiro.")
    db.delete(ativo)
    db.commit()


# ---------- Fecho do exercício ----------

def _so_admin(utilizador: Utilizador, acao: str) -> None:
    if config.MODO_DEMO:
        raise HTTPException(status_code=403, detail=f"Na demonstração não é possível {acao}.")
    if utilizador.papel != PAPEL_ADMIN:
        raise HTTPException(status_code=403, detail=f"Só os administradores podem {acao}.")


@router.get("/fecho")
def estado_fecho(
    ano: int = Query(..., ge=2000, le=9999),
    ajustes: float = 0.0,
    pme: bool = True,
    taxa: float = Query(fecho.TAXA_IRC, ge=0, le=100),
    taxa_pme: float = Query(fecho.TAXA_IRC_PME, ge=0, le=100),
    derrama: float = Query(fecho.TAXA_DERRAMA, ge=0, le=10),
    tributacao_autonoma: float | None = Query(None, ge=0),
    db: Session = Depends(get_db),
    utilizador: Utilizador = Depends(utilizador_atual),
):
    """Verificações antes de fechar, estimativa do IRC e lançamentos do fecho, se já foi feito."""
    empresa = _empresa(db, utilizador)
    parametros = fecho.ParametrosIRC(ajustes, pme, taxa, taxa_pme, derrama, tributacao_autonoma)
    return fecho.estado(db, empresa, ano, parametros)


class Fecho(BaseModel):
    ano: int = Field(ge=2000, le=9999)
    imposto: float = Field(ge=0)
    fechar_periodo: bool = True


@router.post("/fecho", status_code=201)
def fechar_exercicio(dados: Fecho, db: Session = Depends(get_db), utilizador: Utilizador = Depends(utilizador_atual)):
    _so_admin(utilizador, "fechar o exercício")
    empresa = _empresa(db, utilizador)
    try:
        feitos = fecho.fechar(db, empresa, dados.ano, dados.imposto, dados.fechar_periodo, utilizador.id)
    except (fecho.ErroFecho, lancamentos.ErroContabilistico) as erro:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(erro))
    db.commit()
    return [lancamento_dict(la) for la in feitos]


class AnoFecho(BaseModel):
    ano: int = Field(ge=2000, le=9999)
    data: date | None = None


@router.post("/fecho/transferir", status_code=201)
def transferir_resultado(dados: AnoFecho, db: Session = Depends(get_db),
                         utilizador: Utilizador = Depends(utilizador_atual)):
    """Resultado líquido do exercício para resultados transitados (no exercício seguinte)."""
    _so_admin(utilizador, "transferir o resultado")
    empresa = _empresa(db, utilizador)
    try:
        lancamento = fecho.transferir_resultado(db, empresa, dados.ano, dados.data, utilizador.id)
    except (fecho.ErroFecho, lancamentos.ErroContabilistico) as erro:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(erro))
    db.commit()
    return lancamento_dict(lancamento)


@router.post("/fecho/reabrir")
def reabrir_exercicio(dados: AnoFecho, db: Session = Depends(get_db),
                      utilizador: Utilizador = Depends(utilizador_atual)):
    _so_admin(utilizador, "reabrir o exercício")
    empresa = _empresa(db, utilizador)
    try:
        estornados = fecho.reabrir(db, empresa, dados.ano, utilizador.id)
    except (fecho.ErroFecho, lancamentos.ErroContabilistico) as erro:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(erro))
    db.commit()
    return {"estornados": estornados}


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


# ---------- Open banking: ligação direta às contas bancárias ----------

class LigacaoNova(BaseModel):
    conta: str = Field(pattern=r"^1[23]\d+$")
    banco: str = Field(min_length=1, max_length=120)
    pais: str = Field("PT", pattern=r"^[A-Z]{2}$")


class ContaDaLigacao(BaseModel):
    uid: str = Field(min_length=1, max_length=100)


@router.get("/open-banking")
def estado_open_banking(conta: str | None = Query(None, pattern=r"^1[123]\d+$"), db: Session = Depends(get_db),
                        utilizador: Utilizador = Depends(utilizador_atual)):
    """Se a ligação direta aos bancos está disponível e as ligações da empresa (ou de uma conta)."""
    f = open_banking.fornecedor()
    return {"disponivel": f is not None, "simulado": f is not None and f.nome == open_banking.Simulado.nome,
            "ligacoes": [open_banking.ligacao_dict(x) for x in open_banking.listar(db, utilizador.empresa_id, conta)]}


@router.get("/open-banking/bancos")
def bancos_open_banking(pais: str = Query("PT", pattern=r"^[A-Z]{2}$")):
    try:
        return open_banking.bancos(pais)
    except open_banking.ErroOpenBanking as erro:
        raise HTTPException(status_code=409, detail=str(erro))


@router.post("/open-banking/ligacoes", status_code=201)
def ligar_banco(dados: LigacaoNova, db: Session = Depends(get_db), utilizador: Utilizador = Depends(utilizador_atual)):
    """Começa a ligação: devolve o endereço do banco onde o utilizador autoriza o acesso (só leitura)."""
    empresa = _empresa(db, utilizador)
    try:
        lig, url = open_banking.iniciar(db, empresa, dados.conta, dados.banco, dados.pais, utilizador.id)
    except (open_banking.ErroOpenBanking, lancamentos.ErroContabilistico) as erro:
        db.rollback()
        raise _erro(erro)
    db.commit()
    return {"id": lig.id, "url": url}


@router.post("/open-banking/ligacoes/{ligacao_id}/conta")
def escolher_conta_banco(ligacao_id: int, dados: ContaDaLigacao, db: Session = Depends(get_db),
                         utilizador: Utilizador = Depends(utilizador_atual)):
    try:
        lig = open_banking.obter(db, utilizador.empresa_id, ligacao_id)
        open_banking.escolher_conta(db, lig, dados.uid)
    except open_banking.ErroOpenBanking as erro:
        db.rollback()
        raise _erro(erro)
    db.commit()
    return open_banking.ligacao_dict(lig)


@router.post("/open-banking/ligacoes/{ligacao_id}/sincronizar")
def sincronizar_banco(ligacao_id: int, request: Request, db: Session = Depends(get_db),
                      utilizador: Utilizador = Depends(utilizador_atual)):
    """Traz os movimentos novos do banco para a reconciliação."""
    empresa = _empresa(db, utilizador)
    try:
        lig = open_banking.obter(db, empresa.id, ligacao_id)
    except open_banking.ErroOpenBanking as erro:
        raise HTTPException(status_code=404, detail=str(erro))
    try:
        r = open_banking.sincronizar(db, empresa, lig, utilizador.id, request.client.host if request.client else None)
    except open_banking.ErroOpenBanking as erro:
        db.commit()   # guarda o estado da ligação (expirada ou com erro), sem movimentos a meio
        raise _erro(erro)
    except lancamentos.ErroContabilistico as erro:
        db.rollback()
        raise _erro(erro)
    db.commit()
    return {"importados": r.importados, "lidos": r.lidos, "desde": r.desde, "ate": r.ate,
            "ligacao": open_banking.ligacao_dict(lig)}


@router.delete("/open-banking/ligacoes/{ligacao_id}", status_code=204)
def desligar_banco(ligacao_id: int, db: Session = Depends(get_db), utilizador: Utilizador = Depends(utilizador_atual)):
    """Desliga a conta (e termina a autorização no banco). Os movimentos já importados ficam."""
    try:
        lig = open_banking.obter(db, utilizador.empresa_id, ligacao_id)
    except open_banking.ErroOpenBanking as erro:
        raise HTTPException(status_code=404, detail=str(erro))
    open_banking.desligar(lig)
    db.commit()


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


# ---------- Salários ----------

class FuncionarioEntrada(BaseModel):
    nome: str = Field(min_length=1, max_length=200)
    tipo: Literal["pessoal", "orgaos_sociais"] = "pessoal"
    nif: str | None = Field(None, max_length=9)
    niss: str | None = Field(None, max_length=11)
    data_admissao: date
    data_saida: date | None = None
    data_nascimento: date | None = None
    salario_base: float = Field(ge=0)
    subsidio_alimentacao: float = Field(0, ge=0, le=100)
    alimentacao_cartao: bool = True
    duodecimos: bool = False
    taxa_irs: float = Field(0, ge=0, le=100)
    taxa_ss_trabalhador: float | None = Field(None, ge=0, le=100)
    taxa_ss_entidade: float | None = Field(None, ge=0, le=100)
    irs_situacao: Literal["nao_casado", "casado_dois", "casado_unico"] | None = None
    irs_dependentes: int = Field(0, ge=0, le=20)
    irs_dependentes_deficiencia: int = Field(0, ge=0, le=20)
    irs_deficiencia: bool = False
    irs_conjuge_deficiencia: bool = False


def _funcionario(db: Session, utilizador: Utilizador, funcionario_id: int) -> Funcionario:
    f = db.scalar(select(Funcionario).where(Funcionario.id == funcionario_id,
                                            Funcionario.empresa_id == utilizador.empresa_id))
    if f is None:
        raise HTTPException(status_code=404, detail="Pessoa não encontrada.")
    return f


@router.get("/salarios")
def resumo_salarios(ano: int | None = Query(None, ge=2000, le=9999), db: Session = Depends(get_db),
                    utilizador: Utilizador = Depends(utilizador_atual)):
    """Pessoas, processamentos do ano e o que está por processar ou por pagar."""
    empresa = _empresa(db, utilizador)
    hoje = date.today()
    ano = ano or hoje.year
    pessoas = db.scalars(select(Funcionario).where(Funcionario.empresa_id == empresa.id)
                         .order_by(Funcionario.numero)).all()
    return {
        "ano": ano,
        "funcionarios": [salarios.funcionario_dict(f) for f in pessoas],
        "mapa": salarios.mapa_anual(db, empresa.id, ano),
        "por_processar": [{"ano": a, "mes": m, "nome_mes": salarios.nome_mes(a, m)}
                          for a, m in salarios.por_processar(db, empresa.id, hoje)],
        "pagamentos_em_atraso": salarios.pagamentos_em_atraso(db, empresa.id, hoje),
        "limites_alimentacao": {"numerario": salarios.LIMITE_ALIMENTACAO_NUMERARIO,
                                "cartao": salarios.LIMITE_ALIMENTACAO_CARTAO},
        "taxas_ss": {t: {"trabalhador": a, "entidade": b} for t, (a, b) in salarios.TAXAS_SS.items()},
        "aviso": salarios.AVISO,
    }


@router.post("/salarios/funcionarios", status_code=201)
def registar_funcionario(entrada: FuncionarioEntrada, db: Session = Depends(get_db),
                         utilizador: Utilizador = Depends(utilizador_atual)):
    empresa = _empresa(db, utilizador)
    try:
        f = salarios.registar(db, empresa, **entrada.model_dump(exclude={"data_saida"}))
        f.data_saida = entrada.data_saida
    except salarios.ErroSalarios as erro:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(erro))
    db.commit()
    return salarios.funcionario_dict(f)


@router.put("/salarios/funcionarios/{funcionario_id}")
def alterar_funcionario(funcionario_id: int, entrada: FuncionarioEntrada, db: Session = Depends(get_db),
                        utilizador: Utilizador = Depends(utilizador_atual)):
    """Altera a ficha (os meses já processados guardam os valores com que foram calculados)."""
    f = _funcionario(db, utilizador, funcionario_id)
    try:
        salarios.alterar(f, **entrada.model_dump())
    except salarios.ErroSalarios as erro:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(erro))
    db.commit()
    return salarios.funcionario_dict(f)


@router.delete("/salarios/funcionarios/{funcionario_id}", status_code=204)
def apagar_funcionario(funcionario_id: int, db: Session = Depends(get_db),
                       utilizador: Utilizador = Depends(utilizador_atual)):
    f = _funcionario(db, utilizador, funcionario_id)
    for p in db.scalars(select(ProcessamentoSalarial).where(ProcessamentoSalarial.empresa_id == f.empresa_id)):
        if salarios.processado(db, p) and any(r["funcionario_id"] == f.id for r in p.recibos):
            raise HTTPException(status_code=409, detail="Esta pessoa já tem salários processados: indique a data "
                                                        "de saída em vez de a apagar.")
    db.delete(f)
    db.commit()


class VariaveisEntrada(BaseModel):
    funcionario_id: int
    dias: int = Field(salarios.DIAS_UTEIS, ge=0, le=31)
    outros: float = Field(0, ge=0)
    faltas: float = Field(0, ge=0)
    suplementar: float = Field(0, ge=0)


class ProcessamentoEntrada(BaseModel):
    ano: int = Field(ge=2000, le=9999)
    mes: int = Field(ge=1, le=12)
    subsidio_ferias: bool | None = None
    subsidio_natal: bool | None = None
    variaveis: list[VariaveisEntrada] = Field(default_factory=list, max_length=1000)


def _opcoes(entrada: ProcessamentoEntrada) -> salarios.Opcoes:
    extra = {}
    if entrada.subsidio_ferias is not None:
        extra["subsidio_ferias"] = entrada.subsidio_ferias
    if entrada.subsidio_natal is not None:
        extra["subsidio_natal"] = entrada.subsidio_natal
    variaveis = {v.funcionario_id: salarios.Variaveis(v.dias, v.outros, v.faltas, v.suplementar)
                 for v in entrada.variaveis}
    return salarios.opcoes_do_mes(entrada.mes, entrada.ano, variaveis=variaveis, **extra)


@router.get("/salarios/ferias")
def estimativa_ferias(ano: int = Query(..., ge=2000, le=9999), db: Session = Depends(get_db),
                      utilizador: Utilizador = Depends(utilizador_atual)):
    return salarios.estimativa_ferias(db, utilizador.empresa_id, ano)


class AnoEntrada(BaseModel):
    ano: int = Field(ge=2000, le=9999)


@router.post("/salarios/ferias", status_code=201)
def lancar_acrescimo_ferias(dados: AnoEntrada, db: Session = Depends(get_db),
                            utilizador: Utilizador = Depends(utilizador_atual)):
    """Acréscimo de férias e subsídio de férias a 31/12, com reversão a 1 de janeiro."""
    empresa = _empresa(db, utilizador)
    try:
        lancamento = salarios.acrescimo_ferias(db, empresa, dados.ano, utilizador.id)
    except (salarios.ErroSalarios, periodizacao.ErroPeriodizacao, lancamentos.ErroContabilistico) as erro:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(erro))
    db.commit()
    return lancamento_dict(lancamento)


@router.get("/salarios/{ano}/{mes}/seguranca-social")
def ficheiro_seguranca_social(ano: int, mes: int, db: Session = Depends(get_db),
                              utilizador: Utilizador = Depends(utilizador_atual)):
    """Ficheiro da declaração de remunerações para entregar na Segurança Social Direta."""
    if not (2000 <= ano <= 9999 and 1 <= mes <= 12):
        raise HTTPException(status_code=422, detail="Mês inválido.")
    empresa = _empresa(db, utilizador)
    try:
        conteudo, nome, _ = declaracoes.ficheiro_seguranca_social(db, empresa, ano, mes)
    except declaracoes.ErroDeclaracao as erro:
        raise HTTPException(status_code=422, detail=str(erro))
    return Response(conteudo, media_type="text/plain; charset=ascii",
                    headers={"Content-Disposition": f'attachment; filename="{nome}"'})


@router.get("/salarios/{ano}/{mes}/declaracoes")
def resumo_declaracoes(ano: int, mes: int, db: Session = Depends(get_db),
                       utilizador: Utilizador = Depends(utilizador_atual)):
    """Mapa da DMR (AT) e resumo da declaração da Segurança Social, com o que falta preencher."""
    if not (2000 <= ano <= 9999 and 1 <= mes <= 12):
        raise HTTPException(status_code=422, detail="Mês inválido.")
    empresa = _empresa(db, utilizador)
    try:
        dmr = declaracoes.mapa_dmr(db, empresa, ano, mes)
    except declaracoes.ErroDeclaracao as erro:
        raise HTTPException(status_code=422, detail=str(erro))
    try:
        _, nome, ss = declaracoes.ficheiro_seguranca_social(db, empresa, ano, mes)
        ss |= {"pronto": True, "ficheiro": nome}
    except declaracoes.ErroDeclaracao as erro:
        ss = {"pronto": False, "erro": str(erro)}
    try:
        _, _, ficheiro = declaracoes.ficheiro_dmr(db, empresa, ano, mes)
        dmr["ficheiro"] = ficheiro | {"pronto": True}
    except declaracoes.ErroDeclaracao as erro:
        dmr["ficheiro"] = {"pronto": False, "erro": str(erro)}
    return {"dmr": dmr, "seguranca_social": ss}


@router.get("/salarios/{ano}/{mes}/dmr.txt")
def ficheiro_dmr(ano: int, mes: int, substituicao: bool = False, db: Session = Depends(get_db),
                 utilizador: Utilizador = Depends(utilizador_atual)):
    """Ficheiro da DMR (AT) no formato oficial de transferência eletrónica, para o Portal das Finanças."""
    if not (2000 <= ano <= 9999 and 1 <= mes <= 12):
        raise HTTPException(status_code=422, detail="Mês inválido.")
    empresa = _empresa(db, utilizador)
    try:
        conteudo, nome, _ = declaracoes.ficheiro_dmr(db, empresa, ano, mes, substituicao=substituicao)
    except declaracoes.ErroDeclaracao as erro:
        raise HTTPException(status_code=422, detail=str(erro))
    return Response(conteudo, media_type="text/plain; charset=ascii",
                    headers={"Content-Disposition": f'attachment; filename="{nome}"'})


@router.get("/salarios/{ano}/{mes}/dmr.csv")
def csv_dmr(ano: int, mes: int, db: Session = Depends(get_db), utilizador: Utilizador = Depends(utilizador_atual)):
    empresa = _empresa(db, utilizador)
    try:
        mapa = declaracoes.mapa_dmr(db, empresa, ano, mes)
    except declaracoes.ErroDeclaracao as erro:
        raise HTTPException(status_code=422, detail=str(erro))
    return Response(declaracoes.csv_dmr(mapa), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="DMR-{ano}{mes:02d}.csv"'})


@router.get("/salarios/{ano}/{mes}")
def processamento_do_mes(ano: int, mes: int, db: Session = Depends(get_db),
                         utilizador: Utilizador = Depends(utilizador_atual)):
    """O processamento do mês, se existir; senão, a pré-visualização dos recibos com os valores da ficha."""
    if not (2000 <= ano <= 9999 and 1 <= mes <= 12):
        raise HTTPException(status_code=422, detail="Mês inválido.")
    empresa = _empresa(db, utilizador)
    p = salarios.obter(db, empresa.id, ano, mes)
    if salarios.processado(db, p):
        return salarios.processamento_dict(db, p)
    opcoes = salarios.opcoes_do_mes(mes, ano)
    try:
        recibos = salarios.calcular(db, empresa.id, ano, mes, opcoes)
    except salarios.ErroSalarios as erro:
        raise HTTPException(status_code=422, detail=str(erro))
    return {"ano": ano, "mes": mes, "nome_mes": salarios.nome_mes(ano, mes), "processado": False,
            "recibos": recibos, "totais": salarios.totais(recibos), "subsidio_ferias": opcoes.subsidio_ferias,
            "subsidio_natal": opcoes.subsidio_natal}


@router.post("/salarios/simular")
def simular_processamento(entrada: ProcessamentoEntrada, db: Session = Depends(get_db),
                          utilizador: Utilizador = Depends(utilizador_atual)):
    try:
        recibos = salarios.calcular(db, utilizador.empresa_id, entrada.ano, entrada.mes, _opcoes(entrada))
    except salarios.ErroSalarios as erro:
        raise HTTPException(status_code=422, detail=str(erro))
    return {"recibos": recibos, "totais": salarios.totais(recibos)}


@router.post("/salarios/processar", status_code=201)
def processar_salarios(entrada: ProcessamentoEntrada, db: Session = Depends(get_db),
                       utilizador: Utilizador = Depends(utilizador_atual)):
    empresa = _empresa(db, utilizador)
    try:
        p = salarios.processar(db, empresa, entrada.ano, entrada.mes, _opcoes(entrada), utilizador.id)
    except (salarios.ErroSalarios, lancamentos.ErroContabilistico) as erro:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(erro))
    db.commit()
    return salarios.processamento_dict(db, p)


class PagamentoSalarios(BaseModel):
    tipo: Literal["salarios", "seguranca_social", "irs"]
    data: date | None = None
    conta: str = Field("121", pattern=r"^1[123]\d+$")


@router.post("/salarios/{ano}/{mes}/pagar", status_code=201)
def pagar_salarios(ano: int, mes: int, dados: PagamentoSalarios, db: Session = Depends(get_db),
                   utilizador: Utilizador = Depends(utilizador_atual)):
    """Pagamento dos salários, da Segurança Social ou do IRS retido (por omissão, na data-limite)."""
    empresa = _empresa(db, utilizador)
    p = salarios.obter(db, empresa.id, ano, mes)
    if p is None:
        raise HTTPException(status_code=404, detail="Processamento não encontrado.")
    data = dados.data or salarios.prazo_pagamento(ano, mes, dados.tipo)
    try:
        lancamento = salarios.pagar(db, empresa, p, dados.tipo, data, dados.conta, utilizador.id)
    except (salarios.ErroSalarios, lancamentos.ErroContabilistico) as erro:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(erro))
    db.commit()
    return lancamento_dict(lancamento)


# ---------- Acréscimos e diferimentos ----------

@router.get("/periodizacao")
def resumo_periodizacao(db: Session = Depends(get_db), utilizador: Utilizador = Depends(utilizador_atual)):
    empresa = _empresa(db, utilizador)
    hoje = date.today()
    lista = db.scalars(select(Diferimento).where(Diferimento.empresa_id == empresa.id)
                       .order_by(Diferimento.inicio.desc(), Diferimento.id.desc())).all()
    dif = [periodizacao.diferimento_dict(db, d, hoje) for d in lista]
    validos = [d for d in dif if not d["anulado"]]
    fim_mes_passado = date(hoje.year, hoje.month, 1) - timedelta(days=1)
    atraso = periodizacao.em_atraso(db, empresa.id, fim_mes_passado)
    saldos = relatorios.saldos_por_conta(db, empresa.id, ate=hoje)
    return {
        "diferimentos": dif,
        "acrescimos": periodizacao.listar_acrescimos(db, empresa.id),
        "faturas_por_diferir": [{
            "fatura_id": f.id, "fornecedor": f.nome_fornecedor, "documento": lancamentos.documento_fatura(f),
            "data": f.data_emissao, "conta": conta, "referencia": f"{la.diario} {la.ano}/{la.numero}",
            "proposta": periodizacao.proposta_dict(periodizacao.propor(
                f, conta, periodizacao.valor_na_conta(la.linhas, conta))),
        } for f, la, conta in periodizacao.faturas_por_diferir(db, empresa.id, date(hoje.year - 1, 1, 1))],
        "por_reconhecer": {"ate": fim_mes_passado, "quantidade": len(atraso),
                           "valor": round(sum(v for _, _, v in atraso), 2)},
        "totais": {
            "gastos_a_reconhecer": round(sum(d["por_reconhecer"] for d in validos if d["tipo"] == "gasto"), 2),
            "rendimentos_a_reconhecer": round(sum(d["por_reconhecer"] for d in validos
                                                  if d["tipo"] == "rendimento"), 2),
        },
        "saldos": {c: round(saldos.get(c, 0.0), 2) for c in ("281", "282", "2721", "2722")},
    }


class DiferimentoEntrada(BaseModel):
    tipo: Literal["gasto", "rendimento"] = "gasto"
    descricao: str = Field(min_length=1, max_length=200)
    conta: str = Field(min_length=2, max_length=20)
    valor: float = Field(gt=0)
    inicio: date
    meses: int = Field(ge=2, le=120)
    data: date | None = None


@router.post("/periodizacao/diferimentos", status_code=201)
def registar_diferimento(entrada: DiferimentoEntrada, db: Session = Depends(get_db),
                         utilizador: Utilizador = Depends(utilizador_atual)):
    empresa = _empresa(db, utilizador)
    try:
        d = periodizacao.registar(db, empresa, **entrada.model_dump(), utilizador_id=utilizador.id)
    except (periodizacao.ErroPeriodizacao, lancamentos.ErroContabilistico) as erro:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(erro))
    db.commit()
    return periodizacao.diferimento_dict(db, d)


class AteEntrada(BaseModel):
    ate: date


@router.post("/periodizacao/reconhecer", status_code=201)
def reconhecer_diferimentos(dados: AteEntrada, db: Session = Depends(get_db),
                            utilizador: Utilizador = Depends(utilizador_atual)):
    """Lança a parte dos diferimentos que diz respeito aos meses até ao fim do mês de `ate`."""
    empresa = _empresa(db, utilizador)
    try:
        lancamento = periodizacao.reconhecer(db, empresa, dados.ate, utilizador.id)
    except (periodizacao.ErroPeriodizacao, lancamentos.ErroContabilistico) as erro:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(erro))
    db.commit()
    return lancamento_dict(lancamento)


class LinhaAcrescimo(BaseModel):
    conta: str = Field(min_length=2, max_length=20)
    valor: float = Field(gt=0)
    descricao: str | None = Field(None, max_length=200)


class AcrescimoEntrada(BaseModel):
    tipo: Literal["gasto", "rendimento"] = "gasto"
    data: date
    descricao: str = Field(min_length=1, max_length=300)
    linhas: list[LinhaAcrescimo] = Field(min_length=1, max_length=50)
    reversao: date | None = None


@router.post("/periodizacao/acrescimos", status_code=201)
def lancar_acrescimo(entrada: AcrescimoEntrada, db: Session = Depends(get_db),
                     utilizador: Utilizador = Depends(utilizador_atual)):
    empresa = _empresa(db, utilizador)
    try:
        lancamento = periodizacao.acrescimo(
            db, empresa, entrada.tipo, entrada.data, entrada.descricao,
            [(ln.conta, ln.valor, ln.descricao) for ln in entrada.linhas], entrada.reversao, utilizador.id)
    except (periodizacao.ErroPeriodizacao, lancamentos.ErroContabilistico) as erro:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(erro))
    db.commit()
    return lancamento_dict(lancamento)


# ---------- Balanço e saldos de abertura ----------

@router.get("/balanco")
def obter_balanco(ate: date | None = None, db: Session = Depends(get_db),
                  utilizador: Utilizador = Depends(utilizador_atual)):
    _empresa(db, utilizador)
    return relatorios.balanco(db, utilizador.empresa_id, ate or date.today())


@router.get("/abertura")
def estado_abertura(db: Session = Depends(get_db), utilizador: Utilizador = Depends(utilizador_atual)):
    la = abertura.existente(db, utilizador.empresa_id)
    return {"lancamento": lancamento_dict(la) if la else None}


class SaldoAberturaEntrada(BaseModel):
    conta: str = Field(min_length=1, max_length=20)
    nome: str | None = Field(None, max_length=200)
    nif: str | None = Field(None, max_length=20)
    debito: float = Field(0, ge=0)
    credito: float = Field(0, ge=0)


class AberturaEntrada(BaseModel):
    data: date
    saldos: list[SaldoAberturaEntrada] = Field(min_length=1, max_length=20000)


def _lancar_abertura(db: Session, utilizador: Utilizador, data: date, saldos: list) -> dict:
    empresa = _empresa(db, utilizador)
    try:
        lancamento = abertura.lancar(db, empresa, data, saldos, utilizador.id)
    except (abertura.ErroAbertura, lancamentos.ErroContabilistico) as erro:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(erro))
    db.commit()
    return lancamento_dict(lancamento)


@router.post("/abertura", status_code=201)
def lancar_abertura(entrada: AberturaEntrada, db: Session = Depends(get_db),
                    utilizador: Utilizador = Depends(utilizador_atual)):
    saldos = [abertura.SaldoAbertura(s.conta, s.debito, s.credito, s.nome, s.nif) for s in entrada.saldos]
    return _lancar_abertura(db, utilizador, entrada.data, saldos)


@router.post("/abertura/importar", status_code=201)
def importar_abertura(ficheiro: UploadFile = File(...), data: date = Form(...), db: Session = Depends(get_db),
                      utilizador: Utilizador = Depends(utilizador_atual)):
    """Balancete do programa anterior em CSV ou Excel (conta, nome, débito, crédito)."""
    conteudo = ficheiro.file.read()
    if len(conteudo) > config.TAMANHO_MAXIMO:
        raise HTTPException(status_code=413, detail="O ficheiro tem mais de 10 MB.")
    try:
        saldos = abertura.ler_ficheiro(conteudo, ficheiro.filename or "balancete.csv")
    except abertura.ErroAbertura as erro:
        raise HTTPException(status_code=422, detail=str(erro))
    return _lancar_abertura(db, utilizador, data, saldos)
