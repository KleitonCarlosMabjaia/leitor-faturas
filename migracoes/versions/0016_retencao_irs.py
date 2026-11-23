"""Retenção de IRS pelas tabelas da AT: situação familiar e dependentes de cada pessoa.

Revisão: 0016
Anterior: 0015
Criada em: 2026-11-23
"""

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

COLUNAS = [
    ("irs_situacao", sa.String(length=15), None),
    ("irs_dependentes", sa.Integer(), "0"),
    ("irs_dependentes_deficiencia", sa.Integer(), "0"),
    ("irs_deficiencia", sa.Boolean(), sa.false()),
    ("irs_conjuge_deficiencia", sa.Boolean(), sa.false()),
]


def upgrade() -> None:
    existentes = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("funcionarios")}
    with op.batch_alter_table("funcionarios", schema=None) as batch_op:
        for nome, tipo, omissao in COLUNAS:
            if nome not in existentes:
                batch_op.add_column(sa.Column(nome, tipo, nullable=omissao is None, server_default=omissao))


def downgrade() -> None:
    existentes = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("funcionarios")}
    with op.batch_alter_table("funcionarios", schema=None) as batch_op:
        for nome, _, _ in reversed(COLUNAS):
            if nome in existentes:
                batch_op.drop_column(nome)
