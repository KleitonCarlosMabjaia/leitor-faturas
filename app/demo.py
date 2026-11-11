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
from app.modelos.fatura import DadosFatura
from app.dependencias import EMAIL_DEMO
from app.modelos.tabelas import (
    ESTADO_CONFIRMADA, ESTADO_POR_REVER, PAPEL_ADMIN, Empresa, Fatura, Fornecedor, Sessao, TokenAcesso, Utilizador,
)
from app.servicos import armazenamento, autenticacao, repositorio

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
    soma = sum(int(d) * p for d, p in zip(corpo, range(9, 1, -1)))
    resto = soma % 11
    return corpo + str(0 if resto < 2 else 11 - resto)


def texto_qr(nif: str, nif_cliente: str, numero: str, atcud: str, emissao: date, linhas_iva: list[dict], total: float) -> str:
    """Monta o texto do QR segundo a Portaria n.º 195/2020 (Continente)."""
    campos = {"reduzida": ("I3", "I4", 6), "intermedia": ("I5", "I6", 13), "normal": ("I7", "I8", 23)}
    partes = [f"A:{nif}", f"B:{nif_cliente}", "C:PT", "D:FT", "E:N", f"F:{emissao:%Y%m%d}",
              f"G:{numero}", f"H:{atcud}", "I1:PT"]
    for campo_base, campo_iva, taxa in campos.values():
        linha = next((l for l in linhas_iva if l["taxa"] == taxa), None)
        if linha:
            partes += [f"{campo_base}:{linha['base']:.2f}", f"{campo_iva}:{linha['valor']:.2f}"]
    total_iva = sum(l["valor"] for l in linhas_iva)
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
    escrever(300, y, f"Total sem IVA: {_euros(sum(l['base'] for l in linhas_iva))}", 10)
    escrever(300, y - 15, f"Total IVA: {_euros(sum(l['valor'] for l in linhas_iva))}", 10)
    escrever(300, y - 35, f"TOTAL: {_euros(total)}", 13, negrito=True)

    # Rodapé fiscal com QR
    texto = texto_qr(nif, nif_cliente, numero, atcud, emissao, linhas_iva, total)
    matriz = memoryview(zxingcpp.create_barcode(texto, zxingcpp.BarcodeFormat.QRCode)
                        .to_image(scale=1, add_quiet_zones=False))
    lado = matriz.shape[0]
    tamanho_modulo = 110 / lado
    origem_x, origem_y = 50, 60
    retangulos = [
        f"{origem_x + c * tamanho_modulo:.2f} {origem_y + (lado - 1 - l) * tamanho_modulo:.2f} "
        f"{tamanho_modulo:.2f} {tamanho_modulo:.2f} re"
        for l in range(lado) for c in range(lado) if matriz[l, c] == 0  # 0 = módulo preto
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
    nifs = {f.nome: gerar_nif(rng, "5") for f in FORNECEDORES}
    series = {f.nome: rng.randint(10, 80) for f in FORNECEDORES}

    # Datas espalhadas pelos últimos 6 meses, das mais antigas para as mais recentes
    datas = sorted(hoje - timedelta(days=rng.randint(0, 180)) for _ in range(quantidade))
    # Duas faturas antigas ficam por pagar (aparecem como vencidas no dashboard)
    indices_vencidas = {quantidade // 3, quantidade // 2}

    for indice, emissao in enumerate(datas):
        fornecedor = FORNECEDORES[indice % len(FORNECEDORES)] if indice < len(FORNECEDORES) else rng.choice(FORNECEDORES)
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
        valor_sem_iva = round(sum(l["base"] for l in linhas_iva), 2)
        total = round(valor_sem_iva + sum(l["valor"] for l in linhas_iva), 2)

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

    db.commit()
    return quantidade


def apagar_empresa_demo(db: Session) -> None:
    """Apaga a empresa de demonstração e tudo o que lhe pertence. As empresas reais não são tocadas."""
    empresa = empresa_demo(db)
    if empresa is None:
        return
    for fatura in db.scalars(select(Fatura).where(Fatura.empresa_id == empresa.id)):
        armazenamento.apagar_pdf(fatura.ficheiro)
        db.delete(fatura)
    db.flush()
    db.execute(delete(Fornecedor).where(Fornecedor.empresa_id == empresa.id))
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
