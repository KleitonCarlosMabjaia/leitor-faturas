"""Webhooks da API pública: endereços por empresa e entregas (com novas tentativas).

Revisão: 0022
Anterior: 0021
Criada em: 2026-11-24
"""

import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    tabelas = set(sa.inspect(op.get_bind()).get_table_names())
    if "webhooks" not in tabelas:
        op.create_table(
            "webhooks",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("empresa_id", sa.Integer(), sa.ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False),
            sa.Column("url", sa.String(500), nullable=False),
            sa.Column("eventos", sa.JSON(), nullable=False),
            sa.Column("segredo", sa.String(80), nullable=False),
            sa.Column("ativo", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("falhas_seguidas", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("ultimo_estado", sa.String(10)),
            sa.Column("ultima_entrega_em", sa.DateTime()),
            sa.Column("criado_por_id", sa.Integer(), sa.ForeignKey("utilizadores.id", ondelete="SET NULL")),
            sa.Column("criado_em", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_webhooks_empresa_id", "webhooks", ["empresa_id"])
    if "entregas_webhook" not in tabelas:
        op.create_table(
            "entregas_webhook",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("endpoint_id", sa.Integer(), sa.ForeignKey("webhooks.id", ondelete="CASCADE"), nullable=False),
            sa.Column("empresa_id", sa.Integer(), sa.ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False),
            sa.Column("evento", sa.String(40), nullable=False),
            sa.Column("carga", sa.JSON()),
            sa.Column("estado", sa.String(10), nullable=False),
            sa.Column("tentativas", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("codigo_resposta", sa.Integer()),
            sa.Column("erro", sa.String(300)),
            sa.Column("criada_em", sa.DateTime(), nullable=False),
            sa.Column("proxima_tentativa", sa.DateTime()),
            sa.Column("entregue_em", sa.DateTime()),
        )
        for coluna in ("endpoint_id", "empresa_id", "estado", "proxima_tentativa"):
            op.create_index(f"ix_entregas_webhook_{coluna}", "entregas_webhook", [coluna])


def downgrade() -> None:
    op.drop_table("entregas_webhook")
    op.drop_table("webhooks")
