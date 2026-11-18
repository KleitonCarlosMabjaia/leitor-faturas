"""
SAF-T (PT) da contabilidade, versão 1.04_01 (Portaria n.º 302/2016).

O ficheiro tem:
- Header: identificação da empresa, do período e do programa (TaxAccountingBasis "C");
- MasterFiles: plano de contas com saldos de abertura e de fecho, categoria (GR/GA/GM)
  e código de taxonomia S de cada conta de movimento; fornecedores com subconta própria;
- GeneralLedgerEntries: os lançamentos do período, por diário, com as linhas a débito e a crédito.

O resultado é validado nos testes contra o XSD oficial da AT (tests/dados/SAFTPT1.04_01.xsd).
O ficheiro é gerado em Windows-1252, como os ficheiros SAF-T (PT) da AT.
"""

import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app import config
from app.modelos.tabelas import CONSUMIDOR_FINAL, Conta, Empresa, Lancamento, LinhaLancamento
from app.servicos import validacao
from app.servicos.contabilidade import lancamentos, plano_snc, relatorios

NS = "urn:OECD:StandardAuditFile-Tax:PT_1.04_01"
VERSAO = "1.04_01"
PADRAO_CODIGO_POSTAL = re.compile(r"^\d{4}-\d{3}$")
CONTROLO = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
RAIZES_FORNECEDORES = ("2211", "2711")


class ErroSAFT(Exception):
    """O ficheiro não pode ser gerado. `problemas` diz o que corrigir."""

    def __init__(self, problemas: list[str]):
        super().__init__(" ".join(problemas))
        self.problemas = problemas


# ---------- Verificações ----------

def verificar(db: Session, empresa: Empresa, de: date, ate: date) -> list[str]:
    """Tudo o que impediria a AT de aceitar o ficheiro, em português e com a solução."""
    problemas = []
    if de > ate:
        problemas.append("A data inicial é posterior à data final.")
    if de.year != ate.year:
        problemas.append("O SAF-T da contabilidade é por exercício: escolha datas do mesmo ano.")
    if de.year < 2000:
        problemas.append("O SAF-T só aceita períodos a partir de 2000.")
    if not empresa.nif or not validacao.nif_valido(empresa.nif):
        problemas.append("Defina o NIF da empresa (Obrigações → Configuração).")
    if not empresa.morada or not empresa.localidade:
        problemas.append("Defina a morada e a localidade da sede da empresa (Obrigações → Configuração).")
    if not empresa.codigo_postal or not PADRAO_CODIGO_POSTAL.match(empresa.codigo_postal):
        problemas.append("Defina o código postal da sede no formato 0000-000 (Obrigações → Configuração).")

    contas = plano_snc.contas_por_codigo(db, empresa.id)
    for codigo, conta in sorted(contas.items()):
        if not conta.movimento or len(codigo) == 1:
            continue
        if len(codigo) == 2:
            problemas.append(f"A conta {codigo} é de 1.º grau mas é de movimento: crie uma subconta e use-a.")
        elif plano_snc.taxonomia(codigo) is None:
            problemas.append(f"A conta {codigo} {conta.nome} não tem código de taxonomia: "
                             "crie-a como subconta de uma conta do SNC com taxonomia.")
    return problemas


# ---------- Utilitários ----------

def _texto(valor: str | None, maximo: int, omissao: str = "Desconhecido") -> str:
    texto = CONTROLO.sub("", (valor or "").strip())
    return (texto or omissao)[:maximo]


def _valor(v: float) -> str:
    return f"{max(round(v, 2), 0.0) + 0.0:.2f}"


def _sub(pai: ET.Element, nome: str, texto: str | int | None = None) -> ET.Element:
    el = ET.SubElement(pai, f"{{{NS}}}{nome}")
    if texto is not None:
        el.text = str(texto)
    return el


def _pai(codigo: str, codigos: set[str]) -> str | None:
    """Conta-mãe mais próxima com 2 ou mais dígitos (as classes não entram no SAF-T)."""
    for n in range(len(codigo) - 1, 1, -1):
        if codigo[:n] in codigos:
            return codigo[:n]
    return None


def _saldos(db: Session, empresa_id: int, de: date, ate: date) -> tuple[dict[str, float], dict[str, float]]:
    """Saldos (débito - crédito) das contas de movimento na abertura e no fecho do período.
    Classes 1 a 5 e 8: desde o início; classes 6 e 7: desde 1 de janeiro (recomeçam em cada exercício)."""
    inicio_exercicio = date(ate.year, 1, 1)
    dia_anterior = de - timedelta(days=1)

    def saldos_ate(limite: date) -> dict[str, float]:
        balanco = relatorios.saldos_por_conta(db, empresa_id, ate=limite)
        resultados = relatorios.saldos_por_conta(db, empresa_id, ate=limite, de=inicio_exercicio)
        return {c: (resultados.get(c, 0.0) if c.startswith(relatorios.CLASSES_RESULTADOS) else v)
                for c, v in balanco.items()}

    # Se o período começa a 1 de janeiro, as contas de resultados abrem a zero (o intervalo fica vazio)
    return saldos_ate(dia_anterior), saldos_ate(ate)


def _agregar(saldos: dict[str, float], codigos: set[str]) -> dict[str, float]:
    """Saldo de cada conta do plano (as agregadoras somam as subcontas)."""
    total: dict[str, float] = defaultdict(float)
    for codigo, valor in saldos.items():
        for n in range(2, len(codigo) + 1):
            if codigo[:n] in codigos:
                total[codigo[:n]] += valor
    return total


# ---------- Ficheiro ----------

def gerar(db: Session, empresa: Empresa, de: date, ate: date, agora: datetime | None = None) -> bytes:
    """Gera o XML do SAF-T (PT) da contabilidade. Lança ErroSAFT se faltar alguma coisa."""
    plano_snc.garantir_plano(db, empresa.id)
    problemas = verificar(db, empresa, de, ate)
    if problemas:
        raise ErroSAFT(problemas)
    agora = (agora or datetime.now()).replace(microsecond=0)

    ET.register_namespace("", NS)
    raiz = ET.Element(f"{{{NS}}}AuditFile")
    _cabecalho(raiz, empresa, de, ate, agora)

    contas = {c.codigo: c for c in plano_snc.contas_por_codigo(db, empresa.id).values() if len(c.codigo) >= 2}
    mestres = _sub(raiz, "MasterFiles")
    _plano_de_contas(mestres, db, empresa.id, contas, de, ate)
    clientes = _clientes(mestres, contas)
    fornecedores = _fornecedores(mestres, contas)
    _movimentos(raiz, db, empresa.id, de, ate, clientes, fornecedores)

    return ET.tostring(raiz, encoding="Windows-1252", xml_declaration=True)


def _cabecalho(raiz: ET.Element, empresa: Empresa, de: date, ate: date, agora: datetime) -> None:
    c = _sub(raiz, "Header")
    _sub(c, "AuditFileVersion", VERSAO)
    _sub(c, "CompanyID", empresa.nif)
    _sub(c, "TaxRegistrationNumber", empresa.nif)
    _sub(c, "TaxAccountingBasis", "C")  # contabilidade
    _sub(c, "CompanyName", _texto(empresa.nome, 100))
    morada = _sub(c, "CompanyAddress")
    _sub(morada, "AddressDetail", _texto(empresa.morada, 210))
    _sub(morada, "City", _texto(empresa.localidade, 50))
    _sub(morada, "PostalCode", empresa.codigo_postal)
    _sub(morada, "Country", "PT")
    _sub(c, "FiscalYear", ate.year)
    _sub(c, "StartDate", de.isoformat())
    _sub(c, "EndDate", ate.isoformat())
    _sub(c, "CurrencyCode", "EUR")
    _sub(c, "DateCreated", agora.date().isoformat())
    _sub(c, "TaxEntity", "Global")  # ficheiro de contabilidade: a empresa toda
    _sub(c, "ProductCompanyTaxID", config.SAFT_NIF_PRODUTOR or empresa.nif)
    _sub(c, "SoftwareCertificateNumber", 0)  # programa de contabilidade: não sujeito a certificação
    produtor = _texto(config.SAFT_NOME_PRODUTOR or empresa.nome, 200).replace("/", "-")
    _sub(c, "ProductID", f"Leitor de Faturas/{produtor}")
    _sub(c, "ProductVersion", config.VERSAO_APLICACAO)
    _sub(c, "HeaderComment", "SAF-T (PT) da contabilidade gerado pelo Leitor de Faturas")


def _plano_de_contas(mestres: ET.Element, db: Session, empresa_id: int, contas: dict[str, Conta],
                     de: date, ate: date) -> None:
    codigos = set(contas)
    abertura, fecho = _saldos(db, empresa_id, de, ate)
    abertura, fecho = _agregar(abertura, codigos), _agregar(fecho, codigos)

    plano = _sub(mestres, "GeneralLedgerAccounts")
    _sub(plano, "TaxonomyReference", "S")
    for codigo in sorted(codigos):
        conta = contas[codigo]
        el = _sub(plano, "Account")
        _sub(el, "AccountID", codigo)
        _sub(el, "AccountDescription", _texto(conta.nome, 100))
        a, f = abertura.get(codigo, 0.0), fecho.get(codigo, 0.0)
        _sub(el, "OpeningDebitBalance", _valor(a if a > 0 else 0))
        _sub(el, "OpeningCreditBalance", _valor(-a if a < 0 else 0))
        _sub(el, "ClosingDebitBalance", _valor(f if f > 0 else 0))
        _sub(el, "ClosingCreditBalance", _valor(-f if f < 0 else 0))
        if len(codigo) == 2:
            _sub(el, "GroupingCategory", "GR")
        elif conta.movimento:
            _sub(el, "GroupingCategory", "GM")
            _sub(el, "GroupingCode", _pai(codigo, codigos))
            _sub(el, "TaxonomyCode", plano_snc.taxonomia(codigo))
        else:
            _sub(el, "GroupingCategory", "GA")
            _sub(el, "GroupingCode", _pai(codigo, codigos))


def _morada_desconhecida(pai: ET.Element) -> None:
    morada = _sub(pai, "BillingAddress")
    _sub(morada, "AddressDetail", "Desconhecido")
    _sub(morada, "City", "Desconhecido")
    _sub(morada, "PostalCode", "Desconhecido")
    _sub(morada, "Country", "PT")


def _clientes(mestres: ET.Element, contas: dict[str, Conta]) -> set[str]:
    """Um cliente por subconta de 2111 (a dos diversos fica com o NIF de consumidor final)."""
    usados = set()
    for codigo in sorted(contas):
        conta = contas[codigo]
        if not codigo.startswith("2111") or not conta.movimento:
            continue
        el = _sub(mestres, "Customer")
        _sub(el, "CustomerID", codigo)
        _sub(el, "AccountID", codigo)
        _sub(el, "CustomerTaxID", conta.nif or CONSUMIDOR_FINAL)
        _sub(el, "CompanyName", _texto(conta.nome, 100))
        _morada_desconhecida(el)
        _sub(el, "SelfBillingIndicator", 0)
        usados.add(codigo)
    return usados


def _fornecedores(mestres: ET.Element, contas: dict[str, Conta]) -> set[str]:
    """Um fornecedor por subconta com NIF. Devolve os SupplierID usados (= código da subconta)."""
    usados = set()
    for codigo in sorted(contas):
        conta = contas[codigo]
        if not conta.nif or not codigo.startswith(RAIZES_FORNECEDORES):
            continue
        el = _sub(mestres, "Supplier")
        _sub(el, "SupplierID", codigo)
        _sub(el, "AccountID", codigo)
        _sub(el, "SupplierTaxID", conta.nif)
        _sub(el, "CompanyName", _texto(conta.nome, 100))
        _morada_desconhecida(el)
        _sub(el, "SelfBillingIndicator", 0)
        usados.add(codigo)
    return usados


def _movimentos(raiz: ET.Element, db: Session, empresa_id: int, de: date, ate: date, clientes: set[str],
                fornecedores: set[str]) -> None:
    todos = db.scalars(
        select(Lancamento)
        .options(selectinload(Lancamento.linhas).joinedload(LinhaLancamento.conta))
        .where(Lancamento.empresa_id == empresa_id, Lancamento.data >= de, Lancamento.data <= ate)
        .order_by(Lancamento.diario, Lancamento.ano, Lancamento.numero)
    ).all()

    total_debito = sum(ln.debito for la in todos for ln in la.linhas)
    total_credito = sum(ln.credito for la in todos for ln in la.linhas)
    movimentos = _sub(raiz, "GeneralLedgerEntries")
    _sub(movimentos, "NumberOfEntries", len(todos))
    _sub(movimentos, "TotalDebit", _valor(total_debito))
    _sub(movimentos, "TotalCredit", _valor(total_credito))

    por_diario: dict[str, list[Lancamento]] = defaultdict(list)
    for la in todos:
        por_diario[la.diario].append(la)
    for codigo_diario in sorted(por_diario):
        diario = _sub(movimentos, "Journal")
        _sub(diario, "JournalID", codigo_diario)
        _sub(diario, "Description", lancamentos.DIARIOS.get(codigo_diario, codigo_diario))
        for la in por_diario[codigo_diario]:
            _transacao(diario, la, clientes, fornecedores)


def _transacao(diario: ET.Element, la: Lancamento, clientes: set[str], fornecedores: set[str]) -> None:
    arquivo = f"{la.ano}/{la.numero}"
    registo = (la.criado_em or datetime.now()).replace(microsecond=0)
    t = _sub(diario, "Transaction")
    _sub(t, "TransactionID", f"{la.data.isoformat()} {la.diario} {arquivo}")
    _sub(t, "Period", la.data.month)
    _sub(t, "TransactionDate", la.data.isoformat())
    _sub(t, "SourceID", f"U{la.criado_por_id}" if la.criado_por_id else "Sistema")
    _sub(t, "Description", _texto(la.descricao, 200))
    _sub(t, "DocArchivalNumber", arquivo)
    # A: apuramento de resultados (fecho do exercício); N: movimentos normais
    _sub(t, "TransactionType", "A" if la.origem == "fecho" else "N")
    _sub(t, "GLPostingDate", registo.date().isoformat())
    cliente = next((ln.conta.codigo for ln in la.linhas if ln.conta.codigo in clientes), None)
    fornecedor = next((ln.conta.codigo for ln in la.linhas if ln.conta.codigo in fornecedores), None)
    if cliente:
        _sub(t, "CustomerID", cliente)
    elif fornecedor:
        _sub(t, "SupplierID", fornecedor)

    linhas = _sub(t, "Lines")
    for ln in la.linhas:
        debito = ln.debito > 0
        el = _sub(linhas, "DebitLine" if debito else "CreditLine")
        _sub(el, "RecordID", str(ln.id))
        _sub(el, "AccountID", ln.conta.codigo)
        if la.documento:
            _sub(el, "SourceDocumentID", _texto(la.documento, 60))
        _sub(el, "SystemEntryDate", registo.isoformat())
        _sub(el, "Description", _texto(ln.descricao or la.descricao, 200))
        _sub(el, "DebitAmount" if debito else "CreditAmount", _valor(ln.debito if debito else ln.credito))


def nome_ficheiro(empresa: Empresa, de: date, ate: date) -> str:
    return f"SAFT-PT-contabilidade-{empresa.nif}-{de.isoformat()}-a-{ate.isoformat()}.xml"
