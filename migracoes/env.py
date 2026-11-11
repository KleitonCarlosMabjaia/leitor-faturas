"""
Ambiente do Alembic: diz-lhe que base de dados usar e onde estão os modelos.

Pode receber uma ligação já aberta (é o que faz app/db.py ao arrancar e os testes)
ou abrir uma a partir de URL_BASE_DADOS (quando se usa o comando "alembic").
"""

from logging.config import fileConfig

from alembic import context

from app import config as config_app
from app.db import Base, criar_motor
from app.modelos import tabelas  # noqa: F401  (regista as tabelas em Base.metadata)

config = context.config
if config.config_file_name and config.attributes.get("configurar_registos", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)


def correr(ligacao) -> None:
    context.configure(
        connection=ligacao,
        target_metadata=Base.metadata,
        # O SQLite quase não suporta ALTER TABLE: o modo "batch" recria a tabela com a alteração
        render_as_batch=ligacao.dialect.name == "sqlite",
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


ligacao = config.attributes.get("ligacao")
if ligacao is not None:
    correr(ligacao)
else:
    motor = criar_motor(config_app.URL_BASE_DADOS)
    with motor.connect() as nova:
        correr(nova)
    motor.dispose()
