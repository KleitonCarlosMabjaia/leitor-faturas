"""Funcionários, processamentos salariais e diferimentos.

Revisão: 0008
Anterior: 0007
Criada em: 2026-11-18

Bases de dados anteriores ao Alembic são preparadas com create_all (que já cria isto):
por isso só se cria o que falta.
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existentes = set(sa.inspect(op.get_bind()).get_table_names())
    if "funcionarios" not in existentes:
        op.create_table(
            "funcionarios",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("empresa_id", sa.Integer(), nullable=False),
            sa.Column("numero", sa.Integer(), nullable=False),
            sa.Column("nome", sa.String(length=200), nullable=False),
            sa.Column("nif", sa.String(length=9), nullable=True),
            sa.Column("niss", sa.String(length=11), nullable=True),
            sa.Column("tipo", sa.String(length=15), nullable=False),
            sa.Column("data_admissao", sa.Date(), nullable=False),
            sa.Column("data_saida", sa.Date(), nullable=True),
            sa.Column("salario_base", sa.Float(), nullable=False),
            sa.Column("subsidio_alimentacao", sa.Float(), nullable=False),
            sa.Column("alimentacao_cartao", sa.Boolean(), nullable=False),
            sa.Column("duodecimos", sa.Boolean(), nullable=False),
            sa.Column("taxa_irs", sa.Float(), nullable=False),
            sa.Column("taxa_ss_trabalhador", sa.Float(), nullable=False),
            sa.Column("taxa_ss_entidade", sa.Float(), nullable=False),
            sa.Column("criado_em", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["empresa_id"], ["empresas.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        with op.batch_alter_table("funcionarios", schema=None) as batch_op:
            batch_op.create_index(batch_op.f("ix_funcionarios_empresa_id"), ["empresa_id"], unique=False)

    if "processamentos_salariais" not in existentes:
        op.create_table(
            "processamentos_salariais",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("empresa_id", sa.Integer(), nullable=False),
            sa.Column("ano", sa.Integer(), nullable=False),
            sa.Column("mes", sa.Integer(), nullable=False),
            sa.Column("recibos", sa.JSON(), nullable=False),
            sa.Column("pagamentos", sa.JSON(), nullable=False),
            sa.Column("lancamento_id", sa.Integer(), nullable=True),
            sa.Column("criado_em", sa.DateTime(), nullable=False),
            sa.Column("criado_por_id", sa.Integer(), nullable=True),
            sa.ForeignKeyConstraint(["empresa_id"], ["empresas.id"]),
            sa.ForeignKeyConstraint(["lancamento_id"], ["lancamentos.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["criado_por_id"], ["utilizadores.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("empresa_id", "ano", "mes", name="uq_processamento_empresa_mes"),
        )
        with op.batch_alter_table("processamentos_salariais", schema=None) as batch_op:
            batch_op.create_index(batch_op.f("ix_processamentos_salariais_empresa_id"), ["empresa_id"], unique=False)

    if "diferimentos" not in existentes:
        op.create_table(
            "diferimentos",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("empresa_id", sa.Integer(), nullable=False),
            sa.Column("tipo", sa.String(length=10), nullable=False),
            sa.Column("descricao", sa.String(length=200), nullable=False),
            sa.Column("conta", sa.String(length=20), nullable=False),
            sa.Column("valor", sa.Float(), nullable=False),
            sa.Column("inicio", sa.Date(), nullable=False),
            sa.Column("meses", sa.Integer(), nullable=False),
            sa.Column("lancamento_id", sa.Integer(), nullable=True),
            sa.Column("reconhecimentos", sa.JSON(), nullable=False),
            sa.Column("criado_em", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["empresa_id"], ["empresas.id"]),
            sa.ForeignKeyConstraint(["lancamento_id"], ["lancamentos.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
        with op.batch_alter_table("diferimentos", schema=None) as batch_op:
            batch_op.create_index(batch_op.f("ix_diferimentos_empresa_id"), ["empresa_id"], unique=False)


def downgrade() -> None:
    op.drop_table("diferimentos")
    op.drop_table("processamentos_salariais")
    op.drop_table("funcionarios")
