"""
Ficheiro da declaração periódica do IVA, para abrir na aplicação da AT (Portal das Finanças ›
IVA › Declaração periódica › Entregar › abrir ficheiro) e submeter depois de rever.

Formato: XML do esquema oficial "dpiva-06.xsd" (Suporte_informatico_IVA_2023.zip, publicado em
Apoio ao contribuinte › Suporte tecnológico › Formato de ficheiros; em vigor desde 2021-10-01),
namespace http://www.at.gov.pt/schemas/dpiva. O teste valida cada ficheiro contra esse XSD
(tests/dados/dpiva-06.xsd).

- Valores em cêntimos de euro (o esquema usa inteiros), só os campos com valor.
- Período: "MM" na declaração mensal e "MMT" na trimestral (mês em que acaba o trimestre).
- Os campos vêm do mapa do IVA (relatorios.mapa_iva), calculado a partir dos lançamentos.
  O que a contabilidade não distingue (operações isentas, campos 7 a 9; anexos das
  regularizações, campos 40 e 41) fica para completar na aplicação da AT, com aviso.
"""

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.modelos.tabelas import Empresa
from app.servicos.contabilidade import relatorios

NS = "http://www.at.gov.pt/schemas/dpiva"
VERSAO = "06"
LOCALIZACAO_SEDE = {"continente": "1", "acores": "2", "madeira": "3"}  # ordem do quadro 01 do modelo

# (campo do modelo, elemento do XSD, descrição) na ordem da sequência do esquema
APURAMENTO = [
    ("14", "btBensUEArtigo15", "Aquisições intracomunitárias de bens (art. 15.º do RITI) - base"),
    ("15", "btBensUEArtigo22", "Aquisições intracomunitárias de bens (art. 22.º do RITI) - base"),
    ("12", "btBensUELiquidadoDeclarante", "Aquisições intracomunitárias de bens liquidadas pelo declarante - base"),
    ("10", "btBensUETotal", "Aquisições intracomunitárias de bens - base"),
    ("18", "btImportDeclarante", "Importação de bens com liquidação pelo declarante - base"),
    ("8", "btOperacoesIsentasComDeducao", "Operações isentas com direito à dedução"),
    ("9", "btOperacoesIsentasSemDeducao", "Operações isentas sem direito à dedução"),
    ("16", "btServicosUE", "Serviços adquiridos a sujeitos passivos de outros Estados-membros - base"),
    ("5", "btTaxaIntermedia", "Operações à taxa intermédia - base"),
    ("3", "btTaxaNormal", "Operações à taxa normal - base"),
    ("1", "btTaxaReduzida", "Operações à taxa reduzida - base"),
    ("90", "btTotal", "Total da base tributável"),
    ("7", "btTransmissoesUEIsentas", "Transmissões intracomunitárias de bens e serviços isentos"),
    ("96", "excessoAReportar", "Excesso a reportar"),
    ("61", "excessoAReportarPeriodoAnterior", "Excesso a reportar do período anterior"),
    ("93", "ivaAEntregar", "Imposto a entregar ao Estado"),
    ("94", "ivaARecuperar", "Crédito de imposto a recuperar"),
    ("13", "ivaBensUELiquidadoDeclarante", "Aquisições intracomunitárias de bens, imposto liquidado pelo declarante"),
    ("11", "ivaBensUETotal", "Aquisições intracomunitárias de bens - imposto"),
    ("65", "ivaDedutivelAnexoR1", "Imposto dedutível (anexo R)"),
    ("67", "ivaDedutivelAnexoR2", "Imposto dedutível (anexo R)"),
    (None, "ivaDedutivelAnexosRTotal", "Total dos campos 65 e 67"),
    ("23", "ivaDedutivelExistenciasTaxaIntermedia", "Imposto dedutível - existências à taxa intermédia"),
    ("22", "ivaDedutivelExistenciasTaxaNormal", "Imposto dedutível - existências à taxa normal"),
    ("21", "ivaDedutivelExistenciasTaxaReduzida", "Imposto dedutível - existências à taxa reduzida"),
    ("20", "ivaDedutivelImobilizado", "Imposto dedutível - ativos fixos tangíveis e intangíveis"),
    ("24", "ivaDedutivelOutros", "Imposto dedutível - outros bens e serviços"),
    (None, "ivaDedutivelTotal", "Total dos campos 20 a 24"),
    ("92", "ivaFavorEstadoTotal", "Total do imposto a favor do Estado"),
    ("91", "ivaFavorSujPassivoTotal", "Total do imposto a favor do sujeito passivo"),
    ("19", "ivaImportDeclarante", "Importação de bens com liquidação pelo declarante - imposto"),
    ("66", "ivaLiquidadoAnexoR1", "Imposto liquidado (anexo R)"),
    ("68", "ivaLiquidadoAnexoR2", "Imposto liquidado (anexo R)"),
    ("17", "ivaServicosUE", "Serviços adquiridos a sujeitos passivos de outros Estados-membros - imposto"),
    ("6", "ivaTaxaIntermedia", "Operações à taxa intermédia - imposto"),
    ("4", "ivaTaxaNormal", "Operações à taxa normal - imposto"),
    ("2", "ivaTaxaReduzida", "Operações à taxa reduzida - imposto"),
    ("95", "reembolso", "Reembolso pedido"),
    ("41", "regularizacoesFavorEstado", "Regularizações a favor do Estado"),
    ("81", "regularizacoesFavorSujPassivoComunicadasCobranca", "Regularizações a favor do sujeito passivo (cobrança)"),
    ("40", "regularizacoesFavorSujPassivoNaoComunicadasCobranca", "Regularizações a favor do sujeito passivo"),
]
DESENVOLVIMENTO = [
    ("102", "inversaoSujPassivoConstrucaoCivil", "Inversão do sujeito passivo - construção civil"),
    ("106", "totalQuadro06A", "Total do quadro 06-A"),
]
ORDEM_DESENVOLVIMENTO = [
    "aquisicaoSujPassivoCorticaProdutosSivicola", "bensServicosParaPessoalEmpresa", "impostoLiquidado",
    "inversaoSujPassivoConstrucaoCivil", "inversaoSujPassivoEmissaoGasesEfEstufa",
    "inversaoSujPassivoImoveisRenunciaIsencao", "inversaoSujPassivoOuro", "inversaoSujPassivoSucatas",
    "operacoesArtigo42", "operacoesPTFeitasPorContribuintesForaUE", "operacoesPTFeitasPorContribuintesUE",
    "totalQuadro06A", "valorOperacoesAcessoriasBancariasFinanceirasSeguros", "valorOperacoesIsentasEAcessoriasSujIMT",
]
# Campos que entram nos totais (instruções do modelo)
SOMA_90 = ("1", "3", "5", "7", "8", "9", "10", "16")
SOMA_91 = ("20", "21", "22", "23", "24", "40", "61", "65", "67", "81")
SOMA_92 = ("2", "4", "6", "11", "17", "19", "41", "66", "68")


class ErroDeclaracaoIVA(Exception):
    """Dados em falta ou inválidos (mensagem pronta a mostrar)."""


@dataclass
class Declaracao:
    de: date
    ate: date
    ano: int
    periodo: str            # "MM" ou "MMT"
    campos: dict[str, int]  # campo do modelo -> cêntimos (só os que têm valor)
    avisos: list[str]


def _centimos(valor: float) -> int:
    return int(round(valor * 100))


def periodo_declarativo(de: date, ate: date, regime_iva: str) -> tuple[int, str]:
    """(ano, período) da declaração; o intervalo tem de ser um mês ou um trimestre civil completo."""
    fim_mes = (ate.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    if de.day != 1 or ate != fim_mes or de.year != ate.year:
        raise ErroDeclaracaoIVA("O período da declaração tem de começar no dia 1 e acabar no último dia do mês.")
    meses = ate.month - de.month + 1
    if regime_iva == "mensal":
        if meses != 1:
            raise ErroDeclaracaoIVA("No regime mensal, a declaração é de um mês.")
        return ate.year, f"{ate.month:02d}"
    if meses != 3 or ate.month % 3:
        raise ErroDeclaracaoIVA("No regime trimestral, a declaração é de um trimestre civil (janeiro a março, ...).")
    return ate.year, f"{ate.month:02d}T"


def calcular(db: Session, empresa: Empresa, de: date, ate: date) -> Declaracao:
    ano, periodo = periodo_declarativo(de, ate, empresa.regime_iva)
    mapa = relatorios.mapa_iva(db, empresa.id, de, ate)
    valores: dict[str, float] = {}
    for c in mapa["dedutivel"]:
        valores[c["campo"]] = c["valor"]
    for c in mapa["liquidado"]:
        valores[c["campo_base"]], valores[c["campo_imposto"]] = c["base"], c["imposto"]
    for c in mapa["autoliquidado"]:
        valores[c["campo_base"]], valores[c["campo_imposto"]] = c["base"], c["imposto"]
    # As aquisições intracomunitárias de bens são todas autoliquidadas pela empresa: o total (10/11) é o 12/13
    valores["10"], valores["11"] = valores.get("12", 0.0), valores.get("13", 0.0)
    valores["102"] = valores["106"] = mapa["quadro_06a"]["base"]
    valores.update(mapa["isentas"])

    avisos = []
    movimentos_regularizacoes = relatorios.movimentos_por_conta(db, empresa.id, de, ate, excluir_origem="apuramento")
    reg_d, reg_c = movimentos_regularizacoes.get("2434", (0.0, 0.0))
    valores["40"], valores["41"] = reg_d, reg_c
    if reg_d or reg_c:
        avisos.append("Há regularizações (campos 40/41): na aplicação da AT preencha o anexo das regularizações "
                      "antes de submeter.")

    # Crédito do período anterior (campo 61): saldo devedor de IVA a recuperar (2437) antes do período
    saldos = relatorios.saldos_por_conta(db, empresa.id, ate=de - timedelta(days=1))
    valores["61"] = max(0.0, sum(v for c, v in saldos.items() if c.startswith("2437")))

    campos = {c: _centimos(v) for c, v in valores.items()}
    negativos = sorted((c for c, v in campos.items() if v < 0), key=int)
    if negativos:
        raise ErroDeclaracaoIVA(
            "A declaração não aceita valores negativos e os campos " + ", ".join(negativos) + " ficaram negativos "
            "(por exemplo, notas de crédito superiores às vendas do período). Corrija com regularizações "
            "(campos 40/41) ou reveja os lançamentos.")

    campos["90"] = sum(campos.get(c, 0) for c in SOMA_90)
    campos["91"] = sum(campos.get(c, 0) for c in SOMA_91)
    campos["92"] = sum(campos.get(c, 0) for c in SOMA_92)
    diferenca = campos["92"] - campos["91"]
    campos["93"], campos["94"] = max(diferenca, 0), max(-diferenca, 0)
    campos["96"] = campos["94"]  # sem pedido de reembolso, o crédito transita para o período seguinte
    for p in mapa["isentas_por_classificar"]:
        valor = f"{p['base']:.2f}".replace(".", ",")
        avisos.append(f"Vendas sem IVA com o motivo {p['codigo']} ({valor} €) não entram nos campos 7 a 9: "
                      "indique-as no campo certo na aplicação da AT.")
    return Declaracao(de, ate, ano, periodo, {c: v for c, v in campos.items() if v}, avisos)


def _faltas(empresa: Empresa) -> list[str]:
    faltas = []
    if not (empresa.nif and empresa.nif.isdigit() and len(empresa.nif) == 9):
        faltas.append("o NIF da empresa")
    if empresa.nif_contabilista and not (empresa.nif_contabilista.isdigit() and len(empresa.nif_contabilista) == 9):
        faltas.append("um NIF do contabilista certificado válido")
    return faltas


def gerar_xml(empresa: Empresa, d: Declaracao) -> bytes:
    faltas = _faltas(empresa)
    if faltas:
        raise ErroDeclaracaoIVA("Para gerar o ficheiro falta " + " e ".join(faltas) + ", em Dados da empresa.")
    ET.register_namespace("", NS)

    def el(pai: ET.Element, nome: str, texto: str | None = None) -> ET.Element:
        filho = ET.SubElement(pai, f"{{{NS}}}{nome}")
        if texto is not None:
            filho.text = texto
        return filho

    raiz = ET.Element(f"{{{NS}}}dpiva", {"version": VERSAO})
    rosto = el(raiz, "rosto")
    apuramento = el(rosto, "apuramento")
    sem_operacoes = not any(d.campos.get(c) for c in (*SOMA_90, *SOMA_91, *SOMA_92, "102"))
    for campo, nome, _ in APURAMENTO:
        if campo and d.campos.get(campo):
            el(apuramento, nome, str(d.campos[campo]))
        elif nome == "ivaDedutivelTotal" and any(d.campos.get(c) for c in ("20", "21", "22", "23", "24")):
            el(apuramento, nome, str(sum(d.campos.get(c, 0) for c in ("20", "21", "22", "23", "24"))))
    adquirente = any(d.campos.get(c) for c in ("10", "16", "18", "102"))
    liquidou = adquirente or any(d.campos.get(c) for c in ("1", "3", "5"))
    dedutiveis = any(d.campos.get(c) for c in ("20", "21", "22", "23", "24", "40", "41", "61"))
    el(apuramento, "temOperacoesAdquirenteComLiqImposto", "S" if adquirente else "N")
    el(apuramento, "temOperacoesComLiqImposto", "S" if liquidou else "N")
    el(apuramento, "temOperacoesDedutiveis", "S" if dedutiveis else "N")
    el(apuramento, "temOperacoesSemLiqImposto", "S" if any(d.campos.get(c) for c in ("7", "8", "9")) else "N")

    desenvolvimento = el(rosto, "desenvolvimento")
    por_nome = {nome: campo for campo, nome, _ in DESENVOLVIMENTO}
    for nome in ORDEM_DESENVOLVIMENTO:
        campo = por_nome.get(nome)
        if campo and d.campos.get(campo):
            el(desenvolvimento, nome, str(d.campos[campo]))

    inicio = el(rosto, "inicio")
    el(inicio, "anoDeclaracao", str(d.ano))
    el(inicio, "apresentouDeclRecapitulativa", "false")
    el(inicio, "atividadesImobiliarias", "false")
    el(inicio, "localizacaoSede", LOCALIZACAO_SEDE.get(empresa.regiao_sede, "1"))
    el(inicio, "nif", empresa.nif)
    if empresa.nif_contabilista:
        el(inicio, "nifCC", empresa.nif_contabilista)
    el(inicio, "periodoDeclaracao", d.periodo)
    el(inicio, "prazo", "1")
    el(inicio, "semOperacoes", "true" if sem_operacoes else "false")
    for regiao in ("Acores", "Continente", "Madeira"):
        el(inicio, f"temAnexoR{regiao}", "false")
    return ET.tostring(raiz, encoding="UTF-8", xml_declaration=True)


def nome_ficheiro(empresa: Empresa, d: Declaracao) -> str:
    return f"DPIVA_{empresa.nif or 'empresa'}_{d.ano}_{d.periodo}.xml"


def resumo(empresa: Empresa, d: Declaracao) -> dict:
    """Campos da declaração em euros, pela ordem do modelo, e o que falta para gerar o ficheiro."""
    descricoes = {campo: desc for campo, _, desc in (*APURAMENTO, *DESENVOLVIMENTO) if campo}
    linhas = [{"campo": c, "descricao": descricoes.get(c, ""), "valor": round(v / 100, 2)}
              for c, v in sorted(d.campos.items(), key=lambda i: int(i[0]))]
    faltas = _faltas(empresa)
    return {
        "ano": d.ano, "periodo": d.periodo, "de": d.de, "ate": d.ate, "campos": linhas, "avisos": d.avisos,
        "pronto": not faltas, "erro": ("Para gerar o ficheiro falta " + " e ".join(faltas) + ", em Dados da empresa.")
        if faltas else None,
        "ficheiro": nome_ficheiro(empresa, d),
        "aviso_contabilista": None if empresa.nif_contabilista else
        "Sem NIF do contabilista certificado: é obrigatório se a empresa tiver contabilidade organizada.",
    }
