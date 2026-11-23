"""Várias empresas por utilizador: acessos por empresa e empresa escolhida em cada sessão.

Revisão: 0013
Anterior: 0012
Criada em: 2026-11-23
"""

from datetime import datetime

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    ligacao = op.get_bind()
    inspetor = sa.inspect(ligacao)
    if not inspetor.has_table("acessos_empresa"):
        op.create_table(
            "acessos_empresa",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("utilizador_id", sa.Integer(), sa.ForeignKey("utilizadores.id", ondelete="CASCADE"), nullable=False),
            sa.Column("empresa_id", sa.Integer(), sa.ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False),
            sa.Column("papel", sa.String(length=10), nullable=False),
            sa.Column("ativo", sa.Boolean(), nullable=False),
            sa.Column("criado_em", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("utilizador_id", "empresa_id", name="uq_acesso_utilizador_empresa"),
        )
        op.create_index("ix_acessos_empresa_utilizador_id", "acessos_empresa", ["utilizador_id"])
        op.create_index("ix_acessos_empresa_empresa_id", "acessos_empresa", ["empresa_id"])
        # Cada pessoa existente fica com acesso à sua empresa, com o papel e o estado que já tinha
        utilizadores = ligacao.execute(sa.text("SELECT id, empresa_id, papel, ativo FROM utilizadores")).fetchall()
        tabela = sa.table("acessos_empresa", sa.column("utilizador_id"), sa.column("empresa_id"), sa.column("papel"),
                          sa.column("ativo", sa.Boolean()), sa.column("criado_em", sa.DateTime()))
        if utilizadores:
            op.bulk_insert(tabela, [
                {"utilizador_id": u[0], "empresa_id": u[1], "papel": u[2] or "membro", "ativo": bool(u[3]),
                 "criado_em": datetime.now()} for u in utilizadores
            ])
    if "empresa_id" not in {c["name"] for c in inspetor.get_columns("sessoes")}:
        with op.batch_alter_table("sessoes", schema=None) as batch_op:
            batch_op.add_column(sa.Column("empresa_id", sa.Integer(), nullable=True))
            batch_op.create_foreign_key("fk_sessoes_empresa_id", "empresas", ["empresa_id"], ["id"], ondelete="CASCADE")


def downgrade() -> None:
    with op.batch_alter_table("sessoes", schema=None) as batch_op:
        batch_op.drop_constraint("fk_sessoes_empresa_id", type_="foreignkey")
        batch_op.drop_column("empresa_id")
    op.drop_index("ix_acessos_empresa_empresa_id", table_name="acessos_empresa")
    op.drop_index("ix_acessos_empresa_utilizador_id", table_name="acessos_empresa")
    op.drop_table("acessos_empresa")
