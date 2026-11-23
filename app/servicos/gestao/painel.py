"""
Painel de gestão e clientes: o essencial para o empresário decidir, com ligações à contabilidade.

As vendas vêm das faturas emitidas registadas (SAF-T de faturação ou à mão); as compras, das
faturas de fornecedores confirmadas. Valores sem IVA; as notas de crédito abatem às vendas.
"""

from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modelos.tabelas import ESTADO_CONFIRMADA, VENDA_NORMAL, Empresa, Fatura, FaturaVenda
from app.servicos.contabilidade import relatorios
from app.servicos.gestao import orcamento, stock, tesouraria

NOMES_MESES_CURTOS = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]


def _r(v: float) -> float:
    return round(v + 0.0, 2)


def _vendas(db: Session, empresa_id: int, de: date, ate: date) -> list[FaturaVenda]:
    return list(db.scalars(select(FaturaVenda).where(
        FaturaVenda.empresa_id == empresa_id, FaturaVenda.estado == VENDA_NORMAL,
        FaturaVenda.data >= de, FaturaVenda.data <= ate)))


def _valor_venda(v: FaturaVenda) -> float:
    return -v.valor_sem_iva if v.tipo == "NC" else v.valor_sem_iva


def _compras(db: Session, empresa_id: int, de: date, ate: date) -> list[Fatura]:
    return list(db.scalars(select(Fatura).where(
        Fatura.empresa_id == empresa_id, Fatura.estado == ESTADO_CONFIRMADA,
        Fatura.data_emissao >= de, Fatura.data_emissao <= ate)))


def _somar_meses(ano: int, mes: int, n: int) -> tuple[int, int]:
    total = ano * 12 + (mes - 1) + n
    return total // 12, total % 12 + 1


def evolucao(db: Session, empresa_id: int, hoje: date, meses: int = 12) -> list[dict]:
    ano0, mes0 = _somar_meses(hoje.year, hoje.month, -(meses - 1))
    de = date(ano0, mes0, 1)
    vendas: dict[tuple[int, int], float] = defaultdict(float)
    compras: dict[tuple[int, int], float] = defaultdict(float)
    for v in _vendas(db, empresa_id, de, hoje):
        vendas[(v.data.year, v.data.month)] += _valor_venda(v)
    for f in _compras(db, empresa_id, de, hoje):
        compras[(f.data_emissao.year, f.data_emissao.month)] += f.valor_sem_iva or 0
    resultado = []
    for n in range(meses):
        a, m = _somar_meses(ano0, mes0, n)
        resultado.append({"ano": a, "mes": m, "rotulo": f"{NOMES_MESES_CURTOS[m - 1]} {str(a)[2:]}",
                          "vendas": _r(vendas[(a, m)]), "compras": _r(compras[(a, m)])})
    return resultado


# ---------- Clientes ----------

def clientes(db: Session, empresa_id: int, hoje: date | None = None) -> list[dict]:
    """Por cliente: faturado no ano, em dívida, vencido e atraso médio dos recebimentos em dívida."""
    hoje = hoje or date.today()
    inicio_ano = date(hoje.year, 1, 1)
    por_nif: dict[str, dict] = {}
    for v in db.scalars(select(FaturaVenda).where(FaturaVenda.empresa_id == empresa_id,
                                                  FaturaVenda.estado == VENDA_NORMAL).order_by(FaturaVenda.data)):
        c = por_nif.setdefault(v.cliente_nif, {
            "nif": v.cliente_nif, "nome": v.cliente_nome, "documentos": 0, "faturado_ano": 0.0, "faturado_total": 0.0,
            "em_divida": 0.0, "vencido": 0.0, "dias_atraso": 0, "ultimo_documento": None})
        c["nome"] = v.cliente_nome or c["nome"]
        c["documentos"] += 1
        valor = _valor_venda(v)
        c["faturado_total"] += valor
        if v.data >= inicio_ano:
            c["faturado_ano"] += valor
        c["ultimo_documento"] = v.data
        if v.tipo != "NC" and not v.recebida:
            divida = v.total - v.valor_recebido
            c["em_divida"] += divida
            vencimento = v.data_vencimento or v.data + timedelta(days=tesouraria.PRAZO_OMISSAO)
            if vencimento < hoje and divida > 0:
                c["vencido"] += divida
                c["dias_atraso"] = max(c["dias_atraso"], (hoje - vencimento).days)
    lista = [{**c, **{k: _r(c[k]) for k in ("faturado_ano", "faturado_total", "em_divida", "vencido")}}
             for c in por_nif.values()]
    lista.sort(key=lambda c: (c["faturado_ano"], c["faturado_total"]), reverse=True)
    total_ano = sum(c["faturado_ano"] for c in lista) or 0
    for c in lista:
        c["peso"] = round(c["faturado_ano"] / total_ano * 100, 1) if total_ano > 0 else 0.0
    return lista


def documentos_cliente(db: Session, empresa_id: int, nif: str) -> list[dict]:
    from app.servicos.contabilidade import vendas

    return [vendas.resumo_venda(v) for v in db.scalars(select(FaturaVenda).where(
        FaturaVenda.empresa_id == empresa_id, FaturaVenda.cliente_nif == nif).order_by(FaturaVenda.data.desc()))]


# ---------- Painel ----------

def painel(db: Session, empresa: Empresa, hoje: date | None = None) -> dict:
    hoje = hoje or date.today()
    inicio_mes = date(hoje.year, hoje.month, 1)
    a, m = _somar_meses(hoje.year, hoje.month, -1)
    inicio_anterior = date(a, m, 1)
    # Mês anterior até ao mesmo dia, para comparar períodos iguais
    dia_comparavel = min(hoje.day, (inicio_mes - timedelta(days=1)).day)
    vendas_mes = _r(sum(_valor_venda(v) for v in _vendas(db, empresa.id, inicio_mes, hoje)))
    vendas_anterior = _r(sum(_valor_venda(v) for v in _vendas(
        db, empresa.id, inicio_anterior, date(a, m, dia_comparavel))))
    vendas_ano = _r(sum(_valor_venda(v) for v in _vendas(db, empresa.id, date(hoje.year, 1, 1), hoje)))
    compras_mes = _r(sum(f.valor_sem_iva or 0 for f in _compras(db, empresa.id, inicio_mes, hoje)))

    previsao = tesouraria.previsao(db, empresa, hoje, semanas=6)
    lista_clientes = clientes(db, empresa.id, hoje)
    por_pagar = tesouraria._faturas_por_pagar(db, empresa.id)
    a_pagar = _r(sum(f.total for f in por_pagar))
    a_pagar_vencido = _r(sum(f.total for f in por_pagar
                             if (f.data_vencimento or (f.data_emissao or hoje) + timedelta(days=30)) < hoje))
    resultados = relatorios.demonstracao_resultados(db, empresa.id, date(hoje.year, 1, 1), hoje)
    stock_resumo = stock.resumo(db, empresa.id)
    margens = stock.margens(db, empresa.id, date(hoje.year, 1, 1), hoje)
    comparacao = orcamento.comparar(db, empresa.id, hoje.year, hoje)
    vendas_orc = next(r for r in comparacao["rubricas"] if r["chave"] == "vendas")

    # Fornecedores com mais compras no ano
    por_fornecedor: dict[str, float] = defaultdict(float)
    for f in _compras(db, empresa.id, date(hoje.year, 1, 1), hoje):
        por_fornecedor[f.nome_fornecedor or f.nif_fornecedor or "Sem nome"] += f.valor_sem_iva or 0
    top_fornecedores = [{"nome": n, "valor": _r(v)} for n, v in
                        sorted(por_fornecedor.items(), key=lambda x: x[1], reverse=True)[:5]]

    alertas = []
    if previsao["alerta"]:
        alertas.append({"nivel": "erro", "texto": previsao["alerta"], "ligacao": "#tesouraria"})
    vencido_clientes = _r(sum(c["vencido"] for c in lista_clientes))
    if vencido_clientes > 0:
        n = sum(1 for c in lista_clientes if c["vencido"] > 0)
        texto = f"{n} cliente(s) com {tesouraria._euros(vencido_clientes)} de faturas vencidas."
        alertas.append({"nivel": "aviso", "texto": texto,
                        "ligacao": "#clientes"})
    if a_pagar_vencido > 0:
        texto = f"{tesouraria._euros(a_pagar_vencido)} de faturas de fornecedores vencidas."
        alertas.append({"nivel": "aviso", "texto": texto,
                        "ligacao": "/contabilidade#fornecedores"})
    if stock_resumo["abaixo_minimo"]:
        alertas.append({"nivel": "aviso", "texto": f"{len(stock_resumo['abaixo_minimo'])} artigo(s) abaixo do stock "
                        "mínimo: está na altura de encomendar.", "ligacao": "#artigos"})
    if comparacao["tem_orcamento"] and vendas_orc["execucao"] is not None and vendas_orc["execucao"] < 90:
        alertas.append({"nivel": "aviso", "texto": f"As vendas do ano estão a {vendas_orc['execucao']:.0f}% do orçado.",
                        "ligacao": "#orcamento"})

    return {
        "hoje": hoje,
        "vendas": {"mes": vendas_mes, "mes_anterior_comparavel": vendas_anterior, "ano": vendas_ano,
                   "variacao": round((vendas_mes - vendas_anterior) / vendas_anterior * 100, 1)
                   if vendas_anterior > 0 else None},
        "compras_mes": compras_mes,
        "resultado_ano": resultados["resultado"],
        "margem": {"valor": margens["margem"], "percentual": margens["margem_percentual"]},
        "tesouraria": {"saldo": previsao["saldo_inicial"], "saldo_30_dias": previsao["saldo_30_dias"],
                       "semanas": previsao["semanas"]},
        "receber": {"total": _r(sum(c["em_divida"] for c in lista_clientes)), "vencido": vencido_clientes},
        "pagar": {"total": a_pagar, "vencido": a_pagar_vencido},
        "stock": {"valor": stock_resumo["valor"], "artigos": stock_resumo["artigos"],
                  "abaixo_minimo": stock_resumo["abaixo_minimo"][:6]},
        "orcamento": {"tem": comparacao["tem_orcamento"], "vendas_execucao": vendas_orc["execucao"],
                      "resultado_orcado_ate": comparacao["resultado_orcado_ate"],
                      "resultado_real_ate": comparacao["resultado_real_ate"]},
        "evolucao": evolucao(db, empresa.id, hoje),
        "top_clientes": [{"nome": c["nome"], "nif": c["nif"], "valor": c["faturado_ano"], "peso": c["peso"]}
                         for c in lista_clientes if c["faturado_ano"] > 0][:5],
        "top_fornecedores": top_fornecedores,
        "alertas": alertas,
    }
