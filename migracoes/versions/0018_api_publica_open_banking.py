"""API pública (chaves por empresa) e ligações diretas aos bancos (open banking).

Revisão: 0018
Anterior: 0017
Criada em: 2026-11-24
"""

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    tabelas = set(sa.inspect(op.get_bind()).get_table_names())
    if "chaves_api" not in tabelas:
        op.create_table(
            "chaves_api",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("empresa_id", sa.Integer(), sa.ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False),
            sa.Column("nome", sa.String(80), nullable=False),
            sa.Column("prefixo", sa.String(20), nullable=False),
            sa.Column("hash_chave", sa.String(64), nullable=False, unique=True),
            sa.Column("permissao", sa.String(10), nullable=False),
            sa.Column("criada_por_id", sa.Integer(), sa.ForeignKey("utilizadores.id", ondelete="SET NULL")),
            sa.Column("criada_em", sa.DateTime(), nullable=False),
            sa.Column("ultimo_uso", sa.DateTime()),
            sa.Column("revogada_em", sa.DateTime()),
        )
        op.create_index("ix_chaves_api_empresa_id", "chaves_api", ["empresa_id"])
    if "ligacoes_bancarias" not in tabelas:
        op.create_table(
            "ligacoes_bancarias",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("empresa_id", sa.Integer(), sa.ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False),
            sa.Column("conta_codigo", sa.String(20), nullable=False),
            sa.Column("fornecedor", sa.String(20), nullable=False),
            sa.Column("banco", sa.String(120), nullable=False),
            sa.Column("pais", sa.String(2), nullable=False),
            sa.Column("estado", sa.String(10), nullable=False),
            sa.Column("estado_oauth", sa.String(64), unique=True),
            sa.Column("sessao_externa", sa.String(100)),
            sa.Column("conta_externa", sa.String(100)),
            sa.Column("iban", sa.String(40)),
            sa.Column("contas_disponiveis", sa.JSON()),
            sa.Column("valido_ate", sa.DateTime()),
            sa.Column("ultima_sincronizacao", sa.DateTime()),
            sa.Column("ultimo_erro", sa.String(300)),
            sa.Column("criada_por_id", sa.Integer(), sa.ForeignKey("utilizadores.id", ondelete="SET NULL")),
            sa.Column("criada_em", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_ligacoes_bancarias_empresa_id", "ligacoes_bancarias", ["empresa_id"])


def downgrade() -> None:
    op.drop_table("ligacoes_bancarias")
    op.drop_table("chaves_api")
