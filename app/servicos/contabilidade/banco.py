"""
Reconciliação bancária: liga cada movimento do extrato do banco ao lançamento que o reflete
na contabilidade (a linha da conta 12x/13x/11x).

Para cada movimento por reconciliar há sugestões, da mais segura para a menos segura:
1. associar a um lançamento já feito: mesmo valor na conta do banco, data próxima;
2. pagar uma fatura em aberto com o mesmo total: regista o pagamento nessa data e reconcilia;
3. lançar numa conta: pela regra aprendida com o utilizador para esse tipo de movimento
   (ex.: "COMISSAO MANUTENCAO" -> 6988) ou por palavras-chave (Imposto do Selo, Segurança Social...).

A reconciliação automática só aplica o que não tem dúvidas: um único lançamento ou uma única
fatura com aquele valor e data próxima, ou uma regra que o utilizador já ensinou.

As funções não fazem commit: quem as chama decide quando gravar.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.modelos.tabelas import (
    ESTADO_CONFIRMADA,
    LANCAMENTO_ATIVO,
    MOVIMENTO_IGNORADO,
    MOVIMENTO_PENDENTE,
    MOVIMENTO_RECONCILIADO,
    Conta,
    Empresa,
    ExtratoBancario,
    Fatura,
    FaturaVenda,
    Lancamento,
    LinhaLancamento,
    MovimentoBancario,
    RegraBancaria,
)
from app.servicos import webhooks
from app.servicos.contabilidade import extratos, fluxos_caixa, lancamentos, plano_snc, relatorios, vendas
from app.servicos.contabilidade.lancamentos import ErroContabilistico, LinhaNova

JANELA_DIAS = 10        # diferença máxima de datas entre o banco e a contabilidade
TOLERANCIA = 0.005
PONTUACAO_SEGURA = 85


def contas_bancarias(db: Session, empresa_id: int) -> list[Conta]:
    """Contas de movimento de caixa e bancos (11x, 12x, 13x)."""
    return list(db.scalars(select(Conta).where(
        Conta.empresa_id == empresa_id, Conta.movimento.is_(True),
        Conta.codigo.like("11%") | Conta.codigo.like("12%") | Conta.codigo.like("13%"),
    ).order_by(Conta.codigo)))


def verificar_conta(db: Session, empresa_id: int, codigo: str) -> Conta:
    conta = plano_snc.obter_conta(db, empresa_id, codigo)
    if conta is None or not conta.movimento or not codigo.startswith(("11", "12", "13")):
        raise ErroContabilistico("Escolha uma conta de movimento de bancos ou caixa (ex.: 121).")
    return conta


# ---------- Importação ----------

@dataclass
class ResultadoImportacao:
    extrato: ExtratoBancario
    importados: int
    duplicados: int
    lido: extratos.ExtratoLido


def importar(db: Session, empresa: Empresa, conta_codigo: str, conteudo: bytes, nome_ficheiro: str,
             saldo_final: float | None = None, utilizador_id: int | None = None) -> ResultadoImportacao:
    verificar_conta(db, empresa.id, conta_codigo)
    lido = extratos.ler(conteudo, nome_ficheiro)
    return gravar(db, empresa, conta_codigo, lido, nome_ficheiro, saldo_final, utilizador_id)


def novos(db: Session, empresa_id: int, conta_codigo: str, movimentos: list[extratos.MovimentoLido]) -> list[int]:
    """Posições dos movimentos que ainda não existem (sincronizações sobrepostas não duplicam)."""
    refs = extratos.referencias(conta_codigo, movimentos)
    existentes = set(db.scalars(select(MovimentoBancario.referencia).where(
        MovimentoBancario.empresa_id == empresa_id, MovimentoBancario.conta_codigo == conta_codigo,
        MovimentoBancario.referencia.in_(refs),
    )))
    return [i for i, r in enumerate(refs) if r not in existentes]


def gravar(db: Session, empresa: Empresa, conta_codigo: str, lido: extratos.ExtratoLido, nome: str,
           saldo_final: float | None = None, utilizador_id: int | None = None) -> ResultadoImportacao:
    """Grava os movimentos lidos (de um ficheiro, do banco ou da API) como uma importação, sem duplicar."""
    refs = extratos.referencias(conta_codigo, lido.movimentos)
    existentes = set(db.scalars(select(MovimentoBancario.referencia).where(
        MovimentoBancario.empresa_id == empresa.id, MovimentoBancario.conta_codigo == conta_codigo,
        MovimentoBancario.referencia.in_(refs),
    )))
    extrato = ExtratoBancario(
        empresa_id=empresa.id, conta_codigo=conta_codigo, nome_ficheiro=nome[:200], formato=lido.formato,
        data_inicio=lido.data_inicio, data_fim=lido.data_fim,
        saldo_final=saldo_final if saldo_final is not None else lido.saldo_final, importado_por_id=utilizador_id,
    )
    db.add(extrato)
    db.flush()
    importados = 0
    for movimento, referencia in zip(lido.movimentos, refs, strict=True):
        if referencia in existentes:
            continue
        existentes.add(referencia)
        db.add(MovimentoBancario(
            empresa_id=empresa.id, extrato_id=extrato.id, conta_codigo=conta_codigo, data=movimento.data,
            descricao=movimento.descricao, montante=movimento.montante, saldo=movimento.saldo,
            referencia=referencia, estado=MOVIMENTO_PENDENTE,
        ))
        importados += 1
    db.flush()
    if importados:
        webhooks.emitir(db, empresa.id, "movimentos.importados", {
            "conta": conta_codigo, "importados": importados, "extrato_id": extrato.id,
            "url": f"/api/v1/bancos/movimentos?conta={conta_codigo}"})
    return ResultadoImportacao(extrato, importados, len(lido.movimentos) - importados, lido)


# ---------- Sugestões ----------

PALAVRAS_VAZIAS = {"de", "da", "do", "das", "dos", "a", "o", "e", "para", "p", "lda", "sa", "unipessoal", "trf",
                   "transf", "transferencia", "sepa", "pagamento", "pag", "compra", "mov", "ref", "n"}


def palavras(texto: str) -> set[str]:
    return {p for p in re.findall(r"[a-z]{3,}", extratos.normalizar(texto)) if p not in PALAVRAS_VAZIAS}


def padrao(descricao: str) -> str:
    """'COMISSAO MANUTENCAO CONTA 03/2026 REF 1234' -> 'comissao manutencao conta': o que se repete todos os meses."""
    texto = re.sub(r"[^a-z ]+", " ", extratos.normalizar(descricao))
    termos = [p for p in texto.split() if len(p) > 1 and p not in RUIDO_PADRAO][:3]
    return " ".join(termos)[:80]


# Palavras que mudam de mês para mês ou não identificam nada
RUIDO_PADRAO = {"ref", "referencia", "doc", "num", "nr", "data", "de", "do", "da", "em", "no", "na"}


# (expressão regular, sinal: -1 saídas / 1 entradas / 0 ambos, conta, explicação)
PALAVRAS_BANCO = [
    (r"imposto (do )?selo|imp\.? ?selo|\bi\.? ?selo\b", -1, "6812", "Imposto do Selo sobre serviços bancários"),
    (r"comiss|manutencao (de )?conta|despesas? de manutencao|anuidade|portes|encargos", -1, "6988",
     "comissões e encargos bancários"),
    (r"juro", 1, "7911", "juros de depósitos"),
    (r"juro", -1, "6918", "juros suportados"),
    (r"seg(uranca)?\.? ?social|\btsu\b|\biss\b", -1, "245", "contribuições para a Segurança Social"),
    (r"autoridade tributaria|\bduc\b|financas|\bat\b", -1, "2436",
     "pagamento ao Estado (confirme: IVA 2436, retenções 2421/2422 ou IRC 241)"),
    (r"vencimento|ordenado|salario", -1, "2312", "remunerações a pagar ao pessoal"),
    (r"levantamento", -1, "111", "levantamento para a caixa"),
    (r"deposito (de )?numerario|deposito em numerario", 1, "111", "depósito de dinheiro da caixa"),
]


def _regra(db: Session, empresa_id: int, mov: MovimentoBancario) -> RegraBancaria | None:
    return db.scalar(select(RegraBancaria).where(
        RegraBancaria.empresa_id == empresa_id, RegraBancaria.padrao == padrao(mov.descricao),
        RegraBancaria.sinal == (1 if mov.montante > 0 else -1),
    ))


def linhas_por_reconciliar(db: Session, empresa_id: int, conta: str, de: date | None = None,
                            ate: date | None = None) -> list[LinhaLancamento]:
    """Linhas da conta do banco ainda sem movimento do extrato (lançamentos estornados não contam)."""
    ligadas = select(MovimentoBancario.linha_id).where(MovimentoBancario.linha_id.is_not(None))
    consulta = (
        select(LinhaLancamento)
        .join(LinhaLancamento.conta).join(LinhaLancamento.lancamento)
        .options(joinedload(LinhaLancamento.lancamento), joinedload(LinhaLancamento.conta))
        .where(Lancamento.empresa_id == empresa_id, Conta.codigo == conta,
               Lancamento.estado == LANCAMENTO_ATIVO, Lancamento.estorno_de_id.is_(None),
               LinhaLancamento.id.not_in(ligadas))
        .order_by(Lancamento.data)
    )
    if de:
        consulta = consulta.where(Lancamento.data >= de)
    if ate:
        consulta = consulta.where(Lancamento.data <= ate)
    return list(db.scalars(consulta).unique())


def faturas_por_pagar(db: Session, empresa_id: int) -> list[Fatura]:
    contabilizadas = select(Lancamento.fatura_id).where(
        Lancamento.empresa_id == empresa_id, Lancamento.origem == "fatura", Lancamento.estado == LANCAMENTO_ATIVO,
        Lancamento.fatura_id.is_not(None),
    )
    return list(db.scalars(select(Fatura).where(
        Fatura.empresa_id == empresa_id, Fatura.estado == ESTADO_CONFIRMADA, Fatura.paga.is_(False),
        Fatura.total.is_not(None), Fatura.id.in_(contabilizadas),
    )))


def _semelhanca(a: str, b: str) -> int:
    comuns = palavras(a) & palavras(b)
    return min(len(comuns) * 8, 20)


def sugestoes(db: Session, empresa_id: int, mov: MovimentoBancario, linhas: list[LinhaLancamento] | None = None,
              faturas: list[Fatura] | None = None, vendas_abertas: list[FaturaVenda] | None = None) -> list[dict]:
    """Sugestões para um movimento por reconciliar, da melhor para a pior (com pontuação 0-100)."""
    valor = abs(mov.montante)
    resultado: list[dict] = []

    # 1. Lançamentos já feitos na conta do banco com o mesmo valor e data próxima
    if linhas is None:
        linhas = linhas_por_reconciliar(db, empresa_id, mov.conta_codigo,
                                         mov.data - timedelta(days=JANELA_DIAS), mov.data + timedelta(days=JANELA_DIAS))
    for ln in linhas:
        lado = ln.debito if mov.montante > 0 else ln.credito
        dias = abs((ln.lancamento.data - mov.data).days)
        if abs(lado - valor) > TOLERANCIA or dias > JANELA_DIAS:
            continue
        la = ln.lancamento
        texto_la = f"{la.descricao} {la.documento or ''}"
        resultado.append({
            "tipo": "associar", "linha_id": ln.id,
            "pontuacao": min(max(100 - dias * 4, 50) + _semelhanca(mov.descricao, texto_la) // 2, 100),
            "texto": f"{la.diario} {la.ano}/{la.numero} de {la.data:%d/%m/%Y}: {la.descricao}",
            "lancamento_id": la.id, "dias": dias,
        })

    # 2. Faturas por pagar com o mesmo total (só nas saídas)
    if mov.montante < 0:
        if faturas is None:
            faturas = faturas_por_pagar(db, empresa_id)
        for f in faturas:
            if abs(f.total - valor) > TOLERANCIA or (f.data_emissao and f.data_emissao > mov.data):
                continue
            pontuacao = 80 + _semelhanca(mov.descricao, f.nome_fornecedor or "")
            resultado.append({
                "tipo": "pagar_fatura", "fatura_id": f.id, "pontuacao": min(pontuacao, 98),
                "texto": f"Pagar a fatura {lancamentos.documento_fatura(f)} de {f.nome_fornecedor or 'fornecedor'} "
                         f"({f.data_emissao:%d/%m/%Y})",
            })

    # 2b. Vendas por receber com o mesmo valor em dívida (só nas entradas)
    if mov.montante > 0:
        if vendas_abertas is None:
            vendas_abertas = vendas.por_receber(db, empresa_id)
        for v in vendas_abertas:
            em_divida = round(v.total - v.valor_recebido, 2)
            if abs(em_divida - valor) > TOLERANCIA or v.data > mov.data:
                continue
            resultado.append({
                "tipo": "receber_venda", "venda_id": v.id,
                "pontuacao": min(80 + _semelhanca(mov.descricao, v.cliente_nome), 98),
                "texto": f"Receber a {v.numero} de {v.cliente_nome} ({v.data:%d/%m/%Y})",
            })

    # 3. Lançar numa conta: regra aprendida ou palavras-chave
    regra = _regra(db, empresa_id, mov)
    sinal = 1 if mov.montante > 0 else -1
    if regra and plano_snc.obter_conta(db, empresa_id, regra.conta_codigo):
        conta = plano_snc.obter_conta(db, empresa_id, regra.conta_codigo)
        resultado.append({
            "tipo": "lancar", "conta": regra.conta_codigo, "origem": "regra", "pontuacao": 90,
            "texto": f"Lançar em {regra.conta_codigo} {conta.nome} (como nas {regra.usos} vezes anteriores)",
        })
    else:
        texto = extratos.normalizar(mov.descricao)
        for expressao, sinal_regra, conta_codigo, motivo in PALAVRAS_BANCO:
            if sinal_regra in (0, sinal) and re.search(expressao, texto):
                conta = plano_snc.obter_conta(db, empresa_id, conta_codigo)
                if conta:
                    resultado.append({
                        "tipo": "lancar", "conta": conta_codigo, "origem": "palavras", "pontuacao": 60,
                        "texto": f"Lançar em {conta_codigo} {conta.nome} ({motivo})",
                    })
                break

    resultado.sort(key=lambda s: s["pontuacao"], reverse=True)
    return resultado


def sugestao_segura(sugs: list[dict]) -> dict | None:
    """A melhor sugestão, se não houver dúvidas: pontuação alta e sem outra do mesmo tipo empatada."""
    if not sugs or sugs[0]["pontuacao"] < PONTUACAO_SEGURA:
        return None
    melhor = sugs[0]
    if melhor["tipo"] == "lancar":
        return melhor if melhor["origem"] == "regra" else None
    rivais = [s for s in sugs[1:]
              if s["tipo"] in ("associar", "pagar_fatura", "receber_venda") and s["pontuacao"] >= PONTUACAO_SEGURA]
    return None if rivais else melhor


# ---------- Ações ----------

def _marcar(mov: MovimentoBancario, linha: LinhaLancamento, utilizador_id: int | None) -> None:
    mov.linha_id = linha.id
    mov.estado = MOVIMENTO_RECONCILIADO
    mov.reconciliado_em = datetime.now()
    mov.reconciliado_por_id = utilizador_id


def _verificar_pendente(mov: MovimentoBancario) -> None:
    if mov.estado != MOVIMENTO_PENDENTE:
        raise ErroContabilistico("Este movimento já não está por reconciliar.")


def associar(db: Session, empresa_id: int, mov: MovimentoBancario, linha_id: int,
             utilizador_id: int | None = None) -> None:
    _verificar_pendente(mov)
    linha = db.scalar(
        select(LinhaLancamento).join(LinhaLancamento.lancamento).join(LinhaLancamento.conta)
        .where(LinhaLancamento.id == linha_id, Lancamento.empresa_id == empresa_id)
    )
    if linha is None:
        raise ErroContabilistico("Lançamento não encontrado.")
    if linha.conta.codigo != mov.conta_codigo:
        raise ErroContabilistico(f"O lançamento não movimenta a conta {mov.conta_codigo}.")
    if linha.lancamento.estado != LANCAMENTO_ATIVO or linha.lancamento.estorno_de_id:
        raise ErroContabilistico("Não se reconcilia um lançamento estornado.")
    lado = linha.debito if mov.montante > 0 else linha.credito
    if abs(lado - abs(mov.montante)) > TOLERANCIA:
        raise ErroContabilistico("O valor do lançamento não é igual ao do movimento do banco.")
    if db.scalar(select(MovimentoBancario.id).where(MovimentoBancario.linha_id == linha.id)):
        raise ErroContabilistico("Esse lançamento já está reconciliado com outro movimento.")
    _marcar(mov, linha, utilizador_id)


def _linha_do_banco(lancamento: Lancamento, conta: str) -> LinhaLancamento:
    return next(ln for ln in lancamento.linhas if ln.conta.codigo == conta)


def pagar_fatura(db: Session, empresa: Empresa, mov: MovimentoBancario, fatura_id: str,
                 utilizador_id: int | None = None) -> Lancamento:
    """Regista o pagamento da fatura na data do banco, pela conta do extrato, e reconcilia."""
    _verificar_pendente(mov)
    fatura = db.scalar(select(Fatura).where(Fatura.id == fatura_id, Fatura.empresa_id == empresa.id))
    if fatura is None:
        raise ErroContabilistico("Fatura não encontrada.")
    if mov.montante >= 0 or abs(abs(mov.montante) - (fatura.total or 0)) > TOLERANCIA:
        raise ErroContabilistico("O valor do movimento não é igual ao total da fatura.")
    lancamento = lancamentos.registar_pagamento(db, empresa, fatura, mov.data, mov.conta_codigo,
                                                utilizador_id=utilizador_id,
                                                nota=f"Pago conforme extrato bancário: {mov.descricao}")
    fatura.paga = True
    _marcar(mov, _linha_do_banco(lancamento, mov.conta_codigo), utilizador_id)
    return lancamento


def receber_venda(db: Session, empresa: Empresa, mov: MovimentoBancario, venda_id: int,
                  utilizador_id: int | None = None) -> Lancamento:
    """Regista o recebimento da venda na data do banco, pela conta do extrato, e reconcilia."""
    _verificar_pendente(mov)
    venda = db.scalar(select(FaturaVenda).where(FaturaVenda.id == venda_id, FaturaVenda.empresa_id == empresa.id))
    if venda is None:
        raise ErroContabilistico("Venda não encontrada.")
    if mov.montante <= 0 or abs(round(venda.total - venda.valor_recebido, 2) - mov.montante) > TOLERANCIA:
        raise ErroContabilistico("O valor do movimento não é igual ao que falta receber da venda.")
    lancamento = vendas.receber(db, empresa, venda, mov.data, mov.conta_codigo, mov.montante, utilizador_id,
                                nota=f"Recebido conforme extrato bancário: {mov.descricao}")
    _marcar(mov, _linha_do_banco(lancamento, mov.conta_codigo), utilizador_id)
    return lancamento


def lancar(db: Session, empresa: Empresa, mov: MovimentoBancario, conta_codigo: str,
           descricao: str | None = None, utilizador_id: int | None = None, aprender: bool = True,
           fluxo: str | None = None) -> Lancamento:
    """Cria o lançamento do movimento (banco contra a conta escolhida), reconcilia e memoriza a escolha."""
    _verificar_pendente(mov)
    if conta_codigo == mov.conta_codigo:
        raise ErroContabilistico("A contrapartida não pode ser a própria conta do banco.")
    valor = round(abs(mov.montante), 2)
    entrada = mov.montante > 0
    linhas = [
        LinhaNova(mov.conta_codigo, debito=valor if entrada else 0, credito=0 if entrada else valor,
                  descricao=mov.descricao[:200], fluxo=fluxo),
        LinhaNova(conta_codigo, debito=0 if entrada else valor, credito=valor if entrada else 0),
    ]
    lancamento = lancamentos.criar(
        db, empresa, "CX" if mov.conta_codigo.startswith("11") else "BAN", mov.data,
        (descricao or mov.descricao)[:300], linhas, documento="Extrato bancário", origem="banco",
        utilizador_id=utilizador_id,
    )
    _marcar(mov, _linha_do_banco(lancamento, mov.conta_codigo), utilizador_id)
    if aprender:
        _aprender(db, empresa.id, mov, conta_codigo)
    if fluxo and fluxos_caixa.terceiro(conta_codigo):   # escolha feita na reconciliação: memorizada
        fluxos_caixa.memorizar_para(db, empresa.id, conta_codigo, fluxo)
    return lancamento


def _aprender(db: Session, empresa_id: int, mov: MovimentoBancario, conta_codigo: str) -> None:
    chave = padrao(mov.descricao)
    if not chave:
        return
    regra = _regra(db, empresa_id, mov)
    if regra is None:
        regra = RegraBancaria(empresa_id=empresa_id, padrao=chave, sinal=1 if mov.montante > 0 else -1,
                              conta_codigo=conta_codigo, usos=0)
        db.add(regra)
    regra.conta_codigo = conta_codigo
    regra.usos += 1


def ignorar(mov: MovimentoBancario, utilizador_id: int | None = None) -> None:
    _verificar_pendente(mov)
    mov.estado = MOVIMENTO_IGNORADO
    mov.reconciliado_em = datetime.now()
    mov.reconciliado_por_id = utilizador_id


def desfazer(mov: MovimentoBancario) -> None:
    """Volta a pôr o movimento por reconciliar. Lançamentos criados entretanto ficam (estornam-se à parte)."""
    mov.estado = MOVIMENTO_PENDENTE
    mov.linha_id = None
    mov.reconciliado_em = None
    mov.reconciliado_por_id = None


def soltar_linhas(db: Session, linha_ids: list[int]) -> None:
    """Chamado ao estornar um lançamento: os movimentos do banco ligados a ele voltam a estar pendentes."""
    if not linha_ids:
        return
    for mov in db.scalars(select(MovimentoBancario).where(MovimentoBancario.linha_id.in_(linha_ids))):
        desfazer(mov)


def reconciliar_automaticamente(db: Session, empresa: Empresa, conta_codigo: str,
                                utilizador_id: int | None = None) -> dict:
    """Aplica só as sugestões seguras. Devolve quantos movimentos reconciliou, por tipo."""
    verificar_conta(db, empresa.id, conta_codigo)
    contagem = {"associar": 0, "pagar_fatura": 0, "receber_venda": 0, "lancar": 0}
    pendentes = list(db.scalars(select(MovimentoBancario).where(
        MovimentoBancario.empresa_id == empresa.id, MovimentoBancario.conta_codigo == conta_codigo,
        MovimentoBancario.estado == MOVIMENTO_PENDENTE,
    ).order_by(MovimentoBancario.data, MovimentoBancario.id)))
    for mov in pendentes:
        melhor = sugestao_segura(sugestoes(db, empresa.id, mov))
        if melhor is None:
            continue
        try:
            if melhor["tipo"] == "associar":
                associar(db, empresa.id, mov, melhor["linha_id"], utilizador_id)
            elif melhor["tipo"] == "pagar_fatura":
                pagar_fatura(db, empresa, mov, melhor["fatura_id"], utilizador_id)
            elif melhor["tipo"] == "receber_venda":
                receber_venda(db, empresa, mov, melhor["venda_id"], utilizador_id)
            else:
                lancar(db, empresa, mov, melhor["conta"], utilizador_id=utilizador_id)
        except ErroContabilistico:
            continue  # período fechado, por exemplo: fica para o utilizador
        db.flush()
        contagem[melhor["tipo"]] += 1
    return contagem


# ---------- Mapa de reconciliação ----------

def mapa(db: Session, empresa_id: int, conta_codigo: str, ate: date | None = None) -> dict:
    """
    Explica a diferença entre o saldo do banco e o da contabilidade numa data:
    saldo contabilístico + entradas do banco por lançar - saídas do banco por lançar
    - débitos na contabilidade que não estão no banco + créditos que não estão no banco = saldo do banco.
    Só contam os lançamentos a partir do primeiro extrato importado (antes disso não há com que comparar).
    """
    ate = ate or date.today()
    saldo_contabilistico = relatorios.saldos_por_conta(db, empresa_id, ate=ate).get(conta_codigo, 0.0)

    extrato = db.scalar(select(ExtratoBancario).where(
        ExtratoBancario.empresa_id == empresa_id, ExtratoBancario.conta_codigo == conta_codigo,
        ExtratoBancario.saldo_final.is_not(None), ExtratoBancario.data_fim <= ate,
    ).order_by(ExtratoBancario.data_fim.desc(), ExtratoBancario.id.desc()).limit(1))
    referencia = extrato.data_fim if extrato else ate

    inicio = db.scalar(select(func.min(MovimentoBancario.data)).where(
        MovimentoBancario.empresa_id == empresa_id, MovimentoBancario.conta_codigo == conta_codigo,
    ))
    pendentes_banco = list(db.scalars(select(MovimentoBancario).where(
        MovimentoBancario.empresa_id == empresa_id, MovimentoBancario.conta_codigo == conta_codigo,
        MovimentoBancario.estado == MOVIMENTO_PENDENTE, MovimentoBancario.data <= referencia,
    ).order_by(MovimentoBancario.data)))
    linhas_soltas = linhas_por_reconciliar(db, empresa_id, conta_codigo, inicio, referencia) if inicio else []

    saldo_contab_ref = relatorios.saldos_por_conta(db, empresa_id, ate=referencia).get(conta_codigo, 0.0)
    entradas_banco = round(sum(m.montante for m in pendentes_banco if m.montante > 0), 2)
    saidas_banco = round(-sum(m.montante for m in pendentes_banco if m.montante < 0), 2)
    debitos_contab = round(sum(ln.debito for ln in linhas_soltas), 2)
    creditos_contab = round(sum(ln.credito for ln in linhas_soltas), 2)
    saldo_explicado = round(saldo_contab_ref + entradas_banco - saidas_banco - debitos_contab + creditos_contab, 2)
    saldo_banco = extrato.saldo_final if extrato else None
    return {
        "conta": conta_codigo,
        "data": referencia,
        "saldo_contabilistico": round(saldo_contabilistico, 2),
        "saldo_contabilistico_na_data": round(saldo_contab_ref, 2),
        "saldo_banco": saldo_banco,
        "entradas_banco_por_lancar": entradas_banco,
        "saidas_banco_por_lancar": saidas_banco,
        "debitos_contabilidade_fora_do_banco": debitos_contab,
        "creditos_contabilidade_fora_do_banco": creditos_contab,
        "saldo_explicado": saldo_explicado,
        "diferenca_por_explicar": round(saldo_banco - saldo_explicado, 2) if saldo_banco is not None else None,
        "lancamentos_fora_do_banco": [{
            "linha_id": ln.id, "lancamento_id": ln.lancamento.id, "data": ln.lancamento.data,
            "referencia": f"{ln.lancamento.diario} {ln.lancamento.ano}/{ln.lancamento.numero}",
            "descricao": ln.lancamento.descricao, "debito": ln.debito, "credito": ln.credito,
        } for ln in linhas_soltas],
    }
