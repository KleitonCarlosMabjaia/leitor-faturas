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
