"""Código de fluxo de caixa nas linhas de caixa e bancos, e códigos memorizados por fornecedor/cliente.

Revisão: 0020
Anterior: 0019
Criada em: 2026-11-24
"""

import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspetor = sa.inspect(op.get_bind())
    if "fluxo_caixa" not in {c["name"] for c in inspetor.get_columns("linhas_lancamento")}:
        with op.batch_alter_table("linhas_lancamento", schema=None) as batch_op:
            batch_op.add_column(sa.Column("fluxo_caixa", sa.String(4), nullable=True))
    if "regras_fluxo" not in inspetor.get_table_names():
        op.create_table(
            "regras_fluxo",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("empresa_id", sa.Integer(), sa.ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False),
            sa.Column("conta_codigo", sa.String(20), nullable=False),
            sa.Column("fluxo", sa.String(4), nullable=False),
            sa.UniqueConstraint("empresa_id", "conta_codigo", name="uq_regra_fluxo_conta"),
        )
        op.create_index("ix_regras_fluxo_empresa_id", "regras_fluxo", ["empresa_id"])


def downgrade() -> None:
    op.drop_index("ix_regras_fluxo_empresa_id", table_name="regras_fluxo")
    op.drop_table("regras_fluxo")
    with op.batch_alter_table("linhas_lancamento", schema=None) as batch_op:
        batch_op.drop_column("fluxo_caixa")
