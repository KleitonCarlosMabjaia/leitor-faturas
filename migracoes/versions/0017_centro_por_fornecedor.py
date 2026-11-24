"""Centro de custo memorizado por fornecedor (sugerido ao contabilizar as faturas seguintes).

Revisão: 0017
Anterior: 0016
Criada em: 2026-11-24
"""

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "centro_custo_id" in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("regras_fornecedor")}:
        return
    with op.batch_alter_table("regras_fornecedor", schema=None) as batch_op:
        batch_op.add_column(sa.Column("centro_custo_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key("fk_regra_fornecedor_centro", "centros_custo", ["centro_custo_id"], ["id"],
                                    ondelete="SET NULL")


def downgrade() -> None:
    with op.batch_alter_table("regras_fornecedor", schema=None) as batch_op:
        batch_op.drop_constraint("fk_regra_fornecedor_centro", type_="foreignkey")
        batch_op.drop_column("centro_custo_id")
