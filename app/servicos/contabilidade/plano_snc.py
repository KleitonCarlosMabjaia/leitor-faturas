"""
Plano de contas base, segundo o código de contas do SNC (Portaria n.º 218/2015), e
códigos da taxonomia S (Portaria n.º 302/2016, Anexo II) para o SAF-T (PT) da contabilidade.

Não é o código de contas completo: tem as contas que uma pequena empresa usa no
dia a dia, com as subcontas de IVA divididas por taxa para preencher a declaração
periódica sem contas à mão. A empresa pode acrescentar contas suas.

Regras que o plano respeita por causa do SAF-T:
- as contas de 2 dígitos (1.º grau) são sempre agregadoras: os movimentos vão para
  subcontas (ex.: 121 em vez de 12);
- cada conta de movimento tem um código de taxonomia (as subcontas herdam o da
  conta-mãe, como manda a Portaria: 243211 fica com o da 2432, 22110003 com o da 2211).

As subcontas de cada fornecedor (2211xxxx e 2711xxxx) são criadas automaticamente
na primeira fatura desse fornecedor.
"""

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.modelos.tabelas import Conta, LinhaLancamento

PLANO: list[tuple[str, str]] = [
    ("1", "Meios financeiros líquidos"),
    ("11", "Caixa"),
    ("111", "Caixa"),
    ("12", "Depósitos à ordem"),
    ("121", "Depósitos à ordem - banco principal"),
    ("13", "Outros depósitos bancários"),
    ("131", "Depósitos a prazo"),

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
    ("24334", "IVA liquidado - aquisições intracomunitárias de bens"),
    ("24335", "IVA liquidado - serviços adquiridos noutros Estados-membros"),
    ("2434", "IVA regularizações"),
    ("2435", "IVA apuramento"),
    ("2436", "IVA a pagar"),
    ("2437", "IVA a recuperar"),
    ("245", "Contribuições para a Segurança Social"),
    ("25", "Financiamentos obtidos"),
    ("251", "Instituições de crédito e sociedades financeiras"),
    ("2511", "Empréstimos bancários"),
    ("25111", "Empréstimos bancários - corrente"),
    ("25112", "Empréstimos bancários - não corrente"),
    ("2512", "Descobertos bancários"),
    ("25121", "Descobertos bancários - corrente"),
    ("26", "Acionistas/sócios"),
    ("268", "Outras operações com sócios"),
    ("2681", "Outras operações com sócios - corrente"),
    ("2682", "Outras operações com sócios - não corrente"),
    ("27", "Outras contas a receber e a pagar"),
    ("271", "Fornecedores de investimentos"),
    ("2711", "Fornecedores de investimentos c/c"),
    ("272", "Devedores e credores por acréscimos"),
    ("2721", "Devedores por acréscimos de rendimentos"),
    ("2722", "Credores por acréscimos de gastos"),
    ("278", "Outros devedores e credores"),
    ("2781", "Outros devedores e credores - corrente"),
    ("2782", "Outros devedores e credores - não corrente"),
    ("28", "Diferimentos"),
    ("281", "Gastos a reconhecer"),
    ("282", "Rendimentos a reconhecer"),

    ("3", "Inventários e ativos biológicos"),
    ("31", "Compras"),
    ("311", "Compras de mercadorias"),
    ("312", "Compras de matérias-primas, subsidiárias e de consumo"),
    ("317", "Devoluções de compras"),
    ("3171", "Devoluções de compras - mercadorias"),
    ("3172", "Devoluções de compras - matérias-primas, subsidiárias e de consumo"),
    ("318", "Descontos e abatimentos em compras"),
    ("3181", "Descontos e abatimentos em compras - mercadorias"),
    ("3182", "Descontos e abatimentos em compras - matérias-primas, subsidiárias e de consumo"),
    ("32", "Mercadorias"),
    ("321", "Mercadorias"),
    ("33", "Matérias-primas, subsidiárias e de consumo"),
    ("331", "Matérias-primas"),
    ("332", "Matérias subsidiárias"),
    ("334", "Materiais diversos"),

    ("4", "Investimentos"),
    ("43", "Ativos fixos tangíveis"),
    ("431", "Terrenos e recursos naturais"),
    ("432", "Edifícios e outras construções"),
    ("433", "Equipamento básico"),
    ("434", "Equipamento de transporte"),
    ("435", "Equipamento administrativo"),
    ("437", "Outros ativos fixos tangíveis"),
    ("438", "Depreciações acumuladas"),
    ("4382", "Depreciações acumuladas - edifícios e outras construções"),
    ("4383", "Depreciações acumuladas - equipamento básico"),
    ("4384", "Depreciações acumuladas - equipamento de transporte"),
    ("4385", "Depreciações acumuladas - equipamento administrativo"),
    ("4387", "Depreciações acumuladas - outros ativos fixos tangíveis"),
    ("44", "Ativos intangíveis"),
    ("443", "Programas de computador"),
    ("448", "Amortizações acumuladas"),
    ("4483", "Amortizações acumuladas - programas de computador"),

    ("5", "Capital, reservas e resultados transitados"),
    ("51", "Capital subscrito"),
    ("511", "Capital subscrito"),
    ("55", "Reservas"),
    ("551", "Reservas legais"),
    ("552", "Outras reservas"),
    ("56", "Resultados transitados"),
    ("561", "Resultados transitados"),

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
    ("6422", "Depreciações - edifícios e outras construções"),
    ("6423", "Depreciações - equipamento básico"),
    ("6424", "Depreciações - equipamento de transporte"),
    ("6425", "Depreciações - equipamento administrativo"),
    ("6427", "Depreciações - outros ativos fixos tangíveis"),
    ("643", "Amortizações - Ativos intangíveis"),
    ("6433", "Amortizações - programas de computador"),
    ("68", "Outros gastos"),
    ("687", "Gastos em investimentos não financeiros"),
    ("6871", "Alienações de investimentos não financeiros"),
    ("6873", "Abates de investimentos não financeiros"),
    ("681", "Impostos"),
    ("6811", "Impostos diretos"),
    ("6812", "Impostos indiretos"),
    ("6813", "Taxas"),
    ("688", "Outros gastos"),
    ("6881", "Correções relativas a períodos anteriores"),
    ("6882", "Donativos"),
    ("6883", "Quotizações"),
    ("6888", "Outros não especificados"),
    ("69", "Gastos de financiamento"),
    ("691", "Juros suportados"),
    ("6911", "Juros de financiamentos obtidos"),
    ("6918", "Outros juros"),
    ("698", "Outros gastos de financiamento"),
    ("6981", "Relativos a financiamentos obtidos"),
    ("6988", "Outros gastos de financiamento"),

    ("7", "Rendimentos"),
    ("71", "Vendas"),
    ("711", "Vendas de mercadorias"),
    ("712", "Vendas de produtos acabados"),
    ("72", "Prestações de serviços"),
    ("721", "Prestações de serviços - atividade principal"),
    ("78", "Outros rendimentos"),
    ("782", "Descontos de pronto pagamento obtidos"),
    ("787", "Rendimentos em investimentos não financeiros"),
    ("7871", "Alienações de investimentos não financeiros"),
    ("788", "Outros"),
    ("7888", "Outros rendimentos não especificados"),
    ("79", "Juros, dividendos e outros rendimentos similares"),
    ("791", "Juros obtidos"),
    ("7911", "Juros de depósitos"),

    ("8", "Resultados"),
    ("81", "Resultado líquido do período"),
    ("811", "Resultado antes de impostos"),
    ("812", "Imposto sobre o rendimento do período"),
    ("8121", "Imposto estimado para o período"),
    ("818", "Resultado líquido"),
]

# Código de taxonomia S (Portaria n.º 302/2016, Anexo II) das contas do plano.
# As subcontas criadas pela empresa ou automaticamente herdam o código da conta-mãe
# mais próxima (ver taxonomia()). 2711: as subcontas por fornecedor são correntes (27111).
TAXONOMIA: dict[str, int] = {
    "11": 1, "12": 2, "13": 3,
    "2111": 10, "2211": 37, "225": 50, "2311": 53, "2312": 54,
    "241": 71, "242": 72, "2431": 73, "2432": 74, "2433": 75, "2434": 76, "2435": 77, "2436": 78, "2437": 79,
    "245": 83, "2711": 124, "2721": 130, "2722": 131, "2781": 138, "2782": 139,
    "25111": 86, "25112": 87, "25121": 88, "2681": 113, "2682": 114, "281": 146, "282": 147,
    "311": 156, "312": 157, "3171": 159, "3172": 160, "3181": 162, "3182": 163, "321": 165,
    "331": 171, "332": 172, "334": 174,
    "431": 268, "432": 269, "433": 270, "434": 271, "435": 272, "437": 274,
    "4382": 276, "4383": 277, "4384": 278, "4385": 279, "4387": 281, "443": 291, "4483": 296,
    "51": 331, "551": 336, "552": 337, "56": 338,
    "611": 353, "612": 354, "621": 356,
    "6221": 357, "6222": 358, "6223": 359, "6224": 360, "6225": 361, "6226": 362, "6228": 363,
    "6231": 364, "6232": 365, "6233": 366, "6234": 367, "6238": 368,
    "6241": 369, "6242": 370, "6243": 371, "6248": 372,
    "6251": 373, "6252": 374, "6253": 375, "6258": 376,
    "6261": 377, "6262": 378, "6263": 379, "6264": 380, "6265": 381, "6266": 382, "6267": 383, "6268": 384,
    "631": 385, "632": 386, "635": 390, "636": 391, "638": 393,
    "6422": 398, "6423": 399, "6424": 400, "6425": 401, "6427": 403, "6433": 406,
    "6811": 471, "6812": 472, "6813": 473, "6871": 487, "6873": 489, "6881": 492, "6882": 493, "6883": 494, "6888": 499,
    "6911": 500, "6918": 501, "6981": 504, "6988": 505,
    "711": 506, "712": 507, "721": 513, "782": 609, "7871": 620, "7888": 630, "7911": 631,
    "811": 643, "8121": 644, "818": 646,
}

# Contas que nunca recebem lançamentos diretos: os movimentos vão para as subcontas de cada terceiro
AGREGADORAS_TERCEIROS = {"2111", "2211", "2711"}

# Planos criados antes desta versão tinham estas contas como contas de movimento. Se já tiverem
# lançamentos, os movimentos passam para a subconta indicada (ver sincronizar_plano).
SUBCONTA_POR_OMISSAO = {
    "11": "111", "12": "121", "13": "131", "32": "321", "33": "331", "51": "511", "56": "561",
    "278": "2781", "317": "3172", "318": "3182", "642": "6427", "681": "6812", "688": "6888",
    "691": "6911", "698": "6988", "78": "7888", "79": "7911", "812": "8121",
}

NOMES_CLASSES = {codigo: nome for codigo, nome in PLANO if len(codigo) == 1}


def classe(codigo: str) -> str:
    return codigo[:1]


def taxonomia(codigo: str) -> int | None:
    """Código de taxonomia S da conta: o da própria conta ou o da conta-mãe mais próxima."""
    for n in range(len(codigo), 1, -1):
        if codigo[:n] in TAXONOMIA:
            return TAXONOMIA[codigo[:n]]
    return None


def _e_agregadora(codigo: str, codigos: set[str]) -> bool:
    return codigo in AGREGADORAS_TERCEIROS or any(c != codigo and c.startswith(codigo) for c in codigos)


def garantir_plano(db: Session, empresa_id: int) -> None:
    """Cria o plano de contas base da empresa ou acrescenta o que lhe falta (idempotente)."""
    existentes = set(db.scalars(select(Conta.codigo).where(Conta.empresa_id == empresa_id)))
    if not existentes:
        codigos = {codigo for codigo, _ in PLANO}
        for codigo, nome in PLANO:
            db.add(Conta(empresa_id=empresa_id, codigo=codigo, nome=nome,
                         movimento=not _e_agregadora(codigo, codigos)))
        db.flush()
    elif any(codigo not in existentes for codigo, _ in PLANO):
        sincronizar_plano(db, empresa_id)


def sincronizar_plano(db: Session, empresa_id: int) -> None:
    """
    Atualiza o plano de uma empresa criado com uma versão anterior: acrescenta as contas
    novas e passa a agregadoras as contas que ganharam subcontas. Se uma dessas contas já
    tinha movimentos, os movimentos passam para a subconta por omissão (ex.: 12 -> 121),
    para o plano continuar a cumprir o SAF-T. Os valores e as datas não mudam.
    """
    contas = contas_por_codigo(db, empresa_id)
    for codigo, nome in PLANO:
        if codigo not in contas:
            conta = Conta(empresa_id=empresa_id, codigo=codigo, nome=nome,
                          movimento=codigo not in AGREGADORAS_TERCEIROS)
            db.add(conta)
            contas[codigo] = conta
    db.flush()

    codigos = set(contas)
    for codigo, conta in contas.items():
        if not conta.movimento or not _e_agregadora(codigo, codigos):
            continue
        destino = contas.get(SUBCONTA_POR_OMISSAO.get(codigo, ""))
        tem_movimentos = db.scalar(select(func.count(LinhaLancamento.id)).where(LinhaLancamento.conta_id == conta.id))
        if tem_movimentos:
            if destino is None:
                continue  # conta criada pela empresa: fica como está (o SAF-T avisa)
            db.execute(update(LinhaLancamento).where(LinhaLancamento.conta_id == conta.id)
                       .values(conta_id=destino.id))
        conta.movimento = False
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
