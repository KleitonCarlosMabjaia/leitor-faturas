"""
Tabelas da base de dados (SQLAlchemy 2.0, estilo com anotações de tipo).

Os valores monetários são guardados como Float e arredondados a 2 casas nas
entradas. Em PostgreSQL usaria Numeric (decimal exato); o SQLite não tem
um tipo decimal verdadeiro, por isso aqui a diferença não existiria.
"""

from datetime import date, datetime

from sqlalchemy import JSON, Date, DateTime, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

ESTADO_POR_REVER = "por rever"
ESTADO_CONFIRMADA = "confirmada"


class Fornecedor(Base):
    __tablename__ = "fornecedores"

    id: Mapped[int] = mapped_column(primary_key=True)
    nif: Mapped[str] = mapped_column(String(9), unique=True, index=True)
    # Pode ficar vazio quando a fatura foi lida só pelo QR (o QR não traz o nome)
    nome: Mapped[str] = mapped_column(String(200), default="")
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    faturas: Mapped[list["Fatura"]] = relationship(back_populates="fornecedor")


class Fatura(Base):
    __tablename__ = "faturas"

    # Identificador curto em hexadecimal (o mesmo usado no nome do PDF)
    id: Mapped[str] = mapped_column(String(12), primary_key=True)
    ficheiro: Mapped[str] = mapped_column(String(200))  # nome do PDF em uploads/
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
