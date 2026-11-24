"""
Tabelas da base de dados (SQLAlchemy 2.0, estilo com anotações de tipo).

Cada empresa cliente só vê os seus dados: faturas e fornecedores têm
empresa_id e todas as consultas filtram por ele (ver servicos/repositorio.py).

Os valores monetários são guardados como Float e arredondados a 2 casas nas
entradas. Em PostgreSQL usaria Numeric (decimal exato); o SQLite não tem
um tipo decimal verdadeiro, por isso aqui a diferença não existiria.
"""

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    event,
    false,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

ESTADO_POR_REVER = "por rever"
ESTADO_CONFIRMADA = "confirmada"

PAPEL_ADMIN = "admin"    # gere a equipa da empresa
PAPEL_MEMBRO = "membro"  # carrega e revê faturas

# Espaço territorial da sede (declaração periódica do IVA, local de obtenção dos rendimentos na DMR)
REGIAO_CONTINENTE = "continente"
REGIOES = (REGIAO_CONTINENTE, "acores", "madeira")


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
    # Número de identificação da Segurança Social da entidade empregadora (declaração de remunerações)
    niss: Mapped[str | None] = mapped_column(String(11))
    # Declarações à AT (ficheiros da declaração periódica do IVA e da DMR)
    servico_financas: Mapped[str | None] = mapped_column(String(4))     # código do serviço de finanças da sede
    nif_contabilista: Mapped[str | None] = mapped_column(String(9))     # contabilista certificado
    nif_representante: Mapped[str | None] = mapped_column(String(9))    # representante legal
    regiao_sede: Mapped[str] = mapped_column(String(10), default=REGIAO_CONTINENTE,
                                             server_default=REGIAO_CONTINENTE)

    # Subscrição (ver servicos/subscricoes.py). As empresas anteriores ficam no plano Contabilidade, ativas.
    plano: Mapped[str] = mapped_column(String(20), default="contabilidade", server_default="contabilidade")
    estado_subscricao: Mapped[str] = mapped_column(String(20), default="ativa", server_default="ativa")
    experimental_ate: Mapped[date | None] = mapped_column(Date)
    em_atraso_desde: Mapped[date | None] = mapped_column(Date)
    fim_periodo: Mapped[date | None] = mapped_column(Date)
    cancela_no_fim: Mapped[bool] = mapped_column(default=False, server_default=false())
    stripe_cliente_id: Mapped[str | None] = mapped_column(String(64), index=True)
    stripe_subscricao_id: Mapped[str | None] = mapped_column(String(64))

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

    # Verificação em dois passos (TOTP, RFC 6238): segredo partilhado com a app autenticadora,
    # último passo de 30 s aceite (impede reutilizar o mesmo código) e códigos de recuperação
    # de uso único, guardados só como hash SHA-256.
    totp_segredo: Mapped[str | None] = mapped_column(String(64))
    totp_ativo: Mapped[bool] = mapped_column(default=False, server_default=false())
    totp_ultimo_passo: Mapped[int | None] = mapped_column()
    codigos_recuperacao: Mapped[list | None] = mapped_column(JSON)
    # Alertas de prazos fiscais por email (pode desligar na página da conta)
    alertas_email: Mapped[bool] = mapped_column(default=True, server_default=true())


class AcessoEmpresa(Base):
    """
    A que empresas cada pessoa tem acesso e com que papel (gabinetes de contabilidade: uma conta,
    várias empresas). Utilizador.empresa_id e Utilizador.papel passam a ser os da empresa escolhida
    na sessão, aplicados a cada pedido depois de confirmado este acesso (ver servicos/empresas.py).
    """

    __tablename__ = "acessos_empresa"
    __table_args__ = (UniqueConstraint("utilizador_id", "empresa_id", name="uq_acesso_utilizador_empresa"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    utilizador_id: Mapped[int] = mapped_column(ForeignKey("utilizadores.id", ondelete="CASCADE"), index=True)
    empresa_id: Mapped[int] = mapped_column(ForeignKey("empresas.id", ondelete="CASCADE"), index=True)
    papel: Mapped[str] = mapped_column(String(10), default=PAPEL_MEMBRO)
    ativo: Mapped[bool] = mapped_column(default=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    utilizador: Mapped["Utilizador"] = relationship()
    empresa: Mapped[Empresa] = relationship()


@event.listens_for(Utilizador, "after_insert")
def _acesso_da_primeira_empresa(_mapper, ligacao, utilizador) -> None:
    """Quem é criado numa empresa (convite, registo, administração) fica logo com acesso a ela."""
    ligacao.execute(AcessoEmpresa.__table__.insert().values(
        utilizador_id=utilizador.id, empresa_id=utilizador.empresa_id, papel=utilizador.papel or PAPEL_MEMBRO,
        ativo=True if utilizador.ativo is None else utilizador.ativo, criado_em=datetime.now(),
    ))


class Sessao(Base):
    """Sessão iniciada. Guarda-se só o hash do token: quem ler a base de dados não consegue entrar."""

    __tablename__ = "sessoes"

    hash_token: Mapped[str] = mapped_column(String(64), primary_key=True)
    utilizador_id: Mapped[int] = mapped_column(ForeignKey("utilizadores.id", ondelete="CASCADE"), index=True)
    utilizador: Mapped[Utilizador] = relationship()
    criada_em: Mapped[datetime] = mapped_column(DateTime)
    expira_em: Mapped[datetime] = mapped_column(DateTime)
    # Empresa escolhida nesta sessão (cada separador/dispositivo pode estar numa empresa diferente)
    empresa_id: Mapped[int | None] = mapped_column(ForeignKey("empresas.id", ondelete="CASCADE"))
    # Para a lista "sessões abertas" na página da conta
    ip: Mapped[str | None] = mapped_column(String(45))
    agente: Mapped[str | None] = mapped_column(String(200))
    ultimo_uso: Mapped[datetime | None] = mapped_column(DateTime)


class RegistoAtividade(Base):
    """Quem fez o quê e quando (auditoria). Só se acrescenta: nunca se altera nem apaga pela aplicação."""

    __tablename__ = "registos_atividade"
    __table_args__ = (Index("ix_registos_empresa_quando", "empresa_id", "quando"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    empresa_id: Mapped[int] = mapped_column(ForeignKey("empresas.id", ondelete="CASCADE"))
    utilizador_id: Mapped[int | None] = mapped_column(ForeignKey("utilizadores.id", ondelete="SET NULL"))
    quando: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    acao: Mapped[str] = mapped_column(String(120))
    detalhe: Mapped[str | None] = mapped_column(String(300))
    ip: Mapped[str | None] = mapped_column(String(45))


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
    # Contabilidade analítica: a que centro de custo pertence este gasto ou rendimento (opcional)
    centro_custo_id: Mapped[int | None] = mapped_column(ForeignKey("centros_custo.id", ondelete="SET NULL"), index=True)
    centro_custo: Mapped["CentroCusto | None"] = relationship()


class CentroCusto(Base):
    """Centro de custo (loja, obra, departamento, projeto...) para a contabilidade analítica."""

    __tablename__ = "centros_custo"
    __table_args__ = (UniqueConstraint("empresa_id", "codigo", name="uq_centro_empresa_codigo"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    empresa_id: Mapped[int] = mapped_column(ForeignKey("empresas.id", ondelete="CASCADE"), index=True)
    codigo: Mapped[str] = mapped_column(String(20))
    nome: Mapped[str] = mapped_column(String(120))
    ativo: Mapped[bool] = mapped_column(default=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


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
    # Último centro de custo escolhido para este fornecedor (sugerido nas próximas faturas)
    centro_custo_id: Mapped[int | None] = mapped_column(ForeignKey("centros_custo.id", ondelete="SET NULL"))
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


# ---------- Pessoal e processamento de salários ----------

FUNCIONARIO_PESSOAL = "pessoal"          # trabalhador por conta de outrem (632, 2312)
FUNCIONARIO_ORGAOS = "orgaos_sociais"    # gerente ou administrador (631, 2311)


class Funcionario(Base):
    """Trabalhador (ou membro dos órgãos sociais) com os dados de que o processamento precisa."""

    __tablename__ = "funcionarios"

    id: Mapped[int] = mapped_column(primary_key=True)
    empresa_id: Mapped[int] = mapped_column(ForeignKey("empresas.id"), index=True)
    numero: Mapped[int] = mapped_column()
    nome: Mapped[str] = mapped_column(String(200))
    nif: Mapped[str | None] = mapped_column(String(9))
    niss: Mapped[str | None] = mapped_column(String(11))
    data_nascimento: Mapped[date | None] = mapped_column(Date)  # obrigatória na declaração da Segurança Social
    tipo: Mapped[str] = mapped_column(String(15), default=FUNCIONARIO_PESSOAL)
    data_admissao: Mapped[date] = mapped_column(Date)
    data_saida: Mapped[date | None] = mapped_column(Date)
    salario_base: Mapped[float] = mapped_column(Float)              # mensal, ilíquido
    subsidio_alimentacao: Mapped[float] = mapped_column(Float, default=0.0)  # por dia útil
    alimentacao_cartao: Mapped[bool] = mapped_column(default=True)
    duodecimos: Mapped[bool] = mapped_column(default=False)        # subsídios de férias e de Natal em duodécimos
    taxa_irs: Mapped[float] = mapped_column(Float, default=0.0)     # % de retenção manual (sem situação IRS)
    # Retenção pelas tabelas da AT (continente): situação familiar e dependentes declarados pela pessoa.
    # Sem situação, usa-se a taxa manual (por exemplo, residentes nos Açores ou na Madeira).
    irs_situacao: Mapped[str | None] = mapped_column(String(15))
    irs_dependentes: Mapped[int] = mapped_column(default=0)
    irs_dependentes_deficiencia: Mapped[int] = mapped_column(default=0)
    irs_deficiencia: Mapped[bool] = mapped_column(default=False)
    irs_conjuge_deficiencia: Mapped[bool] = mapped_column(default=False)
    taxa_ss_trabalhador: Mapped[float] = mapped_column(Float, default=11.0)
    taxa_ss_entidade: Mapped[float] = mapped_column(Float, default=23.75)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class ProcessamentoSalarial(Base):
    """
    Processamento de um mês: o recibo de cada pessoa (guardado tal como foi calculado) e o
    lançamento dos gastos. Os pagamentos (salários, Segurança Social, IRS) são lançamentos à parte.
    """

    __tablename__ = "processamentos_salariais"
    __table_args__ = (UniqueConstraint("empresa_id", "ano", "mes", name="uq_processamento_empresa_mes"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    empresa_id: Mapped[int] = mapped_column(ForeignKey("empresas.id"), index=True)
    ano: Mapped[int] = mapped_column()
    mes: Mapped[int] = mapped_column()
    recibos: Mapped[list] = mapped_column(JSON)
    pagamentos: Mapped[dict] = mapped_column(JSON, default=dict)  # {tipo de pagamento: lancamento_id}
    lancamento_id: Mapped[int | None] = mapped_column(ForeignKey("lancamentos.id", ondelete="SET NULL"))
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    criado_por_id: Mapped[int | None] = mapped_column(ForeignKey("utilizadores.id", ondelete="SET NULL"))


# ---------- Diferimentos (gastos e rendimentos a reconhecer) ----------

class Diferimento(Base):
    """
    Gasto pago (ou rendimento faturado) que diz respeito a vários meses, ex.: seguro anual.
    O valor sai da conta de gasto/rendimento para 281/282 e volta mês a mês.
    """

    __tablename__ = "diferimentos"

    id: Mapped[int] = mapped_column(primary_key=True)
    empresa_id: Mapped[int] = mapped_column(ForeignKey("empresas.id"), index=True)
    tipo: Mapped[str] = mapped_column(String(10))                  # gasto | rendimento
    descricao: Mapped[str] = mapped_column(String(200))
    conta: Mapped[str] = mapped_column(String(20))                 # conta de gasto (6) ou de rendimento (7)
    valor: Mapped[float] = mapped_column(Float)
    inicio: Mapped[date] = mapped_column(Date)                     # 1.º dia do primeiro mês a reconhecer
    meses: Mapped[int] = mapped_column()
    lancamento_id: Mapped[int | None] = mapped_column(ForeignKey("lancamentos.id", ondelete="SET NULL"))
    fatura_id: Mapped[str | None] = mapped_column(ForeignKey("faturas.id", ondelete="SET NULL"), index=True)
    # [{"ate": "AAAA-MM-DD", "meses": n, "valor": v, "lancamento_id": id}]
    reconhecimentos: Mapped[list] = mapped_column(JSON, default=list)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


# ---------- Gestão: artigos, stock e orçamento ----------

ARTIGO_MERCADORIA = "M"   # entra no inventário de mercadorias (321)
ARTIGO_MATERIA = "P"      # matérias-primas, subsidiárias e de consumo (331)
ARTIGO_SERVICO = "S"      # sem stock

MOVIMENTO_ENTRADA = "entrada"
MOVIMENTO_SAIDA = "saida"
MOVIMENTO_AJUSTE = "ajuste"


class Artigo(Base):
    """Produto, matéria ou serviço que a empresa compra ou vende, com o stock e o custo médio."""

    __tablename__ = "artigos"
    __table_args__ = (UniqueConstraint("empresa_id", "codigo", name="uq_artigo_empresa_codigo"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    empresa_id: Mapped[int] = mapped_column(ForeignKey("empresas.id", ondelete="CASCADE"), index=True)
    codigo: Mapped[str] = mapped_column(String(60))
    nome: Mapped[str] = mapped_column(String(200))
    tipo: Mapped[str] = mapped_column(String(1), default=ARTIGO_MERCADORIA)
    unidade: Mapped[str] = mapped_column(String(10), default="UN")
    preco_venda: Mapped[float] = mapped_column(Float, default=0.0)     # sem IVA
    taxa_iva: Mapped[float] = mapped_column(Float, default=23.0)
    stock_minimo: Mapped[float] = mapped_column(Float, default=0.0)
    # Atualizados a cada movimento (custo médio ponderado)
    quantidade: Mapped[float] = mapped_column(Float, default=0.0)
    custo_medio: Mapped[float] = mapped_column(Float, default=0.0)
    ativo: Mapped[bool] = mapped_column(default=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class MovimentoStock(Base):
    """Entrada (compra), saída (venda ou consumo) ou acerto de contagem de um artigo."""

    __tablename__ = "movimentos_stock"

    id: Mapped[int] = mapped_column(primary_key=True)
    empresa_id: Mapped[int] = mapped_column(ForeignKey("empresas.id", ondelete="CASCADE"), index=True)
    artigo_id: Mapped[int] = mapped_column(ForeignKey("artigos.id", ondelete="CASCADE"), index=True)
    artigo: Mapped[Artigo] = relationship()
    data: Mapped[date] = mapped_column(Date, index=True)
    tipo: Mapped[str] = mapped_column(String(10))
    quantidade: Mapped[float] = mapped_column(Float)          # positiva nas entradas, negativa nas saídas
    custo_unitario: Mapped[float] = mapped_column(Float)
    valor: Mapped[float] = mapped_column(Float)               # quantidade x custo (com sinal)
    preco_unitario: Mapped[float | None] = mapped_column(Float)  # nas saídas por venda: preço sem IVA
    documento: Mapped[str | None] = mapped_column(String(80))
    fatura_id: Mapped[str | None] = mapped_column(ForeignKey("faturas.id", ondelete="SET NULL"), index=True)
    venda_id: Mapped[int | None] = mapped_column(ForeignKey("faturas_venda.id", ondelete="SET NULL"), index=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    criado_por_id: Mapped[int | None] = mapped_column(ForeignKey("utilizadores.id", ondelete="SET NULL"))


class LinhaOrcamento(Base):
    """Valor orçamentado por mês para uma rubrica de gestão (vendas, pessoal, FSE...)."""

    __tablename__ = "linhas_orcamento"
    __table_args__ = (UniqueConstraint("empresa_id", "ano", "rubrica", name="uq_orcamento_empresa_ano_rubrica"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    empresa_id: Mapped[int] = mapped_column(ForeignKey("empresas.id", ondelete="CASCADE"), index=True)
    ano: Mapped[int] = mapped_column()
    rubrica: Mapped[str] = mapped_column(String(30))
    valores: Mapped[list] = mapped_column(JSON)   # 12 valores, de janeiro a dezembro


# ---------- Integrações: API pública e open banking ----------

PERMISSAO_LEITURA = "leitura"
PERMISSAO_ESCRITA = "escrita"


class ChaveApi(Base):
    """
    Chave da API pública (/api/v1) de uma empresa, para ligar outros programas. Guarda-se só o
    hash SHA-256: a chave completa é mostrada uma única vez, quando é criada.
    """

    __tablename__ = "chaves_api"

    id: Mapped[int] = mapped_column(primary_key=True)
    empresa_id: Mapped[int] = mapped_column(ForeignKey("empresas.id", ondelete="CASCADE"), index=True)
    nome: Mapped[str] = mapped_column(String(80))
    prefixo: Mapped[str] = mapped_column(String(20))            # início da chave, para a reconhecer na lista
    hash_chave: Mapped[str] = mapped_column(String(64), unique=True)
    permissao: Mapped[str] = mapped_column(String(10), default=PERMISSAO_LEITURA)
    criada_por_id: Mapped[int | None] = mapped_column(ForeignKey("utilizadores.id", ondelete="SET NULL"))
    criada_em: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    ultimo_uso: Mapped[datetime | None] = mapped_column(DateTime)
    revogada_em: Mapped[datetime | None] = mapped_column(DateTime)


LIGACAO_PENDENTE = "pendente"            # à espera da autorização no site do banco
LIGACAO_ESCOLHER_CONTA = "escolher"      # autorizada, com várias contas: falta escolher uma
LIGACAO_ATIVA = "ativa"
LIGACAO_EXPIRADA = "expirada"            # o consentimento PSD2 terminou: é preciso renovar
LIGACAO_REVOGADA = "revogada"
LIGACAO_ERRO = "erro"


class LigacaoBancaria(Base):
    """
    Ligação direta a uma conta bancária (open banking, PSD2): os movimentos chegam sozinhos à
    reconciliação bancária, sem exportar extratos. Uma ligação por conta SNC (12x).
    """

    __tablename__ = "ligacoes_bancarias"

    id: Mapped[int] = mapped_column(primary_key=True)
    empresa_id: Mapped[int] = mapped_column(ForeignKey("empresas.id", ondelete="CASCADE"), index=True)
    conta_codigo: Mapped[str] = mapped_column(String(20))
    fornecedor: Mapped[str] = mapped_column(String(20))         # enablebanking | simulado
    banco: Mapped[str] = mapped_column(String(120))
    pais: Mapped[str] = mapped_column(String(2), default="PT")
    estado: Mapped[str] = mapped_column(String(10), default=LIGACAO_PENDENTE)
    # Parâmetro "state" do OAuth: liga o regresso do banco a este pedido (uso único)
    estado_oauth: Mapped[str | None] = mapped_column(String(64), unique=True)
    sessao_externa: Mapped[str | None] = mapped_column(String(100))
    conta_externa: Mapped[str | None] = mapped_column(String(100))
    iban: Mapped[str | None] = mapped_column(String(40))
    contas_disponiveis: Mapped[list | None] = mapped_column(JSON)   # [{uid, iban, nome, moeda}]
    valido_ate: Mapped[datetime | None] = mapped_column(DateTime)   # fim do consentimento no banco
    ultima_sincronizacao: Mapped[datetime | None] = mapped_column(DateTime)
    ultimo_erro: Mapped[str | None] = mapped_column(String(300))
    criada_por_id: Mapped[int | None] = mapped_column(ForeignKey("utilizadores.id", ondelete="SET NULL"))
    criada_em: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
