"""Gestão: artigos, movimentos de stock e orçamento; NISS da empresa e data de nascimento dos trabalhadores.

Revisão: 0015
Anterior: 0014
Criada em: 2026-11-23
"""

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def _colunas(inspetor, tabela: str) -> set[str]:
    return {c["name"] for c in inspetor.get_columns(tabela)}


def upgrade() -> None:
    inspetor = sa.inspect(op.get_bind())
    if "niss" not in _colunas(inspetor, "empresas"):
        with op.batch_alter_table("empresas", schema=None) as batch_op:
            batch_op.add_column(sa.Column("niss", sa.String(length=11), nullable=True))
    if "data_nascimento" not in _colunas(inspetor, "funcionarios"):
        with op.batch_alter_table("funcionarios", schema=None) as batch_op:
            batch_op.add_column(sa.Column("data_nascimento", sa.Date(), nullable=True))
    if not inspetor.has_table("artigos"):
        op.create_table(
            "artigos",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("empresa_id", sa.Integer(), sa.ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False),
            sa.Column("codigo", sa.String(length=60), nullable=False),
            sa.Column("nome", sa.String(length=200), nullable=False),
            sa.Column("tipo", sa.String(length=1), nullable=False),
            sa.Column("unidade", sa.String(length=10), nullable=False),
            sa.Column("preco_venda", sa.Float(), nullable=False),
            sa.Column("taxa_iva", sa.Float(), nullable=False),
            sa.Column("stock_minimo", sa.Float(), nullable=False),
            sa.Column("quantidade", sa.Float(), nullable=False),
            sa.Column("custo_medio", sa.Float(), nullable=False),
            sa.Column("ativo", sa.Boolean(), nullable=False),
            sa.Column("criado_em", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("empresa_id", "codigo", name="uq_artigo_empresa_codigo"),
        )
        op.create_index("ix_artigos_empresa_id", "artigos", ["empresa_id"])
    if not inspetor.has_table("movimentos_stock"):
        op.create_table(
            "movimentos_stock",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("empresa_id", sa.Integer(), sa.ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False),
            sa.Column("artigo_id", sa.Integer(), sa.ForeignKey("artigos.id", ondelete="CASCADE"), nullable=False),
            sa.Column("data", sa.Date(), nullable=False),
            sa.Column("tipo", sa.String(length=10), nullable=False),
            sa.Column("quantidade", sa.Float(), nullable=False),
            sa.Column("custo_unitario", sa.Float(), nullable=False),
            sa.Column("valor", sa.Float(), nullable=False),
            sa.Column("preco_unitario", sa.Float(), nullable=True),
            sa.Column("documento", sa.String(length=80), nullable=True),
            sa.Column("fatura_id", sa.String(length=12), sa.ForeignKey("faturas.id", ondelete="SET NULL"),
                      nullable=True),
            sa.Column("venda_id", sa.Integer(), sa.ForeignKey("faturas_venda.id", ondelete="SET NULL"),
                      nullable=True),
            sa.Column("criado_em", sa.DateTime(), nullable=False),
            sa.Column("criado_por_id", sa.Integer(), sa.ForeignKey("utilizadores.id", ondelete="SET NULL"),
                      nullable=True),
        )
        for coluna in ("empresa_id", "artigo_id", "data", "fatura_id", "venda_id"):
            op.create_index(f"ix_movimentos_stock_{coluna}", "movimentos_stock", [coluna])
    if not inspetor.has_table("linhas_orcamento"):
        op.create_table(
            "linhas_orcamento",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("empresa_id", sa.Integer(), sa.ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False),
            sa.Column("ano", sa.Integer(), nullable=False),
            sa.Column("rubrica", sa.String(length=30), nullable=False),
            sa.Column("valores", sa.JSON(), nullable=False),
            sa.UniqueConstraint("empresa_id", "ano", "rubrica", name="uq_orcamento_empresa_ano_rubrica"),
        )
        op.create_index("ix_linhas_orcamento_empresa_id", "linhas_orcamento", ["empresa_id"])


def downgrade() -> None:
    op.drop_table("linhas_orcamento")
    op.drop_table("movimentos_stock")
    op.drop_table("artigos")
    with op.batch_alter_table("funcionarios", schema=None) as batch_op:
        batch_op.drop_column("data_nascimento")
    with op.batch_alter_table("empresas", schema=None) as batch_op:
        batch_op.drop_column("niss")
