"""
Tabelas da base de dados (SQLAlchemy 2.0, estilo com anotações de tipo).

Cada empresa cliente só vê os seus dados: faturas e fornecedores têm
empresa_id e todas as consultas filtram por ele (ver servicos/repositorio.py).

Os valores monetários são guardados como Float e arredondados a 2 casas nas
entradas. Em PostgreSQL usaria Numeric (decimal exato); o SQLite não tem
um tipo decimal verdadeiro, por isso aqui a diferença não existiria.
"""

from datetime import date, datetime

from sqlalchemy import JSON, Date, DateTime, Float, ForeignKey, Index, String, Text, UniqueConstraint
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
    # Contabilidade: periodicidade da declaração de IVA e data até à qual os períodos estão fechados
    regime_iva: Mapped[str] = mapped_column(String(12), default="trimestral", server_default="trimestral")
    fechado_ate: Mapped[date | None] = mapped_column(Date)
    # Morada da sede (cabeçalho do SAF-T)
    morada: Mapped[str | None] = mapped_column(String(210))
    codigo_postal: Mapped[str | None] = mapped_column(String(8))
    localidade: Mapped[str | None] = mapped_column(String(50))

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
    # pendente | concluida | erro | sem_texto
    estado_extracao: Mapped[str] = mapped_column(String(20), default="pendente")
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


# ---------- Contabilidade (SNC) ----------

LANCAMENTO_ATIVO = "ativo"
LANCAMENTO_ESTORNADO = "estornado"  # anulado por um lançamento de estorno (nunca se apaga)


class Conta(Base):
    """
    Conta do plano de contas da empresa (SNC). A hierarquia vem do próprio código:
    "6242" é filha de "624", que é filha de "62", que pertence à classe "6".
    Só as contas de movimento (sem subcontas) recebem lançamentos.
    """

    __tablename__ = "contas"
    __table_args__ = (UniqueConstraint("empresa_id", "codigo", name="uq_conta_empresa_codigo"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    empresa_id: Mapped[int] = mapped_column(ForeignKey("empresas.id"), index=True)
    codigo: Mapped[str] = mapped_column(String(20))
    nome: Mapped[str] = mapped_column(String(200))
    movimento: Mapped[bool] = mapped_column(default=True)  # False: conta agregadora
    # Subcontas de terceiros (2211xxxx fornecedores, 2711xxxx fornecedores de investimentos)
    nif: Mapped[str | None] = mapped_column(String(9), index=True)
    criada_em: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class Lancamento(Base):
    """
    Lançamento contabilístico (partidas dobradas): a soma dos débitos é sempre igual
    à soma dos créditos. Um lançamento nunca é apagado nem alterado: corrige-se com
    um estorno (lançamento simétrico), como exige a boa prática e o SAF-T.
    """

    __tablename__ = "lancamentos"
    __table_args__ = (
        UniqueConstraint("empresa_id", "diario", "ano", "numero", name="uq_lancamento_numero"),
        Index("ix_lancamentos_empresa_data", "empresa_id", "data"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    empresa_id: Mapped[int] = mapped_column(ForeignKey("empresas.id"), index=True)
    diario: Mapped[str] = mapped_column(String(4))     # COM, VEN, BAN, CX, OD, APU
    ano: Mapped[int] = mapped_column()
    numero: Mapped[int] = mapped_column()               # sequencial por diário e ano
    data: Mapped[date] = mapped_column(Date)
    descricao: Mapped[str] = mapped_column(String(300))
    documento: Mapped[str | None] = mapped_column(String(80))
    # manual | fatura | pagamento | estorno | apuramento | assistente
    origem: Mapped[str] = mapped_column(String(12), default="manual")
    fatura_id: Mapped[str | None] = mapped_column(ForeignKey("faturas.id", ondelete="SET NULL"), index=True)
    venda_id: Mapped[int | None] = mapped_column(ForeignKey("faturas_venda.id", ondelete="SET NULL"), index=True)
    estado: Mapped[str] = mapped_column(String(10), default=LANCAMENTO_ATIVO)
    estorno_de_id: Mapped[int | None] = mapped_column(ForeignKey("lancamentos.id"))
    notas: Mapped[list | None] = mapped_column(JSON)    # explicações da classificação (art. 21.º CIVA, etc.)
    criado_por_id: Mapped[int | None] = mapped_column(ForeignKey("utilizadores.id", ondelete="SET NULL"))
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    linhas: Mapped[list["LinhaLancamento"]] = relationship(
        back_populates="lancamento", cascade="all, delete-orphan", order_by="LinhaLancamento.ordem"
    )
    criado_por: Mapped[Utilizador | None] = relationship()


class LinhaLancamento(Base):
    __tablename__ = "linhas_lancamento"

    id: Mapped[int] = mapped_column(primary_key=True)
    lancamento_id: Mapped[int] = mapped_column(ForeignKey("lancamentos.id", ondelete="CASCADE"), index=True)
    lancamento: Mapped[Lancamento] = relationship(back_populates="linhas")
    ordem: Mapped[int] = mapped_column(default=0)
    conta_id: Mapped[int] = mapped_column(ForeignKey("contas.id"), index=True)
    conta: Mapped[Conta] = relationship()
    debito: Mapped[float] = mapped_column(Float, default=0.0)
    credito: Mapped[float] = mapped_column(Float, default=0.0)
    descricao: Mapped[str | None] = mapped_column(String(200))
    # Para o apuramento do IVA: base | iva | iva_nd (IVA não dedutível levado a gasto)
    tipo_iva: Mapped[str | None] = mapped_column(String(8))
    taxa_iva: Mapped[float | None] = mapped_column(Float)


class RegraFornecedor(Base):
    """
    O que a aplicação aprendeu sobre cada fornecedor: em que conta se lança e como se
    trata o IVA. Criada (ou corrigida) sempre que alguém contabiliza uma fatura dele.
    """

    __tablename__ = "regras_fornecedor"
    __table_args__ = (UniqueConstraint("empresa_id", "nif", name="uq_regra_empresa_nif"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    empresa_id: Mapped[int] = mapped_column(ForeignKey("empresas.id"), index=True)
    nif: Mapped[str] = mapped_column(String(9))
    conta_codigo: Mapped[str] = mapped_column(String(20))
    perfil_iva: Mapped[str] = mapped_column(String(20))
    origem: Mapped[str] = mapped_column(String(12), default="utilizador")  # utilizador | ia | palavras
    usos: Mapped[int] = mapped_column(default=0)
    atualizada_em: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


# ---------- Bancos (extratos e reconciliação) ----------

MOVIMENTO_PENDENTE = "pendente"
MOVIMENTO_RECONCILIADO = "reconciliado"
MOVIMENTO_IGNORADO = "ignorado"  # já refletido na contabilidade por outra via


class ExtratoBancario(Base):
    """Um ficheiro de extrato importado (CSV, Excel ou OFX) de uma conta bancária."""

    __tablename__ = "extratos_bancarios"

    id: Mapped[int] = mapped_column(primary_key=True)
    empresa_id: Mapped[int] = mapped_column(ForeignKey("empresas.id"), index=True)
    conta_codigo: Mapped[str] = mapped_column(String(20))
    nome_ficheiro: Mapped[str] = mapped_column(String(200))
    formato: Mapped[str] = mapped_column(String(10))           # csv | xlsx | ofx
    data_inicio: Mapped[date | None] = mapped_column(Date)
    data_fim: Mapped[date | None] = mapped_column(Date)
    saldo_final: Mapped[float | None] = mapped_column(Float)   # do ficheiro ou indicado na importação
    importado_em: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    importado_por_id: Mapped[int | None] = mapped_column(ForeignKey("utilizadores.id", ondelete="SET NULL"))


class MovimentoBancario(Base):
    """
    Uma linha do extrato. Montante com sinal: positivo entra na conta, negativo sai.
    Fica reconciliado quando está ligado à linha do lançamento que o reflete na contabilidade.
    """

    __tablename__ = "movimentos_bancarios"
    __table_args__ = (
        UniqueConstraint("empresa_id", "conta_codigo", "referencia", name="uq_movimento_referencia"),
        Index("ix_movimentos_empresa_conta_estado", "empresa_id", "conta_codigo", "estado"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    empresa_id: Mapped[int] = mapped_column(ForeignKey("empresas.id"), index=True)
    extrato_id: Mapped[int] = mapped_column(ForeignKey("extratos_bancarios.id", ondelete="CASCADE"), index=True)
    conta_codigo: Mapped[str] = mapped_column(String(20))
    data: Mapped[date] = mapped_column(Date)
    descricao: Mapped[str] = mapped_column(String(300))
    montante: Mapped[float] = mapped_column(Float)
    saldo: Mapped[float | None] = mapped_column(Float)
    # Identifica o movimento entre importações (FITID do OFX ou um hash): evita duplicados
    referencia: Mapped[str] = mapped_column(String(64))
    estado: Mapped[str] = mapped_column(String(12), default=MOVIMENTO_PENDENTE)
    linha_id: Mapped[int | None] = mapped_column(
        ForeignKey("linhas_lancamento.id", ondelete="SET NULL"), unique=True, index=True
    )
    linha: Mapped[LinhaLancamento | None] = relationship()
    reconciliado_em: Mapped[datetime | None] = mapped_column(DateTime)
    reconciliado_por_id: Mapped[int | None] = mapped_column(ForeignKey("utilizadores.id", ondelete="SET NULL"))


class RegraBancaria(Base):
    """Conta onde se lança um tipo de movimento do banco, aprendida com o utilizador (ex.: comissões)."""

    __tablename__ = "regras_bancarias"
    __table_args__ = (UniqueConstraint("empresa_id", "padrao", "sinal", name="uq_regra_bancaria"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    empresa_id: Mapped[int] = mapped_column(ForeignKey("empresas.id"), index=True)
    padrao: Mapped[str] = mapped_column(String(80))      # descrição normalizada, sem números
    sinal: Mapped[int] = mapped_column()                  # 1 entradas, -1 saídas
    conta_codigo: Mapped[str] = mapped_column(String(20))
    usos: Mapped[int] = mapped_column(default=0)
    atualizada_em: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


# ---------- Vendas (faturas de clientes) ----------

VENDA_NORMAL = "normal"
VENDA_ANULADA = "anulada"
VENDA_FATURADA = "faturada"  # documento já incluído noutra fatura (estado F no SAF-T): não se contabiliza

CONSUMIDOR_FINAL = "999999990"


class FaturaVenda(Base):
    """
    Fatura emitida a um cliente. Não é emitida aqui (emitir faturas exige um programa
    certificado pela AT): vem do SAF-T de faturação desse programa ou é registada à mão.
    Valores sempre positivos; as notas de crédito (NC) invertem-se ao contabilizar.
    """

    __tablename__ = "faturas_venda"
    __table_args__ = (UniqueConstraint("empresa_id", "numero", name="uq_venda_empresa_numero"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    empresa_id: Mapped[int] = mapped_column(ForeignKey("empresas.id"), index=True)
    numero: Mapped[str] = mapped_column(String(60))           # ex.: "FT A/123" (já inclui o tipo)
    tipo: Mapped[str] = mapped_column(String(2))              # FT FS FR NC ND
    atcud: Mapped[str | None] = mapped_column(String(100))
    data: Mapped[date] = mapped_column(Date, index=True)
    data_vencimento: Mapped[date | None] = mapped_column(Date)
    cliente_nif: Mapped[str] = mapped_column(String(20), default=CONSUMIDOR_FINAL)
    cliente_nome: Mapped[str] = mapped_column(String(200), default="Consumidor final")
    # [{taxa, base, valor, conta, isencao}]: uma linha por taxa de IVA e conta de rendimento
    linhas: Mapped[list] = mapped_column(JSON)
    valor_sem_iva: Mapped[float] = mapped_column(Float)
    total_iva: Mapped[float] = mapped_column(Float)
    total: Mapped[float] = mapped_column(Float)
    estado: Mapped[str] = mapped_column(String(10), default=VENDA_NORMAL)
    valor_recebido: Mapped[float] = mapped_column(Float, default=0.0)
    recebida: Mapped[bool] = mapped_column(default=False, index=True)
    meio_pagamento: Mapped[str | None] = mapped_column(String(2))  # NU numerário, TB transferência, CC cartão...
    origem: Mapped[str] = mapped_column(String(10), default="manual")  # saft | manual
    criada_em: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    criada_por_id: Mapped[int | None] = mapped_column(ForeignKey("utilizadores.id", ondelete="SET NULL"))


# ---------- Inventários ----------

CATEGORIA_MERCADORIAS = "M"   # conta 321, compras 311, CMVMC 611
CATEGORIA_MATERIAS = "P"      # conta 331, compras 312, CMVMC 612


class Inventario(Base):
    """
    Contagem física das existências numa data (sistema de inventário periódico).
    Serve de inventário final de um período e de inicial do seguinte; o apuramento
    gera o lançamento do CMVMC (compras -> inventários -> custo das vendas).
    """

    __tablename__ = "inventarios"
    __table_args__ = (UniqueConstraint("empresa_id", "data", name="uq_inventario_empresa_data"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    empresa_id: Mapped[int] = mapped_column(ForeignKey("empresas.id"), index=True)
    data: Mapped[date] = mapped_column(Date)
    descricao: Mapped[str] = mapped_column(String(200), default="Inventário")
    inicial: Mapped[bool] = mapped_column(default=False)  # saldo de abertura (sem apuramento de CMVMC)
    lancamento_id: Mapped[int | None] = mapped_column(ForeignKey("lancamentos.id", ondelete="SET NULL"))
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    criado_por_id: Mapped[int | None] = mapped_column(ForeignKey("utilizadores.id", ondelete="SET NULL"))

    linhas: Mapped[list["LinhaInventario"]] = relationship(
        back_populates="inventario", cascade="all, delete-orphan", order_by="LinhaInventario.id"
    )


class LinhaInventario(Base):
    __tablename__ = "linhas_inventario"

    id: Mapped[int] = mapped_column(primary_key=True)
    inventario_id: Mapped[int] = mapped_column(ForeignKey("inventarios.id", ondelete="CASCADE"), index=True)
    inventario: Mapped[Inventario] = relationship(back_populates="linhas")
    codigo: Mapped[str] = mapped_column(String(60))
    descricao: Mapped[str] = mapped_column(String(200))
    categoria: Mapped[str] = mapped_column(String(1), default=CATEGORIA_MERCADORIAS)
    unidade: Mapped[str] = mapped_column(String(10), default="UN")
    quantidade: Mapped[float] = mapped_column(Float)
    custo_unitario: Mapped[float] = mapped_column(Float)
    valor: Mapped[float] = mapped_column(Float)


# ---------- Ativos fixos e depreciações ----------

ATIVO_EM_USO = "em_uso"
ATIVO_ABATIDO = "abatido"
ATIVO_ALIENADO = "alienado"


class AtivoFixo(Base):
    """Ativo fixo tangível ou intangível, depreciado por quotas constantes (em duodécimos)."""

    __tablename__ = "ativos_fixos"

    id: Mapped[int] = mapped_column(primary_key=True)
    empresa_id: Mapped[int] = mapped_column(ForeignKey("empresas.id"), index=True)
    codigo: Mapped[str] = mapped_column(String(20))               # AF0001, AF0002...
    descricao: Mapped[str] = mapped_column(String(200))
    conta_ativo: Mapped[str] = mapped_column(String(20))          # 432..437, 443
    conta_depreciacao: Mapped[str] = mapped_column(String(20))    # 438x / 4483
    conta_gasto: Mapped[str] = mapped_column(String(20))          # 642x / 6433
    data_aquisicao: Mapped[date] = mapped_column(Date)
    data_inicio: Mapped[date] = mapped_column(Date)               # entrada em funcionamento
    valor_aquisicao: Mapped[float] = mapped_column(Float)
    valor_residual: Mapped[float] = mapped_column(Float, default=0.0)
    taxa: Mapped[float] = mapped_column(Float)                     # % ao ano
    estado: Mapped[str] = mapped_column(String(10), default=ATIVO_EM_USO)
    data_saida: Mapped[date | None] = mapped_column(Date)
    fatura_id: Mapped[str | None] = mapped_column(ForeignKey("faturas.id", ondelete="SET NULL"))
    lancamento_saida_id: Mapped[int | None] = mapped_column(ForeignKey("lancamentos.id", ondelete="SET NULL"))
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    depreciacoes: Mapped[list["DepreciacaoAtivo"]] = relationship(
        back_populates="ativo", cascade="all, delete-orphan", order_by="DepreciacaoAtivo.ate"
    )


class DepreciacaoAtivo(Base):
    """Depreciação de um ativo num intervalo de meses, lançada num lançamento (estornável)."""

    __tablename__ = "depreciacoes_ativos"

    id: Mapped[int] = mapped_column(primary_key=True)
    ativo_id: Mapped[int] = mapped_column(ForeignKey("ativos_fixos.id", ondelete="CASCADE"), index=True)
    ativo: Mapped[AtivoFixo] = relationship(back_populates="depreciacoes")
    de: Mapped[date] = mapped_column(Date)
    ate: Mapped[date] = mapped_column(Date)
    meses: Mapped[int] = mapped_column()
    valor: Mapped[float] = mapped_column(Float)
    lancamento_id: Mapped[int] = mapped_column(ForeignKey("lancamentos.id", ondelete="CASCADE"), index=True)
