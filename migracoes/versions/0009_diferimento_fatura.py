"""Ligação do diferimento à fatura de compra que lhe deu origem.

Revisão: 0009
Anterior: 0008
Criada em: 2026-11-18
"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    colunas = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("diferimentos")}
    if "fatura_id" in colunas:
        return
    with op.batch_alter_table("diferimentos", schema=None) as batch_op:
        batch_op.add_column(sa.Column("fatura_id", sa.String(length=12), nullable=True))
        batch_op.create_index(batch_op.f("ix_diferimentos_fatura_id"), ["fatura_id"], unique=False)
        batch_op.create_foreign_key("fk_diferimentos_fatura_id", "faturas", ["fatura_id"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    with op.batch_alter_table("diferimentos", schema=None) as batch_op:
        batch_op.drop_constraint("fk_diferimentos_fatura_id", type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_diferimentos_fatura_id"))
        batch_op.drop_column("fatura_id")
