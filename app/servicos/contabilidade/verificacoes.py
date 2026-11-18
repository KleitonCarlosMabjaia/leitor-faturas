"""
Verificações do dia a dia: o que um contabilista atento veria ao abrir a contabilidade.

Cada alerta tem um nível (erro, aviso, info), um título, uma explicação e, quando faz
sentido, uma ação (a secção da página onde se resolve).
"""

from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modelos.tabelas import (
    ESTADO_CONFIRMADA,
    ESTADO_POR_REVER,
    LANCAMENTO_ATIVO,
    MOVIMENTO_PENDENTE,
    Empresa,
    Fatura,
    Lancamento,
    MovimentoBancario,
)
from app.servicos.contabilidade import ativos, iva, obrigacoes, periodizacao, relatorios, salarios, vendas
from app.servicos.contabilidade.lancamentos import _euros

TAXA_TA_REPRESENTACAO = 0.10  # art. 88.º, n.º 7 do CIRC


def faturas_por_contabilizar(db: Session, empresa_id: int) -> list[Fatura]:
    """Faturas confirmadas, com dados, sem lançamento de compra ativo. As mais antigas primeiro."""
    contabilizadas = select(Lancamento.fatura_id).where(
        Lancamento.empresa_id == empresa_id, Lancamento.origem == "fatura", Lancamento.estado == LANCAMENTO_ATIVO,
        Lancamento.fatura_id.is_not(None),
    )
    return list(db.scalars(
        select(Fatura).where(
            Fatura.empresa_id == empresa_id, Fatura.estado == ESTADO_CONFIRMADA, Fatura.estado_extracao == "concluida",
            Fatura.data_emissao.is_not(None), Fatura.total.is_not(None), Fatura.id.not_in(contabilizadas),
        ).order_by(Fatura.data_emissao, Fatura.numero_fatura)
    ))


def _alerta(nivel: str, titulo: str, texto: str, acao: tuple[str, str] | None = None) -> dict:
    return {"nivel": nivel, "titulo": titulo, "texto": texto,
            "acao": {"texto": acao[0], "destino": acao[1]} if acao else None}


def alertas(db: Session, empresa: Empresa, hoje: date | None = None) -> list[dict]:
    hoje = hoje or date.today()
    resultado: list[dict] = []

    # 1. Faturas por contabilizar
    pendentes = faturas_por_contabilizar(db, empresa.id)
    if pendentes:
        mais_antiga = pendentes[0].data_emissao
        resultado.append(_alerta(
            "aviso" if (hoje - mais_antiga).days > 30 else "info",
            f"{len(pendentes)} fatura{'s' if len(pendentes) > 1 else ''} por contabilizar",
            f"Total de {_euros(sum(f.total for f in pendentes))}. A mais antiga é de {mais_antiga:%d/%m/%Y}.",
            ("Contabilizar", "por-contabilizar"),
        ))

    # 2. Faturas por rever há mais de uma semana
    por_rever = db.scalars(select(Fatura).where(
        Fatura.empresa_id == empresa.id, Fatura.estado == ESTADO_POR_REVER,
        Fatura.carregada_em < datetime.combine(hoje - timedelta(days=7), time.min),
    )).all()
    if por_rever:
        resultado.append(_alerta(
            "info", f"{len(por_rever)} fatura{'s' if len(por_rever) > 1 else ''} por rever há mais de 7 dias",
            "Só as faturas confirmadas entram na contabilidade.", ("Rever faturas", "/"),
        ))

    # 3. Período de IVA terminado e ainda não apurado
    inicio, fim, descricao = obrigacoes.periodo_iva(hoje, empresa.regime_iva)
    apurado = db.scalar(select(Lancamento.id).where(
        Lancamento.empresa_id == empresa.id, Lancamento.origem == "apuramento", Lancamento.estado == LANCAMENTO_ATIVO,
        Lancamento.data >= inicio, Lancamento.data <= fim,
    ))
    mapa = relatorios.mapa_iva(db, empresa.id, inicio, fim)
    if not apurado and (mapa["total_dedutivel"] or mapa["total_liquidado"]):
        prazo = next((o["prazo"] for o in obrigacoes.proximas(hoje, empresa.regime_iva, 120)
                      if o["chave"] == "iva_dp"), None)
        sentido = "a pagar" if mapa["saldo"] > 0 else "a recuperar"
        resultado.append(_alerta(
            "aviso", f"IVA do {descricao} por apurar",
            f"Estimativa: {_euros(abs(mapa['saldo']))} {sentido}."
            + (f" Prazo da declaração: {prazo:%d/%m/%Y}." if prazo else ""),
            ("Ver apuramento", "iva"),
        ))

    # 4. Saldos anómalos
    saldos = relatorios.saldos_por_conta(db, empresa.id, ate=hoje)
    caixa = sum(v for c, v in saldos.items() if c.startswith("11"))
    if caixa < -0.005:
        resultado.append(_alerta(
            "erro", "Caixa com saldo credor",
            f"A caixa (11) tem saldo credor de {_euros(-caixa)}: é fisicamente impossível. Há pagamentos em "
            "numerário sem a entrada de dinheiro correspondente, ou movimentos na conta errada.",
            ("Ver extrato", "extrato:11"),
        ))
    devedores = [(c, v) for c, v in saldos.items() if c.startswith(("2211", "2711")) and v > 0.005]
    if devedores:
        resultado.append(_alerta(
            "aviso", f"{len(devedores)} fornecedor{'es' if len(devedores) > 1 else ''} com saldo devedor",
            "Pagou-se mais do que se deve: adiantamento, nota de crédito por lançar ou pagamento em duplicado. "
            f"Total: {_euros(sum(v for _, v in devedores))}.",
            ("Ver contas correntes", "fornecedores"),
        ))

    # 5. Impacto fiscal no ano: IVA não dedutível e tributação autónoma das despesas de representação
    inicio_ano = date(hoje.year, 1, 1)
    mapa_ano = relatorios.mapa_iva(db, empresa.id, inicio_ano, hoje)
    if mapa_ano["iva_nao_dedutivel"] > 0:
        resultado.append(_alerta(
            "info", f"{_euros(mapa_ano['iva_nao_dedutivel'])} de IVA não dedutível em {hoje.year}",
            "IVA que ficou como gasto por força do art. 21.º do CIVA (combustíveis, viaturas, refeições, "
            "deslocações). Em regra, conta como gasto para efeitos de IRC.",
        ))
    movimentos_ano = relatorios.movimentos_por_conta(db, empresa.id, inicio_ano, hoje)
    representacao = sum(d - c for codigo, (d, c) in movimentos_ano.items() if codigo.startswith("6266"))
    if representacao > 0.005:
        resultado.append(_alerta(
            "info", f"Tributação autónoma estimada: {_euros(representacao * TAXA_TA_REPRESENTACAO)}",
            f"Despesas de representação de {_euros(representacao)} em {hoje.year}, à taxa de 10% "
            "(art. 88.º, n.º 7 do CIRC). Pagas com o Modelo 22, mesmo que a empresa tenha prejuízo.",
            ("Ver extrato", "extrato:6266"),
        ))

    # 6. Movimentos do banco por reconciliar
    por_reconciliar = db.execute(
        select(MovimentoBancario.conta_codigo, func.count(MovimentoBancario.id), func.min(MovimentoBancario.data))
        .where(MovimentoBancario.empresa_id == empresa.id, MovimentoBancario.estado == MOVIMENTO_PENDENTE)
        .group_by(MovimentoBancario.conta_codigo)
    ).all()
    for conta, quantidade, mais_antigo in por_reconciliar:
        resultado.append(_alerta(
            "aviso" if (hoje - mais_antigo).days > 30 else "info",
            f"{quantidade} movimento{'s' if quantidade > 1 else ''} do banco por reconciliar (conta {conta})",
            f"O mais antigo é de {mais_antigo:%d/%m/%Y}. Associe-os aos lançamentos ou lance-os a partir do extrato.",
            ("Reconciliar", f"bancos:{conta}"),
        ))

    # 7. Vendas vencidas por receber e vendas por contabilizar
    vencidas = [v for v in vendas.por_receber(db, empresa.id) if v.data_vencimento and v.data_vencimento < hoje]
    if vencidas:
        resultado.append(_alerta(
            "aviso", f"{len(vencidas)} vendas vencidas por receber" if len(vencidas) > 1
            else "1 venda vencida por receber",
            f"Total em dívida: {_euros(sum(v.total - v.valor_recebido for v in vencidas))}. A mais antiga venceu a "
            f"{min(v.data_vencimento for v in vencidas):%d/%m/%Y}.",
            ("Ver vendas", "vendas"),
        ))
    sem_lancamento = vendas.por_contabilizar(db, empresa.id)
    if sem_lancamento:
        resultado.append(_alerta(
            "aviso", f"{len(sem_lancamento)} venda{'s' if len(sem_lancamento) > 1 else ''} por contabilizar",
            "Normalmente por o período estar fechado na data da venda. Contabilize-as na secção Vendas.",
            ("Ver vendas", "vendas"),
        ))

    # 8. Ativos fixos: depreciações em atraso e ficha diferente da contabilidade
    fim_mes_passado = date(hoje.year, hoje.month, 1) - timedelta(days=1)
    quotas = ativos.calcular(db, empresa.id, fim_mes_passado)
    if quotas:
        resultado.append(_alerta(
            "aviso", f"Depreciações por lançar até {fim_mes_passado:%m/%Y}",
            f"{len(quotas)} ativo{'s' if len(quotas) > 1 else ''}, {_euros(sum(q.valor for q in quotas))}. Sem elas o "
            "resultado e o balanço ficam sobreavaliados.",
            ("Lançar depreciações", "ativos"),
        ))
    for d in ativos.diferencas_com_contabilidade(db, empresa.id):
        resultado.append(_alerta(
            "info", f"Ativos da conta {d['conta']} diferentes da contabilidade",
            f"Na contabilidade: {_euros(d['contabilidade'])}; na ficha de ativos: {_euros(d['registados'])}. "
            "Registe na ficha os ativos comprados (ou lance a aquisição que falta).",
            ("Ver ativos", "ativos"),
        ))

    # 9. Salários por processar ou pagamentos ao Estado em atraso
    meses = salarios.por_processar(db, empresa.id, hoje)
    if meses:
        nomes = ", ".join(salarios.NOMES_MESES[m - 1] for _, m in meses)
        resultado.append(_alerta(
            "aviso", f"Salários por processar: {nomes}",
            "Há pessoas ao serviço nesses meses sem processamento. Sem ele faltam os gastos com o pessoal e as "
            "dívidas à Segurança Social e ao Estado.",
            ("Processar salários", "salarios"),
        ))
    for a in salarios.pagamentos_em_atraso(db, empresa.id, hoje):
        resultado.append(_alerta(
            "erro" if a["tipo"] != "salarios" else "aviso",
            f"{a['nome']} de {salarios.nome_mes(a['ano'], a['mes'])} por pagar",
            f"{_euros(a['valor'])}. O prazo já passou: pagamentos fora de prazo à Segurança Social e à AT têm juros "
            "e coimas.",
            ("Ver salários", "salarios"),
        ))

    # 10. Diferimentos por reconhecer
    atraso = periodizacao.em_atraso(db, empresa.id, fim_mes_passado)
    if atraso:
        resultado.append(_alerta(
            "aviso", f"Diferimentos por reconhecer até {fim_mes_passado:%m/%Y}",
            f"{len(atraso)} diferimento{'s' if len(atraso) > 1 else ''}, {_euros(sum(v for _, _, v in atraso))}.",
            ("Reconhecer", "periodizacao"),
        ))

    # 11. NIF inválido em faturas confirmadas
    nif_invalido = db.scalars(select(Fatura).where(
        Fatura.empresa_id == empresa.id, Fatura.estado == ESTADO_CONFIRMADA, Fatura.nif_valido.is_(False),
    )).all()
    # Fornecedores de outros Estados-membros têm número de IVA estrangeiro (não é um NIF português)
    nif_invalido = [f for f in nif_invalido if not iva.fornecedor_da_ue(f.nif_fornecedor)]
    if nif_invalido:
        resultado.append(_alerta(
            "erro", f"{len(nif_invalido)} fatura{'s' if len(nif_invalido) > 1 else ''} com NIF inválido",
            "O IVA de faturas com NIF errado pode não ser aceite pela AT. Peça ao fornecedor uma fatura corrigida.",
            ("Ver faturas", "/"),
        ))

    ordem = {"erro": 0, "aviso": 1, "info": 2}
    return sorted(resultado, key=lambda a: ordem[a["nivel"]])
