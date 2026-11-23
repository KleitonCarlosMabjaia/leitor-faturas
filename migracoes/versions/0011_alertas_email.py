"""Preferência de alertas de prazos fiscais por email.

Revisão: 0011
Anterior: 0010
Criada em: 2026-11-23
"""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    colunas = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("utilizadores")}
    if "alertas_email" in colunas:
        return
    with op.batch_alter_table("utilizadores", schema=None) as batch_op:
        batch_op.add_column(sa.Column("alertas_email", sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade() -> None:
    with op.batch_alter_table("utilizadores", schema=None) as batch_op:
        batch_op.drop_column("alertas_email")
