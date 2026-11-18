"""Ativos fixos e as suas depreciações.

Revisão: 0007
Anterior: 0006
Criada em: 2026-11-18

Bases de dados anteriores ao Alembic são preparadas com create_all (que já cria isto):
por isso só se cria o que falta.
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "ativos_fixos" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "ativos_fixos",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("empresa_id", sa.Integer(), nullable=False),
        sa.Column("codigo", sa.String(length=20), nullable=False),
        sa.Column("descricao", sa.String(length=200), nullable=False),
        sa.Column("conta_ativo", sa.String(length=20), nullable=False),
        sa.Column("conta_depreciacao", sa.String(length=20), nullable=False),
        sa.Column("conta_gasto", sa.String(length=20), nullable=False),
        sa.Column("data_aquisicao", sa.Date(), nullable=False),
        sa.Column("data_inicio", sa.Date(), nullable=False),
        sa.Column("valor_aquisicao", sa.Float(), nullable=False),
        sa.Column("valor_residual", sa.Float(), nullable=False),
        sa.Column("taxa", sa.Float(), nullable=False),
        sa.Column("estado", sa.String(length=10), nullable=False),
        sa.Column("data_saida", sa.Date(), nullable=True),
        sa.Column("fatura_id", sa.String(length=12), nullable=True),
        sa.Column("lancamento_saida_id", sa.Integer(), nullable=True),
        sa.Column("criado_em", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["empresa_id"], ["empresas.id"]),
        sa.ForeignKeyConstraint(["fatura_id"], ["faturas.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["lancamento_saida_id"], ["lancamentos.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("ativos_fixos", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_ativos_fixos_empresa_id"), ["empresa_id"], unique=False)

    op.create_table(
        "depreciacoes_ativos",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ativo_id", sa.Integer(), nullable=False),
        sa.Column("de", sa.Date(), nullable=False),
        sa.Column("ate", sa.Date(), nullable=False),
        sa.Column("meses", sa.Integer(), nullable=False),
        sa.Column("valor", sa.Float(), nullable=False),
        sa.Column("lancamento_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["ativo_id"], ["ativos_fixos.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["lancamento_id"], ["lancamentos.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("depreciacoes_ativos", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_depreciacoes_ativos_ativo_id"), ["ativo_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_depreciacoes_ativos_lancamento_id"), ["lancamento_id"], unique=False)


def downgrade() -> None:
    op.drop_table("depreciacoes_ativos")
    op.drop_table("ativos_fixos")
