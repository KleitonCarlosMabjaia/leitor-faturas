"""Dados da empresa para os ficheiros oficiais da declaração periódica do IVA e da DMR.

Revisão: 0019
Anterior: 0018
Criada em: 2026-11-24
"""

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

COLUNAS = (
    ("servico_financas", sa.String(4)),
    ("nif_contabilista", sa.String(9)),
    ("nif_representante", sa.String(9)),
)


def upgrade() -> None:
    existentes = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("empresas")}
    with op.batch_alter_table("empresas", schema=None) as batch_op:
        for nome, tipo in COLUNAS:
            if nome not in existentes:
                batch_op.add_column(sa.Column(nome, tipo, nullable=True))
        if "regiao_sede" not in existentes:
            batch_op.add_column(sa.Column("regiao_sede", sa.String(10), nullable=False, server_default="continente"))


def downgrade() -> None:
    with op.batch_alter_table("empresas", schema=None) as batch_op:
        for nome in ("regiao_sede", *(n for n, _ in reversed(COLUNAS))):
            batch_op.drop_column(nome)
