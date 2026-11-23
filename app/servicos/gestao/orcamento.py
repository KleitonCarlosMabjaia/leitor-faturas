"""
Orçamento anual por rubrica de gestão e comparação com o real da contabilidade.

O real de cada mês vem dos lançamentos (classes 6 e 7 e compras na 31), com as mesmas
rubricas da demonstração de resultados; o apuramento do fecho do exercício não conta.
"""

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modelos.tabelas import LinhaOrcamento
from app.servicos.contabilidade import relatorios, salarios

# (chave, nome, prefixos das contas, +1 rendimento / -1 gasto)
RUBRICAS = [
    ("vendas", "Vendas e serviços prestados", ("71", "72"), 1),
    ("outros_rendimentos", "Outros rendimentos", ("73", "74", "75", "76", "77", "78", "79"), 1),
    ("cmvmc", "Compras e custo das mercadorias", ("61", "31"), -1),
    ("fse", "Fornecimentos e serviços externos", ("62",), -1),
    ("pessoal", "Gastos com o pessoal", ("63",), -1),
    ("outros_gastos", "Depreciações, juros e outros gastos", ("64", "65", "66", "67", "68", "69"), -1),
]
CHAVES = {r[0] for r in RUBRICAS}


class ErroOrcamento(Exception):
    """Mensagem pronta a mostrar."""


def _r(v: float) -> float:
    return round(v + 0.0, 2)


def real(db: Session, empresa_id: int, ano: int, ate: date | None = None) -> dict[str, list[float]]:
    """{rubrica: 12 valores} (positivos) do que foi lançado em cada mês do ano."""
    resultado = {chave: [0.0] * 12 for chave in CHAVES}
    for mes in range(1, 13):
        inicio = date(ano, mes, 1)
        if ate and inicio > ate:
            break
        movimentos = relatorios.movimentos_por_conta(db, empresa_id, inicio, salarios.fim_do_mes(ano, mes),
                                                     excluir_origem="fecho")
        for chave, _, prefixos, sinal in RUBRICAS:
            valor = sum((c - d) for cod, (d, c) in movimentos.items() if cod.startswith(prefixos))
            resultado[chave][mes - 1] = _r(valor if sinal > 0 else -valor)
    return resultado


def orcado(db: Session, empresa_id: int, ano: int) -> dict[str, list[float]]:
    linhas = {ln.rubrica: ln.valores for ln in db.scalars(select(LinhaOrcamento).where(
        LinhaOrcamento.empresa_id == empresa_id, LinhaOrcamento.ano == ano))}
    return {chave: [float(v) for v in linhas.get(chave, [0.0] * 12)] for chave in CHAVES}


def guardar(db: Session, empresa_id: int, ano: int, valores: dict[str, list[float]]) -> None:
    for chave, lista in valores.items():
        if chave not in CHAVES:
            raise ErroOrcamento(f"Rubrica desconhecida: {chave}.")
        if len(lista) != 12 or any(v is None or v < 0 for v in lista):
            raise ErroOrcamento("Indique 12 valores (de janeiro a dezembro), sem valores negativos.")
        linha = db.scalar(select(LinhaOrcamento).where(
            LinhaOrcamento.empresa_id == empresa_id, LinhaOrcamento.ano == ano, LinhaOrcamento.rubrica == chave))
        if linha is None:
            linha = LinhaOrcamento(empresa_id=empresa_id, ano=ano, rubrica=chave)
            db.add(linha)
        linha.valores = [_r(v) for v in lista]
    db.flush()


def proposta(db: Session, empresa_id: int, ano: int, crescimento: float = 0.0) -> dict[str, list[float]]:
    """Orçamento sugerido: o real do ano anterior, mês a mês, com a variação indicada (%)."""
    anterior = real(db, empresa_id, ano - 1)
    fator = 1 + crescimento / 100
    return {chave: [_r(max(v, 0.0) * (fator if sinal > 0 else 1)) for v in anterior[chave]]
            for chave, _, _, sinal in RUBRICAS}


def comparar(db: Session, empresa_id: int, ano: int, hoje: date | None = None) -> dict:
    hoje = hoje or date.today()
    ate_mes = 12 if ano < hoje.year else (hoje.month if ano == hoje.year else 0)
    o, r = orcado(db, empresa_id, ano), real(db, empresa_id, ano, hoje)
    rubricas = []
    for chave, nome, _, sinal in RUBRICAS:
        orc_ate = _r(sum(o[chave][:ate_mes]))
        real_ate = _r(sum(r[chave][:ate_mes]))
        desvio = _r((real_ate - orc_ate) * sinal)   # positivo = melhor do que o orçado
        rubricas.append({
            "chave": chave, "nome": nome, "tipo": "rendimento" if sinal > 0 else "gasto",
            "orcado": o[chave], "real": r[chave], "orcado_ano": _r(sum(o[chave])), "real_ano": _r(sum(r[chave])),
            "orcado_ate": orc_ate, "real_ate": real_ate, "desvio": desvio,
            "execucao": round(real_ate / orc_ate * 100, 1) if orc_ate else None,
        })

    def resultado(campo: str, ate: int | None = None) -> float:
        total = 0.0
        for x, (_, _, _, sinal) in zip(rubricas, RUBRICAS, strict=True):
            valor = sum(x[campo][:ate]) if ate is not None else x[campo]
            total += valor * sinal
        return _r(total)

    return {
        "ano": ano, "ate_mes": ate_mes, "rubricas": rubricas, "tem_orcamento": any(any(v) for v in o.values()),
        "resultado_orcado_ano": resultado("orcado", 12), "resultado_orcado_ate": resultado("orcado", ate_mes),
        "resultado_real_ate": resultado("real", ate_mes),
    }
