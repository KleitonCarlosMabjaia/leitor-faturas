"""
Ativos fixos e depreciações (quotas constantes, em duodécimos).

- Depreciação mensal = (valor de aquisição - valor residual) x taxa anual / 12, a partir do mês
  de entrada em funcionamento, até o ativo estar totalmente depreciado (nunca mais do que isso).
- As depreciações lançam-se de uma vez para todos os ativos, até ao fim de um mês: um lançamento
  no diário de operações diversas (D 642x / C 438x; intangíveis 6433 / 4483). Cada ativo guarda o
  intervalo de meses depreciado, por isso o mesmo mês nunca é depreciado duas vezes; estornar o
  lançamento devolve esses meses.
- Abate (D 438x e 6873 / C 43x) e alienação (com IVA liquidado e ganho em 7871 ou perda em 6871)
  depreciam primeiro até ao mês da saída.

As taxas de referência são as máximas do Decreto Regulamentar n.º 25/2009 para os casos mais
comuns; a taxa fica sempre editável (a atividade pode ter taxas próprias na tabela I).

As funções não fazem commit: quem as chama decide quando gravar.
"""

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.modelos.tabelas import (
    ATIVO_ABATIDO,
    ATIVO_ALIENADO,
    ATIVO_EM_USO,
    LANCAMENTO_ATIVO,
    AtivoFixo,
    DepreciacaoAtivo,
    Empresa,
    Lancamento,
)
from app.servicos.contabilidade import iva, lancamentos, relatorios
from app.servicos.contabilidade.lancamentos import LinhaNova

# conta do ativo -> (depreciações acumuladas, gasto de depreciação)
CONTAS = {
    "432": ("4382", "6422"),
    "433": ("4383", "6423"),
    "434": ("4384", "6424"),
    "435": ("4385", "6425"),
    "437": ("4387", "6427"),
    "443": ("4483", "6433"),
}
NOMES_CONTAS = {"432": "Edifícios e outras construções", "433": "Equipamento básico",
                "434": "Equipamento de transporte", "435": "Equipamento administrativo",
                "437": "Outros ativos fixos tangíveis",
                "443": "Programas de computador"}

# (descrição, conta, taxa anual em %): taxas máximas de referência (Decreto Regulamentar n.º 25/2009)
TAXAS_REFERENCIA = [
    ("Edifícios comerciais e administrativos", "432", 2.0),
    ("Equipamento básico (confirme a taxa da sua atividade na tabela I)", "433", 12.5),
    ("Viaturas ligeiras de passageiros", "434", 25.0),
    ("Mobiliário e equipamento de escritório", "435", 12.5),
    ("Computadores e equipamento informático", "435", 33.33),
    ("Programas de computador", "443", 33.33),
    ("Outros ativos fixos tangíveis", "437", 12.5),
]


class ErroAtivo(Exception):
    """Dados de ativo inválidos (mensagem pronta a mostrar)."""


def fim_do_mes(d: date) -> date:
    return date(d.year + d.month // 12, d.month % 12 + 1, 1) - timedelta(days=1)


def _meses(de: date, ate: date) -> int:
    return (ate.year - de.year) * 12 + ate.month - de.month + 1


def obter(db: Session, empresa_id: int, ativo_id: int) -> AtivoFixo | None:
    return db.scalar(select(AtivoFixo).options(selectinload(AtivoFixo.depreciacoes)).where(
        AtivoFixo.id == ativo_id, AtivoFixo.empresa_id == empresa_id))


def registar(db: Session, empresa: Empresa, *, descricao: str, conta_ativo: str, data_aquisicao: date,
             valor_aquisicao: float, taxa: float, data_inicio: date | None = None, valor_residual: float = 0.0,
             fatura_id: str | None = None) -> AtivoFixo:
    if conta_ativo not in CONTAS:
        raise ErroAtivo("Conta do ativo desconhecida: use 432, 433, 434, 435, 437 ou 443 (terrenos não se depreciam).")
    if valor_aquisicao <= 0:
        raise ErroAtivo("O valor de aquisição tem de ser positivo.")
    if not 0 < taxa <= 100:
        raise ErroAtivo("A taxa de depreciação tem de estar entre 0 e 100%.")
    if not 0 <= valor_residual < valor_aquisicao:
        raise ErroAtivo("O valor residual tem de ser menor do que o valor de aquisição.")
    data_inicio = data_inicio or data_aquisicao
    if data_inicio < data_aquisicao:
        raise ErroAtivo("A entrada em funcionamento não pode ser anterior à aquisição.")
    numero = (db.scalar(select(func.count(AtivoFixo.id)).where(AtivoFixo.empresa_id == empresa.id)) or 0) + 1
    conta_dep, conta_gasto = CONTAS[conta_ativo]
    ativo = AtivoFixo(
        empresa_id=empresa.id, codigo=f"AF{numero:04d}", descricao=descricao.strip()[:200] or "Ativo",
        conta_ativo=conta_ativo, conta_depreciacao=conta_dep, conta_gasto=conta_gasto,
        data_aquisicao=data_aquisicao, data_inicio=data_inicio, valor_aquisicao=round(valor_aquisicao, 2),
        valor_residual=round(valor_residual, 2), taxa=round(taxa, 2), estado=ATIVO_EM_USO, fatura_id=fatura_id,
    )
    db.add(ativo)
    db.flush()
    return ativo


def _ativas(db: Session, ativo: AtivoFixo) -> list[DepreciacaoAtivo]:
    """Depreciações do ativo cujo lançamento não foi estornado."""
    ids = {d.lancamento_id for d in ativo.depreciacoes}
    validos = set(db.scalars(select(Lancamento.id).where(Lancamento.id.in_(ids),
                                                         Lancamento.estado == LANCAMENTO_ATIVO))) if ids else set()
    return [d for d in ativo.depreciacoes if d.lancamento_id in validos]


def acumulada(db: Session, ativo: AtivoFixo, ate: date | None = None) -> float:
    return round(sum(d.valor for d in _ativas(db, ativo) if ate is None or d.ate <= ate), 2)


def mensal(ativo: AtivoFixo) -> float:
    return round((ativo.valor_aquisicao - ativo.valor_residual) * ativo.taxa / 100 / 12, 2)


@dataclass
class Quota:
    ativo: AtivoFixo
    de: date
    ate: date
    meses: int
    valor: float


def calcular(db: Session, empresa_id: int, ate: date, so_ativo: AtivoFixo | None = None) -> list[Quota]:
    """Depreciações por lançar até ao fim do mês de `ate` (nada é gravado)."""
    ate = fim_do_mes(ate)
    ativos = [so_ativo] if so_ativo else list(db.scalars(
        select(AtivoFixo).options(selectinload(AtivoFixo.depreciacoes))
        .where(AtivoFixo.empresa_id == empresa_id, AtivoFixo.estado == ATIVO_EM_USO).order_by(AtivoFixo.codigo)))
    quotas = []
    for ativo in ativos:
        feitas = _ativas(db, ativo)
        ultimo = max((d.ate for d in feitas), default=None)
        inicio = (ultimo + timedelta(days=1)) if ultimo else date(ativo.data_inicio.year, ativo.data_inicio.month, 1)
        if inicio > ate:
            continue
        depreciavel = round(ativo.valor_aquisicao - ativo.valor_residual - sum(d.valor for d in feitas), 2)
        if depreciavel <= 0:
            continue
        meses = _meses(inicio, ate)
        valor = round(min(mensal(ativo) * meses, depreciavel), 2)
        # Se o ativo fica totalmente depreciado antes do fim do intervalo, só contam os meses necessários
        if valor == depreciavel and mensal(ativo) > 0:
            meses = min(meses, -(-int(round(depreciavel * 100)) // int(round(mensal(ativo) * 100))))
        ultimo_mes = inicio.month - 1 + meses - 1  # meses contados a partir de janeiro do ano de início
        fim = fim_do_mes(date(inicio.year + ultimo_mes // 12, ultimo_mes % 12 + 1, 1))
        if valor > 0:
            quotas.append(Quota(ativo, inicio, fim, meses, valor))
    return quotas


def lancar_depreciacoes(db: Session, empresa: Empresa, ate: date, utilizador_id: int | None = None,
                        so_ativo: AtivoFixo | None = None) -> Lancamento:
    quotas = calcular(db, empresa.id, ate, so_ativo)
    if not quotas:
        raise ErroAtivo(f"Não há depreciações por lançar até {fim_do_mes(ate):%d/%m/%Y}.")
    por_conta: dict[tuple[str, str], float] = {}
    for q in quotas:
        chave = (q.ativo.conta_gasto, q.ativo.conta_depreciacao)
        por_conta[chave] = round(por_conta.get(chave, 0.0) + q.valor, 2)
    linhas = []
    for (gasto, dep), valor in sorted(por_conta.items()):
        linhas += [LinhaNova(gasto, debito=valor, descricao="Depreciações do período"),
                   LinhaNova(dep, credito=valor, descricao="Depreciações acumuladas")]
    fim = fim_do_mes(ate)
    lancamento = lancamentos.criar(
        db, empresa, "OD", fim, f"Depreciações até {fim:%m/%Y}", linhas, documento="Mapa de depreciações",
        origem="depreciacao", utilizador_id=utilizador_id,
        notas=[f"{q.ativo.codigo} {q.ativo.descricao}: {q.meses} mês(es) x {mensal(q.ativo):.2f} € = {q.valor:.2f} €"
               for q in quotas],
    )
    for q in quotas:
        q.ativo.depreciacoes.append(DepreciacaoAtivo(de=q.de, ate=q.ate, meses=q.meses, valor=q.valor,
                                                     lancamento_id=lancamento.id))
    db.flush()
    return lancamento


def _preparar_saida(db: Session, empresa: Empresa, ativo: AtivoFixo, data: date, utilizador_id: int | None) -> float:
    if ativo.estado != ATIVO_EM_USO:
        raise ErroAtivo("Este ativo já saiu (abatido ou alienado).")
    if data < ativo.data_aquisicao:
        raise ErroAtivo("A data de saída é anterior à aquisição.")
    if data >= ativo.data_inicio and calcular(db, empresa.id, data, ativo):
        lancar_depreciacoes(db, empresa, data, utilizador_id, so_ativo=ativo)
    return acumulada(db, ativo)


def abater(db: Session, empresa: Empresa, ativo: AtivoFixo, data: date, utilizador_id: int | None = None) -> Lancamento:
    """Abate (avaria, obsolescência): o valor líquido que falta vai para gasto (6873)."""
    acum = _preparar_saida(db, empresa, ativo, data, utilizador_id)
    liquido = round(ativo.valor_aquisicao - acum, 2)
    linhas = [LinhaNova(ativo.conta_depreciacao, debito=acum, descricao="Depreciações acumuladas"),
              LinhaNova("6873", debito=liquido, descricao="Valor líquido abatido"),
              LinhaNova(ativo.conta_ativo, credito=ativo.valor_aquisicao, descricao=ativo.descricao)]
    lancamento = lancamentos.criar(db, empresa, "OD", data, f"Abate do ativo {ativo.codigo} {ativo.descricao}",
                                   linhas, documento=ativo.codigo, origem="ativo", utilizador_id=utilizador_id)
    ativo.estado, ativo.data_saida, ativo.lancamento_saida_id = ATIVO_ABATIDO, data, lancamento.id
    return lancamento


def alienar(db: Session, empresa: Empresa, ativo: AtivoFixo, data: date, valor_venda: float, taxa_iva: float = 23.0,
            conta_recebimento: str = "121", utilizador_id: int | None = None) -> Lancamento:
    """Venda do ativo: recebimento com IVA liquidado; ganho (7871) ou perda (6871) pela diferença."""
    if valor_venda < 0:
        raise ErroAtivo("O valor de venda não pode ser negativo.")
    acum = _preparar_saida(db, empresa, ativo, data, utilizador_id)
    liquido = round(ativo.valor_aquisicao - acum, 2)
    imposto = round(valor_venda * taxa_iva / 100, 2)
    resultado = round(valor_venda - liquido, 2)
    linhas = [LinhaNova(conta_recebimento, debito=round(valor_venda + imposto, 2), descricao="Venda do ativo"),
              LinhaNova(ativo.conta_depreciacao, debito=acum, descricao="Depreciações acumuladas"),
              LinhaNova(ativo.conta_ativo, credito=ativo.valor_aquisicao, descricao=ativo.descricao)]
    if imposto:
        conta_iva = iva.CONTA_IVA_LIQUIDADO.get(iva.escalao(taxa_iva), "24333")
        linhas.append(LinhaNova(conta_iva, credito=imposto, descricao=f"IVA liquidado {taxa_iva:g}%", tipo_iva="iva",
                                taxa_iva=taxa_iva))
    if resultado > 0:
        linhas.append(LinhaNova("7871", credito=resultado, descricao="Ganho na alienação"))
    elif resultado < 0:
        linhas.append(LinhaNova("6871", debito=-resultado, descricao="Perda na alienação"))
    lancamento = lancamentos.criar(db, empresa, "OD", data, f"Alienação do ativo {ativo.codigo} {ativo.descricao}",
                                   linhas, documento=ativo.codigo, origem="ativo", utilizador_id=utilizador_id)
    ativo.estado, ativo.data_saida, ativo.lancamento_saida_id = ATIVO_ALIENADO, data, lancamento.id
    return lancamento


def resumo(db: Session, ativo: AtivoFixo, ano: int | None = None) -> dict:
    ano = ano or date.today().year
    feitas = _ativas(db, ativo)
    acum_inicio = round(sum(d.valor for d in feitas if d.ate < date(ano, 1, 1)), 2)
    do_ano = round(sum(d.valor for d in feitas if d.ate.year == ano), 2)
    acum = round(sum(d.valor for d in feitas), 2)
    return {
        "id": ativo.id, "codigo": ativo.codigo, "descricao": ativo.descricao, "conta_ativo": ativo.conta_ativo,
        "conta_nome": NOMES_CONTAS.get(ativo.conta_ativo, ""), "data_aquisicao": ativo.data_aquisicao,
        "data_inicio": ativo.data_inicio, "valor_aquisicao": ativo.valor_aquisicao,
        "valor_residual": ativo.valor_residual, "taxa": ativo.taxa, "depreciacao_mensal": mensal(ativo),
        "acumulada_inicio_ano": acum_inicio, "depreciacao_ano": do_ano, "acumulada": acum,
        "valor_liquido": round(ativo.valor_aquisicao - acum, 2), "estado": ativo.estado, "data_saida": ativo.data_saida,
        "depreciado_ate": max((d.ate for d in feitas), default=None),
    }


def diferencas_com_contabilidade(db: Session, empresa_id: int) -> list[dict]:
    """Contas de ativo cujo saldo contabilístico não bate com o valor dos ativos registados nelas."""
    registados: dict[str, float] = {}
    for a in db.scalars(select(AtivoFixo).where(AtivoFixo.empresa_id == empresa_id, AtivoFixo.estado == ATIVO_EM_USO)):
        registados[a.conta_ativo] = round(registados.get(a.conta_ativo, 0.0) + a.valor_aquisicao, 2)
    saldos = relatorios.saldos_por_conta(db, empresa_id)
    resultado = []
    for conta in CONTAS:
        contab = round(sum(v for c, v in saldos.items() if c.startswith(conta)), 2)
        reg = registados.get(conta, 0.0)
        if abs(contab - reg) > 0.01:
            resultado.append({"conta": conta, "nome": NOMES_CONTAS[conta], "contabilidade": contab, "registados": reg})
    return resultado

