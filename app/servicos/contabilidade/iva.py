"""
Regras do IVA português usadas na contabilização.

- Escalões de taxa (Continente, Açores e Madeira) e as subcontas 2432x / 2433x de cada um.
- Perfis de dedutibilidade do art. 21.º do CIVA: a parte não dedutível é levada a gasto.
- Campos da declaração periódica (quadro 06) a que corresponde cada subconta.

Os valores percentuais estão aqui e não espalhados pelo código: quando a lei mudar,
muda-se este ficheiro.
"""

from dataclasses import dataclass

# Taxas em vigor: Continente 6/13/23, Madeira 4/12/22, Açores 4/9/16 (art. 18.º do CIVA)
ESCALOES = {
    "reduzida": {4.0, 5.0, 6.0},
    "intermedia": {9.0, 12.0, 13.0},
    "normal": {16.0, 22.0, 23.0},
}


def escalao(taxa: float) -> str:
    """Escalão da taxa: isenta, reduzida, intermedia ou normal (taxas desconhecidas vão para o mais próximo)."""
    if taxa <= 0:
        return "isenta"
    for nome, taxas in ESCALOES.items():
        if round(taxa, 1) in taxas:
            return nome
    return "reduzida" if taxa < 8 else "intermedia" if taxa < 15 else "normal"


# Subcontas de IVA dedutível: existências por taxa; investimentos e outros bens e serviços sem divisão
CONTA_IVA_EXISTENCIAS = {"reduzida": "243211", "intermedia": "243212", "normal": "243213"}
CONTA_IVA_INVESTIMENTO = "24322"
CONTA_IVA_OUTROS = "24323"
CONTA_IVA_LIQUIDADO = {"reduzida": "24331", "intermedia": "24332", "normal": "24333"}

# Declaração periódica, quadro 06
CAMPOS_DEDUTIVEL = {
    "24322": ("20", "Ativos fixos tangíveis e intangíveis"),
    "243211": ("21", "Existências à taxa reduzida"),
    "243213": ("22", "Existências à taxa normal"),
    "243212": ("23", "Existências à taxa intermédia"),
    "24323": ("24", "Outros bens e serviços"),
}
CAMPOS_LIQUIDADO = {  # conta -> (campo da base, campo do imposto, descrição)
    "24331": ("1", "2", "Taxa reduzida"),
    "24333": ("3", "4", "Taxa normal"),
    "24332": ("5", "6", "Taxa intermédia"),
}


@dataclass(frozen=True)
class PerfilIVA:
    chave: str
    nome: str
    dedutivel: float          # fração dedutível (0 a 1)
    explicacao: str
    tributacao_autonoma: str | None = None  # aviso sobre o art. 88.º do CIRC, quando se aplica


PERFIS: dict[str, PerfilIVA] = {p.chave: p for p in (
    PerfilIVA("total", "Dedutível a 100%", 1.0, "IVA dedutível na totalidade (art. 20.º do CIVA)."),
    PerfilIVA(
        "gasoleo_50", "Gasóleo, GPL ou gás natural (50%)", 0.5,
        "Gasóleo, GPL, gás natural e biocombustíveis: só 50% do IVA é dedutível (art. 21.º, n.º 1, al. b) "
        "do CIVA). É dedutível a 100% em viaturas pesadas, táxis, transporte público, máquinas agrícolas "
        "e veículos de aluguer com condutor.",
        "Encargos com viaturas ligeiras de passageiros estão sujeitos a tributação autónoma (art. 88.º do CIRC).",
    ),
    PerfilIVA(
        "nao_dedutivel", "Não dedutível", 0.0,
        "IVA não dedutível (art. 21.º do CIVA): o imposto é levado a gasto com a despesa. É o caso da "
        "gasolina, do tabaco e de bens e serviços que não são usados na atividade tributada.",
    ),
    PerfilIVA(
        "representacao", "Despesas de representação", 0.0,
        "Alimentação, bebidas e receções: IVA não dedutível (art. 21.º, n.º 1, al. d) do CIVA).",
        "Despesas de representação estão sujeitas a tributação autónoma de 10% (art. 88.º, n.º 7 do CIRC).",
    ),
    PerfilIVA(
        "viatura_ligeira", "Viatura ligeira de passageiros", 0.0,
        "Aquisição, aluguer, locação e manutenção de viaturas ligeiras de passageiros: IVA não dedutível "
        "(art. 21.º, n.º 1, al. a) do CIVA), salvo viaturas elétricas ou híbridas plug-in dentro dos "
        "limites legais.",
        "Encargos com viaturas ligeiras de passageiros estão sujeitos a tributação autónoma (art. 88.º do CIRC).",
    ),
    PerfilIVA(
        "deslocacoes", "Deslocações, alojamento e portagens", 0.0,
        "Transportes, viagens de negócios, portagens e alojamento: IVA não dedutível (art. 21.º, n.º 1, "
        "al. c) e d) do CIVA). Em congressos, feiras e exposições pode ser dedutível a 50% (n.º 2, al. d)).",
    ),
    PerfilIVA(
        "eventos_50", "Congressos e feiras (50%)", 0.5,
        "Alojamento, alimentação e transporte na participação em congressos, feiras, seminários e "
        "exposições: 50% dedutível (art. 21.º, n.º 2, al. d) do CIVA).",
    ),
)}


def conta_iva_dedutivel(conta_gasto: str, taxa: float) -> str:
    """Subconta de IVA dedutível onde vai o imposto desta linha, conforme a natureza do gasto."""
    if conta_gasto.startswith(("43", "44")):
        return CONTA_IVA_INVESTIMENTO
    if conta_gasto.startswith("31"):
        return CONTA_IVA_EXISTENCIAS.get(escalao(taxa), CONTA_IVA_EXISTENCIAS["normal"])
    return CONTA_IVA_OUTROS


def dividir_iva(valor: float, perfil: str) -> tuple[float, float]:
    """(parte dedutível, parte não dedutível), arredondadas ao cêntimo e somando sempre o valor original."""
    fracao = PERFIS.get(perfil, PERFIS["total"]).dedutivel
    dedutivel = round(valor * fracao, 2)
    return dedutivel, round(valor - dedutivel, 2)


# ---------- Autoliquidação (o adquirente liquida o IVA) ----------
# Aquisições intracomunitárias de bens (art. 1.º e 23.º do RITI): campos 12 e 13 da declaração.
# Serviços adquiridos a sujeitos passivos de outros Estados-membros (art. 6.º, n.º 6, al. a) do CIVA):
# campos 16 e 17. Inversão do sujeito passivo em operações internas (art. 2.º, n.º 1, al. j) e
# seguintes do CIVA, ex.: construção civil, sucatas): base e imposto nos campos 1 a 6, conforme
# a taxa, e a base também no quadro 06-A (campo 102 para a construção civil).
# O IVA liquidado é dedutível nos termos gerais (campos 20 a 24), conforme o perfil de dedução.
# Numeração do modelo em vigor até 30/06/2027 (Portaria n.º 298/2026/1 muda o modelo a partir daí).

REGIMES = {
    "nacional": "IVA nacional (o fornecedor liquidou)",
    "intracomunitario_bens": "Aquisição intracomunitária de bens",
    "intracomunitario_servicos": "Serviços de fornecedor de outro Estado-membro",
    "autoliquidacao": "Autoliquidação (inversão do sujeito passivo, ex.: construção civil)",
}
TIPO_BASE_REGIME = {"intracomunitario_bens": "base_ib", "intracomunitario_servicos": "base_is",
                    "autoliquidacao": "base_al"}
CONTA_IVA_INTRACOMUNITARIO = {"intracomunitario_bens": "24334", "intracomunitario_servicos": "24335"}
CAMPOS_AUTOLIQUIDADO = {  # conta -> (campo da base, campo do imposto, descrição)
    "24334": ("12", "13", "Aquisições intracomunitárias de bens"),
    "24335": ("16", "17", "Serviços adquiridos a sujeitos passivos de outros Estados-membros"),
}
CAMPO_AUTOLIQUIDACAO_INTERNA = ("102", "Operações em que liquidou o IVA por inversão do sujeito passivo (quadro 06-A)")

# Vendas sem IVA na declaração periódica, pelo código do motivo de isenção da linha (SAF-T TaxExemptionCode):
#   7 transmissões intracomunitárias de bens e serviços prestados noutros Estados-membros
#   8 operações isentas ou não tributadas com direito à dedução (exportações, art. 13.º e 15.º do CIVA...)
#   9 operações isentas sem direito à dedução (art. 9.º do CIVA)
# M40/M41 (autoliquidação pelo adquirente) contam no 7 se o cliente for de outro Estado-membro, senão no 8.
# M99 (não sujeitas) não entram na declaração. Os outros códigos ficam por classificar, com aviso.
CAMPO_ISENCAO = {"M16": "7", "M04": "8", "M05": "8", "M06": "8", "M07": "9"}
CODIGOS_AUTOLIQUIDACAO = ("M40", "M41")
CODIGOS_FORA_DA_DECLARACAO = ("M99",)


def campo_isencao(codigo: str | None, cliente_nif: str | None) -> str | None:
    """Campo (7, 8 ou 9) de uma venda sem IVA, ou None se o código não permite decidir."""
    codigo = (codigo or "").upper()
    if codigo in CODIGOS_AUTOLIQUIDACAO:
        prefixo = (cliente_nif or "")[:2].upper()
        return "7" if prefixo in PREFIXOS_UE else "8"
    return CAMPO_ISENCAO.get(codigo)


# Prefixos dos números de IVA dos outros Estados-membros (a Grécia usa EL)
PREFIXOS_UE = {"AT", "BE", "BG", "CY", "CZ", "DE", "DK", "EE", "EL", "ES", "FI", "FR", "HR", "HU", "IE", "IT",
               "LT", "LU", "LV", "MT", "NL", "PL", "RO", "SE", "SI", "SK", "XI"}


def conta_iva_autoliquidado(regime: str, taxa: float) -> str:
    return CONTA_IVA_INTRACOMUNITARIO.get(regime) or CONTA_IVA_LIQUIDADO.get(escalao(taxa), "24333")


def fornecedor_da_ue(nif: str | None) -> bool:
    nif = (nif or "").replace(" ", "").upper()
    return len(nif) > 4 and nif[:2] in PREFIXOS_UE


def regime_sugerido(nif: str | None, total_iva: float | None) -> str:
    """Fatura sem IVA de um fornecedor com número de IVA de outro Estado-membro: serviços intracomunitários."""
    if fornecedor_da_ue(nif) and not total_iva:
        return "intracomunitario_servicos"
    return "nacional"
