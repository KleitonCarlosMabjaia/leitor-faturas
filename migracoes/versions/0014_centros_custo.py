"""Contabilidade analítica: centros de custo e centro de cada linha de lançamento.

Revisão: 0014
Anterior: 0013
Criada em: 2026-11-23
"""

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspetor = sa.inspect(op.get_bind())
    if not inspetor.has_table("centros_custo"):
        op.create_table(
            "centros_custo",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("empresa_id", sa.Integer(), sa.ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False),
            sa.Column("codigo", sa.String(length=20), nullable=False),
            sa.Column("nome", sa.String(length=120), nullable=False),
            sa.Column("ativo", sa.Boolean(), nullable=False),
            sa.Column("criado_em", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("empresa_id", "codigo", name="uq_centro_empresa_codigo"),
        )
        op.create_index("ix_centros_custo_empresa_id", "centros_custo", ["empresa_id"])
    if "centro_custo_id" not in {c["name"] for c in inspetor.get_columns("linhas_lancamento")}:
        with op.batch_alter_table("linhas_lancamento", schema=None) as batch_op:
            batch_op.add_column(sa.Column("centro_custo_id", sa.Integer(), nullable=True))
            batch_op.create_index("ix_linhas_lancamento_centro_custo_id", ["centro_custo_id"], unique=False)
            batch_op.create_foreign_key("fk_linhas_centro_custo", "centros_custo", ["centro_custo_id"], ["id"],
                                        ondelete="SET NULL")


def downgrade() -> None:
    with op.batch_alter_table("linhas_lancamento", schema=None) as batch_op:
        batch_op.drop_constraint("fk_linhas_centro_custo", type_="foreignkey")
        batch_op.drop_index("ix_linhas_lancamento_centro_custo_id")
        batch_op.drop_column("centro_custo_id")
    op.drop_index("ix_centros_custo_empresa_id", table_name="centros_custo")
    op.drop_table("centros_custo")
