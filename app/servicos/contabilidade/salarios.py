"""
Processamento de salários e o seu lançamento (SNC).

Recibo de cada pessoa, por mês:
    remuneração base + subsídios de férias e de Natal (no mês próprio ou em duodécimos)
    + outros abonos + subsídio de alimentação
    - Segurança Social do trabalhador (11%) - retenção de IRS = líquido a receber
O subsídio de alimentação só está isento até ao limite diário (numerário ou cartão); o excesso
soma à remuneração sujeita a IRS e a Segurança Social.

Lançamento do mês (diário de operações diversas, origem "salarios"):
    D 632 Remunerações do pessoal (631 para órgãos sociais)  pelo ilíquido
    D 635 Encargos sobre remunerações                         Segurança Social da entidade (23,75%)
    C 2421 Retenção de IRS - trabalho dependente
    C 245 Contribuições para a Segurança Social              trabalhador + entidade
    C 2312 Remunerações a pagar ao pessoal (2311 órgãos sociais) pelo líquido
Pagamentos (origem "salarios_pagamento"): salários (D 231x), Segurança Social até dia 20
(D 245) e IRS retido até dia 20 (D 2421), contra caixa ou bancos.

A taxa de retenção de IRS de cada pessoa é indicada pelo utilizador, a partir das tabelas de
retenção na fonte em vigor (dependem do vencimento, da situação familiar e dos dependentes).
As taxas e limites por omissão estão aqui e não espalhados pelo código.

As funções não fazem commit: quem as chama decide quando gravar.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modelos.tabelas import (
    FUNCIONARIO_ORGAOS,
    FUNCIONARIO_PESSOAL,
    LANCAMENTO_ATIVO,
    Empresa,
    Funcionario,
    Lancamento,
    ProcessamentoSalarial,
)
from app.servicos import validacao
from app.servicos.contabilidade import lancamentos, periodizacao
from app.servicos.contabilidade.lancamentos import LinhaNova

# Limites diários de isenção do subsídio de alimentação em 2026 (confirmar no Orçamento do Estado)
LIMITE_ALIMENTACAO_NUMERARIO = 6.15
LIMITE_ALIMENTACAO_CARTAO = 10.46
DIAS_UTEIS = 22
# Taxas contributivas do regime geral: (trabalhador, entidade empregadora)
TAXAS_SS = {FUNCIONARIO_PESSOAL: (11.0, 23.75), FUNCIONARIO_ORGAOS: (11.0, 20.3)}
CONTAS = {  # tipo -> (gasto, remunerações a pagar)
    FUNCIONARIO_PESSOAL: ("632", "2312"),
    FUNCIONARIO_ORGAOS: ("631", "2311"),
}
CONTA_ENCARGOS, CONTA_IRS, CONTA_SS = "635", "2421", "245"
MES_SUBSIDIO_FERIAS, MES_SUBSIDIO_NATAL = 6, 11
PAGAMENTOS = {
    "salarios": "Salários",
    "seguranca_social": "Segurança Social",
    "irs": "IRS retido",
}
DESCRICOES_PAGAMENTO = {
    "salarios": "Pagamento de salários",
    "seguranca_social": "Pagamento à Segurança Social",
    "irs": "Pagamento do IRS retido",
}
AVISO = ("A taxa de retenção de IRS de cada pessoa vem das tabelas de retenção na fonte em vigor (situação "
         "familiar, dependentes e vencimento). A declaração mensal de remunerações (AT) e a declaração de "
         "remunerações da Segurança Social entregam-se nos respetivos portais com os valores deste mapa.")
NOMES_MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro",
               "outubro", "novembro", "dezembro"]


class ErroSalarios(Exception):
    """Dados ou operação de salários inválidos (mensagem pronta a mostrar)."""


def fim_do_mes(ano: int, mes: int) -> date:
    return date(ano + mes // 12, mes % 12 + 1, 1) - timedelta(days=1)


def nome_mes(ano: int, mes: int) -> str:
    return f"{NOMES_MESES[mes - 1]} de {ano}"


def _r(v: float) -> float:
    return round(v + 0.0, 2)


# ---------- Funcionários ----------

def registar(db: Session, empresa: Empresa, *, nome: str, data_admissao: date, salario_base: float,
             tipo: str = FUNCIONARIO_PESSOAL, nif: str | None = None, niss: str | None = None,
             subsidio_alimentacao: float = 0.0, alimentacao_cartao: bool = True, duodecimos: bool = False,
             taxa_irs: float = 0.0, taxa_ss_trabalhador: float | None = None,
             taxa_ss_entidade: float | None = None) -> Funcionario:
    f = Funcionario(empresa_id=empresa.id, numero=(db.scalar(select(func.max(Funcionario.numero)).where(
        Funcionario.empresa_id == empresa.id)) or 0) + 1)
    db.add(f)
    alterar(f, nome=nome, data_admissao=data_admissao, salario_base=salario_base, tipo=tipo, nif=nif, niss=niss,
            subsidio_alimentacao=subsidio_alimentacao, alimentacao_cartao=alimentacao_cartao, duodecimos=duodecimos,
            taxa_irs=taxa_irs, taxa_ss_trabalhador=taxa_ss_trabalhador, taxa_ss_entidade=taxa_ss_entidade)
    db.flush()
    return f


def alterar(f: Funcionario, *, nome: str, data_admissao: date, salario_base: float, tipo: str, nif: str | None,
            niss: str | None, subsidio_alimentacao: float, alimentacao_cartao: bool, duodecimos: bool,
            taxa_irs: float, taxa_ss_trabalhador: float | None, taxa_ss_entidade: float | None,
            data_saida: date | None = None) -> None:
    if tipo not in CONTAS:
        raise ErroSalarios("Tipo desconhecido: pessoal ou órgãos sociais.")
    if not nome.strip():
        raise ErroSalarios("Indique o nome.")
    if salario_base < 0 or subsidio_alimentacao < 0:
        raise ErroSalarios("Os valores não podem ser negativos.")
    nif = (nif or "").strip() or None
    if nif and not validacao.nif_valido(nif):
        raise ErroSalarios(f"O NIF {nif} não é válido.")
    niss = (niss or "").strip() or None
    if niss and not (niss.isdigit() and len(niss) == 11):
        raise ErroSalarios("O número de identificação da Segurança Social tem 11 algarismos.")
    for taxa in (taxa_irs, taxa_ss_trabalhador or 0, taxa_ss_entidade or 0):
        if not 0 <= taxa <= 100:
            raise ErroSalarios("As taxas têm de estar entre 0 e 100%.")
    if data_saida and data_saida < data_admissao:
        raise ErroSalarios("A data de saída é anterior à de admissão.")
    omissao_trab, omissao_ent = TAXAS_SS[tipo]
    f.nome, f.tipo, f.nif, f.niss = nome.strip()[:200], tipo, nif, niss
    f.data_admissao, f.data_saida = data_admissao, data_saida
    f.salario_base, f.subsidio_alimentacao = _r(salario_base), _r(subsidio_alimentacao)
    f.alimentacao_cartao, f.duodecimos, f.taxa_irs = alimentacao_cartao, duodecimos, round(taxa_irs, 2)
    f.taxa_ss_trabalhador = omissao_trab if taxa_ss_trabalhador is None else round(taxa_ss_trabalhador, 2)
    f.taxa_ss_entidade = omissao_ent if taxa_ss_entidade is None else round(taxa_ss_entidade, 2)


def funcionario_dict(f: Funcionario) -> dict:
    return {
        "id": f.id, "numero": f.numero, "nome": f.nome, "nif": f.nif, "niss": f.niss, "tipo": f.tipo,
        "data_admissao": f.data_admissao, "data_saida": f.data_saida, "salario_base": f.salario_base,
        "subsidio_alimentacao": f.subsidio_alimentacao, "alimentacao_cartao": f.alimentacao_cartao,
        "duodecimos": f.duodecimos, "taxa_irs": f.taxa_irs, "taxa_ss_trabalhador": f.taxa_ss_trabalhador,
        "taxa_ss_entidade": f.taxa_ss_entidade, "ativo": f.data_saida is None or f.data_saida >= date.today(),
    }


def ativos_no_mes(db: Session, empresa_id: int, ano: int, mes: int) -> list[Funcionario]:
    inicio, fim = date(ano, mes, 1), fim_do_mes(ano, mes)
    return list(db.scalars(select(Funcionario).where(
        Funcionario.empresa_id == empresa_id, Funcionario.data_admissao <= fim,
        (Funcionario.data_saida.is_(None)) | (Funcionario.data_saida >= inicio),
    ).order_by(Funcionario.numero)))


# ---------- Cálculo dos recibos ----------

@dataclass
class Variaveis:
    """O que muda de mês para mês numa pessoa."""
    dias: int = DIAS_UTEIS          # dias com direito a subsídio de alimentação
    outros: float = 0.0             # prémios, horas extraordinárias... (sujeitos a IRS e SS)
    faltas: float = 0.0             # desconto por faltas não remuneradas


@dataclass
class Opcoes:
    subsidio_ferias: bool = False
    subsidio_natal: bool = False
    limite_numerario: float = LIMITE_ALIMENTACAO_NUMERARIO
    limite_cartao: float = LIMITE_ALIMENTACAO_CARTAO
    variaveis: dict[int, Variaveis] = field(default_factory=dict)


def recibo(f: Funcionario, opcoes: Opcoes, var: Variaveis | None = None) -> dict:
    var = var or Variaveis()
    base = _r(f.salario_base)
    if f.duodecimos:
        ferias = natal = _r(base / 12)
    else:
        ferias = base if opcoes.subsidio_ferias else 0.0
        natal = base if opcoes.subsidio_natal else 0.0
    dias = max(int(var.dias), 0)
    alimentacao = _r(dias * f.subsidio_alimentacao)
    limite = opcoes.limite_cartao if f.alimentacao_cartao else opcoes.limite_numerario
    alimentacao_isenta = _r(dias * min(f.subsidio_alimentacao, limite))
    alimentacao_sujeita = _r(alimentacao - alimentacao_isenta)
    faltas = _r(max(var.faltas, 0))
    outros = _r(max(var.outros, 0))
    sujeita = _r(base + ferias + natal + outros + alimentacao_sujeita - faltas)
    if sujeita < 0:
        raise ErroSalarios(f"O desconto por faltas de {f.nome} é maior do que a remuneração.")
    ss_trabalhador = _r(sujeita * f.taxa_ss_trabalhador / 100)
    ss_entidade = _r(sujeita * f.taxa_ss_entidade / 100)
    irs = _r(sujeita * f.taxa_irs / 100)
    iliquido = _r(base + ferias + natal + outros + alimentacao - faltas)
    return {
        "funcionario_id": f.id, "numero": f.numero, "nome": f.nome, "nif": f.nif, "niss": f.niss, "tipo": f.tipo,
        "base": base, "subsidio_ferias": ferias, "subsidio_natal": natal, "outros": outros, "faltas": faltas,
        "dias": dias, "alimentacao": alimentacao, "alimentacao_isenta": alimentacao_isenta,
        "remuneracao_sujeita": sujeita, "taxa_irs": f.taxa_irs, "irs": irs,
        "taxa_ss_trabalhador": f.taxa_ss_trabalhador, "ss_trabalhador": ss_trabalhador,
        "taxa_ss_entidade": f.taxa_ss_entidade, "ss_entidade": ss_entidade,
        "iliquido": iliquido, "liquido": _r(iliquido - ss_trabalhador - irs),
        "custo_total": _r(iliquido + ss_entidade),
    }


def opcoes_do_mes(mes: int, **kwargs) -> Opcoes:
    """Por omissão, o subsídio de férias vai em junho e o de Natal em novembro."""
    kwargs.setdefault("subsidio_ferias", mes == MES_SUBSIDIO_FERIAS)
    kwargs.setdefault("subsidio_natal", mes == MES_SUBSIDIO_NATAL)
    return Opcoes(**kwargs)


def calcular(db: Session, empresa_id: int, ano: int, mes: int, opcoes: Opcoes) -> list[dict]:
    return [recibo(f, opcoes, opcoes.variaveis.get(f.id)) for f in ativos_no_mes(db, empresa_id, ano, mes)]


def totais(recibos: list[dict]) -> dict:
    chaves = ("base", "subsidio_ferias", "subsidio_natal", "outros", "faltas", "alimentacao", "remuneracao_sujeita",
              "irs", "ss_trabalhador", "ss_entidade", "iliquido", "liquido", "custo_total")
    t = {k: _r(sum(r[k] for r in recibos)) for k in chaves}
    t["seguranca_social"] = _r(t["ss_trabalhador"] + t["ss_entidade"])
    return t


# ---------- Processamento e lançamento ----------

def obter(db: Session, empresa_id: int, ano: int, mes: int) -> ProcessamentoSalarial | None:
    return db.scalar(select(ProcessamentoSalarial).where(
        ProcessamentoSalarial.empresa_id == empresa_id, ProcessamentoSalarial.ano == ano,
        ProcessamentoSalarial.mes == mes))


def _ativo(db: Session, lancamento_id: int | None) -> Lancamento | None:
    if not lancamento_id:
        return None
    la = db.get(Lancamento, lancamento_id)
    return la if la and la.estado == LANCAMENTO_ATIVO else None


def processado(db: Session, p: ProcessamentoSalarial | None) -> bool:
    return bool(p and _ativo(db, p.lancamento_id))


def linhas_processamento(recibos: list[dict]) -> list[LinhaNova]:
    linhas: list[LinhaNova] = []
    por_tipo: dict[str, dict] = {}
    for r in recibos:
        t = por_tipo.setdefault(r["tipo"], {"iliquido": 0.0, "liquido": 0.0, "ss_entidade": 0.0})
        for k in t:
            t[k] += r[k]
    irs = _r(sum(r["irs"] for r in recibos))
    ss = _r(sum(r["ss_trabalhador"] + r["ss_entidade"] for r in recibos))
    for tipo, t in sorted(por_tipo.items()):
        gasto, a_pagar = CONTAS[tipo]
        nome = "órgãos sociais" if tipo == FUNCIONARIO_ORGAOS else "pessoal"
        linhas.append(LinhaNova(gasto, debito=_r(t["iliquido"]), descricao=f"Remunerações - {nome}"))
        linhas.append(LinhaNova(CONTA_ENCARGOS, debito=_r(t["ss_entidade"]),
                                descricao=f"Segurança Social da entidade - {nome}"))
        linhas.append(LinhaNova(a_pagar, credito=_r(t["liquido"]), descricao=f"Líquido a pagar - {nome}"))
    linhas.append(LinhaNova(CONTA_IRS, credito=irs, descricao="Retenção de IRS"))
    linhas.append(LinhaNova(CONTA_SS, credito=ss, descricao="Segurança Social (trabalhador e entidade)"))
    return linhas


def processar(db: Session, empresa: Empresa, ano: int, mes: int, opcoes: Opcoes,
              utilizador_id: int | None = None) -> ProcessamentoSalarial:
    existente = obter(db, empresa.id, ano, mes)
    if processado(db, existente):
        raise ErroSalarios(f"Os salários de {nome_mes(ano, mes)} já estão processados: estorne o lançamento "
                           "para voltar a processar.")
    recibos = calcular(db, empresa.id, ano, mes, opcoes)
    if not recibos:
        raise ErroSalarios(f"Não há pessoas ao serviço em {nome_mes(ano, mes)}.")
    if not any(r["iliquido"] for r in recibos):
        raise ErroSalarios("Os recibos estão todos a zero.")
    fim = fim_do_mes(ano, mes)
    t = totais(recibos)
    lancamento = lancamentos.criar(
        db, empresa, "OD", fim, f"Processamento de salários - {nome_mes(ano, mes)}", linhas_processamento(recibos),
        documento=f"Salários {mes:02d}/{ano}", origem="salarios", utilizador_id=utilizador_id,
        notas=[f"{len(recibos)} recibo(s): ilíquido {t['iliquido']:.2f} €, IRS {t['irs']:.2f} €, "
               f"Segurança Social {t['seguranca_social']:.2f} €, líquido {t['liquido']:.2f} €."],
    )
    if existente:  # processamento anterior estornado: é substituído
        existente.recibos, existente.pagamentos, existente.lancamento_id = recibos, {}, lancamento.id
        existente.criado_por_id = utilizador_id
        p = existente
    else:
        p = ProcessamentoSalarial(empresa_id=empresa.id, ano=ano, mes=mes, recibos=recibos, pagamentos={},
                                  lancamento_id=lancamento.id, criado_por_id=utilizador_id)
        db.add(p)
    db.flush()
    return p


def _valor_a_pagar(p: ProcessamentoSalarial, tipo: str) -> list[tuple[str, float]]:
    t = totais(p.recibos)
    if tipo == "seguranca_social":
        return [(CONTA_SS, t["seguranca_social"])]
    if tipo == "irs":
        return [(CONTA_IRS, t["irs"])]
    por_conta: dict[str, float] = {}
    for r in p.recibos:
        conta = CONTAS[r["tipo"]][1]
        por_conta[conta] = _r(por_conta.get(conta, 0.0) + r["liquido"])
    return sorted(por_conta.items())


def estado_pagamentos(db: Session, p: ProcessamentoSalarial) -> dict:
    resultado = {}
    for tipo, nome in PAGAMENTOS.items():
        la = _ativo(db, (p.pagamentos or {}).get(tipo))
        resultado[tipo] = {"nome": nome, "valor": _r(sum(v for _, v in _valor_a_pagar(p, tipo))),
                           "pago": la is not None, "data": la.data if la else None,
                           "lancamento_id": la.id if la else None}
    return resultado


def prazo_pagamento(ano: int, mes: int, tipo: str) -> date:
    """Segurança Social e IRS retido: até ao dia 20 do mês seguinte. Salários: último dia do mês."""
    if tipo == "salarios":
        return fim_do_mes(ano, mes)
    return date(ano + mes // 12, mes % 12 + 1, 20)


def pagar(db: Session, empresa: Empresa, p: ProcessamentoSalarial, tipo: str, data: date,
          conta_financeira: str = "121", utilizador_id: int | None = None) -> Lancamento:
    if tipo not in PAGAMENTOS:
        raise ErroSalarios("Pagamento desconhecido.")
    if not processado(db, p):
        raise ErroSalarios("Este processamento foi estornado.")
    if _ativo(db, (p.pagamentos or {}).get(tipo)):
        raise ErroSalarios(f"O pagamento ({PAGAMENTOS[tipo]}) de {nome_mes(p.ano, p.mes)} já está registado.")
    if not conta_financeira.startswith(("11", "12", "13")):
        raise ErroSalarios("O pagamento tem de sair da caixa (11x) ou de bancos (12x ou 13x).")
    valores = [(c, v) for c, v in _valor_a_pagar(p, tipo) if v]
    if not valores:
        raise ErroSalarios("Não há nada a pagar.")
    total = _r(sum(v for _, v in valores))
    linhas = [LinhaNova(c, debito=v, descricao=PAGAMENTOS[tipo]) for c, v in valores]
    linhas.append(LinhaNova(conta_financeira, credito=total, descricao=PAGAMENTOS[tipo]))
    lancamento = lancamentos.criar(
        db, empresa, "CX" if conta_financeira.startswith("11") else "BAN", data,
        f"{DESCRICOES_PAGAMENTO[tipo]} - {nome_mes(p.ano, p.mes)}", linhas,
        documento=f"Salários {p.mes:02d}/{p.ano}", origem="salarios_pagamento", utilizador_id=utilizador_id,
    )
    p.pagamentos = {**(p.pagamentos or {}), tipo: lancamento.id}  # novo dict: o SQLAlchemy deteta a alteração
    db.flush()
    return lancamento


def processamento_dict(db: Session, p: ProcessamentoSalarial) -> dict:
    return {
        "id": p.id, "ano": p.ano, "mes": p.mes, "nome_mes": nome_mes(p.ano, p.mes),
        "processado": processado(db, p), "lancamento_id": p.lancamento_id, "recibos": p.recibos,
        "totais": totais(p.recibos), "pagamentos": estado_pagamentos(db, p),
        "prazos": {t: prazo_pagamento(p.ano, p.mes, t) for t in PAGAMENTOS},
    }


def mapa_anual(db: Session, empresa_id: int, ano: int) -> dict:
    """Totais por mês (para a declaração mensal de remunerações e a da Segurança Social)."""
    meses = []
    for p in db.scalars(select(ProcessamentoSalarial).where(
            ProcessamentoSalarial.empresa_id == empresa_id, ProcessamentoSalarial.ano == ano)
            .order_by(ProcessamentoSalarial.mes)):
        if processado(db, p):
            meses.append({"mes": p.mes, "nome_mes": nome_mes(ano, p.mes), "pessoas": len(p.recibos),
                          **totais(p.recibos), "pagamentos": estado_pagamentos(db, p)})
    chaves = ("iliquido", "remuneracao_sujeita", "irs", "seguranca_social", "liquido", "custo_total")
    return {"ano": ano, "meses": meses, "totais": {k: _r(sum(m[k] for m in meses)) for k in chaves}}


def por_processar(db: Session, empresa_id: int, hoje: date) -> list[tuple[int, int]]:
    """Meses (deste ano até ao mês passado) com pessoas ao serviço e sem processamento."""
    resultado = []
    for mes in range(1, hoje.month):
        if ativos_no_mes(db, empresa_id, hoje.year, mes) and not processado(db, obter(db, empresa_id, hoje.year, mes)):
            resultado.append((hoje.year, mes))
    return resultado


def pagamentos_em_atraso(db: Session, empresa_id: int, hoje: date) -> list[dict]:
    atrasados = []
    for p in db.scalars(select(ProcessamentoSalarial).where(ProcessamentoSalarial.empresa_id == empresa_id)):
        if not processado(db, p):
            continue
        for tipo, e in estado_pagamentos(db, p).items():
            if not e["pago"] and e["valor"] and prazo_pagamento(p.ano, p.mes, tipo) < hoje:
                atrasados.append({"ano": p.ano, "mes": p.mes, "tipo": tipo, "nome": e["nome"], "valor": e["valor"]})
    return atrasados


# ---------- Acréscimo de férias (31/12) ----------

def estimativa_ferias(db: Session, empresa_id: int, ano: int) -> dict:
    """
    Férias e subsídio de férias vencidos a 1 de janeiro do ano seguinte, que respeitam ao trabalho
    deste ano: uma remuneração base de férias + uma de subsídio, mais os encargos da entidade.
    Quem recebe o subsídio de férias em duodécimos já o recebeu: só conta a remuneração das férias.
    """
    pessoas = [f for f in ativos_no_mes(db, empresa_id, ano, 12) if not f.data_saida or f.data_saida.year > ano]
    detalhe = []
    for f in pessoas:
        meses_servico = 12 if f.data_admissao.year < ano else 12 - f.data_admissao.month + 1
        fator = meses_servico / 12
        remuneracoes = _r(f.salario_base * (1 if f.duodecimos else 2) * fator)
        encargos = _r(remuneracoes * f.taxa_ss_entidade / 100)
        detalhe.append({"funcionario_id": f.id, "nome": f.nome, "tipo": f.tipo, "remuneracoes": remuneracoes,
                        "encargos": encargos})
    return {"ano": ano, "detalhe": detalhe, "remuneracoes": _r(sum(d["remuneracoes"] for d in detalhe)),
            "encargos": _r(sum(d["encargos"] for d in detalhe))}


def acrescimo_ferias(db: Session, empresa: Empresa, ano: int, utilizador_id: int | None = None) -> Lancamento:
    est = estimativa_ferias(db, empresa.id, ano)
    if not est["remuneracoes"]:
        raise ErroSalarios(f"Não há pessoas ao serviço a 31/12/{ano}.")
    por_conta: dict[str, float] = {}
    for d in est["detalhe"]:
        conta = CONTAS[d["tipo"]][0]
        por_conta[conta] = _r(por_conta.get(conta, 0.0) + d["remuneracoes"])
    linhas = [(c, v, "Férias e subsídio de férias a pagar no ano seguinte") for c, v in sorted(por_conta.items())]
    linhas.append((CONTA_ENCARGOS, est["encargos"], "Encargos sobre as férias e o subsídio de férias"))
    return periodizacao.acrescimo(
        db, empresa, "gasto", date(ano, 12, 31), f"Acréscimo de férias e subsídio de férias de {ano}",
        linhas, utilizador_id=utilizador_id,
        notas=[f"{d['nome']}: {d['remuneracoes']:.2f} € + encargos {d['encargos']:.2f} €" for d in est["detalhe"]],
    )
