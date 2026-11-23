"""
Previsão de tesouraria (próximas semanas), a partir do que já está no Dobra:

    saldo de hoje      caixa e bancos na contabilidade (contas 11, 12 e 13)
  + recebimentos       vendas por receber, na data de vencimento
  - pagamentos         faturas de fornecedores confirmadas por pagar, na data de vencimento
  - salários           líquido no fim de cada mês; Segurança Social e IRS retido até dia 20 do mês seguinte
                       (estimados pelas fichas das pessoas ativas, para os meses ainda não pagos)
  - IVA                saldo a pagar de cada período de IVA ainda não vencido, no prazo de pagamento

Os valores já vencidos entram na primeira semana (o dinheiro ainda não entrou nem saiu).
"""

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modelos.tabelas import (
    ESTADO_CONFIRMADA,
    LANCAMENTO_ATIVO,
    VENDA_NORMAL,
    Empresa,
    Fatura,
    FaturaVenda,
    Lancamento,
)
from app.servicos.contabilidade import obrigacoes, relatorios, salarios

PRAZO_OMISSAO = 30
PREFIXOS_DISPONIVEL = ("11", "12", "13")


def _r(v: float) -> float:
    return round(v + 0.0, 2)


def _euros(v: float) -> str:
    """1234.5 -> "1234,50 €" (como no resto da aplicação)."""
    return f"{v:.2f} €".replace(".", ",")


def saldo_disponivel(db: Session, empresa_id: int, ate: date) -> float:
    saldos = relatorios.saldos_por_conta(db, empresa_id, ate=ate)
    return _r(sum(v for c, v in saldos.items() if c.startswith(PREFIXOS_DISPONIVEL)))


def _vendas_por_receber(db: Session, empresa_id: int) -> list[FaturaVenda]:
    return list(db.scalars(select(FaturaVenda).where(
        FaturaVenda.empresa_id == empresa_id, FaturaVenda.estado == VENDA_NORMAL, FaturaVenda.recebida.is_(False),
        FaturaVenda.tipo != "NC")))


def _faturas_por_pagar(db: Session, empresa_id: int) -> list[Fatura]:
    return list(db.scalars(select(Fatura).where(
        Fatura.empresa_id == empresa_id, Fatura.estado == ESTADO_CONFIRMADA, Fatura.paga.is_(False),
        Fatura.total.is_not(None))))


def _salarios(db: Session, empresa_id: int, hoje: date, limite: date) -> list[dict]:
    """Saídas previstas de salários, Segurança Social e IRS, mês a mês, até ao limite."""
    itens = []
    ano, mes = hoje.year, hoje.month
    # Começa no mês anterior: a Segurança Social e o IRS desse mês ainda se pagam este mês
    ano, mes = (ano, mes - 1) if mes > 1 else (ano - 1, 12)
    while salarios.fim_do_mes(ano, mes) <= limite + timedelta(days=31):
        p = salarios.obter(db, empresa_id, ano, mes)
        pagos = salarios.estado_pagamentos(db, p) if salarios.processado(db, p) else {}
        if salarios.processado(db, p):
            t = salarios.totais(p.recibos)
        else:
            try:
                t = salarios.totais(salarios.calcular(db, empresa_id, ano, mes, salarios.opcoes_do_mes(mes, ano)))
            except salarios.ErroSalarios:
                t = None
        if t and t["iliquido"]:
            nome = salarios.nome_mes(ano, mes)
            for tipo, valor, descricao in (
                ("salarios", t["liquido"], f"Salários de {nome}"),
                ("seguranca_social", t["seguranca_social"], f"Segurança Social de {nome}"),
                ("irs", t["irs"], f"IRS retido de {nome}"),
            ):
                data = salarios.prazo_pagamento(ano, mes, tipo)
                if valor and not (pagos.get(tipo) or {}).get("pago") and hoje <= data <= limite:
                    itens.append({"data": data, "descricao": descricao, "valor": -_r(valor), "tipo": "salarios",
                                  "ligacao": "/contabilidade#salarios", "estimado": not salarios.processado(db, p)})
        ano, mes = (ano, mes + 1) if mes < 12 else (ano + 1, 1)
    return itens


def _iva(db: Session, empresa: Empresa, hoje: date, limite: date) -> list[dict]:
    """
    IVA a entregar: o já apurado e ainda não pago (saldo credor da 2436) no prazo do último período
    apurado; os períodos terminados ou em curso sem apuramento, pelo mapa do IVA.
    """
    itens = []
    passo = 1 if empresa.regime_iva == "mensal" else 3
    apurados = set(db.scalars(select(Lancamento.data).where(
        Lancamento.empresa_id == empresa.id, Lancamento.origem == "apuramento",
        Lancamento.estado == LANCAMENTO_ATIVO)))
    ultimo_prazo_apurado = None
    for desvio in range(-6, 2):
        total = hoje.year * 12 + (hoje.month - 1) + desvio
        a, m = total // 12, total % 12 + 1
        if passo == 3 and m % 3:
            continue
        fim = salarios.fim_do_mes(a, m)
        inicio = date(a, m, 1) if passo == 1 else date(a, m - 2, 1)
        prazo = next((o.prazo for o in obrigacoes._iva(a, m, empresa.regime_iva) if o.chave == "iva_pagamento"), None)
        if not prazo:
            continue
        if fim in apurados:
            ultimo_prazo_apurado = prazo
            continue
        if not hoje <= prazo <= limite:
            continue
        saldo = relatorios.mapa_iva(db, empresa.id, inicio, min(fim, hoje))["saldo"]
        if saldo > 0:
            periodo = f"{inicio:%m/%Y}" + (f" a {fim:%m/%Y}" if passo == 3 else "")
            itens.append({"data": prazo, "descricao": f"IVA a pagar ({periodo})", "valor": -_r(saldo), "tipo": "estado",
                          "ligacao": "/contabilidade#iva", "estimado": fim >= hoje})
    por_pagar = -relatorios.saldos_por_conta(db, empresa.id, ate=hoje).get("2436", 0.0)
    if por_pagar > 0.005:
        data = ultimo_prazo_apurado or hoje
        itens.append({"data": data, "descricao": "IVA apurado por pagar", "valor": -_r(por_pagar), "tipo": "estado",
                      "ligacao": "/contabilidade#iva", "vencido": data < hoje, "estimado": False})
    return itens


def previsao(db: Session, empresa: Empresa, hoje: date | None = None, semanas: int = 13) -> dict:
    hoje = hoje or date.today()
    inicio_semana = hoje - timedelta(days=hoje.weekday())
    limite = inicio_semana + timedelta(weeks=semanas, days=-1)
    itens: list[dict] = []
    for v in _vendas_por_receber(db, empresa.id):
        valor = _r(v.total - v.valor_recebido)
        if valor > 0:
            data = v.data_vencimento or v.data + timedelta(days=PRAZO_OMISSAO)
            itens.append({"data": data, "descricao": f"{v.numero} · {v.cliente_nome}", "valor": valor,
                          "tipo": "recebimento", "vencido": data < hoje, "ligacao": "/contabilidade#vendas"})
    for f in _faturas_por_pagar(db, empresa.id):
        data = f.data_vencimento or (f.data_emissao or hoje) + timedelta(days=PRAZO_OMISSAO)
        fornecedor = f.nome_fornecedor or f.nif_fornecedor
        itens.append({"data": data, "descricao": f"{f.numero_fatura or 'Fatura'} · {fornecedor}",
                      "valor": -_r(f.total), "tipo": "pagamento", "vencido": data < hoje,
                      "ligacao": f"/faturas/{f.id}"})
    itens += _salarios(db, empresa.id, hoje, limite)
    itens += _iva(db, empresa, hoje, limite)
    itens = [i for i in itens if i["data"] <= limite]

    saldo_inicial = saldo_disponivel(db, empresa.id, hoje)
    saldo = saldo_inicial
    semanas_lista = []
    for n in range(semanas):
        de = inicio_semana + timedelta(weeks=n)
        ate = de + timedelta(days=6)
        da_semana = [i for i in itens if (i["data"] < hoje if n == 0 else False) or de <= i["data"] <= ate]
        entradas = _r(sum(i["valor"] for i in da_semana if i["valor"] > 0))
        saidas = _r(-sum(i["valor"] for i in da_semana if i["valor"] < 0))
        saldo = _r(saldo + entradas - saidas)
        semanas_lista.append({"de": de, "ate": ate, "entradas": entradas, "saidas": saidas, "saldo": saldo})
    negativa = next((s for s in semanas_lista if s["saldo"] < 0), None)
    em_30 = next((s["saldo"] for s in semanas_lista if s["ate"] >= hoje + timedelta(days=30)), saldo)
    itens.sort(key=lambda i: i["data"])
    return {
        "hoje": hoje, "saldo_inicial": saldo_inicial, "saldo_final": saldo, "saldo_30_dias": em_30,
        "semanas": semanas_lista, "movimentos": itens[:150],
        "recebimentos_em_atraso": _r(sum(i["valor"] for i in itens if i["tipo"] == "recebimento" and i.get("vencido"))),
        "pagamentos_em_atraso": _r(-sum(i["valor"] for i in itens if i["tipo"] == "pagamento" and i.get("vencido"))),
        "alerta": (f"A tesouraria fica negativa na semana de {negativa['de']:%d/%m} ({_euros(negativa['saldo'])}). "
                   "Antecipe recebimentos, negoceie prazos com fornecedores ou reforce a conta.") if negativa else None,
    }
