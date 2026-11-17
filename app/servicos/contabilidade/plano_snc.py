"""
Plano de contas base, segundo o código de contas do SNC (Portaria n.º 218/2015).

Não é o código de contas completo: tem as contas que uma pequena empresa usa no
dia a dia, com as subcontas de IVA divididas por taxa para preencher a declaração
periódica sem contas à mão. A empresa pode acrescentar contas suas.

As subcontas de cada fornecedor (2211xxxx e 2711xxxx) são criadas automaticamente
na primeira fatura desse fornecedor.
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modelos.tabelas import Conta

PLANO: list[tuple[str, str]] = [
    ("1", "Meios financeiros líquidos"),
    ("11", "Caixa"),
    ("12", "Depósitos à ordem"),
    ("13", "Outros depósitos bancários"),

    ("2", "Contas a receber e a pagar"),
    ("21", "Clientes"),
    ("211", "Clientes c/c"),
    ("2111", "Clientes gerais"),
    ("22", "Fornecedores"),
    ("221", "Fornecedores c/c"),
    ("2211", "Fornecedores gerais"),
    ("225", "Faturas em receção e conferência"),
    ("23", "Pessoal"),
    ("231", "Remunerações a pagar"),
    ("2311", "Aos órgãos sociais"),
    ("2312", "Ao pessoal"),
    ("24", "Estado e outros entes públicos"),
    ("241", "Imposto sobre o rendimento"),
    ("242", "Retenção de impostos sobre rendimentos"),
    ("2421", "Trabalho dependente"),
    ("2422", "Trabalho independente"),
    ("2428", "Outras retenções"),
    ("243", "Imposto sobre o valor acrescentado (IVA)"),
    ("2431", "IVA suportado"),
    ("2432", "IVA dedutível"),
    ("24321", "IVA dedutível - Existências"),
    ("243211", "IVA dedutível - Existências - taxa reduzida"),
    ("243212", "IVA dedutível - Existências - taxa intermédia"),
    ("243213", "IVA dedutível - Existências - taxa normal"),
    ("24322", "IVA dedutível - Ativos fixos tangíveis e intangíveis"),
    ("24323", "IVA dedutível - Outros bens e serviços"),
    ("2433", "IVA liquidado"),
    ("24331", "IVA liquidado - taxa reduzida"),
    ("24332", "IVA liquidado - taxa intermédia"),
    ("24333", "IVA liquidado - taxa normal"),
    ("2434", "IVA regularizações"),
    ("2435", "IVA apuramento"),
    ("2436", "IVA a pagar"),
    ("2437", "IVA a recuperar"),
    ("245", "Contribuições para a Segurança Social"),
    ("27", "Outras contas a receber e a pagar"),
    ("271", "Fornecedores de investimentos"),
    ("2711", "Fornecedores de investimentos c/c"),
    ("272", "Devedores e credores por acréscimos"),
    ("2721", "Devedores por acréscimos de rendimentos"),
    ("2722", "Credores por acréscimos de gastos"),
    ("278", "Outros devedores e credores"),

    ("3", "Inventários e ativos biológicos"),
    ("31", "Compras"),
    ("311", "Compras de mercadorias"),
    ("312", "Compras de matérias-primas, subsidiárias e de consumo"),
    ("317", "Devoluções de compras"),
    ("318", "Descontos e abatimentos em compras"),
    ("32", "Mercadorias"),
    ("33", "Matérias-primas, subsidiárias e de consumo"),

    ("4", "Investimentos"),
    ("43", "Ativos fixos tangíveis"),
    ("431", "Terrenos e recursos naturais"),
    ("432", "Edifícios e outras construções"),
    ("433", "Equipamento básico"),
    ("434", "Equipamento de transporte"),
    ("435", "Equipamento administrativo"),
    ("437", "Outros ativos fixos tangíveis"),
    ("438", "Depreciações acumuladas"),
    ("44", "Ativos intangíveis"),
    ("443", "Programas de computador"),

    ("5", "Capital, reservas e resultados transitados"),
    ("51", "Capital subscrito"),
    ("55", "Reservas"),
    ("551", "Reservas legais"),
    ("552", "Outras reservas"),
    ("56", "Resultados transitados"),

    ("6", "Gastos"),
    ("61", "Custo das mercadorias vendidas e das matérias consumidas"),
    ("611", "CMVMC - Mercadorias"),
    ("612", "CMVMC - Matérias-primas, subsidiárias e de consumo"),
    ("62", "Fornecimentos e serviços externos"),
    ("621", "Subcontratos"),
    ("622", "Serviços especializados"),
    ("6221", "Trabalhos especializados"),
    ("6222", "Publicidade e propaganda"),
    ("6223", "Vigilância e segurança"),
    ("6224", "Honorários"),
    ("6225", "Comissões"),
    ("6226", "Conservação e reparação"),
    ("6228", "Outros serviços especializados"),
    ("623", "Materiais"),
    ("6231", "Ferramentas e utensílios de desgaste rápido"),
    ("6232", "Livros e documentação técnica"),
    ("6233", "Material de escritório"),
    ("6234", "Artigos para oferta"),
    ("6238", "Outros materiais"),
    ("624", "Energia e fluidos"),
    ("6241", "Eletricidade"),
    ("6242", "Combustíveis"),
    ("6243", "Água"),
    ("6248", "Outros fluidos"),
    ("625", "Deslocações, estadas e transportes"),
    ("6251", "Deslocações e estadas"),
    ("6252", "Transportes de pessoal"),
    ("6253", "Transportes de mercadorias"),
    ("6258", "Outros transportes"),
    ("626", "Serviços diversos"),
    ("6261", "Rendas e alugueres"),
    ("6262", "Comunicação"),
    ("6263", "Seguros"),
    ("6264", "Royalties"),
    ("6265", "Contencioso e notariado"),
    ("6266", "Despesas de representação"),
    ("6267", "Limpeza, higiene e conforto"),
    ("6268", "Outros serviços"),
    ("63", "Gastos com o pessoal"),
    ("631", "Remunerações dos órgãos sociais"),
    ("632", "Remunerações do pessoal"),
    ("635", "Encargos sobre remunerações"),
    ("636", "Seguros de acidentes no trabalho"),
    ("638", "Outros gastos com o pessoal"),
    ("64", "Gastos de depreciação e de amortização"),
    ("642", "Depreciações - Ativos fixos tangíveis"),
    ("68", "Outros gastos"),
    ("681", "Impostos"),
    ("688", "Outros gastos"),
    ("69", "Gastos de financiamento"),
    ("691", "Juros suportados"),
    ("698", "Outros gastos de financiamento"),

    ("7", "Rendimentos"),
    ("71", "Vendas"),
    ("711", "Vendas de mercadorias"),
    ("712", "Vendas de produtos acabados"),
    ("72", "Prestações de serviços"),
    ("721", "Prestações de serviços - atividade principal"),
    ("78", "Outros rendimentos"),
    ("79", "Juros, dividendos e outros rendimentos similares"),

    ("8", "Resultados"),
    ("81", "Resultado líquido do período"),
    ("811", "Resultado antes de impostos"),
    ("812", "Imposto sobre o rendimento do período"),
    ("818", "Resultado líquido"),
]

# Contas que nunca recebem lançamentos diretos: os movimentos vão para as subcontas de cada terceiro
AGREGADORAS_TERCEIROS = {"2111", "2211", "2711"}

NOMES_CLASSES = {codigo: nome for codigo, nome in PLANO if len(codigo) == 1}


def classe(codigo: str) -> str:
    return codigo[:1]


def _e_agregadora(codigo: str, codigos: set[str]) -> bool:
    return codigo in AGREGADORAS_TERCEIROS or any(c != codigo and c.startswith(codigo) for c in codigos)


def garantir_plano(db: Session, empresa_id: int) -> None:
    """Cria o plano de contas base da empresa, se ainda não existir (idempotente)."""
    existe = db.scalar(select(func.count(Conta.id)).where(Conta.empresa_id == empresa_id))
    if existe:
        return
    codigos = {codigo for codigo, _ in PLANO}
    for codigo, nome in PLANO:
        db.add(Conta(empresa_id=empresa_id, codigo=codigo, nome=nome, movimento=not _e_agregadora(codigo, codigos)))
    db.flush()


def contas_por_codigo(db: Session, empresa_id: int) -> dict[str, Conta]:
    return {c.codigo: c for c in db.scalars(select(Conta).where(Conta.empresa_id == empresa_id))}


def obter_conta(db: Session, empresa_id: int, codigo: str) -> Conta | None:
    return db.scalar(select(Conta).where(Conta.empresa_id == empresa_id, Conta.codigo == codigo))


def subconta_terceiro(db: Session, empresa_id: int, raiz: str, nif: str | None, nome: str) -> Conta:
    """
    Devolve a subconta do terceiro (ex.: 22110003 para o 3.º fornecedor), criando-a se preciso.
    Sem NIF, usa uma subconta "diversos" (0000), como na prática corrente.
    """
    if nif:
        conta = db.scalar(select(Conta).where(
            Conta.empresa_id == empresa_id, Conta.nif == nif, Conta.codigo.like(f"{raiz}%")
        ))
        if conta:
            if nome and conta.nome.startswith("NIF "):
                conta.nome = nome[:200]
            return conta
        existentes = db.scalars(select(Conta.codigo).where(
            Conta.empresa_id == empresa_id, Conta.codigo.like(f"{raiz}____")
        )).all()
        proximo = max((int(c[len(raiz):]) for c in existentes if c[len(raiz):].isdigit()), default=0) + 1
        conta = Conta(
            empresa_id=empresa_id, codigo=f"{raiz}{proximo:04d}", nome=(nome or f"NIF {nif}")[:200],
            movimento=True, nif=nif,
        )
    else:
        codigo = f"{raiz}0000"
        conta = obter_conta(db, empresa_id, codigo)
        if conta:
            return conta
        conta = Conta(empresa_id=empresa_id, codigo=codigo, nome="Diversos", movimento=True)
    db.add(conta)
    db.flush()
    return conta
