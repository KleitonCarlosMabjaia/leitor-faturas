"""
Gerador de ficheiros SAF-T (PT) de faturação para os testes, como os exportados por um
programa de faturação certificado. O ficheiro gerado é validado contra o XSD oficial
(test_vendas.py), para os testes da importação usarem a estrutura real.
"""

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

NS = "urn:OECD:StandardAuditFile-Tax:PT_1.04_01"


@dataclass
class Linha:
    produto: str          # código do produto (ver PRODUTOS)
    taxa: float
    valor: float          # sem IVA


@dataclass
class Documento:
    numero: str           # ex.: "FT A/1"
    data: str             # AAAA-MM-DD
    cliente: str          # código do cliente (ver CLIENTES)
    linhas: list[Linha]
    estado: str = "N"     # N normal, A anulado, F faturado
    meio: str | None = None  # forma de pagamento (FR)

    @property
    def tipo(self) -> str:
        return self.numero.split()[0]


@dataclass
class Recibo:
    numero: str           # ex.: "RG A/1"
    data: str
    cliente: str
    fatura: str           # número da fatura paga
    data_fatura: str
    valor: float
    meio: str = "TB"


CLIENTES = {
    "C1": ("500000000", "Hotel Atlântico, S.A."),
    "C2": ("999999990", "Consumidor final"),
    "C3": ("508000009", "Empresa de Eventos Lisboa, Lda."),
}
PRODUTOS = {"P1": ("P", "Refeição de grupo"), "S1": ("S", "Serviço de catering"), "P2": ("P", "Vinho")}
CODIGOS_TAXA = {23.0: "NOR", 13.0: "INT", 6.0: "RED", 0.0: "ISE"}


@dataclass
class Saft:
    nif: str
    documentos: list[Documento] = field(default_factory=list)
    recibos: list[Recibo] = field(default_factory=list)
    base_contabilistica: str = "F"


def _sub(pai, nome, texto=None):
    el = ET.SubElement(pai, f"{{{NS}}}{nome}")
    if texto is not None:
        el.text = str(texto)
    return el


def _morada(pai, nome):
    m = _sub(pai, nome)
    _sub(m, "AddressDetail", "Rua Direita, 45")
    _sub(m, "City", "Lisboa")
    _sub(m, "PostalCode", "1100-200")
    _sub(m, "Country", "PT")


def _valor(v: float) -> str:
    return f"{v:.2f}"


def gerar(saft: Saft) -> bytes:
    ET.register_namespace("", NS)
    raiz = ET.Element(f"{{{NS}}}AuditFile")
    h = _sub(raiz, "Header")
    for nome, valor in [("AuditFileVersion", "1.04_01"), ("CompanyID", saft.nif), ("TaxRegistrationNumber", saft.nif),
                        ("TaxAccountingBasis", saft.base_contabilistica), ("CompanyName", "Empresa de Teste")]:
        _sub(h, nome, valor)
    _morada(h, "CompanyAddress")
    for nome, valor in [("FiscalYear", "2026"), ("StartDate", "2026-10-01"), ("EndDate", "2026-10-31"),
                        ("CurrencyCode", "EUR"), ("DateCreated", "2026-11-02"), ("TaxEntity", "Global"),
                        ("ProductCompanyTaxID", "500000000"), ("SoftwareCertificateNumber", "1234"),
                        ("ProductID", "Programa de Faturação/Empresa de Software"), ("ProductVersion", "1.0")]:
        _sub(h, nome, valor)

    mestres = _sub(raiz, "MasterFiles")
    for codigo, (nif, nome) in CLIENTES.items():
        c = _sub(mestres, "Customer")
        _sub(c, "CustomerID", codigo)
        _sub(c, "AccountID", "Desconhecido")
        _sub(c, "CustomerTaxID", nif)
        _sub(c, "CompanyName", nome)
        _morada(c, "BillingAddress")
        _sub(c, "SelfBillingIndicator", 0)
    for codigo, (tipo, descricao) in PRODUTOS.items():
        p = _sub(mestres, "Product")
        _sub(p, "ProductType", tipo)
        _sub(p, "ProductCode", codigo)
        _sub(p, "ProductDescription", descricao)
        _sub(p, "ProductNumberCode", codigo)
    tabela = _sub(mestres, "TaxTable")
    for taxa, codigo in CODIGOS_TAXA.items():
        t = _sub(tabela, "TaxTableEntry")
        _sub(t, "TaxType", "IVA")
        _sub(t, "TaxCountryRegion", "PT")
        _sub(t, "TaxCode", codigo)
        _sub(t, "Description", f"Taxa {codigo}")
        _sub(t, "TaxPercentage", _valor(taxa))

    origem = _sub(raiz, "SourceDocuments")
    if saft.documentos:
        faturas = _sub(origem, "SalesInvoices")
        validos = [d for d in saft.documentos if d.estado != "A"]
        credito = sum(ln.valor for d in validos if d.tipo != "NC" for ln in d.linhas)
        debito = sum(ln.valor for d in validos if d.tipo == "NC" for ln in d.linhas)
        _sub(faturas, "NumberOfEntries", len(saft.documentos))
        _sub(faturas, "TotalDebit", _valor(debito))
        _sub(faturas, "TotalCredit", _valor(credito))
        for d in saft.documentos:
            f = _sub(faturas, "Invoice")
            _sub(f, "InvoiceNo", d.numero)
            _sub(f, "ATCUD", f"ABCD1234-{d.numero.split('/')[-1]}")
            estado = _sub(f, "DocumentStatus")
            _sub(estado, "InvoiceStatus", d.estado)
            _sub(estado, "InvoiceStatusDate", f"{d.data}T10:00:00")
            if d.estado == "A":
                _sub(estado, "Reason", "Erro na emissão")
            _sub(estado, "SourceID", "U1")
            _sub(estado, "SourceBilling", "P")
            _sub(f, "Hash", "A" * 172)
            _sub(f, "HashControl", "1")
            _sub(f, "InvoiceDate", d.data)
            _sub(f, "InvoiceType", d.tipo)
            regimes = _sub(f, "SpecialRegimes")
            for nome in ("SelfBillingIndicator", "CashVATSchemeIndicator", "ThirdPartiesBillingIndicator"):
                _sub(regimes, nome, 0)
            _sub(f, "SourceID", "U1")
            _sub(f, "SystemEntryDate", f"{d.data}T10:00:00")
            _sub(f, "CustomerID", d.cliente)
            imposto = 0.0
            for i, ln in enumerate(d.linhas, start=1):
                linha = _sub(f, "Line")
                _sub(linha, "LineNumber", i)
                _sub(linha, "ProductCode", ln.produto)
                _sub(linha, "ProductDescription", PRODUTOS[ln.produto][1])
                _sub(linha, "Quantity", "1")
                _sub(linha, "UnitOfMeasure", "UN")
                _sub(linha, "UnitPrice", _valor(ln.valor))
                _sub(linha, "TaxPointDate", d.data)
                _sub(linha, "Description", PRODUTOS[ln.produto][1])
                _sub(linha, "DebitAmount" if d.tipo == "NC" else "CreditAmount", _valor(ln.valor))
                tax = _sub(linha, "Tax")
                _sub(tax, "TaxType", "IVA")
                _sub(tax, "TaxCountryRegion", "PT")
                _sub(tax, "TaxCode", CODIGOS_TAXA[ln.taxa])
                _sub(tax, "TaxPercentage", _valor(ln.taxa))
                if ln.taxa == 0:
                    _sub(linha, "TaxExemptionReason", "Artigo 9.º do CIVA")
                    _sub(linha, "TaxExemptionCode", "M07")
                imposto += round(ln.valor * ln.taxa / 100, 2)
            liquido = sum(ln.valor for ln in d.linhas)
            totais = _sub(f, "DocumentTotals")
            _sub(totais, "TaxPayable", _valor(imposto))
            _sub(totais, "NetTotal", _valor(liquido))
            _sub(totais, "GrossTotal", _valor(liquido + imposto))
            if d.meio:
                pagamento = _sub(totais, "Payment")
                _sub(pagamento, "PaymentMechanism", d.meio)
                _sub(pagamento, "PaymentAmount", _valor(liquido + imposto))
                _sub(pagamento, "PaymentDate", d.data)

    if saft.recibos:
        pagamentos = _sub(origem, "Payments")
        _sub(pagamentos, "NumberOfEntries", len(saft.recibos))
        _sub(pagamentos, "TotalDebit", "0.00")
        _sub(pagamentos, "TotalCredit", _valor(sum(r.valor for r in saft.recibos)))
        for r in saft.recibos:
            p = _sub(pagamentos, "Payment")
            _sub(p, "PaymentRefNo", r.numero)
            _sub(p, "ATCUD", f"EFGH5678-{r.numero.split('/')[-1]}")
            _sub(p, "TransactionDate", r.data)
            _sub(p, "PaymentType", "RG")
            estado = _sub(p, "DocumentStatus")
            _sub(estado, "PaymentStatus", "N")
            _sub(estado, "PaymentStatusDate", f"{r.data}T10:00:00")
            _sub(estado, "SourceID", "U1")
            _sub(estado, "SourcePayment", "P")
            metodo = _sub(p, "PaymentMethod")
            _sub(metodo, "PaymentMechanism", r.meio)
            _sub(metodo, "PaymentAmount", _valor(r.valor))
            _sub(metodo, "PaymentDate", r.data)
            _sub(p, "SourceID", "U1")
            _sub(p, "SystemEntryDate", f"{r.data}T10:00:00")
            _sub(p, "CustomerID", r.cliente)
            linha = _sub(p, "Line")
            _sub(linha, "LineNumber", 1)
            origem_doc = _sub(linha, "SourceDocumentID")
            _sub(origem_doc, "OriginatingON", r.fatura)
            _sub(origem_doc, "InvoiceDate", r.data_fatura)
            _sub(linha, "CreditAmount", _valor(r.valor))
            totais = _sub(p, "DocumentTotals")
            _sub(totais, "TaxPayable", "0.00")
            _sub(totais, "NetTotal", _valor(r.valor))
            _sub(totais, "GrossTotal", _valor(r.valor))
    return ET.tostring(raiz, encoding="Windows-1252", xml_declaration=True)
