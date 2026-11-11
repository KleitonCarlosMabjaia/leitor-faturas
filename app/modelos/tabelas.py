"""
Tabelas da base de dados (SQLAlchemy 2.0, estilo com anotações de tipo).

Cada empresa cliente só vê os seus dados: faturas e fornecedores têm
empresa_id e todas as consultas filtram por ele (ver servicos/repositorio.py).

Os valores monetários são guardados como Float e arredondados a 2 casas nas
entradas. Em PostgreSQL usaria Numeric (decimal exato); o SQLite não tem
um tipo decimal verdadeiro, por isso aqui a diferença não existiria.
"""

from datetime import date, datetime

from sqlalchemy import JSON, Date, DateTime, Float, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

ESTADO_POR_REVER = "por rever"
ESTADO_CONFIRMADA = "confirmada"

PAPEL_ADMIN = "admin"    # gere a equipa da empresa
PAPEL_MEMBRO = "membro"  # carrega e revê faturas


# ---------- Contas ----------

class Empresa(Base):
    __tablename__ = "empresas"

    id: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(String(200))
    nif: Mapped[str | None] = mapped_column(String(9))
    ativa: Mapped[bool] = mapped_column(default=True)
    criada_em: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    utilizadores: Mapped[list["Utilizador"]] = relationship(back_populates="empresa")


class Utilizador(Base):
    __tablename__ = "utilizadores"

    id: Mapped[int] = mapped_column(primary_key=True)
    empresa_id: Mapped[int] = mapped_column(ForeignKey("empresas.id"), index=True)
    empresa: Mapped[Empresa] = relationship(back_populates="utilizadores")
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)  # guardado em minúsculas
    nome: Mapped[str] = mapped_column(String(120))
    papel: Mapped[str] = mapped_column(String(10), default=PAPEL_MEMBRO)
    # Vazio enquanto o convite não for aceite
    hash_palavra_passe: Mapped[str | None] = mapped_column(String(200))
    ativo: Mapped[bool] = mapped_column(default=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    ultimo_acesso: Mapped[datetime | None] = mapped_column(DateTime)

    # Proteção contra tentativas repetidas de adivinhar a palavra-passe
    tentativas_falhadas: Mapped[int] = mapped_column(default=0)
    bloqueado_ate: Mapped[datetime | None] = mapped_column(DateTime)


class Sessao(Base):
    """Sessão iniciada. Guarda-se só o hash do token: quem ler a base de dados não consegue entrar."""

    __tablename__ = "sessoes"

    hash_token: Mapped[str] = mapped_column(String(64), primary_key=True)
    utilizador_id: Mapped[int] = mapped_column(ForeignKey("utilizadores.id", ondelete="CASCADE"), index=True)
    utilizador: Mapped[Utilizador] = relationship()
    criada_em: Mapped[datetime] = mapped_column(DateTime)
    expira_em: Mapped[datetime] = mapped_column(DateTime)


class TokenAcesso(Base):
    """Link de uso único para definir a palavra-passe (convite ou reposição)."""

    __tablename__ = "tokens_acesso"

    hash_token: Mapped[str] = mapped_column(String(64), primary_key=True)
    utilizador_id: Mapped[int] = mapped_column(ForeignKey("utilizadores.id", ondelete="CASCADE"), index=True)
    utilizador: Mapped[Utilizador] = relationship()
    expira_em: Mapped[datetime] = mapped_column(DateTime)
    usado_em: Mapped[datetime | None] = mapped_column(DateTime)


# ---------- Faturas ----------

class Fornecedor(Base):
    __tablename__ = "fornecedores"
    # O mesmo fornecedor pode existir em várias empresas, mas só uma vez em cada
    __table_args__ = (UniqueConstraint("empresa_id", "nif", name="uq_fornecedor_empresa_nif"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    empresa_id: Mapped[int | None] = mapped_column(ForeignKey("empresas.id"), index=True)
    nif: Mapped[str] = mapped_column(String(9), index=True)
    # Pode ficar vazio quando a fatura foi lida só pelo QR (o QR não traz o nome)
    nome: Mapped[str] = mapped_column(String(200), default="")
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    faturas: Mapped[list["Fatura"]] = relationship(back_populates="fornecedor")


class Fatura(Base):
    __tablename__ = "faturas"

    # Identificador curto em hexadecimal (o mesmo usado no nome do PDF)
    id: Mapped[str] = mapped_column(String(12), primary_key=True)
    empresa_id: Mapped[int | None] = mapped_column(ForeignKey("empresas.id"), index=True)
    ficheiro: Mapped[str] = mapped_column(String(200))  # caminho do PDF dentro de uploads/
    nome_original: Mapped[str] = mapped_column(String(200))
    tamanho_kb: Mapped[float] = mapped_column(Float)
    carregada_em: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)

    # Resultado da leitura automática
    estado_extracao: Mapped[str] = mapped_column(String(20), default="pendente")  # pendente | concluida | erro | sem_texto
    erro: Mapped[str | None] = mapped_column(Text)
    origem: Mapped[str | None] = mapped_column(String(20))  # qr | ia | qr+ia | qr+cache
    confianca: Mapped[str | None] = mapped_column(String(10))

    # Revisão humana
    estado: Mapped[str] = mapped_column(String(20), default=ESTADO_POR_REVER, index=True)
    paga: Mapped[bool] = mapped_column(default=False, index=True)

    # Dados da fatura
    fornecedor_id: Mapped[int | None] = mapped_column(ForeignKey("fornecedores.id"), index=True)
    fornecedor: Mapped[Fornecedor | None] = relationship(back_populates="faturas")
    nome_fornecedor: Mapped[str | None] = mapped_column(String(200))  # tal como aparece nesta fatura
    nif_fornecedor: Mapped[str | None] = mapped_column(String(20), index=True)
    numero_fatura: Mapped[str | None] = mapped_column(String(60))
    data_emissao: Mapped[date | None] = mapped_column(Date, index=True)
    data_vencimento: Mapped[date | None] = mapped_column(Date)
    valor_sem_iva: Mapped[float | None] = mapped_column(Float)
    total_iva: Mapped[float | None] = mapped_column(Float)  # soma das linhas: facilita os totais do dashboard
    total: Mapped[float | None] = mapped_column(Float)
    moeda: Mapped[str | None] = mapped_column(String(3))
    # As linhas de IVA são sempre lidas e gravadas em conjunto, por isso ficam em JSON
    linhas_iva: Mapped[list | None] = mapped_column(JSON)

    # Campos extra do QR da AT
    atcud: Mapped[str | None] = mapped_column(String(70), index=True)
    tipo_documento: Mapped[str | None] = mapped_column(String(4))

    # Verificações (recalculadas sempre que os dados mudam)
    alertas: Mapped[list | None] = mapped_column(JSON)
    nif_valido: Mapped[bool | None]
    totais_batem: Mapped[bool | None]
