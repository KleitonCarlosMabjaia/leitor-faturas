"""
Retenção na fonte de IRS sobre rendimentos do trabalho dependente (categoria A), continente.

Tabelas e regras do Despacho n.º 233-A/2026, de 6 de janeiro (tabelas I a VII), com o
ajustamento de novembro de 2026 do Despacho n.º 12523-A/2026, de 14 de outubro:

    retenção = R × taxa marginal máxima − parcela a abater − parcela adicional × n.º de dependentes

- Em algumas linhas a parcela a abater é uma fórmula: taxa × fator × (constante − R).
- Por dependente com incapacidade ≥ 60 %: + 84,82 € à parcela a abater (não casado ou casado
  único titular) ou + 42,41 € (casado, dois titulares).
- Casado único titular com cônjuge sem rendimentos A/H e incapacidade ≥ 60 %: + 135,71 €.
- Três ou mais dependentes: menos um ponto percentual na taxa marginal máxima.
- Subsídios de férias e de Natal: retenção autónoma, com a tabela aplicada ao valor do subsídio;
  pagos em duodécimos, retém-se em cada um a parte proporcional (a taxa do subsídio inteiro).
- Trabalho suplementar: 50 % da taxa efetiva da remuneração mensal do mês.
- Novembro de 2026: só 50 % do valor apurado, exceto se a remuneração mensal passar o limite
  superior do penúltimo escalão da tabela.
- A retenção nunca é negativa e arredonda ao cêntimo.

Os Açores e a Madeira têm tabelas próprias, que aqui não estão: nesses casos usa-se a taxa
indicada à mão na ficha da pessoa.
"""

from dataclasses import dataclass

# Situação familiar (art. 99.º-B do CIRS; os unidos de facto seguem as tabelas dos casados)
NAO_CASADO, CASADO_DOIS, CASADO_UNICO = "nao_casado", "casado_dois", "casado_unico"
SITUACOES = {
    NAO_CASADO: "Não casado",
    CASADO_DOIS: "Casado ou unido de facto, dois titulares",
    CASADO_UNICO: "Casado ou unido de facto, único titular",
}

# Uma linha: (limite superior da remuneração mensal ou None no último escalão, taxa marginal máxima em %,
# parcela a abater: valor fixo ou (fator, constante) para a fórmula taxa × fator × (constante − R))
Linha = tuple[float | None, float, float | tuple[float, float]]


@dataclass(frozen=True)
class Tabela:
    numero: str
    descricao: str
    linhas: tuple[Linha, ...]
    adicional_dependente: float = 0.0


def _t(numero: str, descricao: str, adicional: float, *linhas: Linha) -> Tabela:
    return Tabela(numero, descricao, tuple(linhas), adicional)


# ---------- Despacho n.º 233-A/2026 (continente, a partir de 1 de janeiro de 2026) ----------

_BASE_I_II = (
    (920.00, 0.0, 0.0),
    (1042.00, 12.50, (2.60, 1273.85)),
    (1108.00, 15.70, (1.35, 1554.83)),
    (1154.00, 15.70, 94.71),
    (1212.00, 21.20, 158.18),
    (1819.00, 24.10, 193.33),
    (2119.00, 31.10, 320.66),
    (2499.00, 34.90, 401.19),
    (3305.00, 38.36, 487.66),
    (5547.00, 39.69, 531.62),
    (20221.00, 44.95, 823.40),
    (None, 47.17, 1272.31),
)

TABELAS_2026 = {
    "I": _t("I", "Trabalho dependente - não casado sem dependentes ou casado dois titulares", 21.43, *_BASE_I_II),
    "II": _t("II", "Trabalho dependente - não casado com um ou mais dependentes", 34.29, *_BASE_I_II),
    "III": _t("III", "Trabalho dependente - casado, único titular", 42.86,
              (991.00, 0.0, 0.0),
              (1042.00, 12.50, (2.6, 1372.15)),
              (1108.00, 12.50, (1.35, 1677.85)),
              (1119.00, 12.50, 96.17),
              (1432.00, 12.72, 98.64),
              (1962.00, 15.70, 141.32),
              (2240.00, 19.38, 213.53),
              (2773.00, 22.77, 289.47),
              (3389.00, 25.70, 370.72),
              (5965.00, 28.81, 476.12),
              (20265.00, 38.43, 1049.96),
              (None, 47.17, 2821.13)),
    "IV": _t("IV", "Trabalho dependente - não casado ou casado dois titulares sem dependentes - pessoa com "
             "deficiência", 0.0,
             (1694.00, 0.0, 0.0),
             (2063.00, 21.20, 359.13),
             (2492.00, 31.10, 563.37),
             (4487.00, 34.90, 658.07),
             (4753.00, 38.36, 813.33),
             (6687.00, 39.69, 876.55),
             (20468.00, 44.95, 1228.29),
             (None, 47.17, 1682.68)),
    "V": _t("V", "Trabalho dependente - não casado, com um ou mais dependentes - pessoa com deficiência", 42.86,
            (1938.00, 0.0, 0.0),
            (2063.00, 21.32, 413.19),
            (2854.00, 31.10, 614.96),
            (4504.00, 34.90, 723.42),
            (6826.00, 38.36, 879.26),
            (7048.00, 39.69, 970.05),
            (20468.00, 44.95, 1340.78),
            (None, 47.17, 1795.17)),
    "VI": _t("VI", "Trabalho dependente - casado dois titulares, com um ou mais dependentes - pessoa com "
             "deficiência", 21.43,
             (1668.00, 0.0, 0.0),
             (2068.00, 20.49, 341.78),
             (2497.00, 24.10, 416.44),
             (3107.00, 31.10, 591.23),
             (4504.00, 34.90, 709.30),
             (6826.00, 38.36, 865.14),
             (7048.00, 39.69, 955.93),
             (20468.00, 44.95, 1326.66),
             (None, 47.17, 1781.05)),
    "VII": _t("VII", "Trabalho dependente - casado único titular - pessoa com deficiência", 42.86,
              (2325.00, 0.0, 0.0),
              (3494.00, 22.77, 529.41),
              (3761.00, 25.70, 631.79),
              (6687.00, 28.81, 748.76),
              (20468.00, 42.44, 1660.20),
              (None, 47.17, 2628.34)),
}

# Ano -> (tabelas, valores das alíneas a) e b) do n.º 5, referência legal)
ANOS = {
    2026: {
        "tabelas": TABELAS_2026,
        "dependente_deficiencia": {NAO_CASADO: 84.82, CASADO_UNICO: 84.82, CASADO_DOIS: 42.41},
        "conjuge_deficiencia": 135.71,
        "fonte": "Despacho n.º 233-A/2026",
    },
}
# Meses com ajustamento excecional: (ano, mês) -> (fração a reter, referência legal)
AJUSTAMENTOS = {(2026, 11): (0.5, "Despacho n.º 12523-A/2026")}


class ErroRetencao(Exception):
    """Dados insuficientes para calcular a retenção (mensagem pronta a mostrar)."""


@dataclass(frozen=True)
class Perfil:
    """O que a pessoa declarou à entidade patronal para a retenção (modelo de declaração do art. 99.º-B)."""
    situacao: str
    dependentes: int = 0
    dependentes_deficiencia: int = 0      # dos dependentes, quantos têm incapacidade ≥ 60 %
    deficiencia: bool = False             # o próprio, com incapacidade ≥ 60 %
    conjuge_deficiencia: bool = False     # casado único titular: cônjuge sem rendimentos A/H, incapacidade ≥ 60 %


def ano_das_tabelas(ano: int) -> int:
    """As tabelas do próprio ano; antes da primeira disponível, essa; depois da última, a última."""
    anos = sorted(ANOS)
    return max((a for a in anos if a <= ano), default=anos[0])


def tabela_aplicavel(perfil: Perfil, ano: int) -> Tabela:
    if perfil.situacao not in SITUACOES:
        raise ErroRetencao("Indique a situação familiar para a retenção de IRS.")
    tabelas = ANOS[ano_das_tabelas(ano)]["tabelas"]
    com_dependentes = perfil.dependentes > 0
    if not perfil.deficiencia:
        if perfil.situacao == CASADO_UNICO:
            return tabelas["III"]
        return tabelas["II"] if perfil.situacao == NAO_CASADO and com_dependentes else tabelas["I"]
    if perfil.situacao == CASADO_UNICO:
        return tabelas["VII"]
    if not com_dependentes:
        return tabelas["IV"]
    return tabelas["V"] if perfil.situacao == NAO_CASADO else tabelas["VI"]


def _linha(tabela: Tabela, remuneracao: float) -> Linha:
    for linha in tabela.linhas:
        if linha[0] is None or remuneracao <= linha[0]:
            return linha
    return tabela.linhas[-1]


def calcular(remuneracao: float, perfil: Perfil, ano: int) -> dict:
    """Retenção sobre uma remuneração mensal (ou um subsídio, que se calcula à parte)."""
    tabela = tabela_aplicavel(perfil, ano)
    dados = ANOS[ano_das_tabelas(ano)]
    remuneracao = max(remuneracao, 0.0)
    _, taxa, parcela = _linha(tabela, remuneracao)
    if taxa == 0:
        return {"tabela": tabela.numero, "taxa_marginal": 0.0, "valor": 0.0, "taxa_efetiva": 0.0, "fracao": 0.0}
    if isinstance(parcela, tuple):
        fator, constante = parcela
        parcela = taxa / 100 * fator * (constante - remuneracao)
    if perfil.dependentes >= 3:
        taxa -= 1   # alínea h) do n.º 5: menos um ponto percentual; as parcelas mantêm-se
    abater = parcela + tabela.adicional_dependente * perfil.dependentes
    abater += dados["dependente_deficiencia"][perfil.situacao] * min(perfil.dependentes_deficiencia,
                                                                      perfil.dependentes)
    if perfil.situacao == CASADO_UNICO and perfil.conjuge_deficiencia:
        abater += dados["conjuge_deficiencia"]
    valor = max(round(remuneracao * taxa / 100 - abater + 1e-9, 2), 0.0)
    return {"tabela": tabela.numero, "taxa_marginal": taxa, "valor": valor,
            "taxa_efetiva": round(valor / remuneracao * 100, 2) if remuneracao else 0.0,
            "fracao": valor / remuneracao if remuneracao else 0.0}   # taxa efetiva sem arredondar


def limite_penultimo_escalao(perfil: Perfil, ano: int) -> float:
    return tabela_aplicavel(perfil, ano).linhas[-2][0]


def retencao_mes(perfil: Perfil, ano: int, mes: int, *, mensal: float, subsidio_ferias: float = 0.0,
                 subsidio_natal: float = 0.0, duodecimos: bool = False, base_subsidio: float = 0.0,
                 suplementar: float = 0.0) -> dict:
    """
    Retenção de um recibo, com o detalhe de cada remuneração (n.º 10 do despacho: a taxa efetiva
    de cada uma apresenta-se em separado).

    mensal: remuneração mensal sujeita (base, outros abonos, excesso do subsídio de alimentação,
    menos faltas), sem subsídios nem trabalho suplementar. Com duodécimos, base_subsidio é o valor
    do subsídio inteiro, que dá a taxa aplicada a cada duodécimo.
    """
    partes = []
    r_mensal = calcular(mensal, perfil, ano)
    partes.append({"chave": "mensal", "descricao": "Remuneração mensal", "base": round(mensal, 2),
                   "taxa": r_mensal["taxa_efetiva"], "valor": r_mensal["valor"]})
    for chave, nome, valor in (("subsidio_ferias", "Subsídio de férias", subsidio_ferias),
                               ("subsidio_natal", "Subsídio de Natal", subsidio_natal)):
        if valor <= 0:
            continue
        if duodecimos:
            # A parte proporcional do imposto do subsídio inteiro (art. 99.º-C, n.º 6, do CIRS)
            fracao = calcular(base_subsidio, perfil, ano)["fracao"]
            partes.append({"chave": chave, "descricao": f"{nome} (duodécimo)", "base": round(valor, 2),
                           "taxa": round(fracao * 100, 2), "valor": round(valor * fracao + 1e-9, 2)})
        else:
            r = calcular(valor, perfil, ano)
            partes.append({"chave": chave, "descricao": nome, "base": round(valor, 2), "taxa": r["taxa_efetiva"],
                           "valor": r["valor"]})
    if suplementar > 0:
        fracao = r_mensal["fracao"] / 2
        partes.append({"chave": "suplementar", "descricao": "Trabalho suplementar", "base": round(suplementar, 2),
                       "taxa": round(fracao * 100, 2), "valor": round(suplementar * fracao + 1e-9, 2)})

    nota = None
    ajuste = AJUSTAMENTOS.get((ano, mes))
    if ajuste:
        fracao, fonte = ajuste
        if mensal > limite_penultimo_escalao(perfil, ano):
            nota = f"Sem a redução de novembro ({fonte}): a remuneração passa o penúltimo escalão."
        else:
            for p in partes:
                p["valor"] = round(p["valor"] * fracao + 1e-9, 2)
                p["taxa"] = round(p["taxa"] * fracao, 2)
            nota = f"Retenção a {fracao:.0%} em novembro ({fonte})."
    total = round(sum(p["valor"] for p in partes), 2)
    sujeita = sum(p["base"] for p in partes)
    return {
        "tabela": r_mensal["tabela"],
        "ano_tabelas": ano_das_tabelas(ano),
        "fonte": ANOS[ano_das_tabelas(ano)]["fonte"],
        "partes": partes,
        "valor": total,
        "taxa_efetiva": round(total / sujeita * 100, 2) if sujeita else 0.0,
        "nota": nota,
    }
