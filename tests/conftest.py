"""Utilitários partilhados pelos testes."""

import io

import pytest
import zxingcpp
from fastapi.testclient import TestClient
from PIL import Image
from pypdf import PdfWriter
from sqlalchemy.orm import sessionmaker

from app import config
from app.db import Base, criar_motor, get_db
from app.main import app
from app.modelos import tabelas  # noqa: F401  (regista as tabelas em Base.metadata)
from app.modelos.fatura import DadosFatura
from app.modelos.tabelas import PAPEL_ADMIN, Empresa, Utilizador
from app.servicos import limites
from app.servicos.autenticacao import hash_palavra_passe


def criar_pdf_com_texto(linhas: list[str]) -> bytes:
    """Gera um PDF mínimo com texto selecionável (sem bibliotecas extra)."""
    texto = " ".join(
        f"BT /F1 11 Tf 50 {780 - i * 16} Td ({linha}) Tj ET" for i, linha in enumerate(linhas)
    )
    objetos = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(texto), texto.encode("latin-1")),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    saida = io.BytesIO()
    saida.write(b"%PDF-1.4\n")
    posicoes = []
    for numero, corpo in enumerate(objetos, start=1):
        posicoes.append(saida.tell())
        saida.write(b"%d 0 obj\n%s\nendobj\n" % (numero, corpo))
    inicio_xref = saida.tell()
    saida.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objetos) + 1))
    for posicao in posicoes:
        saida.write(b"%010d 00000 n \n" % posicao)
    saida.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objetos) + 1, inicio_xref))
    return saida.getvalue()


def criar_pdf_sem_texto() -> bytes:
    """Simula uma fatura digitalizada: uma página sem texto."""
    escritor = PdfWriter()
    escritor.add_blank_page(width=595, height=842)
    saida = io.BytesIO()
    escritor.write(saida)
    return saida.getvalue()


QR_EXEMPLO = (
    "A:500000000*B:999999990*C:PT*D:FT*E:N*F:20261005*G:FT 2026/123*H:ABCD1234-123"
    "*I1:PT*I7:100.00*I8:23.00*N:23.00*O:123.00*Q:ab12*R:9999"
)


def qr_com_numero(numero: str, atcud: str) -> str:
    """Variante do QR de exemplo com outro número de fatura (para testar duplicados e cache)."""
    return QR_EXEMPLO.replace("G:FT 2026/123", f"G:{numero}").replace("H:ABCD1234-123", f"H:{atcud}")


def criar_pdf_com_qr(texto_qr: str = QR_EXEMPLO) -> bytes:
    """Simula uma fatura digitalizada com o QR da AT (imagem, sem texto)."""
    codigo = memoryview(zxingcpp.create_barcode(texto_qr, zxingcpp.BarcodeFormat.QRCode).to_image(scale=4))
    imagem_qr = Image.frombytes("L", (codigo.shape[1], codigo.shape[0]), codigo.tobytes())
    pagina = Image.new("L", (1240, 1754), 255)  # A4 a 150 ppp
    pagina.paste(imagem_qr, (900, 1300))        # canto inferior direito, como nas faturas
    saida = io.BytesIO()
    pagina.save(saida, "PDF", resolution=150)
    return saida.getvalue()


def dados_exemplo(**alteracoes) -> DadosFatura:
    base = {
        "fornecedor": "Bebidas do Norte, Lda.",
        "nif_fornecedor": "500000000",
        "numero_fatura": "FT 2026/123",
        "data_emissao": "2026-10-05",
        "data_vencimento": "2026-11-04",
        "valor_sem_iva": 100.00,
        "iva": [{"taxa": 23, "base": 100.00, "valor": 23.00}],
        "total": 123.00,
        "moeda": "EUR",
        "confianca": "alta",
    }
    base.update(alteracoes)
    return DadosFatura(**base)


@pytest.fixture
def fabrica_sessoes(tmp_path, monkeypatch):
    """
    Base de dados SQLite nova para cada teste (os testes não se influenciam uns
    aos outros) e PDFs numa pasta temporária.
    """
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")
    motor = criar_motor(f"sqlite:///{tmp_path / 'teste.db'}")
    Base.metadata.create_all(motor)
    yield sessionmaker(bind=motor, autoflush=False, expire_on_commit=False)
    motor.dispose()  # no Windows, liberta o ficheiro .db para a pasta temporária poder ser apagada


@pytest.fixture(autouse=True)
def limites_limpos():
    """Os contadores de pedidos são globais: cada teste começa do zero."""
    limites.limpar_todos()
    yield
    limites.limpar_todos()


PALAVRA_PASSE_TESTE = "palavra-passe-de-teste-1"


def criar_empresa_com_admin(fabrica_sessoes, nome: str, email: str) -> int:
    """Cria uma empresa com um administrador que já tem palavra-passe. Devolve o id da empresa."""
    with fabrica_sessoes() as db:
        empresa = Empresa(nome=nome)
        db.add(empresa)
        db.flush()
        db.add(Utilizador(
            empresa_id=empresa.id, nome=f"Admin {nome}", email=email, papel=PAPEL_ADMIN,
            hash_palavra_passe=hash_palavra_passe(PALAVRA_PASSE_TESTE),
        ))
        db.commit()
        return empresa.id


def entrar(cliente: TestClient, email: str, palavra_passe: str = PALAVRA_PASSE_TESTE) -> TestClient:
    resposta = cliente.post("/api/auth/entrar", json={"email": email, "palavra_passe": palavra_passe})
    assert resposta.status_code == 200, resposta.text
    return cliente


@pytest.fixture
def cliente_anonimo(fabrica_sessoes):
    """Cliente HTTP sem sessão, ligado à base de dados temporária."""

    def get_db_teste():
        sessao = fabrica_sessoes()
        try:
            yield sessao
        finally:
            sessao.close()

    app.dependency_overrides[get_db] = get_db_teste
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def empresa_teste(fabrica_sessoes) -> int:
    return criar_empresa_com_admin(fabrica_sessoes, "Empresa de Teste", "admin@teste.pt")


@pytest.fixture
def cliente(cliente_anonimo, empresa_teste):
    """Cliente HTTP com sessão iniciada como administrador da empresa de teste."""
    return entrar(cliente_anonimo, "admin@teste.pt")


@pytest.fixture
def novo_cliente(cliente_anonimo):
    """Fábrica de clientes extra (cada um com os seus cookies), para testar várias pessoas ao mesmo tempo."""

    def criar(email: str | None = None, palavra_passe: str = PALAVRA_PASSE_TESTE) -> TestClient:
        outro = TestClient(app)
        return entrar(outro, email, palavra_passe) if email else outro

    return criar
