"""Contabilidade: plano de contas SNC, lançamentos, linhas e regras por fornecedor.

Revisão: 0002
Anterior: 0001
Criada em: 2026-11-18

Bases de dados anteriores ao Alembic são preparadas com create_all (que já cria as
tabelas novas) e marcadas na revisão 0001: por isso esta migração só cria o que falta.
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspetor = sa.inspect(op.get_bind())
    colunas_empresas = {c["name"] for c in inspetor.get_columns("empresas")}
    with op.batch_alter_table("empresas", schema=None) as batch_op:
        if "regime_iva" not in colunas_empresas:
            batch_op.add_column(
                sa.Column("regime_iva", sa.String(length=12), nullable=False, server_default="trimestral")
            )
        if "fechado_ate" not in colunas_empresas:
            batch_op.add_column(sa.Column("fechado_ate", sa.Date(), nullable=True))

    if "contas" in inspetor.get_table_names():
        return  # as tabelas da contabilidade já foram criadas pelo create_all

    op.create_table(
        "contas",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("empresa_id", sa.Integer(), nullable=False),
        sa.Column("codigo", sa.String(length=20), nullable=False),
        sa.Column("nome", sa.String(length=200), nullable=False),
        sa.Column("movimento", sa.Boolean(), nullable=False),
        sa.Column("nif", sa.String(length=9), nullable=True),
        sa.Column("criada_em", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["empresa_id"], ["empresas.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("empresa_id", "codigo", name="uq_conta_empresa_codigo"),
    )
    with op.batch_alter_table("contas", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_contas_empresa_id"), ["empresa_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_contas_nif"), ["nif"], unique=False)

    op.create_table(
        "lancamentos",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("empresa_id", sa.Integer(), nullable=False),
        sa.Column("diario", sa.String(length=4), nullable=False),
        sa.Column("ano", sa.Integer(), nullable=False),
        sa.Column("numero", sa.Integer(), nullable=False),
        sa.Column("data", sa.Date(), nullable=False),
        sa.Column("descricao", sa.String(length=300), nullable=False),
        sa.Column("documento", sa.String(length=80), nullable=True),
        sa.Column("origem", sa.String(length=12), nullable=False),
        sa.Column("fatura_id", sa.String(length=12), nullable=True),
        sa.Column("estado", sa.String(length=10), nullable=False),
        sa.Column("estorno_de_id", sa.Integer(), nullable=True),
        sa.Column("notas", sa.JSON(), nullable=True),
        sa.Column("criado_por_id", sa.Integer(), nullable=True),
        sa.Column("criado_em", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["empresa_id"], ["empresas.id"]),
        sa.ForeignKeyConstraint(["fatura_id"], ["faturas.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["estorno_de_id"], ["lancamentos.id"]),
        sa.ForeignKeyConstraint(["criado_por_id"], ["utilizadores.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("empresa_id", "diario", "ano", "numero", name="uq_lancamento_numero"),
    )
    with op.batch_alter_table("lancamentos", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_lancamentos_empresa_id"), ["empresa_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_lancamentos_fatura_id"), ["fatura_id"], unique=False)
        batch_op.create_index("ix_lancamentos_empresa_data", ["empresa_id", "data"], unique=False)

    op.create_table(
        "linhas_lancamento",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("lancamento_id", sa.Integer(), nullable=False),
        sa.Column("ordem", sa.Integer(), nullable=False),
        sa.Column("conta_id", sa.Integer(), nullable=False),
        sa.Column("debito", sa.Float(), nullable=False),
        sa.Column("credito", sa.Float(), nullable=False),
        sa.Column("descricao", sa.String(length=200), nullable=True),
        sa.Column("tipo_iva", sa.String(length=8), nullable=True),
        sa.Column("taxa_iva", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(["lancamento_id"], ["lancamentos.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conta_id"], ["contas.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("linhas_lancamento", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_linhas_lancamento_lancamento_id"), ["lancamento_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_linhas_lancamento_conta_id"), ["conta_id"], unique=False)

    op.create_table(
        "regras_fornecedor",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("empresa_id", sa.Integer(), nullable=False),
        sa.Column("nif", sa.String(length=9), nullable=False),
        sa.Column("conta_codigo", sa.String(length=20), nullable=False),
        sa.Column("perfil_iva", sa.String(length=20), nullable=False),
        sa.Column("origem", sa.String(length=12), nullable=False),
        sa.Column("usos", sa.Integer(), nullable=False),
        sa.Column("atualizada_em", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["empresa_id"], ["empresas.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("empresa_id", "nif", name="uq_regra_empresa_nif"),
    )
    with op.batch_alter_table("regras_fornecedor", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_regras_fornecedor_empresa_id"), ["empresa_id"], unique=False)


def downgrade() -> None:
    op.drop_table("regras_fornecedor")
    op.drop_table("linhas_lancamento")
    op.drop_table("lancamentos")
    op.drop_table("contas")
    with op.batch_alter_table("empresas", schema=None) as batch_op:
        batch_op.drop_column("fechado_ate")
        batch_op.drop_column("regime_iva")
