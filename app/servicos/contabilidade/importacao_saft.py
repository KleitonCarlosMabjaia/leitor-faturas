"""
Importação do SAF-T (PT) da contabilidade: migração a partir de outro programa
(Primavera, PHC, Sage, TOConline, ...), ou de outro Dobra.

O que se importa:
- plano de contas: as contas que faltam são criadas (as de movimento, GM, e as agregadoras);
  os nomes e NIF de clientes e fornecedores vêm das tabelas Customer e Supplier;
- saldos de abertura (classes 1 a 5, à data de início do ficheiro), como lançamento de abertura;
- lançamentos (GeneralLedgerEntries), um a um, com o diário mapeado para os do Dobra e a
  referência original guardada no documento ("SAF-T VEN 2026/15").

Antes de gravar há sempre uma pré-visualização (simular=True), que mostra o que vai ser criado
e os problemas a resolver. Importar o mesmo período duas vezes é recusado.
"""

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modelos.tabelas import LANCAMENTO_ATIVO, Conta, Empresa, Lancamento
from app.servicos.contabilidade import abertura, lancamentos, plano_snc
from app.servicos.contabilidade.lancamentos import ErroContabilistico, LinhaNova

TAMANHO_MAXIMO = 40 * 1024 * 1024
ORIGEM = "importacao_saft"


class ErroImportacao(Exception):
    pass


@dataclass
class ContaSaft:
    codigo: str
    nome: str
    movimento: bool
    abertura: float = 0.0   # devedor positivo, credor negativo
    nif: str | None = None


@dataclass
class TransacaoSaft:
    diario_origem: str
    numero: str
    data: date
    descricao: str
    linhas: list[tuple[str, float, float, str]]  # conta, débito, crédito, descrição


@dataclass
class FicheiroSaft:
    nif: str
    empresa: str
    inicio: date
    fim: date
    contas: dict[str, ContaSaft]
    transacoes: list[TransacaoSaft] = field(default_factory=list)


def _texto(el: ET.Element | None, caminho: str, ns: str) -> str:
    if el is None:
        return ""
    filho = el.find(caminho.replace("{ns}", ns))
    return (filho.text or "").strip() if filho is not None and filho.text else ""


def _numero(texto: str) -> float:
    try:
        return round(float(texto or 0), 2)
    except ValueError:
        return 0.0


def ler(conteudo: bytes) -> FicheiroSaft:
    if len(conteudo) > TAMANHO_MAXIMO:
        raise ErroImportacao("O ficheiro tem mais de 40 MB.")
    if b"<!DOCTYPE" in conteudo[:2000] or b"<!ENTITY" in conteudo[:20000]:
        raise ErroImportacao("O ficheiro não é um SAF-T válido.")  # sem DTD nem entidades (XML malicioso)
    try:
        raiz = ET.fromstring(conteudo)
    except ET.ParseError:
        raise ErroImportacao("O ficheiro não é um XML válido. Exporte o SAF-T (PT) da contabilidade do outro programa.")
    if not raiz.tag.endswith("AuditFile"):
        raise ErroImportacao("O ficheiro não é um SAF-T (PT).")
    ns = raiz.tag[:raiz.tag.index("}") + 1] if raiz.tag.startswith("{") else ""
    cab = raiz.find(f"{ns}Header")
    base = _texto(cab, "{ns}TaxAccountingBasis", ns)
    if base not in ("C", "I"):
        raise ErroImportacao("Este SAF-T é de faturação. Para migrar a contabilidade, exporte o SAF-T (PT) "
                             "da contabilidade (tipo C ou I).")
    try:
        inicio = date.fromisoformat(_texto(cab, "{ns}StartDate", ns))
        fim = date.fromisoformat(_texto(cab, "{ns}EndDate", ns))
    except ValueError:
        raise ErroImportacao("O cabeçalho do SAF-T não tem as datas de início e fim.")
    nif = re.sub(r"\D", "", _texto(cab, "{ns}TaxRegistrationNumber", ns))
    ficheiro = FicheiroSaft(nif=nif, empresa=_texto(cab, "{ns}CompanyName", ns), inicio=inicio, fim=fim, contas={})

    mestres = raiz.find(f"{ns}MasterFiles")
    for conta in (mestres.iter(f"{ns}Account") if mestres is not None else []):
        codigo = re.sub(r"\D", "", _texto(conta, "{ns}AccountID", ns))
        if not codigo:
            continue
        categoria = _texto(conta, "{ns}GroupingCategory", ns)
        ficheiro.contas[codigo] = ContaSaft(
            codigo=codigo, nome=_texto(conta, "{ns}AccountDescription", ns) or f"Conta {codigo}",
            movimento=categoria in ("GM", ""),
            abertura=_numero(_texto(conta, "{ns}OpeningDebitBalance", ns))
            - _numero(_texto(conta, "{ns}OpeningCreditBalance", ns)),
        )
    for etiqueta, campo_nif, campo_nome in (("Customer", "CustomerTaxID", "CompanyName"),
                                            ("Supplier", "SupplierTaxID", "CompanyName")):
        for terceiro in (mestres.iter(f"{ns}{etiqueta}") if mestres is not None else []):
            codigo = re.sub(r"\D", "", _texto(terceiro, "{ns}AccountID", ns))
            if codigo in ficheiro.contas:
                ficheiro.contas[codigo].nif = re.sub(r"\D", "", _texto(terceiro, f"{{ns}}{campo_nif}", ns)) or None
                nome = _texto(terceiro, f"{{ns}}{campo_nome}", ns)
                if nome:
                    ficheiro.contas[codigo].nome = nome

    movimentos = raiz.find(f"{ns}GeneralLedgerEntries")
    for diario in (movimentos.iter(f"{ns}Journal") if movimentos is not None else []):
        id_diario = _texto(diario, "{ns}JournalID", ns)
        for t in diario.iter(f"{ns}Transaction"):
            linhas = []
            for tipo, campo in (("DebitLine", "DebitAmount"), ("CreditLine", "CreditAmount")):
                for ln in t.iter(f"{ns}{tipo}"):
                    valor = _numero(_texto(ln, f"{{ns}}{campo}", ns))
                    conta = re.sub(r"\D", "", _texto(ln, "{ns}AccountID", ns))
                    descricao = _texto(ln, "{ns}Description", ns)
                    debito = tipo == "DebitLine"
                    linhas.append((conta, valor if debito else 0.0, 0.0 if debito else valor, descricao))
            try:
                data = date.fromisoformat(_texto(t, "{ns}TransactionDate", ns))
            except ValueError:
                raise ErroImportacao(f"O lançamento {_texto(t, '{ns}TransactionID', ns)} não tem uma data válida.")
            ficheiro.transacoes.append(TransacaoSaft(
                diario_origem=id_diario,
                numero=_texto(t, "{ns}DocArchivalNumber", ns) or _texto(t, "{ns}TransactionID", ns),
                data=data, descricao=_texto(t, "{ns}Description", ns) or "Lançamento importado", linhas=linhas,
            ))
    return ficheiro


def diario_do_dobra(id_origem: str) -> str:
    """Os diários de cada programa têm nomes diferentes: mapeia-se pelo nome, e o resto vai para OD."""
    codigo = (id_origem or "").upper()
    if codigo in lancamentos.DIARIOS:
        return codigo
    for pista, diario in (("COMP", "COM"), ("FORN", "COM"), ("VEND", "VEN"), ("CLIE", "VEN"), ("BAN", "BAN"),
                          ("CAIX", "CX"), ("CX", "CX")):
        if pista in codigo:
            return diario
    return "OD"


def _ja_importado(db: Session, empresa_id: int, inicio: date, fim: date) -> bool:
    return db.scalar(select(Lancamento.id).where(
        Lancamento.empresa_id == empresa_id, Lancamento.origem.in_((ORIGEM, "abertura")),
        Lancamento.estado == LANCAMENTO_ATIVO, Lancamento.data >= inicio, Lancamento.data <= fim,
        Lancamento.documento.like("SAF-T%"),
    ).limit(1)) is not None


def _lancamentos_no_periodo(db: Session, empresa_id: int, inicio: date, fim: date) -> int:
    """Lançamentos ativos no período, sem contar a abertura (que pode vir de um balancete importado antes)."""
    return db.scalar(select(func.count()).select_from(Lancamento).where(
        Lancamento.empresa_id == empresa_id, Lancamento.estado == LANCAMENTO_ATIVO, Lancamento.estorno_de_id.is_(None),
        Lancamento.origem != "abertura", Lancamento.data >= inicio, Lancamento.data <= fim,
    )) or 0


def importar(db: Session, empresa: Empresa, conteudo: bytes, *, simular: bool, com_abertura: bool = True,
             com_movimentos: bool = True, utilizador_id: int | None = None) -> dict:
    ficheiro = ler(conteudo)
    plano_snc.garantir_plano(db, empresa.id)
    nossas = plano_snc.contas_por_codigo(db, empresa.id)
    problemas: list[str] = []
    avisos: list[str] = []

    if empresa.nif and ficheiro.nif and ficheiro.nif != empresa.nif:
        problemas.append(f"O ficheiro é da empresa com NIF {ficheiro.nif}, mas esta empresa tem o NIF {empresa.nif}.")
    if _ja_importado(db, empresa.id, ficheiro.inicio, ficheiro.fim):
        problemas.append("Este período já foi importado. Estorne esses lançamentos antes de importar de novo.")
    else:
        existentes = _lancamentos_no_periodo(db, empresa.id, ficheiro.inicio, ficheiro.fim)
        if existentes:
            problemas.append(
                f"Esta empresa já tem {existentes} lançamento(s) entre {ficheiro.inicio.strftime('%d/%m/%Y')} e "
                f"{ficheiro.fim.strftime('%d/%m/%Y')}: importar duplicaria a contabilidade. A migração é feita para "
                "um período ainda sem lançamentos (por exemplo, numa empresa nova).")
    if empresa.fechado_ate and ficheiro.inicio <= empresa.fechado_ate:
        problemas.append(f"O período está fechado até {empresa.fechado_ate.strftime('%d/%m/%Y')}.")

    usadas = {c for t in ficheiro.transacoes for c, *_ in t.linhas}
    novas = sorted(c for c in set(ficheiro.contas) | usadas if c and c not in nossas)
    conflitos = sorted(c for c in usadas if c in nossas and not nossas[c].movimento)
    for c in conflitos:
        tem_filhas = any(o != c and o.startswith(c) for o in nossas)
        if tem_filhas:
            problemas.append(f"A conta {c} é agregadora no Dobra (tem subcontas), mas o ficheiro lança nela.")

    desequilibrados = [t for t in ficheiro.transacoes
                       if abs(sum(d for _, d, _, _ in t.linhas) - sum(c for _, _, c, _ in t.linhas)) > 0.005]
    for t in desequilibrados[:5]:
        problemas.append(f"O lançamento {t.diario_origem} {t.numero} não está equilibrado.")
    fora = [t for t in ficheiro.transacoes if not (ficheiro.inicio <= t.data <= ficheiro.fim)]
    if fora:
        avisos.append(f"{len(fora)} lançamento(s) com data fora do período do ficheiro.")
    saldos_abertura = [c for c in ficheiro.contas.values()
                       if c.movimento and c.abertura and c.codigo[:1] in "12345"]
    if com_abertura and saldos_abertura and abertura.existente(db, empresa.id):
        avisos.append("Já existem saldos de abertura no Dobra: os do ficheiro não serão lançados.")
        com_abertura = False

    resumo = {
        "empresa": ficheiro.empresa, "nif": ficheiro.nif,
        "inicio": ficheiro.inicio.isoformat(), "fim": ficheiro.fim.isoformat(),
        "contas_no_ficheiro": len(ficheiro.contas), "contas_novas": len(novas),
        "lancamentos": len(ficheiro.transacoes) if com_movimentos else 0,
        "saldos_abertura": len(saldos_abertura) if com_abertura else 0,
        "total_debito": round(sum(d for t in ficheiro.transacoes for _, d, _, _ in t.linhas), 2),
        "diarios": sorted({f"{t.diario_origem} → {diario_do_dobra(t.diario_origem)}" for t in ficheiro.transacoes}),
        "problemas": problemas, "avisos": avisos, "importado": False,
    }
    if simular or problemas:
        return resumo

    # Contas: primeiro as agregadoras (mais curtas), para manter a árvore
    for codigo in sorted(novas, key=lambda c: (len(c), c)):
        origem = ficheiro.contas.get(codigo)
        movimento = origem.movimento if origem else True
        db.add(Conta(empresa_id=empresa.id, codigo=codigo, nome=(origem.nome if origem else f"Conta {codigo}")[:200],
                     movimento=movimento, nif=origem.nif if origem else None))
    for codigo in conflitos:
        nossas[codigo].movimento = True  # agregadora sem subcontas no Dobra, de movimento no programa anterior
    db.flush()

    try:
        if com_abertura and saldos_abertura:
            lancamento_abertura = abertura.lancar(db, empresa, ficheiro.inicio, [
                abertura.SaldoAbertura(c.codigo, nome=c.nome, nif=c.nif, debito=max(c.abertura, 0),
                                       credito=max(-c.abertura, 0))
                for c in saldos_abertura
            ], utilizador_id=utilizador_id)
            lancamento_abertura.documento = f"SAF-T abertura {ficheiro.inicio.year}"
        if com_movimentos:
            for t in sorted(ficheiro.transacoes, key=lambda x: (x.data, x.diario_origem, x.numero)):
                linhas = [LinhaNova(c, debito=d, credito=cr, descricao=desc or None) for c, d, cr, desc in t.linhas
                          if d or cr]
                if not linhas:
                    continue
                lancamentos.criar(db, empresa, diario_do_dobra(t.diario_origem), t.data, t.descricao, linhas,
                                  documento=f"SAF-T {t.diario_origem} {t.numero}"[:80], origem=ORIGEM,
                                  utilizador_id=utilizador_id)
    except (ErroContabilistico, abertura.ErroAbertura) as erro:
        db.rollback()
        raise ErroImportacao(f"A importação parou e nada foi gravado: {erro}")
    resumo["importado"] = True
    return resumo
