"""
Ligação à base de dados com SQLAlchemy.

Cada pedido HTTP recebe a sua própria sessão (get_db) e a sessão é fechada
no fim, mesmo que haja erro.

O esquema é gerido pelo Alembic (pasta migracoes/). Ao arrancar, a aplicação
aplica as migrações em falta: preparar_base_dados().
"""

from collections.abc import Iterator

from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app import config


class Base(DeclarativeBase):
    """Classe base de todas as tabelas."""


def criar_motor(url: str):
    if url.startswith("sqlite"):
        # O SQLite só permite usar a ligação na thread que a criou, a não ser que se
        # desative esta verificação. É seguro porque cada pedido tem a sua sessão.
        return create_engine(url, connect_args={"check_same_thread": False})
    # PostgreSQL: pool_pre_ping testa a ligação antes de a usar, porque os serviços
    # geridos (Neon, Render) fecham ligações paradas; pool_recycle renova-as a cada 5 min.
    return create_engine(url, pool_pre_ping=True, pool_recycle=300)


motor = criar_motor(config.URL_BASE_DADOS)
SessaoLocal = sessionmaker(bind=motor, autoflush=False, expire_on_commit=False)


PASTA_MIGRACOES = config.BASE_DIR / "migracoes"
REVISAO_INICIAL = "0001"  # esquema da Fase 5, o último antes do Alembic


def _config_alembic(ligacao):
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(PASTA_MIGRACOES))
    cfg.attributes["ligacao"] = ligacao
    cfg.attributes["configurar_registos"] = False  # os registos já estão configurados pela aplicação
    return cfg


def preparar_base_dados(motor_alvo=None) -> None:
    """
    Deixa a base de dados no esquema mais recente (alembic upgrade head).

    Bases de dados criadas antes do Alembic (Fases 1 a 5) não têm a tabela
    alembic_version: são primeiro atualizadas pelo método antigo (migrar) e
    marcadas como estando na revisão inicial, para o Alembic seguir a partir daí.
    """
    from alembic import command

    from app.modelos import tabelas  # noqa: F401  (regista as tabelas em Base.metadata)

    motor_alvo = motor_alvo or motor
    existentes = set(inspect(motor_alvo).get_table_names())
    with motor_alvo.begin() as ligacao:
        cfg = _config_alembic(ligacao)
        if "alembic_version" not in existentes and "faturas" in existentes:
            Base.metadata.create_all(ligacao)  # tabelas da Fase 5 que ainda não existam
            migrar(ligacao)
            command.stamp(cfg, REVISAO_INICIAL)
        command.upgrade(cfg, "head")


# Colunas acrescentadas depois da criação inicial das tabelas: (tabela, coluna, definição SQL)
COLUNAS_NOVAS = [
    ("faturas", "paga", "BOOLEAN NOT NULL DEFAULT 0"),                     # Fase 4
    ("faturas", "empresa_id", "INTEGER REFERENCES empresas(id)"),          # Fase 5
    ("fornecedores", "empresa_id", "INTEGER REFERENCES empresas(id)"),     # Fase 5
]


def migrar(alvo) -> None:
    """
    Método antigo (Fases 1 a 5): atualiza bases de dados criadas antes do Alembic.
    Aceita um motor ou uma ligação já aberta. As alterações novas vão para migracoes/.
    """
    if isinstance(alvo, Engine):
        with alvo.begin() as ligacao:
            _migrar(ligacao)
    else:
        _migrar(alvo)


def _migrar(ligacao) -> None:
    inspetor = inspect(ligacao)
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
