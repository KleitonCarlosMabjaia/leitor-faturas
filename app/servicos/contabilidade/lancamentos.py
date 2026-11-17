"""
Lançamentos contabilísticos em partidas dobradas.

Regras que nunca se quebram (verificadas em criar()):
- cada linha tem débito OU crédito, positivo;
- a soma dos débitos é igual à soma dos créditos;
- só se lança em contas de movimento;
- não se lança em períodos fechados;
- um lançamento não se apaga nem se altera: anula-se com um estorno.

As funções não fazem commit: quem as chama decide quando gravar.
"""

from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.modelos.tabelas import (
    ESTADO_CONFIRMADA,
    LANCAMENTO_ATIVO,
    LANCAMENTO_ESTORNADO,
    MOVIMENTO_PENDENTE,
    Empresa,
    Fatura,
    Lancamento,
    LinhaLancamento,
    MovimentoBancario,
)
from app.servicos.contabilidade import classificacao, iva, plano_snc, relatorios

DIARIOS = {
    "COM": "Compras",
    "VEN": "Vendas",
    "BAN": "Bancos",
    "CX": "Caixa",
    "OD": "Operações diversas",
}

TIPOS_NOTA_CREDITO = {"NC"}
TOLERANCIA = 0.005


class ErroContabilistico(Exception):
    """Operação que violaria uma regra contabilística (mensagem pronta a mostrar)."""


@dataclass
class LinhaNova:
    conta_codigo: str
    debito: float = 0.0
    credito: float = 0.0
    descricao: str | None = None
    tipo_iva: str | None = None
    taxa_iva: float | None = None


def _euros(valor: float) -> str:
    return f"{valor:,.2f} €".replace(",", " ").replace(".", ",")


def verificar_periodo(empresa: Empresa, data: date) -> None:
    if empresa.fechado_ate and data <= empresa.fechado_ate:
        raise ErroContabilistico(
            f"O período até {empresa.fechado_ate:%d/%m/%Y} está fechado. Use uma data posterior ou reabra o período."
        )


def proximo_numero(db: Session, empresa_id: int, diario: str, ano: int) -> int:
    atual = db.scalar(select(func.max(Lancamento.numero)).where(
        Lancamento.empresa_id == empresa_id, Lancamento.diario == diario, Lancamento.ano == ano
    ))
    return (atual or 0) + 1


def validar(db: Session, empresa: Empresa, diario: str, data: date, descricao: str,
            linhas: list[LinhaNova]) -> list[LinhaLancamento]:
    """Verifica todas as regras e devolve as linhas prontas a gravar (sem as gravar)."""
    if diario not in DIARIOS:
        raise ErroContabilistico(f"Diário desconhecido: {diario}.")
    if not descricao.strip():
        raise ErroContabilistico("Escreva uma descrição para o lançamento.")
    verificar_periodo(empresa, data)

    linhas = [ln for ln in linhas if round(ln.debito, 2) or round(ln.credito, 2)]
    if len(linhas) < 2:
        raise ErroContabilistico("Um lançamento precisa de pelo menos duas linhas (um débito e um crédito).")

    contas = plano_snc.contas_por_codigo(db, empresa.id)
    total_debito = total_credito = 0.0
    novas = []
    for ordem, ln in enumerate(linhas):
        debito, credito = round(ln.debito, 2), round(ln.credito, 2)
        if debito < 0 or credito < 0:
            raise ErroContabilistico("Os valores não podem ser negativos: troque o lado (débito/crédito).")
        if debito and credito:
            raise ErroContabilistico(f"A linha da conta {ln.conta_codigo} tem débito e crédito ao mesmo tempo.")
        conta = contas.get(ln.conta_codigo)
        if conta is None:
            raise ErroContabilistico(f"A conta {ln.conta_codigo} não existe no plano de contas.")
        if not conta.movimento:
            raise ErroContabilistico(
                f"A conta {conta.codigo} {conta.nome} é uma conta agregadora: lance numa das suas subcontas."
            )
        total_debito += debito
        total_credito += credito
        novas.append(LinhaLancamento(
            ordem=ordem, conta_id=conta.id, debito=debito, credito=credito,
            descricao=(ln.descricao or None) and ln.descricao[:200], tipo_iva=ln.tipo_iva, taxa_iva=ln.taxa_iva,
        ))

    if abs(total_debito - total_credito) > TOLERANCIA:
        raise ErroContabilistico(
            f"O lançamento não está equilibrado: débito {_euros(total_debito)}, crédito {_euros(total_credito)} "
            f"(diferença de {_euros(abs(total_debito - total_credito))})."
        )
    return novas


def criar(
    db: Session,
    empresa: Empresa,
    diario: str,
    data: date,
    descricao: str,
    linhas: list[LinhaNova],
    *,
    documento: str | None = None,
    origem: str = "manual",
    fatura_id: str | None = None,
    notas: list[str] | None = None,
    utilizador_id: int | None = None,
    estorno_de_id: int | None = None,
) -> Lancamento:
    novas = validar(db, empresa, diario, data, descricao, linhas)
    lancamento = Lancamento(
        empresa_id=empresa.id, diario=diario, ano=data.year, numero=proximo_numero(db, empresa.id, diario, data.year),
        data=data, descricao=descricao.strip()[:300], documento=(documento or None) and documento[:80],
        origem=origem, fatura_id=fatura_id, estado=LANCAMENTO_ATIVO, estorno_de_id=estorno_de_id,
        notas=notas or None, criado_por_id=utilizador_id, linhas=novas,
    )
    db.add(lancamento)
    db.flush()
    return lancamento


def obter(db: Session, empresa_id: int, lancamento_id: int) -> Lancamento | None:
    return db.scalar(
        select(Lancamento)
        .options(selectinload(Lancamento.linhas).joinedload(LinhaLancamento.conta))
        .where(Lancamento.id == lancamento_id, Lancamento.empresa_id == empresa_id)
    )


def estornar(db: Session, empresa: Empresa, lancamento: Lancamento, utilizador_id: int | None = None,
             data: date | None = None) -> Lancamento:
    """Anula um lançamento com outro de sinal contrário. O original fica marcado como estornado."""
    if lancamento.estado == LANCAMENTO_ESTORNADO:
        raise ErroContabilistico("Este lançamento já foi estornado.")
    if lancamento.estorno_de_id:
        raise ErroContabilistico("Um estorno não se estorna: faça um novo lançamento.")
    # Por omissão o estorno fica na data do original; se esse período estiver fechado, fica na data de hoje
    if data is None:
        data = lancamento.data
        if empresa.fechado_ate and data <= empresa.fechado_ate:
            data = date.today()
    linhas = [
        LinhaNova(ln.conta.codigo, debito=ln.credito, credito=ln.debito, descricao=ln.descricao,
                  tipo_iva=ln.tipo_iva, taxa_iva=ln.taxa_iva)
        for ln in lancamento.linhas
    ]
    estorno = criar(
        db, empresa, lancamento.diario, data,
        f"Estorno de {lancamento.diario} {lancamento.ano}/{lancamento.numero}: {lancamento.descricao}"[:300],
        linhas, documento=lancamento.documento, origem="estorno", fatura_id=lancamento.fatura_id,
        utilizador_id=utilizador_id, estorno_de_id=lancamento.id,
    )
    lancamento.estado = LANCAMENTO_ESTORNADO
    # Movimentos do banco reconciliados com este lançamento voltam a estar por reconciliar
    ids_linhas = [ln.id for ln in lancamento.linhas]
    for mov in db.scalars(select(MovimentoBancario).where(MovimentoBancario.linha_id.in_(ids_linhas))):
        mov.linha_id, mov.estado, mov.reconciliado_em, mov.reconciliado_por_id = None, MOVIMENTO_PENDENTE, None, None
    return estorno


# ---------- Faturas de fornecedores ----------

def lancamento_ativo(db: Session, empresa_id: int, fatura_id: str, origem: str) -> Lancamento | None:
    return db.scalar(select(Lancamento).where(
        Lancamento.empresa_id == empresa_id, Lancamento.fatura_id == fatura_id, Lancamento.origem == origem,
        Lancamento.estado == LANCAMENTO_ATIVO, Lancamento.estorno_de_id.is_(None),
    ))


def documento_fatura(fatura: Fatura) -> str:
    """Ex.: "FT 2026/0064". O número muitas vezes já traz o tipo à frente: não se repete."""
    tipo = fatura.tipo_documento or "FT"
    numero = (fatura.numero_fatura or fatura.id).strip()
    return numero if numero.upper().startswith(f"{tipo.upper()} ") else f"{tipo} {numero}"


def _raiz_fornecedor(conta_gasto: str) -> str:
    # Compras de ativos fixos ficam em "Fornecedores de investimentos" (2711), o resto em 2211
    return "2711" if conta_gasto.startswith(("43", "44")) else "2211"


def linhas_fatura(db: Session, empresa_id: int, fatura: Fatura, conta_gasto: str, perfil_iva: str) -> list[LinhaNova]:
    """Linhas do lançamento de compra: gasto/ativo e IVA a débito, fornecedor a crédito."""
    linhas: list[LinhaNova] = []
    for li in fatura.linhas_iva or []:
        base, valor, taxa = float(li["base"]), float(li["valor"]), float(li["taxa"])
        dedutivel, nao_dedutivel = iva.dividir_iva(valor, perfil_iva)
        linhas.append(LinhaNova(conta_gasto, debito=base, descricao=f"Base {taxa:g}%", tipo_iva="base", taxa_iva=taxa))
        if dedutivel:
            linhas.append(LinhaNova(iva.conta_iva_dedutivel(conta_gasto, taxa), debito=dedutivel,
                                    descricao=f"IVA dedutível {taxa:g}%", tipo_iva="iva", taxa_iva=taxa))
        if nao_dedutivel:
            linhas.append(LinhaNova(conta_gasto, debito=nao_dedutivel, descricao=f"IVA não dedutível {taxa:g}%",
                                    tipo_iva="iva_nd", taxa_iva=taxa))
    if not linhas:
        valor = fatura.valor_sem_iva or fatura.total or 0.0
        linhas.append(LinhaNova(conta_gasto, debito=valor, descricao="Sem IVA", tipo_iva="base", taxa_iva=0.0))

    soma = round(sum(ln.debito for ln in linhas), 2)
    # Arredondamentos de cêntimos entre as linhas e o total da fatura vão para a primeira linha de gasto
    if fatura.total and 0 < abs(fatura.total - soma) <= 0.05:
        linhas[0].debito = round(linhas[0].debito + fatura.total - soma, 2)
        soma = round(fatura.total, 2)

    fornecedor = plano_snc.subconta_terceiro(
        db, empresa_id, _raiz_fornecedor(conta_gasto), fatura.nif_fornecedor, fatura.nome_fornecedor or ""
    )
    linhas.append(LinhaNova(fornecedor.codigo, credito=soma, descricao=fatura.nome_fornecedor))

    # Nota de crédito: o mesmo lançamento com os lados trocados
    if (fatura.tipo_documento or "").upper() in TIPOS_NOTA_CREDITO:
        for ln in linhas:
            ln.debito, ln.credito = ln.credito, ln.debito
    return linhas


def verificar_fatura_contabilizavel(fatura: Fatura) -> None:
    if fatura.estado_extracao != "concluida" or fatura.data_emissao is None or fatura.total is None:
        raise ErroContabilistico("A fatura ainda não tem os dados lidos (data e total).")
    if fatura.estado != ESTADO_CONFIRMADA:
        raise ErroContabilistico("Confirme os dados da fatura antes de a contabilizar.")


def contabilizar_fatura(
    db: Session,
    empresa: Empresa,
    fatura: Fatura,
    *,
    conta_codigo: str | None = None,
    perfil_iva: str | None = None,
    usar_ia: bool = False,
    utilizador_id: int | None = None,
    hoje: date | None = None,
) -> Lancamento:
    """Cria o lançamento da fatura (diário de compras) e, se já estiver paga, o do pagamento."""
    verificar_fatura_contabilizavel(fatura)
    if lancamento_ativo(db, empresa.id, fatura.id, "fatura"):
        raise ErroContabilistico("Esta fatura já está contabilizada.")
    plano_snc.garantir_plano(db, empresa.id)

    sugestao = classificacao.classificar(db, empresa.id, fatura, usar_ia=usar_ia)
    escolhida_pelo_utilizador = conta_codigo is not None or perfil_iva is not None
    conta_codigo = conta_codigo or sugestao.conta_codigo
    perfil_iva = perfil_iva or sugestao.perfil_iva
    if perfil_iva not in iva.PERFIS:
        raise ErroContabilistico("Perfil de IVA desconhecido.")
    if not classificacao.conta_valida_para_gasto(plano_snc.obter_conta(db, empresa.id, conta_codigo)):
        raise ErroContabilistico(
            f"A conta {conta_codigo} não serve para lançar uma compra: escolha uma conta de movimento das classes "
            "3 (compras), 4 (investimentos) ou 6 (gastos)."
        )

    notas = [] if escolhida_pelo_utilizador else [sugestao.explicacao]
    perfil = iva.PERFIS[perfil_iva]
    if perfil.dedutivel < 1 and fatura.total_iva:
        notas.append(perfil.explicacao)
    if perfil.tributacao_autonoma:
        notas.append(perfil.tributacao_autonoma)

    lancamento = criar(
        db, empresa, "COM", fatura.data_emissao,
        f"{fatura.nome_fornecedor or 'Fornecedor'} - {documento_fatura(fatura)}",
        linhas_fatura(db, empresa.id, fatura, conta_codigo, perfil_iva),
        documento=documento_fatura(fatura), origem="fatura", fatura_id=fatura.id,
        notas=list(dict.fromkeys(notas)), utilizador_id=utilizador_id,
    )
    classificacao.aprender(db, empresa.id, fatura.nif_fornecedor, conta_codigo, perfil_iva,
                           "utilizador" if escolhida_pelo_utilizador else sugestao.origem)

    if fatura.paga:
        hoje = hoje or date.today()
        data = fatura.data_vencimento if fatura.data_vencimento and fatura.data_vencimento <= hoje else hoje
        data = max(data, fatura.data_emissao)
        if empresa.fechado_ate and data <= empresa.fechado_ate:
            data = hoje
        registar_pagamento(db, empresa, fatura, data, utilizador_id=utilizador_id,
                           nota=f"Data do pagamento assumida ({data:%d/%m/%Y}): a fatura já estava marcada como paga.")
    return lancamento


def registar_pagamento(db: Session, empresa: Empresa, fatura: Fatura, data: date,
                       conta_financeira: str = "121", utilizador_id: int | None = None,
                       nota: str | None = None) -> Lancamento:
    """Pagamento ao fornecedor: debita a subconta do fornecedor e credita o banco (121) ou a caixa (111)."""
    compra = lancamento_ativo(db, empresa.id, fatura.id, "fatura")
    if compra is None:
        raise ErroContabilistico("Contabilize a fatura antes de registar o pagamento.")
    if lancamento_ativo(db, empresa.id, fatura.id, "pagamento"):
        raise ErroContabilistico("O pagamento desta fatura já está registado.")
    conta = plano_snc.obter_conta(db, empresa.id, conta_financeira)
    if conta is None or not conta.movimento or not conta_financeira.startswith(("11", "12", "13")):
        raise ErroContabilistico(
            "O pagamento tem de sair de uma conta de movimento da caixa (11x) ou de bancos (12x ou 13x)."
        )
    if data < fatura.data_emissao:
        raise ErroContabilistico("A data do pagamento é anterior à da fatura.")

    linha_fornecedor = next(ln for ln in compra.linhas if ln.conta.codigo.startswith(("2211", "2711")))
    valor = linha_fornecedor.credito or linha_fornecedor.debito
    nota_credito = bool(linha_fornecedor.debito)  # numa nota de crédito o fornecedor ficou a débito
    linhas = [
        LinhaNova(linha_fornecedor.conta.codigo, debito=0 if nota_credito else valor,
                  credito=valor if nota_credito else 0, descricao=fatura.nome_fornecedor),
        LinhaNova(conta_financeira, debito=valor if nota_credito else 0, credito=0 if nota_credito else valor),
    ]
    return criar(
        db, empresa, "CX" if conta_financeira.startswith("11") else "BAN", data,
        f"{'Recebimento' if nota_credito else 'Pagamento'} {fatura.nome_fornecedor or ''} - {documento_fatura(fatura)}",
        linhas, documento=documento_fatura(fatura), origem="pagamento", fatura_id=fatura.id,
        notas=[nota] if nota else None, utilizador_id=utilizador_id,
    )


def anular_pagamento(db: Session, empresa: Empresa, fatura: Fatura, utilizador_id: int | None = None) -> None:
    pagamento = lancamento_ativo(db, empresa.id, fatura.id, "pagamento")
    if pagamento:
        estornar(db, empresa, pagamento, utilizador_id)


# ---------- Apuramento do IVA ----------

def apurar_iva(db: Session, empresa: Empresa, ate: date, descricao_periodo: str,
               utilizador_id: int | None = None) -> Lancamento:
    """
    Transfere os saldos de IVA dedutível (2432x), liquidado (2433x) e regularizações (2434)
    para a conta de apuramento (2435), soma o crédito de períodos anteriores (2437) e
    deixa o resultado em IVA a pagar (2436) ou IVA a recuperar (2437).
    """
    saldos = relatorios.saldos_por_conta(db, empresa.id, ate=ate)

    linhas: list[LinhaNova] = []
    a_favor_empresa = a_favor_estado = 0.0
    for codigo in sorted(saldos):
        saldo = saldos[codigo]  # devedor positivo, credor negativo
        if abs(saldo) < TOLERANCIA or not codigo.startswith(("2432", "2433", "2434", "2437")):
            continue
        if saldo > 0:  # saldo devedor: crédito do sujeito passivo sobre o Estado
            linhas.append(LinhaNova(codigo, credito=saldo, descricao="Transferência para apuramento"))
            a_favor_empresa += saldo
        else:          # saldo credor: imposto a entregar
            linhas.append(LinhaNova(codigo, debito=-saldo, descricao="Transferência para apuramento"))
            a_favor_estado += -saldo
    # Só o crédito de períodos anteriores (2437) não chega para haver um apuramento novo
    if all(ln.conta_codigo == "2437" for ln in linhas):
        raise ErroContabilistico(f"Não há IVA por apurar até {ate:%d/%m/%Y}.")

    a_favor_empresa, a_favor_estado = round(a_favor_empresa, 2), round(a_favor_estado, 2)
    linhas.append(LinhaNova("2435", debito=a_favor_empresa, descricao="IVA a favor da empresa"))
    linhas.append(LinhaNova("2435", credito=a_favor_estado, descricao="IVA a favor do Estado"))
    resultado = round(a_favor_estado - a_favor_empresa, 2)
    if resultado > 0:
        linhas += [LinhaNova("2435", debito=resultado, descricao="Saldo do apuramento"),
                   LinhaNova("2436", credito=resultado, descricao="IVA a pagar")]
    elif resultado < 0:
        linhas += [LinhaNova("2437", debito=-resultado, descricao="IVA a recuperar / reportar"),
                   LinhaNova("2435", credito=-resultado, descricao="Saldo do apuramento")]

    return criar(db, empresa, "OD", ate, f"Apuramento do IVA - {descricao_periodo}", linhas,
                 origem="apuramento", utilizador_id=utilizador_id)
