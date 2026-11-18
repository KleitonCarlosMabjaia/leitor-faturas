"""
Fecho do exercício (SNC).

1. Verificações antes de fechar: o que falta lançar até 31 de dezembro.
2. Estimativa do IRC: matéria coletável = resultado antes de impostos + ajustes fiscais (quadro 07
   do Modelo 22, indicados pelo utilizador); IRC à taxa normal, com a taxa reduzida das PME nos
   primeiros 50 000 €; derrama municipal; tributação autónoma. É uma ESTIMATIVA: o imposto
   devido é o do Modelo 22, e as taxas são parâmetros (mudam com o Orçamento do Estado).
3. Apuramento de resultados a 31/12 (lançamentos com origem "fecho", tipo A no SAF-T):
   - saldos das classes 6 e 7 para 811 Resultado antes de impostos;
   - imposto estimado: D 8121 / C 241;
   - 811 e 8121 para 818 Resultado líquido.
4. No exercício seguinte, o resultado líquido passa para resultados transitados (D/C 818 - 561),
   normalmente depois da aprovação das contas.

As funções não fazem commit: quem as chama decide quando gravar.
"""

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modelos.tabelas import (
    LANCAMENTO_ATIVO,
    MOVIMENTO_PENDENTE,
    Empresa,
    Inventario,
    Lancamento,
    MovimentoBancario,
)
from app.servicos.contabilidade import ativos, lancamentos, periodizacao, relatorios, salarios, vendas, verificacoes
from app.servicos.contabilidade.lancamentos import ErroContabilistico, LinhaNova

# Parâmetros por omissão (confirmar no Orçamento do Estado de cada ano)
TAXA_IRC = 19.0
TAXA_IRC_PME = 15.0
LIMITE_PME = 50_000.0
TAXA_DERRAMA = 1.5
AVISO = ("Estimativa para fechar as contas: o imposto devido é o que resultar do Modelo 22. Confirme as taxas do "
         "exercício (IRC, taxa das PME, derrama do município) e os ajustes fiscais com o contabilista certificado.")


class ErroFecho(Exception):
    """Operação de fecho impossível (mensagem pronta a mostrar)."""


@dataclass
class ParametrosIRC:
    ajustes: float = 0.0              # acréscimos menos deduções ao resultado (quadro 07)
    pme: bool = True
    taxa: float = TAXA_IRC
    taxa_pme: float = TAXA_IRC_PME
    derrama: float = TAXA_DERRAMA
    tributacao_autonoma: float | None = None  # None: estimativa automática (despesas de representação)


def lancamentos_fecho(db: Session, empresa_id: int, ano: int) -> list[Lancamento]:
    return list(db.scalars(select(Lancamento).where(
        Lancamento.empresa_id == empresa_id, Lancamento.origem == "fecho", Lancamento.estado == LANCAMENTO_ATIVO,
        Lancamento.estorno_de_id.is_(None), Lancamento.data == date(ano, 12, 31),
    ).order_by(Lancamento.id)))


def resultado_antes_de_impostos(db: Session, empresa_id: int, ano: int) -> float:
    return relatorios.demonstracao_resultados(db, empresa_id, date(ano, 1, 1), date(ano, 12, 31))["resultado"]


def tributacao_autonoma_estimada(db: Session, empresa_id: int, ano: int) -> float:
    movimentos = relatorios.movimentos_por_conta(db, empresa_id, date(ano, 1, 1), date(ano, 12, 31),
                                                 excluir_origem="fecho")
    representacao = sum(d - c for codigo, (d, c) in movimentos.items() if codigo.startswith("6266"))
    return round(max(representacao, 0) * verificacoes.TAXA_TA_REPRESENTACAO, 2)


def estimar_irc(rai: float, p: ParametrosIRC, ta_estimada: float) -> dict:
    materia = round(max(rai + p.ajustes, 0.0), 2)
    if p.pme:
        coleta = min(materia, LIMITE_PME) * p.taxa_pme / 100 + max(materia - LIMITE_PME, 0) * p.taxa / 100
    else:
        coleta = materia * p.taxa / 100
    derrama = max(rai + p.ajustes, 0) * p.derrama / 100  # incide sobre o lucro tributável
    ta = ta_estimada if p.tributacao_autonoma is None else p.tributacao_autonoma
    total = round(coleta + derrama + ta, 2)
    return {"resultado_antes_de_impostos": round(rai, 2), "ajustes": round(p.ajustes, 2), "materia_coletavel": materia,
            "irc": round(coleta, 2), "derrama": round(derrama, 2), "tributacao_autonoma": round(ta, 2),
            "imposto_total": total, "resultado_liquido": round(rai - total, 2),
            "parametros": {"pme": p.pme, "taxa": p.taxa, "taxa_pme": p.taxa_pme, "derrama": p.derrama}}


def verificacoes_fecho(db: Session, empresa: Empresa, ano: int) -> list[dict]:
    """O que convém tratar antes de fechar (estado: ok | aviso)."""
    fim = date(ano, 12, 31)
    itens = []

    def item(nome: str, ok: bool, detalhe: str, destino: str | None = None):
        itens.append({"nome": nome, "estado": "ok" if ok else "aviso", "detalhe": detalhe, "destino": destino})

    faturas = [f for f in verificacoes.faturas_por_contabilizar(db, empresa.id) if f.data_emissao <= fim]
    item("Faturas de compra contabilizadas", not faturas,
         "Todas contabilizadas." if not faturas else f"{len(faturas)} por contabilizar até 31/12.", "por-contabilizar")
    sem_lancamento = [v for v in vendas.por_contabilizar(db, empresa.id) if v.data <= fim]
    item("Vendas contabilizadas", not sem_lancamento,
         "Todas contabilizadas." if not sem_lancamento else f"{len(sem_lancamento)} por contabilizar.", "vendas")
    quotas = ativos.calcular(db, empresa.id, fim)
    item("Depreciações até 31/12", not quotas,
         "Em dia." if not quotas else f"{len(quotas)} ativo(s) por depreciar até 31/12.", "ativos")

    saldos = relatorios.saldos_por_conta(db, empresa.id, ate=fim)
    tem_existencias = any(abs(v) > 0.005 for c, v in saldos.items() if c.startswith(("31", "32", "33")))
    contagem = db.scalar(select(Inventario).where(Inventario.empresa_id == empresa.id, Inventario.data == fim))
    if tem_existencias or contagem:
        apurado = bool(contagem and contagem.lancamento_id and db.get(Lancamento, contagem.lancamento_id).estado
                       == LANCAMENTO_ATIVO)
        compras = round(sum(v for c, v in saldos.items() if c.startswith("31")), 2)
        item("Inventário de 31/12 apurado", apurado and not compras,
             "Inventário final contado e CMVMC apurado." if apurado and not compras
             else "Falta a contagem de 31/12 ou o apuramento do CMVMC (as compras ainda estão na conta 31).",
             "inventarios")

    sem_salarios = [m for m in range(1, 13) if salarios.ativos_no_mes(db, empresa.id, ano, m)
                    and not salarios.processado(db, salarios.obter(db, empresa.id, ano, m))]
    if sem_salarios or salarios.ativos_no_mes(db, empresa.id, ano, 12):
        item("Salários processados", not sem_salarios,
             "Os 12 meses estão processados." if not sem_salarios
             else "Falta processar: " + ", ".join(salarios.NOMES_MESES[m - 1] for m in sem_salarios) + ".", "salarios")
        ferias = db.scalar(select(Lancamento.id).where(
            Lancamento.empresa_id == empresa.id, Lancamento.origem == "acrescimo", Lancamento.data == fim,
            Lancamento.estado == LANCAMENTO_ATIVO, Lancamento.descricao.like("Acréscimo de férias%")))
        item("Acréscimo de férias e subsídio de férias", bool(ferias),
             "Lançado." if ferias else "As férias e o subsídio de férias a pagar no ano seguinte são gasto deste "
             "ano: lance o acréscimo a 31/12.", "salarios")

    dif = periodizacao.em_atraso(db, empresa.id, fim)
    item("Diferimentos reconhecidos até 31/12", not dif,
         "Em dia." if not dif else f"{len(dif)} diferimento(s) com meses de {ano} por reconhecer.", "periodizacao")

    iva_por_apurar = round(sum(v for c, v in saldos.items() if c.startswith(("2432", "2433", "2434"))), 2)
    item("IVA apurado", abs(iva_por_apurar) < 0.01,
         "Apuramentos lançados." if abs(iva_por_apurar) < 0.01
         else "Há saldos nas contas 2432/2433/2434: apure o IVA do último período.", "iva")

    pendentes = db.scalars(select(MovimentoBancario.id).where(
        MovimentoBancario.empresa_id == empresa.id, MovimentoBancario.estado == MOVIMENTO_PENDENTE,
        MovimentoBancario.data <= fim)).all()
    item("Bancos reconciliados", not pendentes,
         "Sem movimentos por reconciliar." if not pendentes else f"{len(pendentes)} movimento(s) por reconciliar.",
         "bancos")
    return itens


def estado(db: Session, empresa: Empresa, ano: int, parametros: ParametrosIRC, hoje: date | None = None) -> dict:
    hoje = hoje or date.today()
    feitos = lancamentos_fecho(db, empresa.id, ano)
    rai = resultado_antes_de_impostos(db, empresa.id, ano)
    saldos = relatorios.saldos_por_conta(db, empresa.id, ate=date(ano + 1, 12, 31))
    return {
        "ano": ano,
        "pode_fechar": hoje > date(ano, 12, 31) and not feitos,
        "fechado": bool(feitos),
        "periodo_fechado_ate": empresa.fechado_ate,
        "verificacoes": verificacoes_fecho(db, empresa, ano),
        "estimativa": estimar_irc(rai, parametros, tributacao_autonoma_estimada(db, empresa.id, ano)),
        "lancamentos": [{"id": la.id, "referencia": f"{la.diario} {la.ano}/{la.numero}", "descricao": la.descricao}
                        for la in feitos],
        "resultado_liquido_por_transferir": round(-saldos.get("818", 0.0), 2),
        "aviso": AVISO,
    }


def fechar(db: Session, empresa: Empresa, ano: int, imposto: float, fechar_periodo: bool = False,
           utilizador_id: int | None = None, hoje: date | None = None) -> list[Lancamento]:
    hoje = hoje or date.today()
    fim = date(ano, 12, 31)
    if hoje <= fim:
        raise ErroFecho(f"O exercício de {ano} só pode ser fechado depois de 31/12/{ano}.")
    if lancamentos_fecho(db, empresa.id, ano):
        raise ErroFecho(f"O exercício de {ano} já está fechado.")
    if imposto < 0:
        raise ErroFecho("O imposto estimado não pode ser negativo.")

    movimentos = relatorios.movimentos_por_conta(db, empresa.id, date(ano, 1, 1), fim, excluir_origem="fecho")
    linhas: list[LinhaNova] = []
    total = 0.0
    for codigo, (d, c) in sorted(movimentos.items()):
        if not codigo.startswith(relatorios.CLASSES_RESULTADOS):
            continue
        saldo = round(d - c, 2)
        if saldo > 0:      # gasto: sai a crédito
            linhas.append(LinhaNova(codigo, credito=saldo, descricao="Apuramento de resultados"))
        elif saldo < 0:    # rendimento: sai a débito
            linhas.append(LinhaNova(codigo, debito=-saldo, descricao="Apuramento de resultados"))
        total += saldo
    if not linhas:
        raise ErroFecho(f"Não há gastos nem rendimentos em {ano}.")
    rai = round(-total, 2)  # positivo: lucro
    if rai > 0:
        linhas.append(LinhaNova("811", credito=rai, descricao="Resultado antes de impostos"))
    elif rai < 0:
        linhas.append(LinhaNova("811", debito=-rai, descricao="Resultado antes de impostos"))
    feitos = [lancamentos.criar(db, empresa, "OD", fim, f"Apuramento de resultados de {ano}", linhas,
                                origem="fecho", utilizador_id=utilizador_id)]

    imposto = round(imposto, 2)
    if imposto:
        feitos.append(lancamentos.criar(
            db, empresa, "OD", fim, f"Estimativa do imposto sobre o rendimento de {ano}",
            [LinhaNova("8121", debito=imposto), LinhaNova("241", credito=imposto)],
            origem="fecho", utilizador_id=utilizador_id, notas=[AVISO]))

    liquido = round(rai - imposto, 2)
    transferir = []
    if rai > 0:
        transferir.append(LinhaNova("811", debito=rai))
    elif rai < 0:
        transferir.append(LinhaNova("811", credito=-rai))
    if imposto:
        transferir.append(LinhaNova("8121", credito=imposto))
    if liquido > 0:
        transferir.append(LinhaNova("818", credito=liquido, descricao="Resultado líquido do período"))
    elif liquido < 0:
        transferir.append(LinhaNova("818", debito=-liquido, descricao="Resultado líquido do período"))
    if len(transferir) >= 2:
        feitos.append(lancamentos.criar(db, empresa, "OD", fim, f"Resultado líquido de {ano}", transferir,
                                        origem="fecho", utilizador_id=utilizador_id))
    if fechar_periodo and (empresa.fechado_ate is None or empresa.fechado_ate < fim):
        empresa.fechado_ate = fim
    return feitos


def transferir_resultado(db: Session, empresa: Empresa, ano: int, data: date | None = None,
                         utilizador_id: int | None = None) -> Lancamento:
    """Resultado líquido (818) para resultados transitados (561), no exercício seguinte."""
    if not lancamentos_fecho(db, empresa.id, ano):
        raise ErroFecho(f"Feche primeiro o exercício de {ano}.")
    data = data or date(ano + 1, 1, 1)
    if data.year <= ano:
        raise ErroFecho("A transferência faz-se no exercício seguinte.")
    saldo = round(relatorios.saldos_por_conta(db, empresa.id, ate=data).get("818", 0.0), 2)
    if abs(saldo) < 0.005:
        raise ErroFecho("O resultado líquido já foi transferido (a conta 818 está saldada).")
    liquido = -saldo  # positivo: lucro (818 credora)
    linhas = ([LinhaNova("818", debito=liquido), LinhaNova("561", credito=liquido)] if liquido > 0
              else [LinhaNova("818", credito=-liquido), LinhaNova("561", debito=-liquido)])
    return lancamentos.criar(db, empresa, "OD", data, f"Resultado líquido de {ano} para resultados transitados",
                             linhas, origem="aplicacao", utilizador_id=utilizador_id,
                             notas=["A distribuição de lucros ou a constituição de reservas, se aprovadas, "
                                    "lançam-se a partir de 561."])


def reabrir(db: Session, empresa: Empresa, ano: int, utilizador_id: int | None = None) -> int:
    """Estorna os lançamentos do fecho (o período até 31/12 tem de estar aberto)."""
    feitos = lancamentos_fecho(db, empresa.id, ano)
    if not feitos:
        raise ErroFecho(f"O exercício de {ano} não está fechado.")
    transferencia = db.scalar(select(Lancamento.id).where(
        Lancamento.empresa_id == empresa.id, Lancamento.origem == "aplicacao", Lancamento.estado == LANCAMENTO_ATIVO,
        Lancamento.estorno_de_id.is_(None), Lancamento.descricao.like(f"Resultado líquido de {ano} %"),
    ))
    if transferencia:
        raise ErroFecho("O resultado já passou para resultados transitados: estorne primeiro essa transferência.")
    if empresa.fechado_ate and empresa.fechado_ate >= date(ano, 12, 31):
        raise ErroFecho(f"O período está fechado até {empresa.fechado_ate:%d/%m/%Y}: reabra-o primeiro "
                        "(Obrigações → Configuração).")
    for lancamento in reversed(feitos):
        try:
            lancamentos.estornar(db, empresa, lancamento, utilizador_id)
        except ErroContabilistico as erro:
            raise ErroFecho(str(erro))
        db.flush()
    return len(feitos)
