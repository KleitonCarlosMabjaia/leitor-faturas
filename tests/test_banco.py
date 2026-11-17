"""Extratos bancários (CSV, Excel, OFX) e reconciliação bancária."""

import io
from datetime import date, datetime

import pytest
from openpyxl import Workbook

from app.servicos.contabilidade import banco, extratos
from tests.conftest import criar_empresa_com_admin
from tests.test_contabilidade import criar_fatura

# ---------- Leitura de extratos ----------

# Ao estilo de um banco português: linhas de apresentação, ";" como separador, Windows-1252,
# montante com sinal, números com vírgula decimal e ponto de milhares
CSV_MONTANTE = """Consulta de movimentos
Conta à ordem;PT50 0000 0000 0000 0000 0000 0
Período;01-10-2026 a 31-10-2026

Data Lançamento;Data Valor;Descrição;Montante;Saldo Contabilístico
08-10-2026;08-10-2026;DD RENDA ESTABELECIMENTO;-1.500,00;3.500,00
31-10-2026;31-10-2026;DEPÓSITO VENDAS POS;2.345,67;5.845,67
Saldo final;;;;5.845,67
""".encode("cp1252")

# Outro banco: débito e crédito em colunas separadas, do mais recente para o mais antigo
CSV_DEBITO_CREDITO = """Data mov.;Data valor;Descrição;Débito;Crédito;Saldo contabilístico
05/11/2026;05/11/2026;COMISSAO MANUTENCAO CONTA;5,20;;1.000,00
02/11/2026;02/11/2026;TRF RECEBIDA CLIENTE;;105,20;1.005,20
""".encode()

CSV_INGLES = b"""Date,Description,Amount,Balance
2026-11-03,Card payment CAFE CENTRAL,-1.20,98.80
2026-11-03,Card payment CAFE CENTRAL,-1.20,97.60
"""

OFX = b"""OFXHEADER:100
DATA:OFXSGML

<OFX><BANKMSGSRSV1><STMTTRNRS><STMTRS><BANKTRANLIST>
<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20261104<TRNAMT>-42.50<FITID>ABC123<NAME>EDP COMERCIAL
<STMTTRN><TRNTYPE>CREDIT<DTPOSTED>20261106120000<TRNAMT>300.00<FITID>ABC124<NAME>TRF<MEMO>Cliente Silva
</BANKTRANLIST><LEDGERBAL><BALAMT>1257.50<DTASOF>20261106</LEDGERBAL></STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>
"""


def test_le_csv_com_apresentacao_montante_e_saldo():
    lido = extratos.ler(CSV_MONTANTE, "movimentos.csv")
    assert lido.formato == "csv"
    assert [(m.data, m.montante) for m in lido.movimentos] == [
        (date(2026, 10, 8), -1500.0), (date(2026, 10, 31), 2345.67)]
    assert lido.movimentos[1].descricao == "DEPÓSITO VENDAS POS"  # Windows-1252 bem lido
    assert lido.saldo_final == 5845.67


def test_le_csv_com_debito_e_credito_do_mais_recente_para_o_mais_antigo():
    lido = extratos.ler(CSV_DEBITO_CREDITO, "extrato.csv")
    assert [m.montante for m in lido.movimentos] == [-5.2, 105.2]
    assert lido.saldo_final == 1000.0  # o do movimento mais recente (primeira linha)


def test_movimentos_iguais_no_mesmo_dia_nao_se_confundem():
    lido = extratos.ler(CSV_INGLES, "statement.csv")
    assert len(lido.movimentos) == 2
    refs = extratos.referencias("121", lido.movimentos)
    assert refs[0] != refs[1]
    assert refs == extratos.referencias("121", extratos.ler(CSV_INGLES, "statement.csv").movimentos)


def test_le_excel():
    livro = Workbook()
    folha = livro.active
    folha.append(["Extrato da conta"])
    folha.append(["Data", "Descritivo", "Valor", "Saldo"])
    folha.append([datetime(2026, 11, 2), "TRF SEPA FORNECEDOR", -250.5, 749.5])
    folha.append([datetime(2026, 11, 3), "JUROS CREDORES", 0.42, 749.92])
    saida = io.BytesIO()
    livro.save(saida)
    lido = extratos.ler(saida.getvalue(), "extrato.xlsx")
    assert lido.formato == "xlsx"
    assert [m.montante for m in lido.movimentos] == [-250.5, 0.42]
    assert lido.saldo_final == 749.92


def test_le_ofx():
    lido = extratos.ler(OFX, "extrato.ofx")
    assert lido.formato == "ofx"
    assert [(m.data, m.montante, m.referencia) for m in lido.movimentos] == [
        (date(2026, 11, 4), -42.5, "ABC123"), (date(2026, 11, 6), 300.0, "ABC124")]
    assert lido.movimentos[1].descricao == "TRF Cliente Silva"
    assert lido.saldo_final == 1257.5


@pytest.mark.parametrize("conteudo, nome, mensagem", [
    (b"a;b;c\n1;2;3\n", "x.csv", "colunas"),
    (b"%PDF-1.4 ...", "extrato.pdf", "PDF"),
    (b"", "x.csv", "vazio"),
])
def test_ficheiros_que_nao_sao_extratos(conteudo, nome, mensagem):
    with pytest.raises(extratos.ErroExtrato, match=mensagem):
        extratos.ler(conteudo, nome, usar_ia=False)


@pytest.mark.parametrize("texto, valor", [
    ("1.234,56", 1234.56), ("-12,50", -12.5), ("12,50-", -12.5), ("(7,00)", -7.0), ("1,234.56", 1234.56),
    ("1.000", 1000.0), ("0.42", 0.42), ("€ 3,10", 3.1), ("", None), ("abc", None),
])
def test_numeros(texto, valor):
    assert extratos.ler_numero(texto) == valor


# ---------- Reconciliação ----------

def preparar(cliente, fabrica_sessoes, empresa_teste) -> str:
    """Capital, uma renda e uma venda já lançadas, e uma fatura de 123 € contabilizada por pagar."""
    def lancar(diario, data, descricao, linhas):
        resposta = cliente.post("/api/contabilidade/lancamentos", json={
            "diario": diario, "data": data, "descricao": descricao, "linhas": linhas})
        assert resposta.status_code == 201, resposta.text

    lancar("OD", "2026-01-02", "Capital", [{"conta": "121", "debito": 5000}, {"conta": "511", "credito": 5000}])
    lancar("BAN", "2026-10-08", "Renda de outubro",
           [{"conta": "6261", "debito": 800}, {"conta": "121", "credito": 800}])
    lancar("VEN", "2026-10-31", "Vendas de outubro",
           [{"conta": "121", "debito": 1230}, {"conta": "721", "credito": 1230}])
    # Um cheque passado mas ainda não descontado: está na contabilidade, não no banco
    lancar("BAN", "2026-11-05", "Cheque n.º 12", [{"conta": "6268", "debito": 30}, {"conta": "121", "credito": 30}])
    id_fatura = criar_fatura(fabrica_sessoes, empresa_teste, fornecedor="Bebidas do Norte, Lda.")
    assert cliente.post(f"/api/contabilidade/faturas/{id_fatura}/contabilizar", json={}).status_code == 201
    return id_fatura


EXTRATO_OUTUBRO = """Data;Descrição;Montante;Saldo
10-10-2026;DD RENDA LOJA OUTUBRO;-800,00;4.200,00
31-10-2026;DEPOSITO VENDAS;1.230,00;5.430,00
05-11-2026;TRF SEPA BEBIDAS DO NORTE LDA;-123,00;5.307,00
06-11-2026;COMISSAO MANUTENCAO CONTA 10/2026;-5,20;5.301,80
06-11-2026;IMPOSTO SELO S/COMISSAO;-0,21;5.301,59
07-11-2026;COMPRA XPTO 1234;-42,00;5.259,59
""".encode()


def importar(cliente, conteudo=EXTRATO_OUTUBRO, nome="extrato.csv"):
    resposta = cliente.post("/api/contabilidade/banco/importar", data={"conta": "121"},
                            files={"ficheiro": (nome, conteudo, "text/csv")})
    assert resposta.status_code == 201, resposta.text
    return resposta.json()


def movimentos(cliente, estado=None) -> dict[str, dict]:
    params = {"conta": "121", **({"estado": estado} if estado else {})}
    resposta = cliente.get("/api/contabilidade/banco/movimentos", params=params).json()
    return {m["descricao"]: m for m in resposta["movimentos"]}


def test_reconciliacao_completa(cliente, fabrica_sessoes, empresa_teste):
    id_fatura = preparar(cliente, fabrica_sessoes, empresa_teste)
    resultado = importar(cliente)
    assert resultado["importados"] == 6 and resultado["saldo_final"] == 5259.59

    # Reimportar o mesmo extrato não duplica nada
    assert importar(cliente)["importados"] == 0

    pendentes = movimentos(cliente, "pendente")
    assert pendentes["DD RENDA LOJA OUTUBRO"]["sugestoes"][0]["tipo"] == "associar"
    assert pendentes["TRF SEPA BEBIDAS DO NORTE LDA"]["sugestoes"][0]["tipo"] == "pagar_fatura"
    comissao = pendentes["COMISSAO MANUTENCAO CONTA 10/2026"]["sugestoes"][0]
    assert (comissao["tipo"], comissao["conta"]) == ("lancar", "6988")
    assert pendentes["IMPOSTO SELO S/COMISSAO"]["sugestoes"][0]["conta"] == "6812"
    assert pendentes["COMPRA XPTO 1234"]["sugestoes"] == []

    # Automático: só o que não tem dúvidas (renda, venda e a fatura); as palavras-chave ficam para o utilizador
    automatico = cliente.post("/api/contabilidade/banco/reconciliar-automaticamente", json={"conta": "121"}).json()
    assert (automatico["associar"], automatico["pagar_fatura"], automatico["lancar"]) == (2, 1, 0)
    fatura = cliente.get(f"/api/faturas/{id_fatura}").json()
    assert fatura["paga"] is True
    pagamento = next(la for la in cliente.get(f"/api/contabilidade/faturas/{id_fatura}").json()["lancamentos"]
                     if la["origem"] == "pagamento")
    assert pagamento["data"] == "2026-11-05"  # a data do banco, não a de hoje

    # Lançar a comissão e o selo a partir do extrato
    pendentes = movimentos(cliente, "pendente")
    for descricao, conta in (("COMISSAO MANUTENCAO CONTA 10/2026", "6988"), ("IMPOSTO SELO S/COMISSAO", "6812")):
        id_mov = pendentes[descricao]["id"]
        r = cliente.post(f"/api/contabilidade/banco/movimentos/{id_mov}/lancar", json={"conta": conta})
        assert r.status_code == 200 and r.json()["estado"] == "reconciliado"

    # Mapa: falta a compra XPTO (no banco) e o cheque (na contabilidade); a diferença fica explicada
    mapa = cliente.get("/api/contabilidade/banco/reconciliacao", params={"conta": "121"}).json()
    assert mapa["saldo_banco"] == 5259.59
    assert mapa["saidas_banco_por_lancar"] == 42.0
    assert mapa["creditos_contabilidade_fora_do_banco"] == 30.0
    assert mapa["diferenca_por_explicar"] == 0.0

    # No mês seguinte, a comissão é reconhecida pela regra aprendida e reconciliada sozinha
    novembro = "Data;Descrição;Montante\n06-12-2026;COMISSAO MANUTENCAO CONTA 11/2026;-5,20\n".encode()
    importar(cliente, novembro, "dezembro.csv")
    sugestao = movimentos(cliente, "pendente")["COMISSAO MANUTENCAO CONTA 11/2026"]["sugestoes"][0]
    assert sugestao["origem"] == "regra"
    assert cliente.post("/api/contabilidade/banco/reconciliar-automaticamente",
                        json={"conta": "121"}).json()["lancar"] == 1


def test_associar_ignorar_desfazer_e_estorno(cliente, fabrica_sessoes, empresa_teste):
    preparar(cliente, fabrica_sessoes, empresa_teste)
    importar(cliente)
    pendentes = movimentos(cliente, "pendente")
    renda = pendentes["DD RENDA LOJA OUTUBRO"]
    linha_id = renda["sugestoes"][0]["linha_id"]

    # Valor errado: recusado
    venda = pendentes["DEPOSITO VENDAS"]
    assert cliente.post(f"/api/contabilidade/banco/movimentos/{venda['id']}/associar",
                        json={"linha_id": linha_id}).status_code == 409

    r = cliente.post(f"/api/contabilidade/banco/movimentos/{renda['id']}/associar", json={"linha_id": linha_id})
    assert r.json()["estado"] == "reconciliado" and r.json()["lancamento"]["descricao"] == "Renda de outubro"
    # A mesma linha não serve dois movimentos
    assert cliente.post(f"/api/contabilidade/banco/movimentos/{renda['id']}/associar",
                        json={"linha_id": linha_id}).status_code == 409

    # Estornar o lançamento volta a pôr o movimento por reconciliar
    lancamento_id = r.json()["lancamento"]["lancamento_id"]
    assert cliente.post(f"/api/contabilidade/lancamentos/{lancamento_id}/estornar").status_code == 201
    assert movimentos(cliente)["DD RENDA LOJA OUTUBRO"]["estado"] == "pendente"

    xpto = pendentes["COMPRA XPTO 1234"]
    assert cliente.post(f"/api/contabilidade/banco/movimentos/{xpto['id']}/ignorar").json()["estado"] == "ignorado"
    assert cliente.post(f"/api/contabilidade/banco/movimentos/{xpto['id']}/desfazer").json()["estado"] == "pendente"


def test_apagar_extrato_so_sem_reconciliacoes(cliente, fabrica_sessoes, empresa_teste):
    preparar(cliente, fabrica_sessoes, empresa_teste)
    extrato = importar(cliente)["extrato_id"]
    xpto = movimentos(cliente)["COMPRA XPTO 1234"]
    cliente.post(f"/api/contabilidade/banco/movimentos/{xpto['id']}/ignorar")
    assert cliente.delete(f"/api/contabilidade/banco/extratos/{extrato}").status_code == 409
    cliente.post(f"/api/contabilidade/banco/movimentos/{xpto['id']}/desfazer")
    assert cliente.delete(f"/api/contabilidade/banco/extratos/{extrato}").status_code == 204
    assert movimentos(cliente) == {}


def test_importacoes_invalidas(cliente):
    r = cliente.post("/api/contabilidade/banco/importar", data={"conta": "121"},
                     files={"ficheiro": ("x.csv", b"isto nao e um extrato", "text/csv")})
    assert r.status_code == 422 and "colunas" in r.json()["detail"]
    r = cliente.post("/api/contabilidade/banco/importar", data={"conta": "12"},
                     files={"ficheiro": ("x.csv", EXTRATO_OUTUBRO, "text/csv")})
    assert r.status_code == 422  # a 12 é agregadora: escolha a 121


def test_bancos_isolados_entre_empresas(cliente, fabrica_sessoes, empresa_teste, novo_cliente):
    preparar(cliente, fabrica_sessoes, empresa_teste)
    importar(cliente)
    mov = movimentos(cliente)["COMPRA XPTO 1234"]
    criar_empresa_com_admin(fabrica_sessoes, "Outra", "admin@outra.pt")
    outro = novo_cliente("admin@outra.pt")
    assert outro.post(f"/api/contabilidade/banco/movimentos/{mov['id']}/ignorar").status_code == 404
    assert outro.get("/api/contabilidade/banco/movimentos", params={"conta": "121"}).json()["movimentos"] == []


def test_padrao_ignora_numeros_e_datas():
    assert banco.padrao("COMISSAO MANUTENCAO CONTA 03/2026 REF 9981") == "comissao manutencao conta"
    assert banco.padrao("Comissão manutenção conta 04/2026") == "comissao manutencao conta"
