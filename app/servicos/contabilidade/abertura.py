"""
Saldos de abertura: para uma empresa que já existia quando começa a usar a aplicação.

Os saldos do balancete de fecho do programa anterior (classes 1 a 5) entram num único
lançamento de abertura (diário de operações diversas, origem "abertura"), no 1.º dia do
exercício. Contas que não existam no plano são criadas como subcontas da conta-mãe mais
próxima (ex.: 22110015 debaixo de 2211), desde que essa conta-mãe ainda não tenha movimentos.

As funções não fazem commit: quem as chama decide quando gravar.
"""

import re
from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modelos.tabelas import LANCAMENTO_ATIVO, Conta, Empresa, Lancamento, LinhaLancamento
from app.servicos import validacao
from app.servicos.contabilidade import extratos, lancamentos, plano_snc
from app.servicos.contabilidade.lancamentos import LinhaNova

CLASSES_PERMITIDAS = ("1", "2", "3", "4", "5")


class ErroAbertura(Exception):
    """Saldos de abertura inválidos (mensagem pronta a mostrar)."""


@dataclass
class SaldoAbertura:
    conta: str
    debito: float = 0.0
    credito: float = 0.0
    nome: str | None = None
    nif: str | None = None


def existente(db: Session, empresa_id: int) -> Lancamento | None:
    return db.scalar(select(Lancamento).where(
        Lancamento.empresa_id == empresa_id, Lancamento.origem == "abertura",
        Lancamento.estado == LANCAMENTO_ATIVO, Lancamento.estorno_de_id.is_(None)))


def _criar_conta(db: Session, empresa_id: int, saldo: SaldoAbertura, contas: dict[str, Conta]) -> Conta:
    codigo = saldo.conta
    mae = next((contas[codigo[:n]] for n in range(len(codigo) - 1, 0, -1) if codigo[:n] in contas), None)
    if mae is None or len(mae.codigo) < 2:
        raise ErroAbertura(f"A conta {codigo} não existe e não encontrei a conta-mãe no plano.")
    if mae.movimento:
        movimentos = db.scalar(select(func.count(LinhaLancamento.id)).where(LinhaLancamento.conta_id == mae.id))
        if movimentos:
            raise ErroAbertura(f"A conta {codigo} não existe e a conta-mãe {mae.codigo} já tem movimentos: "
                               f"use a {mae.codigo} ou crie a subconta no plano de contas.")
        mae.movimento = False
    nif = (saldo.nif or "").strip() or None
    if nif and not validacao.nif_valido(nif):
        nif = None
    conta = Conta(empresa_id=empresa_id, codigo=codigo, nome=(saldo.nome or f"Conta {codigo}").strip()[:200],
                  movimento=True, nif=nif)
    db.add(conta)
    db.flush()
    contas[codigo] = conta
    return conta


def lancar(db: Session, empresa: Empresa, data: date, saldos: list[SaldoAbertura],
           utilizador_id: int | None = None) -> Lancamento:
    if existente(db, empresa.id):
        raise ErroAbertura("Os saldos de abertura já foram lançados: estorne esse lançamento para os voltar a lançar.")
    plano_snc.garantir_plano(db, empresa.id)
    contas = plano_snc.contas_por_codigo(db, empresa.id)
    agrupados: dict[str, SaldoAbertura] = {}
    for s in saldos:
        codigo = re.sub(r"\D", "", s.conta or "")
        if not codigo:
            continue
        if not codigo.startswith(CLASSES_PERMITIDAS):
            raise ErroAbertura(f"A conta {codigo} não é de balanço: a abertura só leva contas das classes 1 a 5 "
                               "(os gastos e rendimentos do ano anterior já estão no resultado).")
        if s.debito < 0 or s.credito < 0:
            raise ErroAbertura(f"A conta {codigo} tem valores negativos: troque o lado.")
        a = agrupados.setdefault(codigo, SaldoAbertura(codigo, nome=s.nome, nif=s.nif))
        a.debito += s.debito
        a.credito += s.credito
    # Um balancete traz também as linhas das contas agregadoras (2, 22, 221...): contam só as de movimento
    codigos = set(agrupados)
    folhas = {c: s for c, s in agrupados.items() if not any(o != c and o.startswith(c) for o in codigos)}
    linhas = []
    for codigo, s in sorted(folhas.items()):
        saldo = round(s.debito - s.credito, 2)
        if not saldo:
            continue
        conta = contas.get(codigo) or _criar_conta(db, empresa.id, s, contas)
        if not conta.movimento:
            raise ErroAbertura(f"A conta {codigo} {conta.nome} é agregadora: indique os saldos das subcontas.")
        linhas.append(LinhaNova(codigo, debito=max(saldo, 0), credito=max(-saldo, 0), descricao="Saldo de abertura"))
    if not linhas:
        raise ErroAbertura("Não há saldos para lançar.")
    return lancamentos.criar(db, empresa, "OD", data, f"Abertura do exercício de {data.year} - saldos iniciais",
                             linhas, documento=f"Abertura {data.year}", origem="abertura", utilizador_id=utilizador_id)


def _mapear(cabecalho: list[str]) -> dict[str, int] | None:
    papeis = {
        "conta": ("conta", "código", "codigo", "cod"),
        "nome": ("nome", "descrição", "descricao", "designação", "designacao"),
        "debito": ("débito", "debito", "saldo devedor", "devedor"),
        "credito": ("crédito", "credito", "saldo credor", "credor"),
        "saldo": ("saldo",),
        "nif": ("nif", "contribuinte"),
    }
    mapa: dict[str, int] = {}
    for j, celula in enumerate(cabecalho):
        texto = celula.strip().lower()
        for papel, nomes in papeis.items():
            if papel not in mapa and any(texto == n or texto.startswith(n) for n in nomes):
                mapa[papel] = j
                break
    if "conta" in mapa and (("debito" in mapa and "credito" in mapa) or "saldo" in mapa):
        return mapa
    return None


def ler_ficheiro(conteudo: bytes, nome_ficheiro: str) -> list[SaldoAbertura]:
    """Balancete em CSV ou Excel: conta, nome, débito e crédito (ou uma coluna de saldo com sinal)."""
    if not conteudo:
        raise ErroAbertura("O ficheiro está vazio.")
    try:
        if nome_ficheiro.lower().endswith((".xlsx", ".xlsm")) or conteudo[:2] == b"PK":
            linhas = extratos._linhas_xlsx(conteudo)
        else:
            linhas = extratos._linhas_csv(extratos._texto(conteudo))
    except extratos.ErroExtrato as erro:
        raise ErroAbertura(str(erro))
    for indice, linha in enumerate(linhas[:30]):
        mapa = _mapear([str(c or "") for c in linha])
        if mapa:
            cabecalho = indice
            break
    else:
        raise ErroAbertura("Não reconheci as colunas: o ficheiro precisa de conta e de débito e crédito (ou saldo).")

    def celula(linha, papel):
        j = mapa.get(papel)
        return linha[j] if j is not None and j < len(linha) else None

    saldos = []
    for linha in linhas[cabecalho + 1:]:
        conta = re.sub(r"\D", "", str(celula(linha, "conta") or ""))
        if not conta:
            continue
        if "saldo" in mapa and "debito" not in mapa:
            valor = extratos.ler_numero(celula(linha, "saldo")) or 0.0
            debito, credito = max(valor, 0.0), max(-valor, 0.0)
        else:
            debito = extratos.ler_numero(celula(linha, "debito")) or 0.0
            credito = extratos.ler_numero(celula(linha, "credito")) or 0.0
        saldos.append(SaldoAbertura(conta, abs(debito), abs(credito), str(celula(linha, "nome") or "") or None,
                                    str(celula(linha, "nif") or "") or None))
    if not saldos:
        raise ErroAbertura("O ficheiro não tem contas com saldo.")
    return saldos
