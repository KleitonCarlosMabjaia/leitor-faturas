"""
Periodização económica (regime do acréscimo): acréscimos e diferimentos.

Acréscimos — gasto ou rendimento deste período cujo documento só chega no seguinte
(ex.: eletricidade de dezembro faturada em janeiro, férias a pagar no ano seguinte):
    gasto:      D 6x / C 2722 Credores por acréscimos de gastos
    rendimento: D 2721 Devedores por acréscimos de rendimentos / C 7x
e, no primeiro dia do período seguinte, a reversão automática (o lançamento ao contrário),
para que o documento, quando chegar, seja lançado normalmente sem duplicar o gasto.

Diferimentos — gasto pago (ou rendimento faturado) que respeita a vários meses
(ex.: seguro anual, renda paga adiantada, contrato de assistência faturado ao ano):
    registo:     o valor sai da conta de gasto para 281 Gastos a reconhecer (D 281 / C 6x)
                 ou da conta de rendimento para 282 Rendimentos a reconhecer (D 7x / C 282);
    mensalmente: a parte de cada mês volta à conta de gasto/rendimento.
A fatura é lançada como sempre (na conta de gasto); o diferimento só a reparte pelos meses.

As funções não fazem commit: quem as chama decide quando gravar.
"""

from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modelos.tabelas import LANCAMENTO_ATIVO, Diferimento, Empresa, Lancamento
from app.servicos.contabilidade import lancamentos, plano_snc
from app.servicos.contabilidade.lancamentos import LinhaNova

CONTA_ACRESCIMO = {"gasto": "2722", "rendimento": "2721"}
CONTA_DIFERIMENTO = {"gasto": "281", "rendimento": "282"}
CLASSE = {"gasto": "6", "rendimento": "7"}


class ErroPeriodizacao(Exception):
    """Dados inválidos (mensagem pronta a mostrar)."""


def _r(v: float) -> float:
    return round(v + 0.0, 2)


def fim_do_mes(d: date) -> date:
    return date(d.year + d.month // 12, d.month % 12 + 1, 1) - timedelta(days=1)


def _somar_meses(d: date, n: int) -> date:
    m = d.month - 1 + n
    return date(d.year + m // 12, m % 12 + 1, 1)


def _verificar_conta(db: Session, empresa_id: int, codigo: str, tipo: str) -> None:
    conta = plano_snc.obter_conta(db, empresa_id, codigo)
    if conta is None or not conta.movimento or not codigo.startswith(CLASSE[tipo]):
        natureza = "gasto (classe 6)" if tipo == "gasto" else "rendimento (classe 7)"
        raise ErroPeriodizacao(f"A conta {codigo} não é uma conta de movimento de {natureza}.")


def _ativo(db: Session, lancamento_id: int | None) -> Lancamento | None:
    if not lancamento_id:
        return None
    la = db.get(Lancamento, lancamento_id)
    return la if la and la.estado == LANCAMENTO_ATIVO else None


# ---------- Acréscimos ----------

def acrescimo(db: Session, empresa: Empresa, tipo: str, data: date, descricao: str,
              linhas: list[tuple[str, float, str | None]], reversao: date | None = None,
              utilizador_id: int | None = None, notas: list[str] | None = None) -> Lancamento:
    """
    Lança o acréscimo (linhas: conta de gasto/rendimento, valor, descrição) e a reversão no dia
    seguinte ao fim do período (por omissão, o 1.º dia do mês seguinte a `data`).
    """
    if tipo not in CONTA_ACRESCIMO:
        raise ErroPeriodizacao("Tipo desconhecido: gasto ou rendimento.")
    linhas = [(c, _r(v), d) for c, v, d in linhas if _r(v)]
    if not linhas or any(v < 0 for _, v, _ in linhas):
        raise ErroPeriodizacao("Indique valores positivos.")
    for conta, _, _ in linhas:
        _verificar_conta(db, empresa.id, conta, tipo)
    reversao = reversao or fim_do_mes(data) + timedelta(days=1)
    if reversao <= data:
        raise ErroPeriodizacao("A reversão tem de ser posterior à data do acréscimo.")
    numero = (db.scalar(select(func.count(Lancamento.id)).where(
        Lancamento.empresa_id == empresa.id, Lancamento.origem == "acrescimo",
        Lancamento.estorno_de_id.is_(None))) or 0) + 1
    documento = f"ACR {data.year}/{numero}"
    total = _r(sum(v for _, v, _ in linhas))
    contrapartida = CONTA_ACRESCIMO[tipo]
    if tipo == "gasto":
        novas = [LinhaNova(c, debito=v, descricao=d) for c, v, d in linhas]
        novas.append(LinhaNova(contrapartida, credito=total, descricao="Acréscimo de gastos"))
    else:
        novas = [LinhaNova(contrapartida, debito=total, descricao="Acréscimo de rendimentos")]
        novas += [LinhaNova(c, credito=v, descricao=d) for c, v, d in linhas]
    principal = lancamentos.criar(db, empresa, "OD", data, descricao, novas, documento=documento, origem="acrescimo",
                                  utilizador_id=utilizador_id,
                                  notas=(notas or []) + [f"Reversão automática em {reversao:%d/%m/%Y}."])
    inversas = [LinhaNova(ln.conta_codigo, debito=ln.credito, credito=ln.debito, descricao=ln.descricao)
                for ln in novas]
    lancamentos.criar(db, empresa, "OD", reversao, f"Reversão: {descricao}"[:300], inversas, documento=documento,
                      origem="acrescimo_reversao", utilizador_id=utilizador_id)
    return principal


def reversao_de(db: Session, lancamento: Lancamento) -> Lancamento | None:
    return db.scalar(select(Lancamento).where(
        Lancamento.empresa_id == lancamento.empresa_id, Lancamento.origem == "acrescimo_reversao",
        Lancamento.documento == lancamento.documento, Lancamento.estado == LANCAMENTO_ATIVO,
        Lancamento.estorno_de_id.is_(None)))


def listar_acrescimos(db: Session, empresa_id: int, ano: int | None = None) -> list[dict]:
    q = select(Lancamento).where(Lancamento.empresa_id == empresa_id, Lancamento.origem == "acrescimo",
                                 Lancamento.estorno_de_id.is_(None))
    if ano:
        q = q.where(Lancamento.ano == ano)
    resultado = []
    for la in db.scalars(q.order_by(Lancamento.data.desc(), Lancamento.id.desc())):
        rev = db.scalar(select(Lancamento).where(
            Lancamento.empresa_id == empresa_id, Lancamento.origem == "acrescimo_reversao",
            Lancamento.documento == la.documento, Lancamento.estorno_de_id.is_(None)))
        tipo = "rendimento" if any(ln.conta.codigo == "2721" and ln.debito for ln in la.linhas) else "gasto"
        resultado.append({
            "id": la.id, "referencia": f"{la.diario} {la.ano}/{la.numero}", "documento": la.documento,
            "data": la.data, "descricao": la.descricao, "tipo": tipo, "estado": la.estado,
            "valor": _r(sum(ln.debito for ln in la.linhas)), "reversao": rev.data if rev else None,
            "contas": sorted({ln.conta.codigo for ln in la.linhas if not ln.conta.codigo.startswith("272")}),
        })
    return resultado


# ---------- Diferimentos ----------

def obter(db: Session, empresa_id: int, diferimento_id: int) -> Diferimento | None:
    return db.scalar(select(Diferimento).where(Diferimento.id == diferimento_id,
                                               Diferimento.empresa_id == empresa_id))


def registar(db: Session, empresa: Empresa, *, tipo: str, descricao: str, conta: str, valor: float, inicio: date,
             meses: int, data: date | None = None, utilizador_id: int | None = None) -> Diferimento:
    """Regista o diferimento e lança a passagem do valor para 281/282 (na data indicada ou no início)."""
    if tipo not in CONTA_DIFERIMENTO:
        raise ErroPeriodizacao("Tipo desconhecido: gasto ou rendimento.")
    if valor <= 0:
        raise ErroPeriodizacao("O valor tem de ser positivo.")
    if not 2 <= meses <= 120:
        raise ErroPeriodizacao("Um diferimento reparte o valor por 2 a 120 meses.")
    _verificar_conta(db, empresa.id, conta, tipo)
    inicio = date(inicio.year, inicio.month, 1)
    data = data or inicio
    valor = _r(valor)
    d = Diferimento(empresa_id=empresa.id, tipo=tipo, descricao=descricao.strip()[:200] or "Diferimento",
                    conta=conta, valor=valor, inicio=inicio, meses=meses, reconhecimentos=[])
    db.add(d)
    db.flush()
    diferida = CONTA_DIFERIMENTO[tipo]
    if tipo == "gasto":
        linhas = [LinhaNova(diferida, debito=valor, descricao="Gasto a reconhecer"),
                  LinhaNova(conta, credito=valor, descricao=d.descricao)]
    else:
        linhas = [LinhaNova(conta, debito=valor, descricao=d.descricao),
                  LinhaNova(diferida, credito=valor, descricao="Rendimento a reconhecer")]
    fim = fim_do_mes(_somar_meses(inicio, meses - 1))
    la = lancamentos.criar(db, empresa, "OD", data, f"Diferimento: {d.descricao}", linhas, documento=f"DIF {d.id}",
                           origem="diferimento", utilizador_id=utilizador_id,
                           notas=[f"{valor:.2f} € repartidos por {meses} meses, de {inicio:%m/%Y} a {fim:%m/%Y}."])
    d.lancamento_id = la.id
    db.flush()
    return d


def _reconhecimentos_ativos(db: Session, d: Diferimento) -> list[dict]:
    return [r for r in (d.reconhecimentos or []) if _ativo(db, r["lancamento_id"])]


def reconhecido(db: Session, d: Diferimento) -> tuple[int, float]:
    ativos = _reconhecimentos_ativos(db, d)
    return sum(r["meses"] for r in ativos), _r(sum(r["valor"] for r in ativos))


def valor_ate(d: Diferimento, meses: int) -> float:
    """Valor acumulado dos primeiros `meses` meses (o último mês absorve os cêntimos)."""
    if meses >= d.meses:
        return d.valor
    return _r(round(d.valor / d.meses, 2) * meses)


def por_reconhecer(db: Session, d: Diferimento, ate: date) -> tuple[int, float, date] | None:
    """(meses, valor, data do fim) a reconhecer até ao fim do mês de `ate`, ou None."""
    if not _ativo(db, d.lancamento_id):
        return None
    feitos, valor_feito = reconhecido(db, d)
    decorridos = (ate.year - d.inicio.year) * 12 + ate.month - d.inicio.month + 1
    alvo = min(max(decorridos, 0), d.meses)
    if alvo <= feitos:
        return None
    return alvo - feitos, _r(valor_ate(d, alvo) - valor_feito), fim_do_mes(_somar_meses(d.inicio, alvo - 1))


def reconhecer(db: Session, empresa: Empresa, ate: date, utilizador_id: int | None = None) -> Lancamento:
    """Um lançamento com a parte de todos os diferimentos até ao fim do mês de `ate`."""
    fim = fim_do_mes(ate)
    pendentes = []
    for d in db.scalars(select(Diferimento).where(Diferimento.empresa_id == empresa.id).order_by(Diferimento.id)):
        p = por_reconhecer(db, d, fim)
        if p:
            pendentes.append((d, *p))
    if not pendentes:
        raise ErroPeriodizacao(f"Não há diferimentos por reconhecer até {fim:%d/%m/%Y}.")
    linhas = []
    for d, _meses, valor, _ in pendentes:
        diferida = CONTA_DIFERIMENTO[d.tipo]
        if d.tipo == "gasto":
            linhas += [LinhaNova(d.conta, debito=valor, descricao=d.descricao),
                       LinhaNova(diferida, credito=valor, descricao="Gastos a reconhecer")]
        else:
            linhas += [LinhaNova(diferida, debito=valor, descricao="Rendimentos a reconhecer"),
                       LinhaNova(d.conta, credito=valor, descricao=d.descricao)]
    la = lancamentos.criar(db, empresa, "OD", fim, f"Reconhecimento de diferimentos até {fim:%m/%Y}", linhas,
                           documento="Mapa de diferimentos", origem="diferimento_reconhecimento",
                           utilizador_id=utilizador_id,
                           notas=[f"{d.descricao}: {m} mês(es), {v:.2f} €" for d, m, v, _ in pendentes])
    for d, meses, valor, ate_mes in pendentes:
        d.reconhecimentos = [*(d.reconhecimentos or []),
                             {"ate": ate_mes.isoformat(), "meses": meses, "valor": valor, "lancamento_id": la.id}]
    db.flush()
    return la


def diferimento_dict(db: Session, d: Diferimento, hoje: date | None = None) -> dict:
    hoje = hoje or date.today()
    meses_feitos, valor_feito = reconhecido(db, d)
    fim = fim_do_mes(_somar_meses(d.inicio, d.meses - 1))
    pendente = por_reconhecer(db, d, date(hoje.year, hoje.month, 1) - timedelta(days=1))  # só meses já terminados
    return {
        "id": d.id, "tipo": d.tipo, "descricao": d.descricao, "conta": d.conta, "valor": d.valor,
        "inicio": d.inicio, "fim": fim, "meses": d.meses, "mensal": _r(d.valor / d.meses),
        "meses_reconhecidos": meses_feitos, "reconhecido": valor_feito, "por_reconhecer": _r(d.valor - valor_feito),
        "atrasado": {"meses": pendente[0], "valor": pendente[1]} if pendente else None,
        "anulado": not _ativo(db, d.lancamento_id), "lancamento_id": d.lancamento_id,
    }


def em_atraso(db: Session, empresa_id: int, ate: date) -> list[tuple[Diferimento, int, float]]:
    resultado = []
    for d in db.scalars(select(Diferimento).where(Diferimento.empresa_id == empresa_id)):
        p = por_reconhecer(db, d, ate)
        if p:
            resultado.append((d, p[0], p[1]))
    return resultado
