"""
Ligação à base de dados com SQLAlchemy.

Cada pedido HTTP recebe a sua própria sessão (get_db) e a sessão é fechada
no fim, mesmo que haja erro.
"""

from collections.abc import Iterator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app import config


class Base(DeclarativeBase):
    """Classe base de todas as tabelas."""


def criar_motor(url: str):
    # O SQLite só permite usar a ligação na thread que a criou, a não ser que se
    # desative esta verificação. É seguro porque cada pedido tem a sua sessão.
    argumentos = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, connect_args=argumentos)


motor = criar_motor(config.URL_BASE_DADOS)
SessaoLocal = sessionmaker(bind=motor, autoflush=False, expire_on_commit=False)


def criar_tabelas() -> None:
    # Importar os modelos regista as tabelas em Base.metadata
    from app.modelos import tabelas  # noqa: F401

    Base.metadata.create_all(motor)
    migrar(motor)


# Colunas acrescentadas depois da criação inicial das tabelas: (tabela, coluna, definição SQL)
COLUNAS_NOVAS = [
    ("faturas", "paga", "BOOLEAN NOT NULL DEFAULT 0"),
]


def migrar(motor_alvo) -> None:
    """
    Acrescenta colunas novas a bases de dados criadas por versões anteriores.
    O create_all só cria tabelas que não existem; não altera as que já existem.
    Num projeto maior usaria o Alembic, que gere migrações com histórico.
    """
    inspetor = inspect(motor_alvo)
    with motor_alvo.begin() as ligacao:
        for tabela, coluna, definicao in COLUNAS_NOVAS:
            existentes = {c["name"] for c in inspetor.get_columns(tabela)}
            if coluna not in existentes:
                ligacao.execute(text(f"ALTER TABLE {tabela} ADD COLUMN {coluna} {definicao}"))


def get_db() -> Iterator[Session]:
    """Dependência do FastAPI: abre uma sessão por pedido."""
    sessao = SessaoLocal()
    try:
        yield sessao
    finally:
        sessao.close()
