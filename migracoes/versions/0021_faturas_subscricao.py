"""Faturas certificadas das subscrições (emitidas no Facturalusa).

Revisão: 0021
Anterior: 0020
Criada em: 2026-11-24
"""

import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "faturas_subscricao" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "faturas_subscricao",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("empresa_id", sa.Integer(), sa.ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False),
        sa.Column("stripe_fatura_id", sa.String(80), nullable=False, unique=True),
        sa.Column("data", sa.Date(), nullable=False),
        sa.Column("descricao", sa.String(200), nullable=False),
        sa.Column("total", sa.Float(), nullable=False),
        sa.Column("iva", sa.Float(), nullable=False, server_default="0"),
        sa.Column("estado", sa.String(10), nullable=False),
        sa.Column("dados", sa.JSON()),
        sa.Column("tentativas", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("erro", sa.String(300)),
        sa.Column("documento_id", sa.String(40)),
        sa.Column("numero", sa.String(60)),
        sa.Column("atcud", sa.String(80)),
        sa.Column("url_pdf", sa.String(500)),
        sa.Column("enviada", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("criada_em", sa.DateTime(), nullable=False),
        sa.Column("emitida_em", sa.DateTime()),
    )
    op.create_index("ix_faturas_subscricao_empresa_id", "faturas_subscricao", ["empresa_id"])


def downgrade() -> None:
    op.drop_index("ix_faturas_subscricao_empresa_id", table_name="faturas_subscricao")
    op.drop_table("faturas_subscricao")
