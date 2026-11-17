"""Bancos: extratos importados, movimentos bancários e regras de lançamento aprendidas.

Revisão: 0004
Anterior: 0003
Criada em: 2026-11-18

Bases de dados anteriores ao Alembic são preparadas com create_all (que já cria estas
tabelas): por isso só se cria o que falta.
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "extratos_bancarios" in sa.inspect(op.get_bind()).get_table_names():
        return

    op.create_table(
        "extratos_bancarios",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("empresa_id", sa.Integer(), nullable=False),
        sa.Column("conta_codigo", sa.String(length=20), nullable=False),
        sa.Column("nome_ficheiro", sa.String(length=200), nullable=False),
        sa.Column("formato", sa.String(length=10), nullable=False),
        sa.Column("data_inicio", sa.Date(), nullable=True),
        sa.Column("data_fim", sa.Date(), nullable=True),
        sa.Column("saldo_final", sa.Float(), nullable=True),
        sa.Column("importado_em", sa.DateTime(), nullable=False),
        sa.Column("importado_por_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["empresa_id"], ["empresas.id"]),
        sa.ForeignKeyConstraint(["importado_por_id"], ["utilizadores.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("extratos_bancarios", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_extratos_bancarios_empresa_id"), ["empresa_id"], unique=False)

    op.create_table(
        "movimentos_bancarios",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("empresa_id", sa.Integer(), nullable=False),
        sa.Column("extrato_id", sa.Integer(), nullable=False),
        sa.Column("conta_codigo", sa.String(length=20), nullable=False),
        sa.Column("data", sa.Date(), nullable=False),
        sa.Column("descricao", sa.String(length=300), nullable=False),
        sa.Column("montante", sa.Float(), nullable=False),
        sa.Column("saldo", sa.Float(), nullable=True),
        sa.Column("referencia", sa.String(length=64), nullable=False),
        sa.Column("estado", sa.String(length=12), nullable=False),
        sa.Column("linha_id", sa.Integer(), nullable=True),
        sa.Column("reconciliado_em", sa.DateTime(), nullable=True),
        sa.Column("reconciliado_por_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["empresa_id"], ["empresas.id"]),
        sa.ForeignKeyConstraint(["extrato_id"], ["extratos_bancarios.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["linha_id"], ["linhas_lancamento.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["reconciliado_por_id"], ["utilizadores.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("empresa_id", "conta_codigo", "referencia", name="uq_movimento_referencia"),
    )
    with op.batch_alter_table("movimentos_bancarios", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_movimentos_bancarios_empresa_id"), ["empresa_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_movimentos_bancarios_extrato_id"), ["extrato_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_movimentos_bancarios_linha_id"), ["linha_id"], unique=True)
        batch_op.create_index("ix_movimentos_empresa_conta_estado", ["empresa_id", "conta_codigo", "estado"],
                              unique=False)

    op.create_table(
        "regras_bancarias",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("empresa_id", sa.Integer(), nullable=False),
        sa.Column("padrao", sa.String(length=80), nullable=False),
        sa.Column("sinal", sa.Integer(), nullable=False),
        sa.Column("conta_codigo", sa.String(length=20), nullable=False),
        sa.Column("usos", sa.Integer(), nullable=False),
        sa.Column("atualizada_em", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["empresa_id"], ["empresas.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("empresa_id", "padrao", "sinal", name="uq_regra_bancaria"),
    )
    with op.batch_alter_table("regras_bancarias", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_regras_bancarias_empresa_id"), ["empresa_id"], unique=False)


def downgrade() -> None:
    op.drop_table("regras_bancarias")
    op.drop_table("movimentos_bancarios")
    op.drop_table("extratos_bancarios")
