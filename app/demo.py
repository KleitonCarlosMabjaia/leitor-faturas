"""
Dados de demonstração: 30 faturas fictícias de um restaurante.

Uso:
    python -m app.demo            # cria a empresa de demonstração (se ainda não existir)
    python -m app.demo --limpar   # apaga a empresa de demonstração e cria-a de novo

Os dados ficam numa empresa própria ("... (demonstração)"), separada das
empresas reais. Os fornecedores, NIFs e valores são inventados. Cada fatura tem um PDF com
texto e um QR da AT verdadeiro (gerado aqui, desenhado em vetores), por isso
estes PDFs também podem ser carregados na aplicação para testar a leitura.
"""

import argparse
import random
import string
import sys
from dataclasses import dataclass
from datetime import date, timedelta

import zxingcpp
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db import SessaoLocal, preparar_base_dados
from app.dependencias import EMAIL_DEMO
from app.modelos.fatura import DadosFatura
from app.modelos.tabelas import (
    ESTADO_CONFIRMADA,
    ESTADO_POR_REVER,
    PAPEL_ADMIN,
    AcessoEmpresa,
    Artigo,
    AtivoFixo,
    CentroCusto,
    Conta,
    DepreciacaoAtivo,
    Diferimento,
    Empresa,
    ExtratoBancario,
    Fatura,
    FaturaVenda,
    Fornecedor,
    Funcionario,
    Inventario,
    Lancamento,
    LinhaInventario,
    LinhaLancamento,
    LinhaOrcamento,
    MovimentoBancario,
    MovimentoStock,
    ProcessamentoSalarial,
    RegistoAtividade,
    RegraBancaria,
    RegraFornecedor,
    Sessao,
    TokenAcesso,
    Utilizador,
)
from app.servicos import armazenamento, autenticacao, repositorio
from app.servicos.contabilidade import (
    ativos,
    banco,
    extratos,
    inventarios,
    lancamentos,
    obrigacoes,
    periodizacao,
    plano_snc,
    relatorios,
    salarios,
    vendas,
)
from app.servicos.contabilidade.lancamentos import LinhaNova

SEMENTE = 2026  # mesma semente -> mesmos dados em todas as execuções


@dataclass
class Produto:
    descricao: str
    unidade: str
    preco: tuple[float, float]  # intervalo de preço unitário sem IVA
    quantidade: tuple[int, int]
    taxa: float


@dataclass
class FornecedorDemo:
    nome: str
    morada: str
    categoria: str
    produtos: list[Produto]


FORNECEDORES = [
    FornecedorDemo("Distribuidora de Bebidas Rio Douro, Lda.", "Rua do Cais, 12, 4050-123 Porto", "bebidas", [
        Produto("Água mineral 1,5 L (pack 6)", "pack", (2.10, 2.60), (10, 30), 6),
        Produto("Refrigerante lata 33 cl (pack 24)", "pack", (11.50, 13.90), (2, 6), 23),
        Produto("Cerveja garrafa 33 cl (grade 24)", "grade", (14.20, 16.80), (3, 8), 23),
    ]),
    FornecedorDemo("Vinhos e Cervejas do Minho, S.A.", "Av. da Liberdade, 230, 4700-251 Braga", "bebidas", [
        Produto("Vinho Verde branco 75 cl", "garrafa", (3.20, 4.60), (12, 36), 23),
        Produto("Vinho tinto Douro 75 cl", "garrafa", (4.50, 7.90), (6, 24), 23),
    ]),
    FornecedorDemo("Talho Central do Mercado, Lda.", "Mercado Municipal, banca 4, 2000-075 Santarém", "carne", [
        Produto("Bife da vazia", "kg", (13.50, 16.90), (4, 10), 6),
        Produto("Entrecosto de porco", "kg", (5.20, 6.80), (5, 15), 6),
        Produto("Frango do campo", "kg", (3.40, 4.30), (8, 20), 6),
    ]),
    FornecedorDemo("Carnes Serra da Estrela, Lda.", "Zona Industrial, lote 18, 6300-625 Guarda", "carne", [
        Produto("Cabrito", "kg", (11.00, 14.50), (3, 8), 6),
        Produto("Enchidos regionais", "kg", (9.50, 12.00), (2, 6), 6),
    ]),
    FornecedorDemo("Peixaria Mar Atlântico, Lda.", "Rua da Lota, 3, 2450-065 Nazaré", "peixe", [
        Produto("Dourada", "kg", (8.50, 11.50), (4, 10), 6),
        Produto("Bacalhau demolhado", "kg", (10.90, 13.90), (5, 12), 6),
        Produto("Polvo", "kg", (12.00, 16.50), (2, 6), 6),
    ]),
    FornecedorDemo("Hortícolas Vale do Tejo, Lda.", "Estrada Nacional 118, km 32, 2140-012 Chamusca", "legumes", [
        Produto("Batata", "kg", (0.65, 0.95), (25, 60), 6),
        Produto("Alface", "un", (0.60, 0.90), (15, 40), 6),
        Produto("Tomate", "kg", (1.40, 2.20), (8, 20), 6),
    ]),
    FornecedorDemo("Frutas e Legumes da Quinta, Unipessoal Lda.", "Quinta do Pinhal, 2580-301 Alenquer", "legumes", [
        Produto("Cebola", "kg", (0.70, 1.10), (10, 30), 6),
        Produto("Laranja", "kg", (0.90, 1.40), (10, 25), 6),
        Produto("Cenoura", "kg", (0.60, 0.95), (8, 20), 6),
    ]),
]

CLIENTE = ("Restaurante O Cantinho da Esquina, Lda.", "Rua Direita, 45, 1100-200 Lisboa")


# ---------- NIF e QR ----------

def gerar_nif(rng: random.Random, prefixo: str = "5") -> str:
    """NIF fictício com dígito de controlo válido."""
    corpo = prefixo + "".join(rng.choice(string.digits) for _ in range(8 - len(prefixo)))
    soma = sum(int(d) * p for d, p in zip(corpo, range(9, 1, -1), strict=True))
    resto = soma % 11
    return corpo + str(0 if resto < 2 else 11 - resto)


def texto_qr(
    nif: str, nif_cliente: str, numero: str, atcud: str, emissao: date, linhas_iva: list[dict], total: float
) -> str:
    """Monta o texto do QR segundo a Portaria n.º 195/2020 (Continente)."""
    campos = {"reduzida": ("I3", "I4", 6), "intermedia": ("I5", "I6", 13), "normal": ("I7", "I8", 23)}
    partes = [f"A:{nif}", f"B:{nif_cliente}", "C:PT", "D:FT", "E:N", f"F:{emissao:%Y%m%d}",
              f"G:{numero}", f"H:{atcud}", "I1:PT"]
    for campo_base, campo_iva, taxa in campos.values():
        linha = next((li for li in linhas_iva if li["taxa"] == taxa), None)
        if linha:
            partes += [f"{campo_base}:{linha['base']:.2f}", f"{campo_iva}:{linha['valor']:.2f}"]
    total_iva = sum(li["valor"] for li in linhas_iva)
    partes += [f"N:{total_iva:.2f}", f"O:{total:.2f}", "Q:demo", "R:0000"]
    return "*".join(partes)


# ---------- PDF ----------

def _texto_pdf(texto: str) -> str:
    """Escapa o texto para dentro de ( ) num PDF."""
    return texto.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _euros(valor: float) -> str:
    inteiro, decimal = f"{valor:,.2f}".split(".")
    return f"{inteiro.replace(',', ' ')},{decimal} €"


def gerar_pdf(fornecedor: FornecedorDemo, nif: str, nif_cliente: str, numero: str, atcud: str,
              emissao: date, vencimento: date, itens: list[tuple], linhas_iva: list[dict], total: float) -> bytes:
    """Fatura A4 simples: texto em Helvetica e QR desenhado com retângulos."""
    ops: list[str] = []

    def escrever(x: float, y: float, texto: str, tamanho: float = 10, negrito: bool = False):
        fonte = "F2" if negrito else "F1"
        ops.append(f"BT /{fonte} {tamanho} Tf {x:.1f} {y:.1f} Td ({_texto_pdf(texto)}) Tj ET")

    def linha_horizontal(y: float):
        ops.append(f"0.6 w 50 {y:.1f} m 545 {y:.1f} l S")

    # Cabeçalho
    escrever(50, 790, fornecedor.nome, 15, negrito=True)
    escrever(50, 774, fornecedor.morada, 9)
    escrever(50, 762, f"NIF {nif}", 9)
    escrever(400, 790, "FATURA", 15, negrito=True)
    escrever(400, 774, numero, 10)
    escrever(400, 760, f"Data: {emissao:%d/%m/%Y}", 9)
    escrever(400, 748, f"Vencimento: {vencimento:%d/%m/%Y}", 9)

    escrever(50, 720, "Cliente", 9, negrito=True)
    escrever(50, 707, CLIENTE[0], 10)
    escrever(50, 695, CLIENTE[1], 9)
    escrever(50, 683, f"NIF {nif_cliente}", 9)

    # Linhas da fatura
    y = 645
    for x, titulo in ((50, "Descrição"), (300, "Qtd."), (350, "Preço un."), (430, "IVA"), (480, "Valor")):
        escrever(x, y, titulo, 9, negrito=True)
    linha_horizontal(y - 5)
    y -= 20
    for descricao, quantidade, unidade, preco, taxa in itens:
        escrever(50, y, descricao, 9)
        escrever(300, y, f"{quantidade} {unidade}", 9)
        escrever(350, y, _euros(preco), 9)
        escrever(430, y, f"{taxa:g}%", 9)
        escrever(480, y, _euros(round(quantidade * preco, 2)), 9)
        y -= 15
    linha_horizontal(y + 5)

    # Resumo do IVA e total
    y -= 20
    escrever(300, y, "Resumo do IVA", 9, negrito=True)
    y -= 15
    for linha in linhas_iva:
        escrever(300, y, f"Taxa {linha['taxa']:g}%   Base {_euros(linha['base'])}   IVA {_euros(linha['valor'])}", 9)
        y -= 13
    y -= 10
    escrever(300, y, f"Total sem IVA: {_euros(sum(li['base'] for li in linhas_iva))}", 10)
    escrever(300, y - 15, f"Total IVA: {_euros(sum(li['valor'] for li in linhas_iva))}", 10)
    escrever(300, y - 35, f"TOTAL: {_euros(total)}", 13, negrito=True)

    # Rodapé fiscal com QR
    texto = texto_qr(nif, nif_cliente, numero, atcud, emissao, linhas_iva, total)
    matriz = memoryview(zxingcpp.create_barcode(texto, zxingcpp.BarcodeFormat.QRCode)
                        .to_image(scale=1, add_quiet_zones=False))
    lado = matriz.shape[0]
    tamanho_modulo = 110 / lado
    origem_x, origem_y = 50, 60
    retangulos = [
        f"{origem_x + c * tamanho_modulo:.2f} {origem_y + (lado - 1 - lin) * tamanho_modulo:.2f} "
        f"{tamanho_modulo:.2f} {tamanho_modulo:.2f} re"
        for lin in range(lado) for c in range(lado) if matriz[lin, c] == 0  # 0 = módulo preto
    ]
    ops.append("0 g " + " ".join(retangulos) + " f")
    escrever(180, 150, f"ATCUD: {atcud}", 9)
    escrever(180, 137, "Documento fictício gerado para demonstração.", 8)
    escrever(180, 125, "Processado por programa certificado n.º 0000/AT (fictício).", 8)

    return _montar_pdf("\n".join(ops))


def _montar_pdf(conteudo: str) -> bytes:
    stream = conteudo.encode("cp1252")  # WinAnsiEncoding: acentos e o símbolo € em 1 byte
    fonte = "<< /Type /Font /Subtype /Type1 /BaseFont /{} /Encoding /WinAnsiEncoding >>"
    objetos = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R /F2 6 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        fonte.format("Helvetica").encode(),
        fonte.format("Helvetica-Bold").encode(),
    ]
    saida = bytearray(b"%PDF-1.4\n")
    posicoes = []
    for numero, corpo in enumerate(objetos, start=1):
        posicoes.append(len(saida))
        saida += b"%d 0 obj\n" % numero + corpo + b"\nendobj\n"
    inicio_xref = len(saida)
    saida += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objetos) + 1)
    for posicao in posicoes:
        saida += b"%010d 00000 n \n" % posicao
    saida += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objetos) + 1, inicio_xref)
    return bytes(saida)


# ---------- Criação dos dados ----------

NOME_EMPRESA_DEMO = "Restaurante O Cantinho da Esquina (demonstração)"


def empresa_demo(db: Session) -> Empresa | None:
    utilizador = db.scalar(select(Utilizador).where(Utilizador.email == EMAIL_DEMO))
    return utilizador.empresa if utilizador else None


def criar_empresa_demo(db: Session) -> Empresa:
    """Empresa e utilizador de demonstração (separados das empresas reais)."""
    empresa = Empresa(nome=NOME_EMPRESA_DEMO)
    db.add(empresa)
    db.flush()
    db.add(Utilizador(empresa_id=empresa.id, nome="Utilizador de demonstração", email=EMAIL_DEMO, papel=PAPEL_ADMIN))
    db.flush()
    return empresa


def criar_dados_demo(db: Session, empresa_id: int | None = None, quantidade: int = 30, hoje: date | None = None) -> int:
    """
    Cria as faturas de demonstração (com PDFs) e devolve quantas criou.
    Sem empresa_id, cria (ou reutiliza) a empresa de demonstração.
    """
    if empresa_id is None:
        empresa = empresa_demo(db) or criar_empresa_demo(db)
        empresa_id = empresa.id

    hoje = hoje or date.today()
    rng = random.Random(SEMENTE)
    nif_cliente = gerar_nif(rng, "5")
    # A empresa é o cliente que aparece nas faturas: mesmo NIF e morada (precisos para o SAF-T)
    empresa = db.get(Empresa, empresa_id)
    if not empresa.nif:
        empresa.nif = nif_cliente
        empresa.morada, empresa.codigo_postal, empresa.localidade = "Rua Direita, 45", "1100-200", "Lisboa"
    nifs = {f.nome: gerar_nif(rng, "5") for f in FORNECEDORES}
    series = {f.nome: rng.randint(10, 80) for f in FORNECEDORES}

    # Datas espalhadas pelos últimos 6 meses, das mais antigas para as mais recentes
    datas = sorted(hoje - timedelta(days=rng.randint(0, 180)) for _ in range(quantidade))
    # Duas faturas antigas ficam por pagar (aparecem como vencidas no dashboard)
    indices_vencidas = {quantidade // 3, quantidade // 2}

    for indice, emissao in enumerate(datas):
        # Os primeiros 7 garantem um de cada fornecedor; os restantes são sorteados
        fornecedor = FORNECEDORES[indice] if indice < len(FORNECEDORES) else rng.choice(FORNECEDORES)
        vencimento = emissao + timedelta(days=30)

        # Linhas de produtos e resumo do IVA
        itens = []
        for produto in rng.sample(fornecedor.produtos, k=rng.randint(1, len(fornecedor.produtos))):
            quantidade_produto = rng.randint(*produto.quantidade)
            preco = round(rng.uniform(*produto.preco), 2)
            itens.append((produto.descricao, quantidade_produto, produto.unidade, preco, produto.taxa))
        bases: dict[float, float] = {}
        for _, qtd, _, preco, taxa in itens:
            bases[taxa] = round(bases.get(taxa, 0.0) + qtd * preco, 2)
        linhas_iva = [{"taxa": t, "base": b, "valor": round(b * t / 100, 2)} for t, b in sorted(bases.items())]
        valor_sem_iva = round(sum(li["base"] for li in linhas_iva), 2)
        total = round(valor_sem_iva + sum(li["valor"] for li in linhas_iva), 2)

        series[fornecedor.nome] += 1
        numero = f"FT {emissao.year}/{series[fornecedor.nome]:04d}"
        codigo_validacao = "".join(rng.choice(string.ascii_uppercase + string.digits) for _ in range(8))
        atcud = f"{codigo_validacao}-{series[fornecedor.nome]}"
        nif = nifs[fornecedor.nome]

        conteudo = gerar_pdf(fornecedor, nif, nif_cliente, numero, atcud, emissao, vencimento, itens, linhas_iva, total)
        nome_ficheiro = f"{fornecedor.categoria}-{numero.replace(' ', '-').replace('/', '-')}.pdf"
        id_fatura, ficheiro = armazenamento.guardar_pdf(conteudo, nome_ficheiro, empresa_id)

        dados = DadosFatura(
            fornecedor=fornecedor.nome, nif_fornecedor=nif, numero_fatura=numero,
            data_emissao=emissao.isoformat(), data_vencimento=vencimento.isoformat(),
            valor_sem_iva=valor_sem_iva, iva=linhas_iva, total=total, moeda="EUR", confianca="alta",
        )
        fatura = Fatura(
            id=id_fatura, empresa_id=empresa_id, ficheiro=ficheiro, nome_original=nome_ficheiro,
            tamanho_kb=round(len(conteudo) / 1024, 1), estado_extracao="concluida",
            origem="qr+ia", atcud=atcud, tipo_documento="FT",
            # As 4 mais recentes ficam por rever, para a demonstração ter trabalho pendente
            estado=ESTADO_POR_REVER if indice >= quantidade - 4 else ESTADO_CONFIRMADA,
            paga=vencimento < hoje and indice not in indices_vencidas,
        )
        repositorio.aplicar_dados(db, fatura, dados)
        db.add(fatura)
        db.flush()  # para o fornecedor criado ficar visível nas próximas faturas

    criar_contabilidade_demo(db, empresa_id, min(datas), hoje)
    criar_centros_demo(db, empresa_id)
    criar_gestao_demo(db, empresa_id, min(datas), hoje)
    db.commit()
    return quantidade


PRECOS_DEMO = {"B01": 14.0, "B02": 22.0, "B03": 43.2, "B04": 2.5, "C01": 0.0, "C02": 0.0, "C03": 0.0, "C04": 0.0}


def criar_gestao_demo(db: Session, empresa_id: int, inicio: date, hoje: date) -> None:
    """Artigos com stock, compras e consumos mensais, saídas ligadas a vendas e um orçamento do ano."""
    from app.servicos.gestao import orcamento as orc
    from app.servicos.gestao import stock

    abertura = date(inicio.year, inicio.month, 1)
    artigos = {}
    for codigo, nome, categoria, unidade, inicial, _, custo in ARTIGOS_DEMO:
        a = stock.guardar(db, empresa_id, None, codigo=codigo, nome=nome, tipo=categoria, unidade=unidade,
                          preco_venda=PRECOS_DEMO[codigo], taxa_iva=13.0 if codigo.startswith("C") else 23.0,
                          stock_minimo=round(inicial * 0.4))
        stock.movimentar(db, empresa_id, a, "entrada", inicial, abertura, custo_unitario=custo,
                         documento="Stock inicial")
        artigos[codigo] = (a, inicial, custo)
    stock.guardar(db, empresa_id, None, codigo="S01", nome="Serviço de catering (por pessoa)", tipo="S",
                  unidade="UN", preco_venda=28.0, taxa_iva=13.0)
    # Cada mês: compras no dia 5 e consumos ao longo do mês (os últimos meses gastam mais do que compram)
    dia = abertura
    n = 0
    while dia <= hoje:
        compra = date(dia.year, dia.month, 5)
        for codigo, (a, inicial, custo) in artigos.items():
            if compra <= hoje:
                stock.movimentar(db, empresa_id, a, "entrada", round(inicial * 0.8), compra,
                                 custo_unitario=round(custo * (1 + 0.01 * n), 2), documento=f"Compra {compra:%m/%Y}")
            consumo = date(dia.year, dia.month, 25)
            if consumo <= hoje:
                quantidade = min(a.quantidade, round(inicial * (0.85 if codigo in ("B03", "C02") else 0.75)))
                if quantidade > 0:
                    stock.movimentar(db, empresa_id, a, "saida", quantidade, consumo,
                                     preco_unitario=a.preco_venda or None, documento=f"Consumo {consumo:%m/%Y}")
        n += 1
        dia = date(dia.year + dia.month // 12, dia.month % 12 + 1, 1)
    # Vendas a empresas com vinho servido (saída ligada à fatura)
    for venda in db.scalars(select(FaturaVenda).where(FaturaVenda.empresa_id == empresa_id,
                                                      FaturaVenda.tipo != "NC").order_by(FaturaVenda.data).limit(3)):
        a = artigos["B02"][0]
        if a.quantidade >= 2:
            stock.saida_por_venda(db, db.get(Empresa, empresa_id), venda,
                                  [stock.LinhaMovimento(a.id, 2, 22.0)])
    # Orçamento: o ritmo real do ano, com mais 8% de vendas
    real = orc.real(db, empresa_id, hoje.year, hoje)
    meses = max(1, hoje.month - 1)
    valores = {}
    for chave, _, _, sinal in orc.RUBRICAS:
        media = sum(real[chave][:meses]) / meses
        valores[chave] = [round(max(media, 0) * (1.08 if sinal > 0 else 1.0), 2)] * 12
    orc.guardar(db, empresa_id, hoje.year, valores)


def criar_centros_demo(db: Session, empresa_id: int) -> None:
    """Contabilidade analítica de exemplo: o restaurante separa a sala do take-away."""
    sala = CentroCusto(empresa_id=empresa_id, codigo="SALA", nome="Restaurante (sala)")
    take = CentroCusto(empresa_id=empresa_id, codigo="TAKE", nome="Take-away e entregas")
    db.add_all([sala, take])
    db.flush()
    linhas = db.scalars(
        select(LinhaLancamento).join(Lancamento).join(Conta, Conta.id == LinhaLancamento.conta_id)
        .where(Lancamento.empresa_id == empresa_id,
               Lancamento.origem.not_in(("fecho", "aplicacao", "abertura", "estorno")))
        .order_by(LinhaLancamento.id)
    )
    for n, linha in enumerate(linhas):
        if linha.conta.codigo.startswith(("6", "7", "31")):
            # Cerca de 3/4 para a sala e 1/4 para o take-away
            linha.centro_custo_id = take.id if n % 4 == 0 else sala.id


def _fim_do_mes(ano: int, mes: int) -> date:
    return date(ano + mes // 12, mes % 12 + 1, 1) - timedelta(days=1)


def criar_contabilidade_demo(db: Session, empresa_id: int, inicio: date, hoje: date,
                             por_contabilizar: int = 3) -> None:
    """
    Contabilidade de um restaurante: capital inicial, vendas, rendas, salários, as faturas
    confirmadas (menos as mais recentes, para haver trabalho pendente) e o apuramento do IVA
    de cada trimestre já terminado, com o respetivo pagamento.
    """
    rng = random.Random(SEMENTE + 1)
    empresa = db.get(Empresa, empresa_id)
    plano_snc.garantir_plano(db, empresa_id)

    def lancar(diario: str, data: date, descricao: str, linhas: list[LinhaNova], documento: str | None = None):
        if data <= hoje:
            lancamentos.criar(db, empresa, diario, data, descricao, linhas, documento=documento)

    abertura = date(inicio.year, inicio.month, 1)
    posto = plano_snc.subconta_terceiro(db, empresa_id, "2211", gerar_nif(rng, "5"),
                                        "Posto de Combustíveis da Estrada, Lda.")
    lancar("OD", abertura, "Entrada de capital e saldo bancário inicial",
           [LinhaNova("121", debito=25000), LinhaNova("511", credito=25000)])
    empresa.niss = "20012345678"
    for nome, tipo, base, alimentacao, irs, nascimento, niss in PESSOAL_DEMO:
        salarios.registar(db, empresa, nome=nome, tipo=tipo, data_admissao=abertura, salario_base=base,
                          subsidio_alimentacao=alimentacao, alimentacao_cartao=tipo == "pessoal",
                          irs_situacao=irs[0], irs_dependentes=irs[1],
                          data_nascimento=nascimento, niss=niss, nif=gerar_nif(rng, "2"))
    # Seguro multirriscos anual pago na abertura e repartido pelos 12 meses
    lancar("BAN", abertura + timedelta(days=9), "Seguro multirriscos anual",
           [LinhaNova("6263", debito=1440), LinhaNova("121", credito=1440)], documento="Apólice 2026")
    if abertura + timedelta(days=9) <= hoje:
        periodizacao.registar(db, empresa, tipo="gasto", descricao="Seguro multirriscos do restaurante", conta="6263",
                              valor=1440, inicio=abertura, meses=12, data=abertura + timedelta(days=9))

    ano, mes = abertura.year, abertura.month
    while date(ano, mes, 1) <= hoje:
        fim_mes = _fim_do_mes(ano, mes)
        fim = min(fim_mes, hoje)
        periodo = f"{obrigacoes.NOMES_MESES[mes - 1]} de {ano}"

        # Vendas do mês: refeições a 13% e bebidas a 23% (depositadas no banco)
        proporcao = fim.day / fim_mes.day
        refeicoes = round(rng.uniform(7000, 9000) * proporcao, 2)
        bebidas = round(rng.uniform(1800, 2600) * proporcao, 2)
        iva_refeicoes, iva_bebidas = round(refeicoes * 0.13, 2), round(bebidas * 0.23, 2)
        lancar("VEN", fim, f"Vendas de {periodo} (resumo do POS)", [
            LinhaNova("121", debito=round(refeicoes + bebidas + iva_refeicoes + iva_bebidas, 2)),
            LinhaNova("721", credito=refeicoes, descricao="Refeições", tipo_iva="base", taxa_iva=13),
            LinhaNova("24332", credito=iva_refeicoes, descricao="IVA 13%", tipo_iva="iva", taxa_iva=13),
            LinhaNova("711", credito=bebidas, descricao="Bebidas", tipo_iva="base", taxa_iva=23),
            LinhaNova("24333", credito=iva_bebidas, descricao="IVA 23%", tipo_iva="iva", taxa_iva=23),
        ], documento=f"Z {ano}/{mes:02d}")

        lancar("BAN", date(ano, mes, 8), f"Renda do estabelecimento - {periodo}",
               [LinhaNova("6261", debito=1500), LinhaNova("121", credito=1500)], documento="Recibo de renda")

        # Gasóleo da carrinha (só 50% do IVA é dedutível) e um almoço com um fornecedor (representação)
        dia = date(ano, mes, 15)
        if dia <= hoje:
            base = round(rng.uniform(90, 160), 2)
            iva_total = round(base * 0.23, 2)
            dedutivel = round(iva_total / 2, 2)
            documento = f"FS {ano}/{mes:02d}15"
            lancar("COM", dia, f"{posto.nome} - {documento}", [
                LinhaNova("6242", debito=base, descricao="Base 23%", tipo_iva="base", taxa_iva=23),
                LinhaNova("24323", debito=dedutivel, descricao="IVA dedutível 23%", tipo_iva="iva", taxa_iva=23),
                LinhaNova("6242", debito=round(iva_total - dedutivel, 2), descricao="IVA não dedutível 23%",
                          tipo_iva="iva_nd", taxa_iva=23),
                LinhaNova(posto.codigo, credito=round(base + iva_total, 2)),
            ], documento=documento)
            lancar("BAN", dia, f"Pagamento {posto.nome} - {documento}",
                   [LinhaNova(posto.codigo, debito=round(base + iva_total, 2)),
                    LinhaNova("121", credito=round(base + iva_total, 2))], documento=documento)
        if mes % 2 == 0 and date(ano, mes, 22) <= hoje:
            refeicao = round(rng.uniform(60, 110), 2)
            iva_refeicao = round(refeicao * 0.13, 2)
            lancar("BAN", date(ano, mes, 22), "Almoço de negócios com fornecedor (despesa de representação)", [
                LinhaNova("6266", debito=refeicao, descricao="Base 13%", tipo_iva="base", taxa_iva=13),
                LinhaNova("6266", debito=iva_refeicao, descricao="IVA não dedutível 13%",
                          tipo_iva="iva_nd", taxa_iva=13),
                LinhaNova("121", credito=round(refeicao + iva_refeicao, 2)),
            ], documento=f"FS {ano}/{mes:02d}22")

        # Salários: processamento, pagamento ao pessoal e entrega ao Estado até dia 20 do mês seguinte
        if fim_mes <= hoje:
            processamento = salarios.processar(db, empresa, ano, mes, salarios.opcoes_do_mes(mes, ano))
            salarios.pagar(db, empresa, processamento, "salarios", fim_mes)
            prazo = salarios.prazo_pagamento(ano, mes, "irs")
            if prazo <= hoje:
                salarios.pagar(db, empresa, processamento, "seguranca_social", prazo)
                salarios.pagar(db, empresa, processamento, "irs", prazo)
        ano, mes = (ano + 1, 1) if mes == 12 else (ano, mes + 1)

    # Faturas confirmadas (as mais recentes ficam por contabilizar)
    confirmadas = db.scalars(select(Fatura).where(
        Fatura.empresa_id == empresa_id, Fatura.estado == ESTADO_CONFIRMADA
    ).order_by(Fatura.data_emissao)).all()
    for fatura in confirmadas[: max(len(confirmadas) - por_contabilizar, 0)]:
        lancamentos.contabilizar_fatura(db, empresa, fatura, hoje=hoje)

    # Inventários: contagem inicial na abertura e contagem do fim do mês passado, com o CMVMC apurado
    criar_inventarios_demo(db, empresa, abertura, hoje)
    criar_ativos_demo(db, empresa, abertura, hoje)
    fim_mes_passado = date(hoje.year, hoje.month, 1) - timedelta(days=1)
    if periodizacao.em_atraso(db, empresa_id, fim_mes_passado):
        periodizacao.reconhecer(db, empresa, fim_mes_passado)

    # Apuramento do IVA de cada trimestre terminado e pagamento até dia 25 do 2.º mês seguinte
    ano, trimestre = abertura.year, (abertura.month - 1) // 3
    while (fim := _fim_do_mes(ano, trimestre * 3 + 3)) < hoje:
        try:
            apuramento = lancamentos.apurar_iva(db, empresa, fim, f"{trimestre + 1}.º trimestre de {ano}")
            a_pagar = sum(ln.credito for ln in apuramento.linhas if ln.conta.codigo == "2436")
        except lancamentos.ErroContabilistico:
            a_pagar = 0
        if a_pagar:
            pagamento = fim + timedelta(days=1)
            pagamento = date(pagamento.year + (pagamento.month + 1 > 12), (pagamento.month % 12) + 1, 25)
            lancar("BAN", pagamento, f"Pagamento do IVA - {trimestre + 1}.º trimestre de {ano}",
                   [LinhaNova("2436", debito=a_pagar), LinhaNova("121", credito=a_pagar)])
        ano, trimestre = (ano + 1, 0) if trimestre == 3 else (ano, trimestre + 1)
    db.flush()
    criar_vendas_demo(db, empresa, hoje)
    criar_extrato_demo(db, empresa, hoje)


PESSOAL_DEMO = [
    # (nome, tipo, vencimento base, subsídio de alimentação por dia, (situação IRS, dependentes), nascimento, NISS)
    ("Maria Fernandes (cozinheira)", "pessoal", 1150.0, 8.0, ("nao_casado", 1), date(1984, 3, 12), "12345678901"),
    ("João Pereira (empregado de mesa)", "pessoal", 920.0, 8.0, ("nao_casado", 0), date(1996, 9, 30),
     "12345678902"),
    ("Carla Sousa (gerente)", "orgaos_sociais", 1800.0, 6.0, ("casado_dois", 2), date(1979, 6, 4), "12345678903"),
]

ARTIGOS_DEMO = [
    # (código, descrição, categoria, unidade, quantidade inicial, quantidade no fim do mês passado, custo)
    ("B01", "Vinho Verde branco 75 cl", "M", "GR", 36, 30, 3.85),
    ("B02", "Vinho tinto Douro 75 cl", "M", "GR", 24, 20, 6.10),
    ("B03", "Cerveja 33 cl (grade 24)", "M", "GR", 6, 5, 15.40),
    ("B04", "Água mineral 1,5 L (pack 6)", "M", "PK", 20, 14, 2.35),
    ("C01", "Bacalhau demolhado", "P", "KG", 12, 9, 12.40),
    ("C02", "Bife da vazia", "P", "KG", 8, 6, 15.20),
    ("C03", "Batata", "P", "KG", 50, 35, 0.80),
    ("C04", "Azeite virgem extra 5 L", "P", "UN", 4, 3, 32.50),
]


def criar_inventarios_demo(db: Session, empresa: Empresa, abertura: date, hoje: date) -> None:
    fim_mes_passado = date(hoje.year, hoje.month, 1) - timedelta(days=1)

    def contagem(data: date, indice: int, descricao: str):
        linhas = [inventarios.LinhaNovaInventario(codigo, nome, artigo[indice], artigo[6], categoria, unidade)
                  for artigo in ARTIGOS_DEMO for codigo, nome, categoria, unidade in [artigo[:4]]]
        return inventarios.criar(db, empresa, data, descricao, linhas)

    inicial = contagem(abertura, 4, "Inventário de abertura")
    inventarios.registar_inicial(db, empresa, inicial)
    if fim_mes_passado > abertura:
        final = contagem(fim_mes_passado, 5, "Contagem do fim do mês")
        inventarios.apurar(db, empresa, final)
    db.flush()


ATIVOS_DEMO = [
    # (descrição, conta, valor sem IVA, taxa)
    ("Fogão e forno industriais", "433", 4800.0, 12.5),
    ("Máquina de café profissional", "433", 1350.0, 12.5),
    ("Computador e impressora do escritório", "435", 890.0, 33.33),
]


def criar_ativos_demo(db: Session, empresa: Empresa, abertura: date, hoje: date) -> None:
    """Equipamento comprado na abertura (pago por transferência) e depreciado até ao fim do mês passado."""
    for descricao, conta, valor, taxa in ATIVOS_DEMO:
        imposto = round(valor * 0.23, 2)
        lancamentos.criar(db, empresa, "COM", abertura + timedelta(days=2), f"Compra: {descricao}", [
            LinhaNova(conta, debito=valor, descricao=descricao, tipo_iva="base", taxa_iva=23),
            LinhaNova("24322", debito=imposto, descricao="IVA dedutível 23%", tipo_iva="iva", taxa_iva=23),
            LinhaNova("121", credito=round(valor + imposto, 2)),
        ], documento="Fatura do fornecedor")
        ativos.registar(db, empresa, descricao=descricao, conta_ativo=conta,
                        data_aquisicao=abertura + timedelta(days=2), valor_aquisicao=valor, taxa=taxa)
    fim_mes_passado = date(hoje.year, hoje.month, 1) - timedelta(days=1)
    if fim_mes_passado > abertura:
        ativos.lancar_depreciacoes(db, empresa, fim_mes_passado)
    db.flush()


CLIENTES_DEMO = [
    ("Hotel Miradouro do Tejo, S.A.", 1),
    ("Eventos e Casamentos Lisboa, Lda.", 2),
    ("Escritórios Avenida Central, Lda.", 3),
]


def criar_vendas_demo(db: Session, empresa: Empresa, hoje: date) -> None:
    """
    Faturas de catering a empresas (registadas como se viessem do programa de faturação),
    a 30 dias: as antigas recebidas por transferência, uma vencida por cobrar, a mais
    recente por receber e uma nota de crédito.
    """
    rng = random.Random(SEMENTE + 3)
    clientes = [(nome, gerar_nif(rng, "5")) for nome, _ in CLIENTES_DEMO]
    datas = sorted(hoje - timedelta(days=d) for d in (150, 128, 101, 86, 63, 47, 40, 12))
    for indice, data in enumerate(datas, start=1):
        nome, nif = clientes[indice % len(clientes)]
        servico = round(rng.uniform(600, 1800), 2)
        bebidas = round(rng.uniform(80, 300), 2)
        venda = vendas.registar(
            db, empresa, numero=f"FT A/{indice}", tipo="FT", data=data, cliente_nif=nif, cliente_nome=nome,
            atcud=f"JJ7K2M9P-{indice}",
            linhas=[vendas.LinhaVenda(13, servico, "721"), vendas.LinhaVenda(23, bebidas, "711")],
        )
        # Recebidas 25 a 35 dias depois, exceto a vencida (a 6.ª) e as que ainda não venceram
        recebimento = data + timedelta(days=rng.randint(25, 35))
        if indice != 6 and recebimento <= hoje - timedelta(days=3):
            vendas.receber(db, empresa, venda, recebimento, "121")
        if indice == 3:
            vendas.registar(db, empresa, numero="NC A/1", tipo="NC", data=data + timedelta(days=4), cliente_nif=nif,
                            cliente_nome=nome, linhas=[vendas.LinhaVenda(13, 50.0, "721")])
    db.flush()


def _descricao_banco(descricao: str) -> str:
    """Como o banco descreveria o movimento: em maiúsculas, abreviado e sem acentos."""
    texto = descricao.split(" - ")[0]
    for antes, depois in (("Pagamento de salários", "TRF ORDENADOS"),
                          ("Pagamento à Segurança Social", "PAG SEG SOCIAL DRI"),
                          ("Pagamento do IRS retido", "PAG ESTADO DUC RETENCOES IRS"),
                          ("Pagamento ", "TRF SEPA "), ("Vendas de", "DEPOSITO VENDAS POS"),
                          ("Renda do estabelecimento", "DD RENDA ESTABELECIMENTO"),
                          ("Almoço de negócios", "COMPRA TPA RESTAURANTE"), ("Seguro", "DD SEGURO")):
        if texto.startswith(antes):
            texto = depois + texto[len(antes):]
            break
    return extratos.normalizar(texto).upper()[:60]


def criar_extrato_demo(db: Session, empresa: Empresa, hoje: date, dias: int = 60) -> None:
    """
    Extrato do banco (conta 121) dos últimos `dias` dias, importado como CSV: os movimentos que
    espelham os lançamentos (alguns com 1 a 2 dias de diferença), as comissões e o Imposto do Selo
    que só o banco conhece, o pagamento de uma fatura vencida e, de fora, os últimos lançamentos
    (ainda não refletidos no banco). Fica tudo por reconciliar, para a demonstração ter trabalho.
    """
    rng = random.Random(SEMENTE + 2)
    inicio = hoje - timedelta(days=dias)
    linhas = [
        ln for ln in db.scalars(
            select(LinhaLancamento).join(LinhaLancamento.lancamento).join(LinhaLancamento.conta)
            .where(Lancamento.empresa_id == empresa.id, Conta.codigo == "121", Lancamento.data >= inicio)
            .order_by(Lancamento.data, Lancamento.id)
        )
    ]
    movimentos = []
    for ln in linhas[:-2]:  # os dois últimos ainda não chegaram ao banco
        data = min(ln.lancamento.data + timedelta(days=rng.choice((0, 0, 1, 2))), hoje)
        movimentos.append((data, _descricao_banco(ln.lancamento.descricao), round(ln.debito - ln.credito, 2)))

    # Só o banco: comissão de manutenção e Imposto do Selo, no dia 3 de cada mês
    dia = date(inicio.year, inicio.month, 3)
    while dia <= hoje:
        if dia >= inicio:
            mes = f"{dia.month:02d}/{dia.year}"
            movimentos += [(dia, f"COMISSAO MANUTENCAO CONTA {mes}", -7.5), (dia, "IMPOSTO SELO S/COMISSAO", -0.3)]
        dia = date(dia.year + dia.month // 12, dia.month % 12 + 1, 3)

    # O pagamento de uma fatura vencida, feito no banco e ainda não registado na aplicação
    vencida = db.scalar(select(Fatura).where(
        Fatura.empresa_id == empresa.id, Fatura.paga.is_(False), Fatura.estado == ESTADO_CONFIRMADA,
        Fatura.data_vencimento < hoje,
    ).order_by(Fatura.data_vencimento))
    if vencida:
        movimentos.append((min(max(vencida.data_vencimento, inicio) + timedelta(days=5), hoje),
                           f"TRF SEPA {extratos.normalizar(vencida.nome_fornecedor).upper()[:40]}", -vencida.total))

    # O cliente com a fatura vencida pagou por transferência, mas ainda ninguém registou o recebimento
    atrasada = next((v for v in vendas.por_receber(db, empresa.id)
                     if v.data_vencimento and v.data_vencimento < hoje - timedelta(days=5)), None)
    if atrasada:
        movimentos.append((hoje - timedelta(days=2),
                           f"TRF RECEBIDA {extratos.normalizar(atrasada.cliente_nome).upper()[:40]}", atrasada.total))

    movimentos.sort(key=lambda m: m[0])
    saldo = relatorios.saldos_por_conta(db, empresa.id, ate=inicio - timedelta(days=1)).get("121", 0.0)
    texto = ["Consulta de movimentos - Conta à ordem", "", "Data movimento;Descrição;Montante;Saldo"]
    for data, descricao, montante in movimentos:
        saldo = round(saldo + montante, 2)
        virgula = lambda v: f"{v:.2f}".replace(".", ",")  # noqa: E731  (números como os bancos portugueses)
        texto.append(f"{data:%d-%m-%Y};{descricao};{virgula(montante)};{virgula(saldo)}")
    banco.importar(db, empresa, "121", "\n".join(texto).encode("cp1252"), "extrato-demonstracao.csv")


def apagar_empresa_demo(db: Session) -> None:
    """Apaga a empresa de demonstração e tudo o que lhe pertence. As empresas reais não são tocadas."""
    empresa = empresa_demo(db)
    if empresa is None:
        return
    db.execute(delete(MovimentoBancario).where(MovimentoBancario.empresa_id == empresa.id))
    db.execute(delete(ExtratoBancario).where(ExtratoBancario.empresa_id == empresa.id))
    db.execute(delete(RegraBancaria).where(RegraBancaria.empresa_id == empresa.id))
    ids_ativos = select(AtivoFixo.id).where(AtivoFixo.empresa_id == empresa.id)
    db.execute(delete(DepreciacaoAtivo).where(DepreciacaoAtivo.ativo_id.in_(ids_ativos)))
    db.execute(delete(AtivoFixo).where(AtivoFixo.empresa_id == empresa.id))
    ids_lancamentos = select(Lancamento.id).where(Lancamento.empresa_id == empresa.id)
    db.execute(delete(ProcessamentoSalarial).where(ProcessamentoSalarial.empresa_id == empresa.id))
    db.execute(delete(Funcionario).where(Funcionario.empresa_id == empresa.id))
    db.execute(delete(Diferimento).where(Diferimento.empresa_id == empresa.id))
    db.execute(delete(RegistoAtividade).where(RegistoAtividade.empresa_id == empresa.id))
    db.execute(delete(CentroCusto).where(CentroCusto.empresa_id == empresa.id))
    db.execute(delete(MovimentoStock).where(MovimentoStock.empresa_id == empresa.id))
    db.execute(delete(Artigo).where(Artigo.empresa_id == empresa.id))
    db.execute(delete(LinhaOrcamento).where(LinhaOrcamento.empresa_id == empresa.id))
    db.execute(delete(LinhaLancamento).where(LinhaLancamento.lancamento_id.in_(ids_lancamentos)))
    db.execute(delete(Lancamento).where(Lancamento.empresa_id == empresa.id))
    db.execute(delete(RegraFornecedor).where(RegraFornecedor.empresa_id == empresa.id))
    db.execute(delete(Conta).where(Conta.empresa_id == empresa.id))
    db.execute(delete(FaturaVenda).where(FaturaVenda.empresa_id == empresa.id))
    ids_inventarios = select(Inventario.id).where(Inventario.empresa_id == empresa.id)
    db.execute(delete(LinhaInventario).where(LinhaInventario.inventario_id.in_(ids_inventarios)))
    db.execute(delete(Inventario).where(Inventario.empresa_id == empresa.id))
    for fatura in db.scalars(select(Fatura).where(Fatura.empresa_id == empresa.id)):
        armazenamento.apagar_pdf(fatura.ficheiro)
        db.delete(fatura)
    db.flush()
    db.execute(delete(Fornecedor).where(Fornecedor.empresa_id == empresa.id))
    db.execute(delete(AcessoEmpresa).where(AcessoEmpresa.empresa_id == empresa.id))
    ids = select(Utilizador.id).where(Utilizador.empresa_id == empresa.id)
    db.execute(delete(Sessao).where(Sessao.utilizador_id.in_(ids)))
    db.execute(delete(TokenAcesso).where(TokenAcesso.utilizador_id.in_(ids)))
    db.execute(delete(Utilizador).where(Utilizador.empresa_id == empresa.id))
    db.delete(empresa)
    db.commit()


def main() -> int:
    parser = argparse.ArgumentParser(description="Cria uma empresa de demonstração com 30 faturas fictícias.")
    parser.add_argument("--limpar", action="store_true", help="apaga a empresa de demonstração e cria-a de novo")
    args = parser.parse_args()

    preparar_base_dados()
    with SessaoLocal() as db:
        if empresa_demo(db) is not None:
            if not args.limpar:
                print("A empresa de demonstração já existe. Para a recriar: python -m app.demo --limpar")
                return 1
            apagar_empresa_demo(db)

        criadas = criar_dados_demo(db)
        print(f"Criada a empresa '{NOME_EMPRESA_DEMO}' com {criadas} faturas.")

        # Fora do modo de demonstração é preciso entrar: gera um link para definir a palavra-passe
        utilizador = db.scalar(select(Utilizador).where(Utilizador.email == EMAIL_DEMO))
        link = autenticacao.criar_link_acesso(db, utilizador)
        db.commit()
        print(f"\nPara entrar como {EMAIL_DEMO}, defina a palavra-passe neste link (válido 72 horas):\n{link}")
        print("\n(Com MODO_DEMO=1 no .env não é preciso: entra-se diretamente.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
