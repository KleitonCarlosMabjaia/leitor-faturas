"""Inventários: contagens físicas e as suas linhas (para o apuramento do CMVMC).

Revisão: 0006
Anterior: 0005
Criada em: 2026-11-18

Bases de dados anteriores ao Alembic são preparadas com create_all (que já cria isto):
por isso só se cria o que falta.
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "inventarios" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "inventarios",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("empresa_id", sa.Integer(), nullable=False),
        sa.Column("data", sa.Date(), nullable=False),
        sa.Column("descricao", sa.String(length=200), nullable=False),
        sa.Column("inicial", sa.Boolean(), nullable=False),
        sa.Column("lancamento_id", sa.Integer(), nullable=True),
        sa.Column("criado_em", sa.DateTime(), nullable=False),
        sa.Column("criado_por_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["empresa_id"], ["empresas.id"]),
        sa.ForeignKeyConstraint(["lancamento_id"], ["lancamentos.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["criado_por_id"], ["utilizadores.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("empresa_id", "data", name="uq_inventario_empresa_data"),
    )
    with op.batch_alter_table("inventarios", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_inventarios_empresa_id"), ["empresa_id"], unique=False)

    op.create_table(
        "linhas_inventario",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("inventario_id", sa.Integer(), nullable=False),
        sa.Column("codigo", sa.String(length=60), nullable=False),
        sa.Column("descricao", sa.String(length=200), nullable=False),
        sa.Column("categoria", sa.String(length=1), nullable=False),
        sa.Column("unidade", sa.String(length=10), nullable=False),
        sa.Column("quantidade", sa.Float(), nullable=False),
        sa.Column("custo_unitario", sa.Float(), nullable=False),
        sa.Column("valor", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["inventario_id"], ["inventarios.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("linhas_inventario", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_linhas_inventario_inventario_id"), ["inventario_id"], unique=False)


def downgrade() -> None:
    op.drop_table("linhas_inventario")
    op.drop_table("inventarios")
