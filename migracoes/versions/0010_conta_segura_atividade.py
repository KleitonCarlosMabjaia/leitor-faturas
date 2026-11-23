"""Verificação em dois passos, sessões com dispositivo e registo de atividade.

Revisão: 0010
Anterior: 0009
Criada em: 2026-11-23
"""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def _colunas(tabela: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(tabela)}


def upgrade() -> None:
    if "totp_segredo" not in _colunas("utilizadores"):
        with op.batch_alter_table("utilizadores", schema=None) as batch_op:
            batch_op.add_column(sa.Column("totp_segredo", sa.String(length=64), nullable=True))
            batch_op.add_column(sa.Column("totp_ativo", sa.Boolean(), nullable=False, server_default=sa.false()))
            batch_op.add_column(sa.Column("totp_ultimo_passo", sa.Integer(), nullable=True))
            batch_op.add_column(sa.Column("codigos_recuperacao", sa.JSON(), nullable=True))
    if "agente" not in _colunas("sessoes"):
        with op.batch_alter_table("sessoes", schema=None) as batch_op:
            batch_op.add_column(sa.Column("ip", sa.String(length=45), nullable=True))
            batch_op.add_column(sa.Column("agente", sa.String(length=200), nullable=True))
            batch_op.add_column(sa.Column("ultimo_uso", sa.DateTime(), nullable=True))
    if not sa.inspect(op.get_bind()).has_table("registos_atividade"):
        op.create_table(
            "registos_atividade",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("empresa_id", sa.Integer(), sa.ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False),
            sa.Column("utilizador_id", sa.Integer(), sa.ForeignKey("utilizadores.id", ondelete="SET NULL"), nullable=True),
            sa.Column("quando", sa.DateTime(), nullable=False),
            sa.Column("acao", sa.String(length=120), nullable=False),
            sa.Column("detalhe", sa.String(length=300), nullable=True),
            sa.Column("ip", sa.String(length=45), nullable=True),
        )
        op.create_index("ix_registos_empresa_quando", "registos_atividade", ["empresa_id", "quando"])


def downgrade() -> None:
    op.drop_index("ix_registos_empresa_quando", table_name="registos_atividade")
    op.drop_table("registos_atividade")
    with op.batch_alter_table("sessoes", schema=None) as batch_op:
        batch_op.drop_column("ultimo_uso")
        batch_op.drop_column("agente")
        batch_op.drop_column("ip")
    with op.batch_alter_table("utilizadores", schema=None) as batch_op:
        batch_op.drop_column("codigos_recuperacao")
        batch_op.drop_column("totp_ultimo_passo")
        batch_op.drop_column("totp_ativo")
        batch_op.drop_column("totp_segredo")
