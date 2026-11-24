"""
Demonstração dos fluxos de caixa (NCRF 2, método direto; modelo da Portaria n.º 220/2015).

Cada linha de caixa e depósitos bancários (contas 11, 12 e 13) tem um código de fluxo:
- atribuído automaticamente quando o lançamento é criado, pela conta de contrapartida
  (clientes -> recebimentos de clientes, fornecedores e gastos -> pagamentos a fornecedores,
  pessoal -> pagamentos ao pessoal, ativos fixos -> investimento, financiamentos -> financiamento...),
  ou pelo código memorizado para o fornecedor/cliente (subconta de terceiro);
- editável depois, no lançamento ou na reconciliação bancária; a escolha fica memorizada para a
  subconta do fornecedor/cliente da contrapartida;
- vazio nas transferências entre caixa e bancos (lançamentos só com contas 11 a 13), que não são
  fluxos de caixa.

Os saldos de abertura (origem "abertura") também não são fluxos: entram na caixa do início.
Linhas antigas sem código são classificadas pela mesma regra automática ao gerar a demonstração.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.modelos.tabelas import Conta, Lancamento, LinhaLancamento, RegraFluxo

PREFIXOS_CAIXA = ("11", "12", "13")
OPERACIONAIS, INVESTIMENTO, FINANCIAMENTO = "operacionais", "investimento", "financiamento"


@dataclass(frozen=True)
class Rubrica:
    codigo: str
    atividade: str
    descricao: str


RUBRICAS = [
    Rubrica("OP01", OPERACIONAIS, "Recebimentos de clientes"),
    Rubrica("OP02", OPERACIONAIS, "Pagamentos a fornecedores"),
    Rubrica("OP03", OPERACIONAIS, "Pagamentos ao pessoal"),
    Rubrica("OP04", OPERACIONAIS, "Pagamento/recebimento do imposto sobre o rendimento"),
    Rubrica("OP05", OPERACIONAIS, "Outros recebimentos/pagamentos"),
    Rubrica("IP01", INVESTIMENTO, "Pagamentos respeitantes a ativos fixos tangíveis"),
    Rubrica("IP02", INVESTIMENTO, "Pagamentos respeitantes a ativos intangíveis"),
    Rubrica("IP03", INVESTIMENTO, "Pagamentos respeitantes a investimentos financeiros"),
    Rubrica("IP04", INVESTIMENTO, "Pagamentos respeitantes a outros ativos"),
    Rubrica("IR01", INVESTIMENTO, "Recebimentos provenientes de ativos fixos tangíveis"),
    Rubrica("IR02", INVESTIMENTO, "Recebimentos provenientes de ativos intangíveis"),
    Rubrica("IR03", INVESTIMENTO, "Recebimentos provenientes de investimentos financeiros"),
    Rubrica("IR04", INVESTIMENTO, "Recebimentos provenientes de outros ativos"),
    Rubrica("IR05", INVESTIMENTO, "Subsídios ao investimento"),
    Rubrica("IR06", INVESTIMENTO, "Juros e rendimentos similares"),
    Rubrica("IR07", INVESTIMENTO, "Dividendos"),
    Rubrica("FR01", FINANCIAMENTO, "Recebimentos provenientes de financiamentos obtidos"),
    Rubrica("FR02", FINANCIAMENTO, "Realizações de capital e de outros instrumentos de capital próprio"),
    Rubrica("FR03", FINANCIAMENTO, "Cobertura de prejuízos"),
    Rubrica("FR04", FINANCIAMENTO, "Doações"),
    Rubrica("FR05", FINANCIAMENTO, "Outras operações de financiamento (recebimentos)"),
    Rubrica("FP01", FINANCIAMENTO, "Pagamentos respeitantes a financiamentos obtidos"),
    Rubrica("FP02", FINANCIAMENTO, "Juros e gastos similares"),
    Rubrica("FP03", FINANCIAMENTO, "Dividendos"),
    Rubrica("FP04", FINANCIAMENTO, "Reduções de capital e de outros instrumentos de capital próprio"),
    Rubrica("FP05", FINANCIAMENTO, "Outras operações de financiamento (pagamentos)"),
]
POR_CODIGO = {r.codigo: r for r in RUBRICAS}
NOMES_ATIVIDADES = {
    OPERACIONAIS: "Fluxos de caixa das atividades operacionais",
    INVESTIMENTO: "Fluxos de caixa das atividades de investimento",
    FINANCIAMENTO: "Fluxos de caixa das atividades de financiamento",
}

# Conta de contrapartida -> código (o mesmo nos dois sentidos) ou (código se recebimento, código se pagamento).
# O prefixo mais comprido ganha.
MAPA: dict[str, str | tuple[str, str]] = {
    "21": "OP01", "71": "OP01", "72": "OP01",
    "22": "OP02", "3": "OP02", "61": "OP02", "62": "OP02",
    "23": "OP03", "63": "OP03", "245": "OP03", "2421": "OP03",   # 2421: IRS retido do trabalho dependente
    "241": "OP04", "8": "OP04",
    "24": "OP05", "27": "OP05", "28": "OP05", "6": "OP05", "7": "OP05",
    "271": ("IR01", "IP01"), "43": ("IR01", "IP01"), "45": ("IR01", "IP01"),
    "44": ("IR02", "IP02"),
    "41": ("IR03", "IP03"),
    "42": ("IR04", "IP04"), "46": ("IR04", "IP04"),
    "593": "IR05",
    "79": "IR06",
    "25": ("FR01", "FP01"),
    "26": ("FR05", "FP05"), "261": ("FR02", "FP04"), "262": ("FR02", "FP04"),
    "263": ("FR05", "FP03"), "264": ("FR05", "FP03"), "265": ("FR05", "FP03"),
    "51": ("FR02", "FP04"), "52": ("FR02", "FP04"), "53": ("FR02", "FP04"),
    "5": ("FR05", "FP05"),
    "69": "FP02",
}


class ErroFluxo(Exception):
    """Mensagem pronta a mostrar."""


def e_caixa(codigo: str) -> bool:
    return codigo.startswith(PREFIXOS_CAIXA)


def por_conta(conta: str, entrada: bool) -> str:
    for n in range(len(conta), 0, -1):
        alvo = MAPA.get(conta[:n])
        if alvo:
            return alvo if isinstance(alvo, str) else alvo[0 if entrada else 1]
    return "OP05"


def terceiro(codigo: str) -> bool:
    """Subconta de cliente, fornecedor ou outro devedor/credor (onde se memoriza o código escolhido)."""
    return codigo.startswith(("21", "22", "27")) and len(codigo) > 4


def regras(db: Session, empresa_id: int) -> dict[str, str]:
    return dict(db.execute(select(RegraFluxo.conta_codigo, RegraFluxo.fluxo)
                           .where(RegraFluxo.empresa_id == empresa_id)).all())


def automatico(linhas: list[tuple[str, float, float]], indice: int, memorizadas: dict[str, str]) -> str | None:
    """
    Código da linha `indice` (de caixa) de um lançamento com as linhas (conta, débito, crédito):
    None nas transferências entre caixa e bancos; senão, pela maior contrapartida.
    """
    conta, debito, credito = linhas[indice]
    contrapartidas = [(c, max(d, cr)) for c, d, cr in linhas if not e_caixa(c) and (d or cr)]
    if not contrapartidas:
        return None
    for c, _ in contrapartidas:
        if c in memorizadas:
            return memorizadas[c]
    principal = max(contrapartidas, key=lambda x: x[1])[0]
    return por_conta(principal, debito > credito)


def atribuir(db: Session, empresa_id: int, contas: list[str], novas: list[LinhaLancamento],
             escolhidos: list[str | None]) -> None:
    """Preenche o código de fluxo das linhas de caixa de um lançamento novo (escolhido ou automático)."""
    if not any(e_caixa(c) for c in contas):
        return
    memorizadas = None
    linhas = [(c, ln.debito, ln.credito) for c, ln in zip(contas, novas, strict=True)]
    for i, (conta, ln, escolhido) in enumerate(zip(contas, novas, escolhidos, strict=True)):
        if not e_caixa(conta):
            continue
        if escolhido:
            if escolhido not in POR_CODIGO:
                raise ErroFluxo(f"Código de fluxo de caixa desconhecido: {escolhido}.")
            ln.fluxo_caixa = escolhido
            continue
        if memorizadas is None:
            memorizadas = regras(db, empresa_id)
        ln.fluxo_caixa = automatico(linhas, i, memorizadas)


def alterar(db: Session, empresa_id: int, linha_id: int, fluxo: str | None, memorizar: bool = True) -> LinhaLancamento:
    """Muda o código de uma linha de caixa já lançada e memoriza-o para o terceiro da contrapartida."""
    linha = db.scalar(
        select(LinhaLancamento).join(LinhaLancamento.lancamento)
        .options(joinedload(LinhaLancamento.conta),
                 joinedload(LinhaLancamento.lancamento).selectinload(Lancamento.linhas).joinedload(LinhaLancamento.conta))
        .where(LinhaLancamento.id == linha_id, Lancamento.empresa_id == empresa_id))
    if linha is None:
        raise ErroFluxo("Linha não encontrada.")
    if not e_caixa(linha.conta.codigo):
        raise ErroFluxo("Só as linhas de caixa e bancos (contas 11 a 13) têm código de fluxo de caixa.")
    if fluxo is not None and fluxo not in POR_CODIGO:
        raise ErroFluxo(f"Código de fluxo de caixa desconhecido: {fluxo}.")
    outras = [ln for ln in linha.lancamento.linhas if not e_caixa(ln.conta.codigo)]
    if fluxo and not outras:
        raise ErroFluxo("É uma transferência entre caixa e bancos: não é um fluxo de caixa.")
    linha.fluxo_caixa = fluxo
    if memorizar and fluxo:
        for ln in outras:
            if terceiro(ln.conta.codigo):
                memorizar_para(db, empresa_id, ln.conta.codigo, fluxo)
    return linha


def memorizar_para(db: Session, empresa_id: int, conta_codigo: str, fluxo: str) -> None:
    regra = db.scalar(select(RegraFluxo).where(RegraFluxo.empresa_id == empresa_id,
                                               RegraFluxo.conta_codigo == conta_codigo))
    if regra is None:
        db.add(RegraFluxo(empresa_id=empresa_id, conta_codigo=conta_codigo, fluxo=fluxo))
    else:
        regra.fluxo = fluxo


# ---------- Demonstração ----------

def _r(v: float) -> float:
    return round(v + 0.0, 2)


def _caixa_ate(db: Session, empresa_id: int, ate: date) -> float:
    total = 0.0
    consulta = (select(Conta.codigo, LinhaLancamento.debito, LinhaLancamento.credito)
                .join(LinhaLancamento.conta).join(LinhaLancamento.lancamento)
                .where(Lancamento.empresa_id == empresa_id, Lancamento.data <= ate))
    for codigo, d, c in db.execute(consulta):
        if e_caixa(codigo):
            total += d - c
    return total


def _fluxos(db: Session, empresa_id: int, de: date, ate: date) -> tuple[dict[str, float], float]:
    """({código: valor com sinal}, caixa dos saldos de abertura lançados no período)."""
    lancs = db.scalars(
        select(Lancamento).options(selectinload(Lancamento.linhas).joinedload(LinhaLancamento.conta))
        .where(Lancamento.empresa_id == empresa_id, Lancamento.data >= de, Lancamento.data <= ate)
    ).all()
    memorizadas = regras(db, empresa_id)
    valores: dict[str, float] = defaultdict(float)
    abertura = 0.0
    for la in lancs:
        linhas = [(ln.conta.codigo, ln.debito, ln.credito) for ln in la.linhas]
        for i, ln in enumerate(la.linhas):
            if not e_caixa(ln.conta.codigo):
                continue
            if la.origem == "abertura":
                abertura += ln.debito - ln.credito
                continue
            codigo = ln.fluxo_caixa or automatico(linhas, i, memorizadas)
            if codigo:
                valores[codigo] += ln.debito - ln.credito
    return valores, abertura


def _periodo_anterior(de: date, ate: date) -> tuple[date, date]:
    """O mesmo intervalo um ano antes (29 de fevereiro passa a 28)."""
    def menos_um_ano(d: date) -> date:
        try:
            return d.replace(year=d.year - 1)
        except ValueError:
            return d.replace(year=d.year - 1, day=28)
    return menos_um_ano(de), menos_um_ano(ate)


def _coluna(db: Session, empresa_id: int, de: date, ate: date) -> dict:
    valores, abertura = _fluxos(db, empresa_id, de, ate)
    inicio = _caixa_ate(db, empresa_id, de - timedelta(days=1)) + abertura
    fim = _caixa_ate(db, empresa_id, ate)
    totais = {a: sum(v for c, v in valores.items() if POR_CODIGO[c].atividade == a) for a in NOMES_ATIVIDADES}
    variacao = sum(totais.values())
    return {
        "de": de, "ate": ate, "valores": {c: _r(v) for c, v in valores.items()},
        "caixa_gerada_operacoes": _r(sum(valores.get(c, 0.0) for c in ("OP01", "OP02", "OP03"))),
        "totais": {a: _r(v) for a, v in totais.items()}, "variacao": _r(variacao),
        "caixa_inicio": _r(inicio), "caixa_fim": _r(fim),
        "diferenca": _r(fim - inicio - variacao),   # 0 quando tudo está classificado
    }


def demonstracao(db: Session, empresa_id: int, de: date, ate: date) -> dict:
    atual = _coluna(db, empresa_id, de, ate)
    anterior = _coluna(db, empresa_id, *_periodo_anterior(de, ate))
    seccoes = []
    for atividade, nome in NOMES_ATIVIDADES.items():
        linhas = [{"codigo": r.codigo, "descricao": r.descricao,
                   "valor": atual["valores"].get(r.codigo, 0.0), "anterior": anterior["valores"].get(r.codigo, 0.0)}
                  for r in RUBRICAS if r.atividade == atividade]
        seccoes.append({"atividade": atividade, "nome": nome, "linhas": linhas,
                        "total": atual["totais"][atividade], "total_anterior": anterior["totais"][atividade]})
    chaves = ("caixa_gerada_operacoes", "variacao", "caixa_inicio", "caixa_fim", "diferenca")
    return {
        "de": de, "ate": ate, "anterior_de": anterior["de"], "anterior_ate": anterior["ate"],
        "seccoes": seccoes, **{k: atual[k] for k in chaves}, "anteriores": {k: anterior[k] for k in chaves},
    }


def rubricas() -> list[dict]:
    return [{"codigo": r.codigo, "atividade": r.atividade, "descricao": r.descricao} for r in RUBRICAS]
