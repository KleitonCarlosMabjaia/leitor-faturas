"""Morada da sede da empresa (cabeçalho do SAF-T da contabilidade).

Revisão: 0003
Anterior: 0002
Criada em: 2026-11-18

Bases de dados anteriores ao Alembic são preparadas com create_all (que já cria estas
colunas): por isso só se acrescenta o que falta.
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

COLUNAS = [
    ("morada", sa.String(length=210)),
    ("codigo_postal", sa.String(length=8)),
    ("localidade", sa.String(length=50)),
]


def upgrade() -> None:
    existentes = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("empresas")}
    with op.batch_alter_table("empresas", schema=None) as batch_op:
        for nome, tipo in COLUNAS:
            if nome not in existentes:
                batch_op.add_column(sa.Column(nome, tipo, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("empresas", schema=None) as batch_op:
        for nome, _ in reversed(COLUNAS):
            batch_op.drop_column(nome)
