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
    ("faturas", "paga", "BOOLEAN NOT NULL DEFAULT 0"),                     # Fase 4
    ("faturas", "empresa_id", "INTEGER REFERENCES empresas(id)"),          # Fase 5
    ("fornecedores", "empresa_id", "INTEGER REFERENCES empresas(id)"),     # Fase 5
]


def migrar(motor_alvo) -> None:
    """
    Atualiza bases de dados criadas por versões anteriores.
    O create_all só cria tabelas que não existem; não altera as que já existem.
    Num projeto maior usaria o Alembic, que gere migrações com histórico.
    """
    inspetor = inspect(motor_alvo)
    with motor_alvo.begin() as ligacao:
        for tabela, coluna, definicao in COLUNAS_NOVAS:
            if tabela not in inspetor.get_table_names():
                continue
            existentes = {c["name"] for c in inspetor.get_columns(tabela)}
            if coluna not in existentes:
                ligacao.execute(text(f"ALTER TABLE {tabela} ADD COLUMN {coluna} {definicao}"))

        # Fase 5: o NIF do fornecedor deixou de ser único em toda a base de dados
        # e passou a ser único dentro de cada empresa.
        if "fornecedores" in inspetor.get_table_names():
            indices = {i["name"]: i for i in inspetor.get_indexes("fornecedores")}
            antigo = indices.get("ix_fornecedores_nif")
            if antigo and antigo["unique"]:
                ligacao.execute(text("DROP INDEX ix_fornecedores_nif"))
                ligacao.execute(text("CREATE INDEX ix_fornecedores_nif ON fornecedores (nif)"))
            ligacao.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_fornecedor_empresa_nif ON fornecedores (empresa_id, nif)"
            ))
            ligacao.execute(text("CREATE INDEX IF NOT EXISTS ix_fornecedores_empresa_id ON fornecedores (empresa_id)"))
        if "faturas" in inspetor.get_table_names():
            ligacao.execute(text("CREATE INDEX IF NOT EXISTS ix_faturas_empresa_id ON faturas (empresa_id)"))


def get_db() -> Iterator[Session]:
    """Dependência do FastAPI: abre uma sessão por pedido."""
    sessao = SessaoLocal()
    try:
        yield sessao
    finally:
        sessao.close()
