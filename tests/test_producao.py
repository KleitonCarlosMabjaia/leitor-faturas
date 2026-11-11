"""
Fase 6: preparação para produção.

Migrações (Alembic), armazenamento S3/R2, cópias de segurança, limite de pedidos
e verificação de saúde. Os testes de PostgreSQL só correm se TESTE_POSTGRES_URL
estiver definido; os restantes usam SQLite e um bucket falso, sem Internet.
"""

import io
import os

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session

from app import config, copias
from app.db import Base, criar_motor, preparar_base_dados
from app.modelos.tabelas import Empresa, Fatura, Fornecedor, Utilizador
from app.servicos import armazenamento, limites
from app.servicos.limites import LimitePedidos
from tests.conftest import QR_EXEMPLO, criar_pdf_com_qr, criar_pdf_com_texto


def _revisao_atual(motor) -> str | None:
    with motor.connect() as ligacao:
        return MigrationContext.configure(ligacao).get_current_revision()


def _diferencas(motor) -> list:
    with motor.connect() as ligacao:
        return compare_metadata(MigrationContext.configure(ligacao), Base.metadata)


# ---------- Migrações ----------

def test_migracoes_criam_o_esquema_dos_modelos(tmp_path):
    """As migrações e os modelos não podem divergir (esqueceu-se de gerar uma migração?)."""
    motor = create_engine(f"sqlite:///{tmp_path / 'nova.db'}")
    preparar_base_dados(motor)
    assert _diferencas(motor) == []
    assert _revisao_atual(motor) is not None
    preparar_base_dados(motor)  # arrancar outra vez não faz nada
    motor.dispose()


def test_base_anterior_ao_alembic_e_marcada_sem_perder_dados(tmp_path):
    """Uma base de dados da Fase 5 (criada com create_all) passa a ser gerida pelo Alembic."""
    motor = create_engine(f"sqlite:///{tmp_path / 'fase5.db'}")
    Base.metadata.create_all(motor)
    with Session(motor) as db:
        db.add(Empresa(nome="Antiga, Lda."))
        db.commit()

    preparar_base_dados(motor)

    assert "alembic_version" in inspect(motor).get_table_names()
    assert _diferencas(motor) == []
    with Session(motor) as db:
        assert db.scalar(select(Empresa.nome)) == "Antiga, Lda."
    motor.dispose()


def test_base_da_fase_4_chega_ao_esquema_atual(tmp_path):
    """Sem empresas nem utilizadores, e com o NIF único em tudo: o caminho antigo + Alembic."""
    motor = create_engine(f"sqlite:///{tmp_path / 'fase4.db'}")
    with motor.begin() as ligacao:
        ligacao.execute(text("CREATE TABLE fornecedores (id INTEGER PRIMARY KEY, nif VARCHAR(9), nome VARCHAR)"))
        ligacao.execute(text("CREATE UNIQUE INDEX ix_fornecedores_nif ON fornecedores (nif)"))
        ligacao.execute(text("CREATE TABLE faturas (id VARCHAR(12) PRIMARY KEY)"))
        ligacao.execute(text("INSERT INTO faturas (id) VALUES ('abc123abc123')"))

    preparar_base_dados(motor)

    tabelas = set(inspect(motor).get_table_names())
    assert {"empresas", "utilizadores", "alembic_version"} <= tabelas
    with motor.connect() as ligacao:
        assert ligacao.execute(text("SELECT id FROM faturas")).scalar() == "abc123abc123"
    motor.dispose()


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        ("postgres://u:p@servidor/bd", "postgresql+psycopg://u:p@servidor/bd"),
        ("postgresql://u:p@servidor/bd?sslmode=require", "postgresql+psycopg://u:p@servidor/bd?sslmode=require"),
        ("postgresql+psycopg://u@s/bd", "postgresql+psycopg://u@s/bd"),
        ("sqlite:///dados.db", "sqlite:///dados.db"),
    ],
)
def test_normalizar_url_da_base_de_dados(entrada, esperado):
    assert config.normalizar_url(entrada) == esperado


# ---------- Armazenamento S3 / R2 ----------

class BucketFalso:
    """Imita as chamadas do boto3 usadas pela aplicação, sem rede."""

    class exceptions:
        class NoSuchKey(Exception):
            pass

    def __init__(self):
        self.objetos: dict[str, tuple[bytes, str]] = {}

    def put_object(self, Bucket, Key, Body, ContentType):
        self.objetos[Key] = (Body, ContentType)

    def get_object(self, Bucket, Key):
        if Key not in self.objetos:
            raise self.exceptions.NoSuchKey()
        return {"Body": io.BytesIO(self.objetos[Key][0])}

    def delete_object(self, Bucket, Key):
        self.objetos.pop(Key, None)

    def get_paginator(self, nome):
        bucket = self

        class Paginador:
            def paginate(self, Bucket, Prefix):
                chaves = [c for c in bucket.objetos if c.startswith(Prefix)]
                yield {"Contents": [{"Key": c} for c in chaves]} if chaves else {}

        return Paginador()


@pytest.fixture
def bucket(monkeypatch):
    falso = BucketFalso()
    monkeypatch.setattr(config, "S3_BUCKET", "faturas-teste")
    monkeypatch.setattr(armazenamento, "_cliente_s3", lambda: falso)
    return falso


def test_com_bucket_os_pdfs_vao_para_o_s3_e_nao_para_o_disco(cliente, bucket):
    pdf = criar_pdf_com_qr(QR_EXEMPLO)
    fatura = cliente.post("/api/faturas", files={"ficheiro": ("fatura.pdf", pdf, "application/pdf")}).json()

    [chave] = bucket.objetos
    assert chave.startswith("empresa-") and chave.endswith("__fatura.pdf")
    assert bucket.objetos[chave] == (pdf, "application/pdf")
    assert not list(config.UPLOAD_DIR.rglob("*.pdf"))

    resposta = cliente.get(f"/api/faturas/{fatura['id']}/pdf")
    assert resposta.status_code == 200
    assert resposta.content == pdf
    assert resposta.headers["content-disposition"].startswith("inline;")

    assert cliente.delete(f"/api/faturas/{fatura['id']}").status_code == 204
    assert bucket.objetos == {}


def test_pdf_em_falta_no_bucket_responde_404(cliente, bucket):
    fatura = cliente.post(
        "/api/faturas", files={"ficheiro": ("f.pdf", criar_pdf_com_qr(QR_EXEMPLO), "application/pdf")}
    ).json()
    bucket.objetos.clear()
    assert cliente.get(f"/api/faturas/{fatura['id']}/pdf").status_code == 404


def test_nome_do_pdf_com_acentos_no_cabecalho(cliente):
    pdf = criar_pdf_com_qr(QR_EXEMPLO)
    fatura = cliente.post("/api/faturas", files={"ficheiro": ("Fatura Março.pdf", pdf, "application/pdf")}).json()
    cabecalho = cliente.get(f"/api/faturas/{fatura['id']}/pdf").headers["content-disposition"]
    assert "filename*=UTF-8''Fatura%20Mar%C3%A7o.pdf" in cabecalho


def test_chaves_fora_do_formato_sao_recusadas(fabrica_sessoes):
    for chave in ("../../app/main.py", "empresa-1/../../dados.db", "/etc/passwd", "copias/x.json.gz"):
        assert armazenamento.ler_pdf(chave) is None
    # As faturas anteriores à Fase 5 (sem pasta da empresa) continuam a abrir
    assert armazenamento.chave_pdf_valida("1d159f40075b__3091820261007S.pdf")


def test_falha_ao_apagar_ficheiro_nao_estraga_o_pedido(cliente, bucket, monkeypatch):
    fatura = cliente.post(
        "/api/faturas", files={"ficheiro": ("f.pdf", criar_pdf_com_qr(QR_EXEMPLO), "application/pdf")}
    ).json()

    def avaria(**_):
        raise ConnectionError("bucket inacessível")

    monkeypatch.setattr(bucket, "delete_object", avaria)
    assert cliente.delete(f"/api/faturas/{fatura['id']}").status_code == 204


# ---------- Cópias de segurança ----------

def _com_dados(fabrica_sessoes, cliente) -> None:
    cliente.post("/api/faturas", files={"ficheiro": ("a.pdf", criar_pdf_com_qr(QR_EXEMPLO), "application/pdf")})


def _motor_de(fabrica_sessoes):
    return fabrica_sessoes.kw["bind"]


def test_copia_e_restauro_repoem_os_dados(tmp_path, fabrica_sessoes, cliente):
    _com_dados(fabrica_sessoes, cliente)
    origem = _motor_de(fabrica_sessoes)
    preparar_base_dados(origem)  # a base dos testes vem do create_all: marca a revisão
    dados = copias.descomprimir(copias.comprimir(copias.exportar(origem)))

    destino = create_engine(f"sqlite:///{tmp_path / 'restaurada.db'}")
    contagens = copias.restaurar(destino, dados)

    assert contagens["faturas"] == 1 and contagens["empresas"] == 1
    assert "sessoes" not in dados["tabelas"]  # as sessões não se copiam
    with Session(destino) as db, fabrica_sessoes() as db_origem:
        nova, antiga = db.scalar(select(Fatura)), db_origem.scalar(select(Fatura))
        assert (nova.total, nova.data_emissao, nova.linhas_iva, nova.carregada_em) == (
            antiga.total, antiga.data_emissao, antiga.linhas_iva, antiga.carregada_em
        )
        assert db.scalar(select(Utilizador.email)) == "admin@teste.pt"
    destino.dispose()


def test_restauro_recusa_base_com_dados_sem_substituir(fabrica_sessoes, cliente):
    _com_dados(fabrica_sessoes, cliente)
    motor = _motor_de(fabrica_sessoes)
    preparar_base_dados(motor)
    dados = copias.exportar(motor)

    with pytest.raises(copias.ErroCopia, match="já tem dados"):
        copias.restaurar(motor, dados)

    copias.restaurar(motor, dados, substituir=True)
    with fabrica_sessoes() as db:
        assert len(db.scalars(select(Fatura)).all()) == 1


def test_restauro_recusa_ficheiro_invalido():
    with pytest.raises(copias.ErroCopia):
        copias.descomprimir(b"isto nao e uma copia")


def test_copias_antigas_sao_apagadas(fabrica_sessoes, monkeypatch, empresa_teste):
    motor = _motor_de(fabrica_sessoes)
    preparar_base_dados(motor)
    monkeypatch.setattr(config, "COPIAS_A_MANTER", 2)
    loja = armazenamento.atual()
    for antiga in ("copias/2026-01-01_030000.json.gz", "copias/2026-01-02_030000.json.gz"):
        loja.guardar(antiga, b"x", "application/gzip")

    nova = copias.criar_no_armazenamento(motor)

    assert loja.listar("copias/") == ["copias/2026-01-02_030000.json.gz", nova]


# ---------- Limite de pedidos ----------

def test_janela_deslizante():
    limite = LimitePedidos(maximo=2, janela_segundos=60)
    assert limite.segundos_ate_poder("ip", agora=0) == 0
    assert limite.segundos_ate_poder("ip", agora=10) == 0
    assert limite.segundos_ate_poder("ip", agora=20) == 40   # o 1.º pedido sai da janela aos 60 s
    assert limite.segundos_ate_poder("outro-ip", agora=20) == 0
    assert limite.segundos_ate_poder("ip", agora=61) == 0


def test_login_bloqueado_por_ip_depois_de_muitas_tentativas(cliente_anonimo, empresa_teste):
    maximo, _ = config.LIMITE_LOGIN
    for i in range(maximo):
        resposta = cliente_anonimo.post("/api/auth/entrar", json={"email": f"x{i}@teste.pt", "palavra_passe": "errada"})
        assert resposta.status_code == 401

    resposta = cliente_anonimo.post("/api/auth/entrar", json={"email": "admin@teste.pt", "palavra_passe": "errada"})
    assert resposta.status_code == 429
    assert int(resposta.headers["retry-after"]) > 0
    assert "Demasiados pedidos" in resposta.json()["detail"]


def test_envio_de_faturas_limitado_por_utilizador(cliente, monkeypatch):
    monkeypatch.setattr(limites, "envios", LimitePedidos(2, 3600))
    pdf = criar_pdf_com_texto(["Fatura de teste sem dados suficientes para nada"])
    for i in range(2):
        cliente.post("/api/faturas", files={"ficheiro": (f"{i}.pdf", pdf, "application/pdf")})
    resposta = cliente.post("/api/faturas", files={"ficheiro": ("3.pdf", pdf, "application/pdf")})
    assert resposta.status_code == 429


# ---------- Saúde ----------

def test_saude_verifica_a_base_de_dados(cliente_anonimo):
    resposta = cliente_anonimo.get("/api/saude")
    assert resposta.status_code == 200
    assert resposta.json()["estado"] == "ok"


# ---------- PostgreSQL (opcional) ----------

URL_POSTGRES = os.getenv("TESTE_POSTGRES_URL")


@pytest.mark.skipif(not URL_POSTGRES, reason="defina TESTE_POSTGRES_URL para testar com PostgreSQL")
def test_postgresql_migracoes_e_restauro(fabrica_sessoes, cliente):
    _com_dados(fabrica_sessoes, cliente)
    origem = _motor_de(fabrica_sessoes)
    preparar_base_dados(origem)
    dados = copias.exportar(origem)

    motor = criar_motor(config.normalizar_url(URL_POSTGRES))
    with motor.begin() as ligacao:  # base de dados limpa
        ligacao.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))

    preparar_base_dados(motor)
    assert _diferencas(motor) == []

    copias.restaurar(motor, dados)  # de SQLite para PostgreSQL
    with Session(motor) as db:
        assert db.scalar(select(Fatura.total)) == 23.0 + 100.0
        # A sequência dos ids foi acertada: criar uma empresa nova não colide com a restaurada
        nova = Empresa(nome="Nova")
        db.add(nova)
        db.commit()
        assert nova.id == 2
        assert db.scalar(select(Fornecedor.nif)) == "500000000"
    motor.dispose()
