"""
Relatórios contabilísticos: balancete, extrato de conta (razão), diário, mapa de apoio
à declaração periódica do IVA, demonstração de resultados por naturezas e contas
correntes de fornecedores com antiguidade de saldos.

Convenção de sinal: saldo = débitos - créditos (positivo = devedor, negativo = credor).

Os lançamentos estornados continuam a contar, tal como os seus estornos: anulam-se um
ao outro, e o histórico fica completo (é assim que um auditor espera encontrá-lo).
"""

import re
from collections import defaultdict
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.modelos.tabelas import (
    ESTADO_CONFIRMADA,
    LANCAMENTO_ATIVO,
    VENDA_NORMAL,
    Conta,
    Fatura,
    FaturaVenda,
    Lancamento,
    LinhaLancamento,
)
from app.servicos.contabilidade import plano_snc
from app.servicos.contabilidade.iva import (
    CAMPO_AUTOLIQUIDACAO_INTERNA,
    CAMPOS_AUTOLIQUIDADO,
    CAMPOS_DEDUTIVEL,
    CAMPOS_LIQUIDADO,
    CODIGOS_FORA_DA_DECLARACAO,
    campo_isencao,
    escalao,
)

# Gastos e rendimentos recomeçam em cada exercício. A classe 8 não: o resultado líquido (818) só passa
# para resultados transitados (56) no exercício seguinte, por isso transita como as contas de balanço.
CLASSES_RESULTADOS = ("6", "7")


def _r(valor: float) -> float:
    return round(valor + 0.0, 2)  # + 0.0 evita "-0.0" nas respostas


def movimentos_por_conta(
    db: Session, empresa_id: int, de: date | None = None, ate: date | None = None, excluir_origem: str | None = None
) -> dict[str, tuple[float, float]]:
    """{código: (total a débito, total a crédito)} das contas de movimento, no intervalo de datas."""
    consulta = (
        select(Conta.codigo, func.sum(LinhaLancamento.debito), func.sum(LinhaLancamento.credito))
        .join(LinhaLancamento.conta)
        .join(LinhaLancamento.lancamento)
        .where(Lancamento.empresa_id == empresa_id)
        .group_by(Conta.codigo)
    )
    if de:
        consulta = consulta.where(Lancamento.data >= de)
    if ate:
        consulta = consulta.where(Lancamento.data <= ate)
    if excluir_origem:
        consulta = consulta.where(Lancamento.origem != excluir_origem)
    return {codigo: (debito or 0.0, credito or 0.0) for codigo, debito, credito in db.execute(consulta)}


def saldos_por_conta(db: Session, empresa_id: int, ate: date | None = None, de: date | None = None) -> dict[str, float]:
    return {c: _r(d - cr) for c, (d, cr) in movimentos_por_conta(db, empresa_id, de, ate).items()}


def _antepassados(codigo: str, codigos: set[str]) -> list[str]:
    return [codigo[:n] for n in range(1, len(codigo)) if codigo[:n] in codigos]


# ---------- Balancete ----------

def balancete(db: Session, empresa_id: int, de: date, ate: date) -> dict:
    """
    Balancete analítico: movimentos do período e acumulados, saldos devedores e credores.
    Acumulado: nas classes 1 a 5 e 8 desde o início; nas classes 6 e 7 (gastos e rendimentos)
    desde 1 de janeiro do ano de "ate", porque os resultados recomeçam em cada exercício.
    """
    plano_snc.garantir_plano(db, empresa_id)
    contas = plano_snc.contas_por_codigo(db, empresa_id)
    codigos = set(contas)
    periodo = movimentos_por_conta(db, empresa_id, de, ate)
    historico = movimentos_por_conta(db, empresa_id, None, ate)
    exercicio = movimentos_por_conta(db, empresa_id, date(ate.year, 1, 1), ate)

    linhas: dict[str, dict] = {}

    def somar(codigo: str, chave: str, valor: float) -> None:
        linha = linhas.setdefault(codigo, {"debito_periodo": 0.0, "credito_periodo": 0.0,
                                           "debito_acumulado": 0.0, "credito_acumulado": 0.0})
        linha[chave] += valor

    for codigo in set(periodo) | set(historico):
        acumulado = exercicio if codigo.startswith(CLASSES_RESULTADOS) else historico
        d_per, c_per = periodo.get(codigo, (0.0, 0.0))
        d_acu, c_acu = acumulado.get(codigo, (0.0, 0.0))
        if not any((d_per, c_per, d_acu, c_acu)):
            continue
        for alvo in [codigo, *_antepassados(codigo, codigos)]:
            somar(alvo, "debito_periodo", d_per)
            somar(alvo, "credito_periodo", c_per)
            somar(alvo, "debito_acumulado", d_acu)
            somar(alvo, "credito_acumulado", c_acu)

    resultado = []
    for codigo in sorted(linhas):
        valores = linhas[codigo]
        saldo = valores["debito_acumulado"] - valores["credito_acumulado"]
        conta = contas.get(codigo)
        resultado.append({
            "codigo": codigo,
            "nome": conta.nome if conta else "",
            "nivel": len(codigo),
            "movimento": bool(conta and conta.movimento),
            **{k: _r(v) for k, v in valores.items()},
            "saldo_devedor": _r(max(saldo, 0)),
            "saldo_credor": _r(max(-saldo, 0)),
        })

    # Totais: só as classes (nível 1), para não contar o mesmo valor várias vezes
    classes = [ln for ln in resultado if ln["nivel"] == 1]
    totais = {k: _r(sum(ln[k] for ln in classes)) for k in (
        "debito_periodo", "credito_periodo", "debito_acumulado", "credito_acumulado", "saldo_devedor", "saldo_credor")}
    return {
        "de": de, "ate": ate, "linhas": resultado, "totais": totais,
        "equilibrado": abs(totais["debito_acumulado"] - totais["credito_acumulado"]) < 0.01,
    }


# ---------- Extrato de conta ----------

def extrato(db: Session, empresa_id: int, codigo: str, de: date, ate: date) -> dict:
    """Movimentos de uma conta (e das suas subcontas), com saldo acumulado linha a linha."""
    conta = plano_snc.obter_conta(db, empresa_id, codigo)
    filtro_conta = (Conta.empresa_id == empresa_id) & (Conta.codigo.like(f"{codigo}%"))

    anterior = db.execute(
        select(func.sum(LinhaLancamento.debito), func.sum(LinhaLancamento.credito))
        .join(LinhaLancamento.conta).join(LinhaLancamento.lancamento)
        .where(filtro_conta, Lancamento.data < de)
    ).one()
    saldo = (anterior[0] or 0.0) - (anterior[1] or 0.0)
    saldo_inicial = saldo

    linhas = db.execute(
        select(LinhaLancamento, Lancamento, Conta.codigo)
        .join(LinhaLancamento.conta).join(LinhaLancamento.lancamento)
        .where(filtro_conta, Lancamento.data >= de, Lancamento.data <= ate)
        .order_by(Lancamento.data, Lancamento.diario, Lancamento.numero, LinhaLancamento.ordem)
    ).all()
    movimentos = []
    total_debito = total_credito = 0.0
    for linha, lancamento, codigo_linha in linhas:
        saldo += linha.debito - linha.credito
        total_debito += linha.debito
        total_credito += linha.credito
        movimentos.append({
            "lancamento_id": lancamento.id,
            "data": lancamento.data,
            "referencia": f"{lancamento.diario} {lancamento.ano}/{lancamento.numero}",
            "conta": codigo_linha,
            "descricao": linha.descricao or lancamento.descricao,
            "documento": lancamento.documento,
            "debito": _r(linha.debito),
            "credito": _r(linha.credito),
            "saldo": _r(saldo),
            "estornado": lancamento.estado == "estornado" or bool(lancamento.estorno_de_id),
        })
    return {
        "codigo": codigo, "nome": conta.nome if conta else "", "de": de, "ate": ate,
        "saldo_inicial": _r(saldo_inicial), "total_debito": _r(total_debito), "total_credito": _r(total_credito),
        "saldo_final": _r(saldo), "movimentos": movimentos,
    }


# ---------- Diário ----------

def diario(db: Session, empresa_id: int, de: date | None = None, ate: date | None = None,
           diario_codigo: str | None = None, pesquisa: str | None = None, limite: int = 500) -> list[Lancamento]:
    consulta = (
        select(Lancamento)
        .options(selectinload(Lancamento.linhas).joinedload(LinhaLancamento.conta), selectinload(Lancamento.criado_por))
        .where(Lancamento.empresa_id == empresa_id)
        .order_by(Lancamento.data.desc(), Lancamento.id.desc())
        .limit(limite)
    )
    if de:
        consulta = consulta.where(Lancamento.data >= de)
    if ate:
        consulta = consulta.where(Lancamento.data <= ate)
    if diario_codigo:
        consulta = consulta.where(Lancamento.diario == diario_codigo)
    if pesquisa:
        termo = f"%{pesquisa.strip()}%"
        consulta = consulta.where(Lancamento.descricao.ilike(termo) | Lancamento.documento.ilike(termo))
    return list(db.scalars(consulta))


# ---------- IVA ----------

def mapa_iva(db: Session, empresa_id: int, de: date, ate: date) -> dict:
    """
    Valores para a declaração periódica (quadro 06), a partir dos lançamentos do período.
    O lançamento de apuramento não entra (só transfere saldos entre contas de IVA).
    """
    movimentos = movimentos_por_conta(db, empresa_id, de, ate, excluir_origem="apuramento")

    dedutivel = []
    for conta, (campo, descricao) in sorted(CAMPOS_DEDUTIVEL.items(), key=lambda i: int(i[1][0])):
        d, c = movimentos.get(conta, (0.0, 0.0))
        dedutivel.append({"campo": campo, "descricao": descricao, "conta": conta, "valor": _r(d - c)})

    # Bases e imposto liquidado (vendas), pelas linhas marcadas com a taxa
    bases_liquidado: dict[str, float] = defaultdict(float)
    bases_autoliquidadas: dict[str, float] = defaultdict(float)
    nao_dedutivel = 0.0
    linhas = db.execute(
        select(LinhaLancamento.tipo_iva, LinhaLancamento.taxa_iva, LinhaLancamento.debito, LinhaLancamento.credito,
               Conta.codigo)
        .join(LinhaLancamento.conta).join(LinhaLancamento.lancamento)
        .where(Lancamento.empresa_id == empresa_id, Lancamento.data >= de, Lancamento.data <= ate,
               Lancamento.origem != "apuramento", LinhaLancamento.tipo_iva.is_not(None))
    ).all()
    isentas: dict[str, float] = defaultdict(float)
    por_classificar: dict[str, float] = defaultdict(float)
    linhas_isentas = db.execute(
        select(LinhaLancamento.descricao, LinhaLancamento.debito, LinhaLancamento.credito, FaturaVenda.cliente_nif)
        .join(LinhaLancamento.conta).join(LinhaLancamento.lancamento)
        .outerjoin(FaturaVenda, FaturaVenda.id == Lancamento.venda_id)
        .where(Lancamento.empresa_id == empresa_id, Lancamento.data >= de, Lancamento.data <= ate,
               Lancamento.origem != "apuramento", LinhaLancamento.tipo_iva == "base",
               func.coalesce(LinhaLancamento.taxa_iva, 0) == 0, Conta.codigo.like("7%"))
    ).all()
    for descricao, debito, credito, cliente_nif in linhas_isentas:
        achado = re.search(r"\b(M\d{2})\b", descricao or "")
        codigo = achado.group(1) if achado else None
        if codigo in CODIGOS_FORA_DA_DECLARACAO:
            continue
        campo = campo_isencao(codigo, cliente_nif)
        if campo:
            isentas[campo] += credito - debito
        else:
            por_classificar[codigo or "sem código"] += credito - debito

    for tipo, taxa, debito, credito, codigo in linhas:
        if tipo == "iva_nd":
            nao_dedutivel += debito - credito
        elif tipo == "base" and codigo.startswith("7") and taxa:
            bases_liquidado[escalao(taxa)] += credito - debito
        elif tipo in ("base_ib", "base_is", "base_al"):
            bases_autoliquidadas[tipo] += debito - credito
            if tipo == "base_al" and taxa:  # inversão do sujeito passivo interna: também nos campos 1 a 6
                bases_liquidado[escalao(taxa)] += debito - credito

    liquidado = []
    for conta, (campo_base, campo_imposto, descricao) in sorted(CAMPOS_LIQUIDADO.items(), key=lambda i: int(i[1][0])):
        d, c = movimentos.get(conta, (0.0, 0.0))
        nome_escalao = {"24331": "reduzida", "24332": "intermedia", "24333": "normal"}[conta]
        liquidado.append({
            "campo_base": campo_base, "campo_imposto": campo_imposto, "descricao": descricao, "conta": conta,
            "base": _r(bases_liquidado.get(nome_escalao, 0.0)), "imposto": _r(c - d),
        })

    autoliquidado = []
    for conta, (campo_base, campo_imposto, descricao) in CAMPOS_AUTOLIQUIDADO.items():
        d, c = movimentos.get(conta, (0.0, 0.0))
        tipo = "base_ib" if conta == "24334" else "base_is"
        autoliquidado.append({"campo_base": campo_base, "campo_imposto": campo_imposto, "descricao": descricao,
                              "conta": conta, "base": _r(bases_autoliquidadas.get(tipo, 0.0)), "imposto": _r(c - d)})

    regularizacoes_d, regularizacoes_c = movimentos.get("2434", (0.0, 0.0))
    total_dedutivel = _r(sum(ln["valor"] for ln in dedutivel) + regularizacoes_d)
    total_liquidado = _r(sum(ln["imposto"] for ln in liquidado) + sum(ln["imposto"] for ln in autoliquidado)
                         + regularizacoes_c)
    return {
        "de": de, "ate": ate,
        "dedutivel": dedutivel, "liquidado": liquidado, "autoliquidado": autoliquidado,
        "isentas": {c: _r(isentas.get(c, 0.0)) for c in ("7", "8", "9")},
        "isentas_por_classificar": [{"codigo": c, "base": _r(v)} for c, v in sorted(por_classificar.items()) if _r(v)],
        "quadro_06a": {"campo": CAMPO_AUTOLIQUIDACAO_INTERNA[0], "descricao": CAMPO_AUTOLIQUIDACAO_INTERNA[1],
                       "base": _r(bases_autoliquidadas.get("base_al", 0.0))},
        "total_dedutivel": total_dedutivel, "total_liquidado": total_liquidado,
        "iva_nao_dedutivel": _r(nao_dedutivel),
        "saldo": _r(total_liquidado - total_dedutivel),  # positivo: a pagar; negativo: a recuperar
    }


# ---------- Demonstração de resultados ----------

RUBRICAS = [
    # (rubrica, prefixos, sinal: +1 rendimento, -1 gasto)
    ("Vendas e serviços prestados", ("71", "72"), 1),
    ("Compras e CMVMC", ("61", "31"), -1),
    ("Fornecimentos e serviços externos", ("62",), -1),
    ("Gastos com o pessoal", ("63",), -1),
    ("Outros rendimentos", ("78",), 1),
    ("Outros gastos", ("68",), -1),
    ("Gastos de depreciação e de amortização", ("64",), -1),
    ("Juros e rendimentos similares obtidos", ("79",), 1),
    ("Juros e gastos similares suportados", ("69",), -1),
]


def demonstracao_resultados(db: Session, empresa_id: int, de: date, ate: date) -> dict:
    """
    Demonstração de resultados por naturezas, simplificada. Sem inventário final, as
    compras do período (31) contam como custo das mercadorias e matérias consumidas.
    """
    plano_snc.garantir_plano(db, empresa_id)
    contas = plano_snc.contas_por_codigo(db, empresa_id)
    # O apuramento de resultados do fecho do exercício salda as classes 6 e 7: não entra aqui
    movimentos = movimentos_por_conta(db, empresa_id, de, ate, excluir_origem="fecho")

    rubricas = []
    resultado = 0.0
    for nome, prefixos, sinal in RUBRICAS:
        valor = sum((c - d) for codigo, (d, c) in movimentos.items() if codigo.startswith(prefixos))
        valor = _r(valor if sinal > 0 else -valor)
        resultado += valor if sinal > 0 else -valor
        rubricas.append({"rubrica": nome, "valor": valor, "tipo": "rendimento" if sinal > 0 else "gasto"})

    # Detalhe dos FSE por conta de 4 dígitos (para o gráfico)
    fse: dict[str, float] = defaultdict(float)
    for codigo, (d, c) in movimentos.items():
        if codigo.startswith("62"):
            fse[codigo[:4]] += d - c
    detalhe_fse = sorted(
        ({"conta": k, "nome": contas[k].nome if k in contas else k, "valor": _r(v)} for k, v in fse.items() if v),
        key=lambda x: x["valor"], reverse=True,
    )
    return {"de": de, "ate": ate, "rubricas": rubricas, "resultado": _r(resultado), "detalhe_fse": detalhe_fse}


# ---------- Contas correntes de fornecedores ----------

ESCALOES_ANTIGUIDADE = [("a_vencer", None), ("ate_30", 30), ("ate_60", 60), ("ate_90", 90), ("mais_90", None)]


def contas_correntes(db: Session, empresa_id: int, ate: date, hoje: date | None = None) -> dict:
    """Saldo de cada fornecedor e antiguidade da dívida (pelas faturas confirmadas por pagar)."""
    hoje = hoje or date.today()
    contas = {c.codigo: c for c in db.scalars(select(Conta).where(
        Conta.empresa_id == empresa_id, Conta.codigo.like("2211%") | Conta.codigo.like("2711%"), Conta.movimento
    ))}
    saldos = saldos_por_conta(db, empresa_id, ate=ate)

    por_pagar = db.scalars(select(Fatura).where(
        Fatura.empresa_id == empresa_id, Fatura.estado == ESTADO_CONFIRMADA, Fatura.paga.is_(False),
        Fatura.total.is_not(None),
    )).all()
    antiguidade: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for f in por_pagar:
        vencimento = f.data_vencimento or f.data_emissao or hoje
        dias = (hoje - vencimento).days
        if dias <= 0:
            escalao_ = "a_vencer"
        elif dias <= 30:
            escalao_ = "ate_30"
        elif dias <= 60:
            escalao_ = "ate_60"
        elif dias <= 90:
            escalao_ = "ate_90"
        else:
            escalao_ = "mais_90"
        antiguidade[f.nif_fornecedor or ""][escalao_] += f.total

    linhas = []
    for codigo, conta in sorted(contas.items()):
        saldo = saldos.get(codigo, 0.0)
        idade = antiguidade.get(conta.nif or "", {})
        if abs(saldo) < 0.005 and not idade:
            continue
        linhas.append({
            "conta": codigo, "nome": conta.nome, "nif": conta.nif,
            "saldo": _r(-saldo),  # positivo: a empresa deve ao fornecedor
            "antiguidade": {nome: _r(idade.get(nome, 0.0)) for nome, _ in ESCALOES_ANTIGUIDADE},
        })
    linhas.sort(key=lambda ln: ln["saldo"], reverse=True)
    return {
        "ate": ate,
        "linhas": linhas,
        "total": _r(sum(ln["saldo"] for ln in linhas)),
        "antiguidade": {nome: _r(sum(ln["antiguidade"][nome] for ln in linhas)) for nome, _ in ESCALOES_ANTIGUIDADE},
    }


def _escalao_antiguidade(dias: int) -> str:
    if dias <= 0:
        return "a_vencer"
    if dias <= 30:
        return "ate_30"
    if dias <= 60:
        return "ate_60"
    if dias <= 90:
        return "ate_90"
    return "mais_90"


def contas_correntes_clientes(db: Session, empresa_id: int, ate: date, hoje: date | None = None) -> dict:
    """Saldo de cada cliente (2111xxxx) e antiguidade do que está por receber."""
    hoje = hoje or date.today()
    contas = {c.codigo: c for c in db.scalars(select(Conta).where(
        Conta.empresa_id == empresa_id, Conta.codigo.like("2111%"), Conta.movimento
    ))}
    saldos = saldos_por_conta(db, empresa_id, ate=ate)
    contabilizadas = select(Lancamento.venda_id).where(
        Lancamento.empresa_id == empresa_id, Lancamento.origem == "venda", Lancamento.estado == LANCAMENTO_ATIVO,
        Lancamento.venda_id.is_not(None),
    )
    por_receber = db.scalars(select(FaturaVenda).where(
        FaturaVenda.empresa_id == empresa_id, FaturaVenda.estado == VENDA_NORMAL, FaturaVenda.recebida.is_(False),
        FaturaVenda.tipo != "NC", FaturaVenda.id.in_(contabilizadas),
    )).all()
    antiguidade: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for v in por_receber:
        vencimento = v.data_vencimento or v.data
        antiguidade[v.cliente_nif][_escalao_antiguidade((hoje - vencimento).days)] += v.total - v.valor_recebido

    linhas = []
    for codigo, conta in sorted(contas.items()):
        saldo = saldos.get(codigo, 0.0)
        idade = antiguidade.get(conta.nif or "", {}) if conta.nif else {}
        if abs(saldo) < 0.005 and not idade:
            continue
        linhas.append({
            "conta": codigo, "nome": conta.nome, "nif": conta.nif, "saldo": _r(saldo),  # positivo: o cliente deve
            "antiguidade": {nome: _r(idade.get(nome, 0.0)) for nome, _ in ESCALOES_ANTIGUIDADE},
        })
    linhas.sort(key=lambda ln: ln["saldo"], reverse=True)
    return {
        "ate": ate, "linhas": linhas, "total": _r(sum(ln["saldo"] for ln in linhas)),
        "antiguidade": {nome: _r(sum(ln["antiguidade"][nome] for ln in linhas)) for nome, _ in ESCALOES_ANTIGUIDADE},
    }


# ---------- Balanço (demonstração da posição financeira) ----------

# (secção, rubrica, prefixos, lado): lado "devedor"/"credor" separa as contas de terceiros pelo
# saldo de cada subconta (ex.: um fornecedor com saldo devedor é um adiantamento, não uma dívida).
RUBRICAS_BALANCO = [
    ("ativo_nao_corrente", "Ativos fixos tangíveis", ("43",), None),
    ("ativo_nao_corrente", "Propriedades de investimento", ("42",), None),
    ("ativo_nao_corrente", "Ativos intangíveis", ("44",), None),
    ("ativo_nao_corrente", "Investimentos financeiros", ("41",), None),
    ("ativo_nao_corrente", "Outros créditos a receber", ("2682", "2782"), "devedor"),
    ("ativo_corrente", "Inventários", ("32", "33", "34", "35", "36", "37", "38", "39"), None),
    ("ativo_corrente", "Clientes", ("21",), "devedor"),
    ("ativo_corrente", "Adiantamentos a fornecedores", ("22",), "devedor"),
    ("ativo_corrente", "Estado e outros entes públicos", ("24",), "devedor"),
    ("ativo_corrente", "Outros créditos a receber", ("23", "26", "27"), "devedor"),
    ("ativo_corrente", "Diferimentos", ("281",), None),
    ("ativo_corrente", "Caixa e depósitos bancários", ("11", "12", "13"), "devedor"),
    ("capital_proprio", "Capital subscrito", ("51",), None),
    ("capital_proprio", "Ações (quotas) próprias", ("52",), None),
    ("capital_proprio", "Outros instrumentos de capital próprio", ("53",), None),
    ("capital_proprio", "Prémios de emissão", ("54",), None),
    ("capital_proprio", "Reservas legais", ("551",), None),
    ("capital_proprio", "Outras reservas", ("552",), None),
    ("capital_proprio", "Excedentes de revalorização", ("58",), None),
    ("capital_proprio", "Outras variações no capital próprio", ("57", "59"), None),
    ("passivo_nao_corrente", "Provisões", ("29",), None),
    ("passivo_nao_corrente", "Financiamentos obtidos", ("25112",), "credor"),
    ("passivo_nao_corrente", "Outras dívidas a pagar", ("2682", "2782"), "credor"),
    ("passivo_corrente", "Fornecedores", ("22",), "credor"),
    ("passivo_corrente", "Adiantamentos de clientes", ("21",), "credor"),
    ("passivo_corrente", "Estado e outros entes públicos", ("24",), "credor"),
    ("passivo_corrente", "Financiamentos obtidos", ("25", "11", "12", "13"), "credor"),
    ("passivo_corrente", "Outras dívidas a pagar", ("23", "26", "27"), "credor"),
    ("passivo_corrente", "Diferimentos", ("282",), None),
]
SECOES_BALANCO = {
    "ativo_nao_corrente": "Ativo não corrente", "ativo_corrente": "Ativo corrente",
    "capital_proprio": "Capital próprio", "passivo_nao_corrente": "Passivo não corrente",
    "passivo_corrente": "Passivo corrente",
}
CONTAS_DE_RESULTADOS = ("6", "7", "31", "8")


def balanco(db: Session, empresa_id: int, ate: date) -> dict:
    """
    Balanço a uma data, a partir dos saldos acumulados. O resultado líquido do período é o do
    exercício em curso (antes de impostos enquanto o exercício não for fechado); os resultados
    de exercícios anteriores ainda não transferidos para a 56 juntam-se aos resultados transitados.
    """
    saldos = saldos_por_conta(db, empresa_id, ate=ate)
    do_ano = movimentos_por_conta(db, empresa_id, date(ate.year, 1, 1), ate, excluir_origem="aplicacao")
    resultado_periodo = _r(-sum(d - c for codigo, (d, c) in do_ano.items() if codigo.startswith(CONTAS_DE_RESULTADOS)))
    resultados_acumulados = _r(-sum(v for c, v in saldos.items() if c.startswith(CONTAS_DE_RESULTADOS)))
    transitados = _r(-sum(v for c, v in saldos.items() if c.startswith("56")) + resultados_acumulados
                     - resultado_periodo)

    valores: dict[tuple[str, str], float] = defaultdict(float)
    for codigo, saldo in saldos.items():
        if not saldo or codigo.startswith(CONTAS_DE_RESULTADOS) or codigo.startswith("56"):
            continue
        lado = "devedor" if saldo > 0 else "credor"
        for secao, rubrica, prefixos, filtro in RUBRICAS_BALANCO:
            if not codigo.startswith(prefixos) or (filtro and filtro != lado):
                continue
            ativo = secao.startswith("ativo")
            valores[(secao, rubrica)] += saldo if ativo else -saldo
            break
        else:
            # Conta sem rubrica própria: vai para outros créditos ou outras dívidas, conforme o saldo
            chave = ("ativo_corrente", "Outros créditos a receber") if saldo > 0 else \
                ("passivo_corrente", "Outras dívidas a pagar")
            valores[chave] += saldo if saldo > 0 else -saldo
    valores[("capital_proprio", "Resultados transitados")] += transitados
    valores[("capital_proprio", "Resultado líquido do período")] += resultado_periodo

    ordem = [(s, r) for s, r, _, _ in RUBRICAS_BALANCO]
    ordem.insert(ordem.index(("capital_proprio", "Outras variações no capital próprio")) + 1,
                 ("capital_proprio", "Resultados transitados"))
    ordem.insert(ordem.index(("capital_proprio", "Resultados transitados")) + 1,
                 ("capital_proprio", "Resultado líquido do período"))
    secoes = []
    for chave, nome in SECOES_BALANCO.items():
        vistas = set()
        rubricas = []
        for s, r in ordem:
            if s != chave or r in vistas:
                continue
            vistas.add(r)
            v = _r(valores.get((s, r), 0.0))
            if v:
                rubricas.append({"rubrica": r, "valor": v})
        secoes.append({"chave": chave, "nome": nome, "rubricas": rubricas,
                       "total": _r(sum(x["valor"] for x in rubricas))})
    por_chave = {s["chave"]: s["total"] for s in secoes}
    total_ativo = _r(por_chave["ativo_nao_corrente"] + por_chave["ativo_corrente"])
    total_passivo = _r(por_chave["passivo_nao_corrente"] + por_chave["passivo_corrente"])
    capital = por_chave["capital_proprio"]
    return {
        "data": ate, "secoes": secoes, "total_ativo": total_ativo, "total_capital_proprio": capital,
        "total_passivo": total_passivo, "total_capital_proprio_passivo": _r(capital + total_passivo),
        "diferenca": _r(total_ativo - capital - total_passivo), "resultado_periodo": resultado_periodo,
        "exercicio_fechado": bool(db.scalar(select(func.count(Lancamento.id)).where(
            Lancamento.empresa_id == empresa_id, Lancamento.origem == "fecho", Lancamento.estado == "ativo",
            Lancamento.estorno_de_id.is_(None), Lancamento.data == date(ate.year, 12, 31)))) and ate.month == 12
            and ate.day == 31,
    }
