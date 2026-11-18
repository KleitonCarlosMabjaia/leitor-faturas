"""
Vendas: faturas emitidas a clientes.

As faturas NÃO são emitidas aqui: emitir faturas em Portugal exige um programa certificado
pela AT (Portaria n.º 363/2010: assinatura, ATCUD, QR, comunicação). A contabilidade recebe-as:
- do SAF-T (PT) de faturação exportado pelo programa certificado (faturas, notas de crédito,
  faturas-recibo, documentos anulados e recibos);
- ou registadas à mão (por exemplo, faturas emitidas no Portal das Finanças).

Cada venda é contabilizada no diário de vendas: cliente (2111xxxx) a débito; vendas (711) ou
prestações de serviços (721) e IVA liquidado (2433x, por taxa) a crédito. As notas de crédito
invertem o lançamento. Os recebimentos debitam a caixa ou o banco e creditam o cliente.

As funções não fazem commit: quem as chama decide quando gravar.
"""

import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modelos.tabelas import (
    CONSUMIDOR_FINAL,
    LANCAMENTO_ATIVO,
    VENDA_ANULADA,
    VENDA_FATURADA,
    VENDA_NORMAL,
    Empresa,
    FaturaVenda,
    Lancamento,
)
from app.servicos import validacao
from app.servicos.contabilidade import iva, lancamentos, plano_snc
from app.servicos.contabilidade.lancamentos import ErroContabilistico, LinhaNova

TIPOS = {"FT": "Fatura", "FS": "Fatura simplificada", "FR": "Fatura-recibo", "NC": "Nota de crédito",
         "ND": "Nota de débito"}
CONTA_VENDAS = "711"       # bens (produtos e mercadorias)
CONTA_SERVICOS = "721"     # serviços
PRAZO_OMISSAO = 30         # dias, quando o documento não traz vencimento
TOLERANCIA = 0.005
MEIOS_CAIXA = {"NU"}       # numerário: entra na caixa; o resto entra no banco


class ErroVenda(Exception):
    """Ficheiro ou dados de venda inválidos (mensagem pronta a mostrar)."""


def conta_financeira(meio: str | None) -> str:
    return "111" if (meio or "").upper() in MEIOS_CAIXA else "121"


# ---------- Contabilização ----------

def lancamento_ativo(db: Session, empresa_id: int, venda_id: int, origem: str) -> Lancamento | None:
    return db.scalar(select(Lancamento).where(
        Lancamento.empresa_id == empresa_id, Lancamento.venda_id == venda_id, Lancamento.origem == origem,
        Lancamento.estado == LANCAMENTO_ATIVO, Lancamento.estorno_de_id.is_(None),
    ))


def conta_cliente(db: Session, empresa_id: int, venda: FaturaVenda):
    nif = venda.cliente_nif if venda.cliente_nif and venda.cliente_nif != CONSUMIDOR_FINAL else None
    nif = nif if nif and validacao.nif_valido(nif) else None
    conta = plano_snc.subconta_terceiro(db, empresa_id, "2111", nif, venda.cliente_nome)
    if not nif and conta.nome == "Diversos":
        conta.nome = "Clientes diversos e consumidores finais"
    return conta


def contabilizar(db: Session, empresa: Empresa, venda: FaturaVenda, utilizador_id: int | None = None) -> Lancamento:
    if venda.estado != VENDA_NORMAL:
        raise ErroContabilistico("Documentos anulados ou já faturados noutro documento não se contabilizam.")
    if lancamento_ativo(db, empresa.id, venda.id, "venda"):
        raise ErroContabilistico("Esta venda já está contabilizada.")
    plano_snc.garantir_plano(db, empresa.id)

    linhas: list[LinhaNova] = []
    for ln in venda.linhas:
        taxa, base, valor = float(ln["taxa"]), float(ln["base"]), float(ln["valor"])
        conta = ln.get("conta") or CONTA_VENDAS
        descricao = f"Base {taxa:g}%" + (f" ({ln['isencao']})" if ln.get("isencao") else "")
        linhas.append(LinhaNova(conta, credito=base, descricao=descricao, tipo_iva="base", taxa_iva=taxa))
        if valor:
            escalao = iva.escalao(taxa)
            linhas.append(LinhaNova(iva.CONTA_IVA_LIQUIDADO.get(escalao, iva.CONTA_IVA_LIQUIDADO["normal"]),
                                    credito=valor, descricao=f"IVA liquidado {taxa:g}%", tipo_iva="iva",
                                    taxa_iva=taxa))
    soma = round(sum(ln.credito for ln in linhas), 2)
    # Arredondamentos de cêntimos entre as linhas e o total do documento vão para a primeira base
    if 0 < abs(venda.total - soma) <= 0.05:
        linhas[0].credito = round(linhas[0].credito + venda.total - soma, 2)
        soma = round(venda.total, 2)
    linhas.insert(0, LinhaNova(conta_cliente(db, empresa.id, venda).codigo, debito=soma, descricao=venda.cliente_nome))

    if venda.tipo == "NC":
        for ln in linhas:
            ln.debito, ln.credito = ln.credito, ln.debito

    return lancamentos.criar(
        db, empresa, "VEN", venda.data, f"{venda.cliente_nome} - {venda.numero}", linhas,
        documento=venda.numero, origem="venda", venda_id=venda.id, utilizador_id=utilizador_id,
    )


def receber(db: Session, empresa: Empresa, venda: FaturaVenda, data: date, conta: str | None = None,
            valor: float | None = None, utilizador_id: int | None = None, nota: str | None = None) -> Lancamento:
    """Recebimento (total ou parcial): caixa ou banco a débito, cliente a crédito."""
    if venda.tipo == "NC":
        raise ErroContabilistico("As notas de crédito não se recebem: abatem ao saldo do cliente.")
    venda_lancada = lancamento_ativo(db, empresa.id, venda.id, "venda")
    if venda_lancada is None:
        raise ErroContabilistico("Contabilize a venda antes de registar o recebimento.")
    em_divida = round(venda.total - venda.valor_recebido, 2)
    valor = round(valor if valor is not None else em_divida, 2)
    if valor <= 0 or valor - em_divida > TOLERANCIA:
        raise ErroContabilistico(f"O valor tem de estar entre 0 e o que falta receber ({em_divida:.2f} €).")
    conta = conta or conta_financeira(venda.meio_pagamento)
    destino = plano_snc.obter_conta(db, empresa.id, conta)
    if destino is None or not destino.movimento or not conta.startswith(("11", "12", "13")):
        raise ErroContabilistico("O recebimento tem de entrar numa conta de movimento da caixa ou de bancos.")
    if data < venda.data:
        raise ErroContabilistico("A data do recebimento é anterior à da fatura.")

    cliente = next(ln.conta.codigo for ln in venda_lancada.linhas if ln.conta.codigo.startswith("2111"))
    lancamento = lancamentos.criar(
        db, empresa, "CX" if conta.startswith("11") else "BAN", data,
        f"Recebimento {venda.cliente_nome} - {venda.numero}",
        [LinhaNova(conta, debito=valor), LinhaNova(cliente, credito=valor, descricao=venda.cliente_nome)],
        documento=venda.numero, origem="recebimento", venda_id=venda.id, notas=[nota] if nota else None,
        utilizador_id=utilizador_id,
    )
    venda.valor_recebido = round(venda.valor_recebido + valor, 2)
    venda.recebida = venda.valor_recebido >= venda.total - TOLERANCIA
    return lancamento


def por_receber(db: Session, empresa_id: int) -> list[FaturaVenda]:
    """Vendas contabilizadas, válidas e ainda não recebidas (as notas de crédito não contam)."""
    contabilizadas = select(Lancamento.venda_id).where(
        Lancamento.empresa_id == empresa_id, Lancamento.origem == "venda", Lancamento.estado == LANCAMENTO_ATIVO,
        Lancamento.venda_id.is_not(None),
    )
    return list(db.scalars(select(FaturaVenda).where(
        FaturaVenda.empresa_id == empresa_id, FaturaVenda.estado == VENDA_NORMAL, FaturaVenda.recebida.is_(False),
        FaturaVenda.tipo != "NC", FaturaVenda.id.in_(contabilizadas),
    ).order_by(FaturaVenda.data)))


def por_contabilizar(db: Session, empresa_id: int) -> list[FaturaVenda]:
    contabilizadas = select(Lancamento.venda_id).where(
        Lancamento.empresa_id == empresa_id, Lancamento.origem == "venda", Lancamento.estado == LANCAMENTO_ATIVO,
        Lancamento.venda_id.is_not(None),
    )
    return list(db.scalars(select(FaturaVenda).where(
        FaturaVenda.empresa_id == empresa_id, FaturaVenda.estado == VENDA_NORMAL,
        FaturaVenda.id.not_in(contabilizadas),
    ).order_by(FaturaVenda.data)))


# ---------- Registo manual ----------

@dataclass
class LinhaVenda:
    taxa: float
    base: float
    conta: str = CONTA_VENDAS
    isencao: str | None = None


def calcular_linhas(linhas: list[LinhaVenda]) -> list[dict]:
    """Agrupa por taxa e conta e calcula o IVA de cada grupo (arredondado ao cêntimo)."""
    grupos: dict[tuple, float] = defaultdict(float)
    isencoes = {}
    for ln in linhas:
        if ln.base < 0 or ln.taxa < 0:
            raise ErroVenda("Os valores e as taxas não podem ser negativos (use uma nota de crédito).")
        grupos[(round(ln.taxa, 2), ln.conta)] += ln.base
        if ln.isencao:
            isencoes[(round(ln.taxa, 2), ln.conta)] = ln.isencao
    resultado = [{"taxa": taxa, "base": round(base, 2), "valor": round(base * taxa / 100, 2), "conta": conta,
                  "isencao": isencoes.get((taxa, conta))}
                 for (taxa, conta), base in sorted(grupos.items()) if round(base, 2)]
    if not resultado:
        raise ErroVenda("A venda precisa de pelo menos uma linha com valor.")
    return resultado


def registar(db: Session, empresa: Empresa, *, numero: str, tipo: str, data: date, cliente_nif: str | None,
             cliente_nome: str | None, linhas: list[LinhaVenda], data_vencimento: date | None = None,
             atcud: str | None = None, meio_pagamento: str | None = None, utilizador_id: int | None = None,
             contabilizar_ja: bool = True) -> FaturaVenda:
    """Regista uma venda emitida noutro programa (ou no Portal das Finanças) e contabiliza-a."""
    tipo = tipo.upper()
    if tipo not in TIPOS:
        raise ErroVenda("Tipo de documento desconhecido: use FT, FS, FR, NC ou ND.")
    numero = " ".join(numero.split())
    if not numero:
        raise ErroVenda("Indique o número do documento (ex.: FT A/123).")
    if not numero.upper().startswith(f"{tipo} "):
        numero = f"{tipo} {numero}"
    nif = (cliente_nif or "").strip().upper().removeprefix("PT").replace(" ", "") or CONSUMIDOR_FINAL
    if nif != CONSUMIDOR_FINAL and nif.isdigit() and not validacao.nif_valido(nif):
        raise ErroVenda("O NIF do cliente não é válido.")
    if db.scalar(select(FaturaVenda.id).where(FaturaVenda.empresa_id == empresa.id, FaturaVenda.numero == numero)):
        raise ErroVenda(f"Já existe uma venda com o número {numero}.")
    agrupadas = calcular_linhas(linhas)
    base = round(sum(ln["base"] for ln in agrupadas), 2)
    imposto = round(sum(ln["valor"] for ln in agrupadas), 2)
    venda = FaturaVenda(
        empresa_id=empresa.id, numero=numero[:60], tipo=tipo, atcud=atcud, data=data,
        data_vencimento=data_vencimento or (None if tipo in ("NC", "FR") else data + timedelta(days=PRAZO_OMISSAO)),
        cliente_nif=nif, cliente_nome=(cliente_nome or "").strip()[:200] or "Consumidor final",
        linhas=agrupadas, valor_sem_iva=base, total_iva=imposto, total=round(base + imposto, 2),
        estado=VENDA_NORMAL, valor_recebido=0.0, recebida=False, meio_pagamento=meio_pagamento,
        origem="manual", criada_por_id=utilizador_id,
    )
    db.add(venda)
    db.flush()
    if contabilizar_ja:
        contabilizar(db, empresa, venda, utilizador_id)
        if tipo == "FR":  # fatura-recibo: paga no momento
            receber(db, empresa, venda, data, conta_financeira(meio_pagamento), utilizador_id=utilizador_id)
    return venda


# ---------- SAF-T (PT) de faturação ----------

@dataclass
class ResultadoImportacao:
    importadas: int = 0
    duplicadas: int = 0
    anuladas: int = 0
    ignoradas: int = 0
    recebimentos: int = 0
    avisos: list[str] = field(default_factory=list)
    nao_contabilizadas: int = 0
    periodo: tuple[date | None, date | None] = (None, None)


def _sem_namespace(raiz: ET.Element) -> None:
    for el in raiz.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]


def _texto(el: ET.Element | None, caminho: str, omissao: str = "") -> str:
    encontrado = el.find(caminho) if el is not None else None
    return (encontrado.text or "").strip() if encontrado is not None and encontrado.text else omissao


def _numero(el: ET.Element | None, caminho: str) -> float:
    try:
        return float(_texto(el, caminho, "0"))
    except ValueError:
        return 0.0


def _data(texto: str) -> date | None:
    try:
        return date.fromisoformat(texto[:10])
    except ValueError:
        return None


def ler_saft(conteudo: bytes) -> ET.Element:
    try:
        raiz = ET.fromstring(conteudo)
    except ET.ParseError:
        raise ErroVenda("O ficheiro não é um XML válido. Exporte o SAF-T de faturação do programa certificado.")
    _sem_namespace(raiz)
    if raiz.tag != "AuditFile":
        raise ErroVenda("O ficheiro não é um SAF-T (PT).")
    return raiz


def _linhas_documento(documento: ET.Element, produtos: dict[str, str], nota_credito: bool) -> list[dict]:
    """Bases por taxa e conta (serviços -> 721, resto -> 711), com o valor de cada linha do SAF-T."""
    grupos: dict[tuple, float] = defaultdict(float)
    isencoes: dict[tuple, str] = {}
    for linha in documento.findall("Line"):
        montante = _numero(linha, "CreditAmount") - _numero(linha, "DebitAmount")
        if nota_credito:
            montante = -montante
        tipo_imposto = _texto(linha, "Tax/TaxType", "IVA")
        taxa = _numero(linha, "Tax/TaxPercentage") if tipo_imposto == "IVA" else 0.0
        conta = CONTA_SERVICOS if produtos.get(_texto(linha, "ProductCode")) == "S" else CONTA_VENDAS
        grupos[(round(taxa, 2), conta)] += montante
        if _texto(linha, "TaxExemptionCode"):
            isencoes[(round(taxa, 2), conta)] = _texto(linha, "TaxExemptionCode")
    return [{"taxa": taxa, "base": round(base, 2), "valor": round(base * taxa / 100, 2), "conta": conta,
             "isencao": isencoes.get((taxa, conta))}
            for (taxa, conta), base in sorted(grupos.items()) if round(base, 2)]


def importar_saft(db: Session, empresa: Empresa, conteudo: bytes, utilizador_id: int | None = None,
                  contabilizar_ja: bool = True) -> ResultadoImportacao:
    raiz = ler_saft(conteudo)
    cabecalho = raiz.find("Header")
    base_contabilistica = _texto(cabecalho, "TaxAccountingBasis")
    if base_contabilistica == "C":
        raise ErroVenda("Este é o SAF-T da contabilidade. Para as vendas, exporte o SAF-T de faturação.")
    nif_ficheiro = _texto(cabecalho, "TaxRegistrationNumber")
    if not empresa.nif:
        raise ErroVenda("Defina o NIF da empresa (Obrigações → Configuração) antes de importar o SAF-T.")
    if nif_ficheiro != empresa.nif:
        raise ErroVenda(f"O SAF-T é da empresa com NIF {nif_ficheiro}, não desta ({empresa.nif}).")
    faturas = raiz.find("SourceDocuments/SalesInvoices")
    pagamentos = raiz.find("SourceDocuments/Payments")
    if faturas is None and pagamentos is None:
        raise ErroVenda("O SAF-T não tem faturas nem recibos (SourceDocuments).")

    clientes = {_texto(c, "CustomerID"): (_texto(c, "CustomerTaxID"), _texto(c, "CompanyName"))
                for c in raiz.findall("MasterFiles/Customer")}
    produtos = {_texto(p, "ProductCode"): _texto(p, "ProductType") for p in raiz.findall("MasterFiles/Product")}
    existentes = {v.numero: v for v in db.scalars(select(FaturaVenda).where(FaturaVenda.empresa_id == empresa.id))}
    r = ResultadoImportacao()
    datas: list[date] = []
    novas: list[tuple[FaturaVenda, ET.Element]] = []

    for doc in (faturas.findall("Invoice") if faturas is not None else []):
        numero = _texto(doc, "InvoiceNo")
        tipo = _texto(doc, "InvoiceType")
        estado_saft = _texto(doc, "DocumentStatus/InvoiceStatus", "N")
        data = _data(_texto(doc, "InvoiceDate"))
        if tipo not in TIPOS or data is None:
            r.ignoradas += 1  # talões de venda, orçamentos e outros documentos que não são faturas
            continue
        datas.append(data)
        anterior = existentes.get(numero)
        if anterior:
            # Documento anulado depois de importado: estorna-se o que foi lançado
            if estado_saft == "A" and anterior.estado == VENDA_NORMAL:
                _anular(db, empresa, anterior, utilizador_id, r)
            else:
                r.duplicadas += 1
            continue

        nif, nome = clientes.get(_texto(doc, "CustomerID"), (CONSUMIDOR_FINAL, "Consumidor final"))
        linhas = _linhas_documento(doc, produtos, tipo == "NC")
        imposto, liquido, total = (_numero(doc, "DocumentTotals/TaxPayable"), _numero(doc, "DocumentTotals/NetTotal"),
                                   _numero(doc, "DocumentTotals/GrossTotal"))
        # O IVA de cada taxa acerta com o total de imposto do documento (arredondamentos do programa)
        diferenca = round(imposto - sum(ln["valor"] for ln in linhas), 2)
        if linhas and diferenca and abs(diferenca) <= 0.05:
            maior = max((ln for ln in linhas if ln["valor"]), key=lambda ln: ln["valor"], default=None)
            if maior:
                maior["valor"] = round(maior["valor"] + diferenca, 2)
        if abs(liquido + imposto - total) > 0.01:
            r.avisos.append(f"{numero}: o total ({total:.2f} €) não é a soma da base com o IVA "
                            "(retenção na fonte ou descontos financeiros): confirme o lançamento.")
        meio = _texto(doc, "DocumentTotals/Payment/PaymentMechanism") or None
        venda = FaturaVenda(
            empresa_id=empresa.id, numero=numero[:60], tipo=tipo, atcud=_texto(doc, "ATCUD") or None, data=data,
            data_vencimento=None if tipo in ("NC", "FR") else data + timedelta(days=PRAZO_OMISSAO),
            cliente_nif=nif or CONSUMIDOR_FINAL, cliente_nome=(nome or "Consumidor final")[:200], linhas=linhas,
            valor_sem_iva=round(liquido, 2), total_iva=round(imposto, 2), total=round(liquido + imposto, 2),
            estado={"A": VENDA_ANULADA, "F": VENDA_FATURADA}.get(estado_saft, VENDA_NORMAL),
            valor_recebido=0.0, recebida=False, meio_pagamento=meio, origem="saft", criada_por_id=utilizador_id,
        )
        db.add(venda)
        existentes[numero] = venda
        if venda.estado == VENDA_ANULADA:
            r.anuladas += 1
        else:
            r.importadas += 1
            novas.append((venda, doc))
    db.flush()

    if contabilizar_ja:
        for venda, doc in novas:
            if venda.estado != VENDA_NORMAL or not venda.linhas:
                continue
            try:
                contabilizar(db, empresa, venda, utilizador_id)
                if venda.tipo == "FR":
                    data_pagamento = _data(_texto(doc, "DocumentTotals/Payment/PaymentDate")) or venda.data
                    receber(db, empresa, venda, max(data_pagamento, venda.data), conta_financeira(venda.meio_pagamento),
                            utilizador_id=utilizador_id)
            except ErroContabilistico as erro:
                r.nao_contabilizadas += 1
                r.avisos.append(f"{venda.numero}: não foi contabilizada ({erro})")

    # Recibos: marcam as faturas como recebidas
    for recibo in (pagamentos.findall("Payment") if pagamentos is not None else []):
        if _texto(recibo, "DocumentStatus/PaymentStatus", "N") != "N":
            continue
        data = _data(_texto(recibo, "TransactionDate"))
        meio = _texto(recibo, "PaymentMethod/PaymentMechanism") or None
        for linha in recibo.findall("Line"):
            venda = existentes.get(_texto(linha, "SourceDocumentID/OriginatingON"))
            valor = round(_numero(linha, "CreditAmount") - _numero(linha, "DebitAmount"), 2)
            if venda is None or venda.recebida or venda.estado != VENDA_NORMAL or valor <= 0 or data is None:
                continue
            em_divida = round(venda.total - venda.valor_recebido, 2)
            try:
                receber(db, empresa, venda, max(data, venda.data), conta_financeira(meio), min(valor, em_divida),
                        utilizador_id, nota=f"Recibo {_texto(recibo, 'PaymentRefNo')}")
                r.recebimentos += 1
            except ErroContabilistico as erro:
                r.avisos.append(f"Recibo {_texto(recibo, 'PaymentRefNo')}: {erro}")
    db.flush()
    r.periodo = (min(datas, default=None), max(datas, default=None))
    return r


def _anular(db: Session, empresa: Empresa, venda: FaturaVenda, utilizador_id: int | None,
            r: ResultadoImportacao) -> None:
    for origem in ("recebimento", "venda"):
        while (lancamento := lancamento_ativo(db, empresa.id, venda.id, origem)) is not None:
            try:
                lancamentos.estornar(db, empresa, lancamento, utilizador_id)
                db.flush()  # a consulta seguinte tem de ver o lançamento já estornado
            except ErroContabilistico as erro:
                r.avisos.append(f"{venda.numero} foi anulada no programa de faturação mas não foi estornada: {erro}")
                return
    venda.estado = VENDA_ANULADA
    r.anuladas += 1


def resumo_venda(venda: FaturaVenda, hoje: date | None = None) -> dict:
    hoje = hoje or date.today()
    em_divida = round(venda.total - venda.valor_recebido, 2) if venda.tipo != "NC" else 0.0
    vencida = bool(em_divida > 0 and venda.estado == VENDA_NORMAL and venda.data_vencimento
                   and venda.data_vencimento < hoje)
    return {
        "id": venda.id, "numero": venda.numero, "tipo": venda.tipo, "tipo_nome": TIPOS.get(venda.tipo, venda.tipo),
        "atcud": venda.atcud, "data": venda.data, "data_vencimento": venda.data_vencimento,
        "cliente_nif": venda.cliente_nif, "cliente_nome": venda.cliente_nome, "linhas": venda.linhas,
        "valor_sem_iva": venda.valor_sem_iva, "total_iva": venda.total_iva, "total": venda.total,
        "estado": venda.estado, "valor_recebido": venda.valor_recebido, "em_divida": em_divida,
        "recebida": venda.recebida, "vencida": vencida, "origem": venda.origem,
        "importada_em": venda.criada_em if isinstance(venda.criada_em, datetime) else None,
    }
