"""Vendas: faturas de clientes (do SAF-T de faturação ou registadas à mão) e a sua ligação aos lançamentos.

Revisão: 0005
Anterior: 0004
Criada em: 2026-11-18

Bases de dados anteriores ao Alembic são preparadas com create_all (que já cria isto):
por isso só se cria o que falta.
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspetor = sa.inspect(op.get_bind())
    if "faturas_venda" not in inspetor.get_table_names():
        op.create_table(
            "faturas_venda",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("empresa_id", sa.Integer(), nullable=False),
            sa.Column("numero", sa.String(length=60), nullable=False),
            sa.Column("tipo", sa.String(length=2), nullable=False),
            sa.Column("atcud", sa.String(length=100), nullable=True),
            sa.Column("data", sa.Date(), nullable=False),
            sa.Column("data_vencimento", sa.Date(), nullable=True),
            sa.Column("cliente_nif", sa.String(length=20), nullable=False),
            sa.Column("cliente_nome", sa.String(length=200), nullable=False),
            sa.Column("linhas", sa.JSON(), nullable=False),
            sa.Column("valor_sem_iva", sa.Float(), nullable=False),
            sa.Column("total_iva", sa.Float(), nullable=False),
            sa.Column("total", sa.Float(), nullable=False),
            sa.Column("estado", sa.String(length=10), nullable=False),
            sa.Column("valor_recebido", sa.Float(), nullable=False),
            sa.Column("recebida", sa.Boolean(), nullable=False),
            sa.Column("meio_pagamento", sa.String(length=2), nullable=True),
            sa.Column("origem", sa.String(length=10), nullable=False),
            sa.Column("criada_em", sa.DateTime(), nullable=False),
            sa.Column("criada_por_id", sa.Integer(), nullable=True),
            sa.ForeignKeyConstraint(["empresa_id"], ["empresas.id"]),
            sa.ForeignKeyConstraint(["criada_por_id"], ["utilizadores.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("empresa_id", "numero", name="uq_venda_empresa_numero"),
        )
        with op.batch_alter_table("faturas_venda", schema=None) as batch_op:
            batch_op.create_index(batch_op.f("ix_faturas_venda_empresa_id"), ["empresa_id"], unique=False)
            batch_op.create_index(batch_op.f("ix_faturas_venda_data"), ["data"], unique=False)
            batch_op.create_index(batch_op.f("ix_faturas_venda_recebida"), ["recebida"], unique=False)

    if "venda_id" not in {c["name"] for c in inspetor.get_columns("lancamentos")}:
        with op.batch_alter_table("lancamentos", schema=None) as batch_op:
            batch_op.add_column(sa.Column("venda_id", sa.Integer(), nullable=True))
            batch_op.create_index(batch_op.f("ix_lancamentos_venda_id"), ["venda_id"], unique=False)
            batch_op.create_foreign_key("fk_lancamentos_venda_id", "faturas_venda", ["venda_id"], ["id"],
                                        ondelete="SET NULL")


def downgrade() -> None:
    with op.batch_alter_table("lancamentos", schema=None) as batch_op:
        batch_op.drop_constraint("fk_lancamentos_venda_id", type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_lancamentos_venda_id"))
        batch_op.drop_column("venda_id")
    op.drop_table("faturas_venda")
