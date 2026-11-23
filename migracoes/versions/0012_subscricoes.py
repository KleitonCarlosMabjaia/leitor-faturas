"""Planos e subscrições das empresas (Stripe).

Revisão: 0012
Anterior: 0011
Criada em: 2026-11-23
"""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    colunas = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("empresas")}
    if "plano" in colunas:
        return
    with op.batch_alter_table("empresas", schema=None) as batch_op:
        batch_op.add_column(sa.Column("plano", sa.String(length=20), nullable=False, server_default="contabilidade"))
        batch_op.add_column(sa.Column("estado_subscricao", sa.String(length=20), nullable=False, server_default="ativa"))
        batch_op.add_column(sa.Column("experimental_ate", sa.Date(), nullable=True))
        batch_op.add_column(sa.Column("em_atraso_desde", sa.Date(), nullable=True))
        batch_op.add_column(sa.Column("fim_periodo", sa.Date(), nullable=True))
        batch_op.add_column(sa.Column("cancela_no_fim", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column("stripe_cliente_id", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("stripe_subscricao_id", sa.String(length=64), nullable=True))
        batch_op.create_index(batch_op.f("ix_empresas_stripe_cliente_id"), ["stripe_cliente_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("empresas", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_empresas_stripe_cliente_id"))
        for coluna in ("stripe_subscricao_id", "stripe_cliente_id", "cancela_no_fim", "fim_periodo",
                       "em_atraso_desde", "experimental_ate", "estado_subscricao", "plano"):
            batch_op.drop_column(coluna)
