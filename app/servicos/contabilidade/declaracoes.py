"""
Declarações de remunerações geradas a partir do processamento de salários do mês.

1. Segurança Social: ficheiro da declaração de remunerações para entrega na Segurança Social
   Direta ("Normas de preenchimento - novo formato", modelo RC4008 versão 01). Registos de
   comprimento fixo (118 caracteres):
       R0  identificação do ficheiro (único, no início)
       R1  entidade empregadora, por estabelecimento e taxa contributiva
       R2  remunerações de cada trabalhador, por natureza (P base, F férias, N Natal, ...)
       R3  totais de cada estabelecimento e taxa
   Valores em cêntimos de euro, sem pontos nem vírgulas; texto em maiúsculas, sem acentos nem
   cedilhas; dias de trabalho com uma casa decimal (30 dias = "300").

2. AT: declaração mensal de remunerações (DMR-AT), ficheiro para envio no Portal das Finanças
   ("Suporte informático DMR-AT", em vigor desde 2024-02-01; códigos das instruções de
   preenchimento aprovadas pela Portaria n.º 69/2026/1). Texto ASCII com registos de
   comprimento fixo (172 caracteres), separados por mudança de linha:
       001 header do ficheiro, 002 header da declaração, 003 header DMR-AT,
       004/005 resumo (quadro 4: rendimentos sujeitos, isentos, não sujeitos e totais),
       006 uma linha por titular, tipo de rendimento e local (quadro 5),
       009 trailer DMR-AT, 099 trailer da declaração, 999 trailer do ficheiro.
   Valores em cêntimos, numéricos alinhados à direita com zeros. Cada recibo dá linhas com os
   códigos A (remuneração), A3 (subsídio de férias), A4 (subsídio de Natal) e A21 (subsídio de
   refeição, parte não sujeita), cada uma com a sua retenção de IRS e contribuições.
"""

import csv
import io
import unicodedata
from collections import defaultdict
from datetime import date

from sqlalchemy.orm import Session

from app.modelos.tabelas import Empresa, Funcionario, ProcessamentoSalarial
from app.servicos import validacao
from app.servicos.contabilidade import salarios

MODELO_SS = "RC4008"
VERSAO_SS = "01"
COMPRIMENTO = 118
ESTABELECIMENTO = "0000"   # sede (sem estabelecimentos registados à parte)

# Natureza de cada parcela do recibo (normas, ponto 1.3)
NATUREZAS = {
    "P": "Remuneração base",
    "F": "Subsídio de férias",
    "N": "Subsídio de Natal",
    "B": "Prémios, bónus e outras prestações de caráter mensal",
    "R": "Subsídio de refeição (parte sujeita)",
}


class ErroDeclaracao(Exception):
    """Dados em falta ou inválidos para a declaração (mensagem pronta a mostrar)."""


def _texto(valor: str, tamanho: int) -> str:
    """Maiúsculas, sem acentos nem caracteres especiais, alinhado à esquerda com espaços."""
    sem_acentos = unicodedata.normalize("NFKD", valor or "").encode("ascii", "ignore").decode("ascii").upper()
    limpo = "".join(c if c.isalnum() or c in " ." else " " for c in sem_acentos)
    return " ".join(limpo.split())[:tamanho].ljust(tamanho)


def _numero(valor: int, tamanho: int) -> str:
    texto = str(abs(int(valor)))
    if len(texto) > tamanho:
        raise ErroDeclaracao(f"O valor {valor} não cabe no ficheiro ({tamanho} algarismos).")
    return texto.zfill(tamanho)


def _sinal(valor: float) -> str:
    return "-" if valor < 0 else "0"


def _centimos(valor: float) -> int:
    return int(round(valor * 100))


def _dias_trabalho(f: Funcionario | None, ano: int, mes: int) -> int:
    """Dias de trabalho do mês (máximo 30); proporcionais na admissão e na saída."""
    inicio, fim = date(ano, mes, 1), salarios.fim_do_mes(ano, mes)
    if f is None:
        return 30
    de = max(inicio, f.data_admissao)
    ate = min(fim, f.data_saida) if f.data_saida else fim
    if de == inicio and ate == fim:
        return 30
    return max(0, min(30, (ate - de).days + 1))


def parcelas(recibo: dict) -> list[tuple[str, float]]:
    """Parcelas do recibo por natureza; a soma é a remuneração sujeita a Segurança Social."""
    alimentacao_sujeita = round(recibo["alimentacao"] - recibo["alimentacao_isenta"], 2)
    candidatas = [
        ("P", round(recibo["base"] - recibo["faltas"], 2)),
        ("F", recibo["subsidio_ferias"]),
        ("N", recibo["subsidio_natal"]),
        ("B", recibo["outros"]),
        ("R", alimentacao_sujeita),
    ]
    return [(n, v) for n, v in candidatas if v]


def _processamento(db: Session, empresa: Empresa, ano: int, mes: int) -> ProcessamentoSalarial:
    p = salarios.obter(db, empresa.id, ano, mes)
    if not salarios.processado(db, p):
        raise ErroDeclaracao(f"Os salários de {salarios.nome_mes(ano, mes)} ainda não foram processados.")
    return p


def ficheiro_seguranca_social(db: Session, empresa: Empresa, ano: int, mes: int) -> tuple[bytes, str, dict]:
    """(conteúdo, nome do ficheiro, resumo) da declaração de remunerações do mês."""
    p = _processamento(db, empresa, ano, mes)
    faltas = []
    if not (empresa.niss and empresa.niss.isdigit() and len(empresa.niss) == 11):
        faltas.append("o NISS da empresa (11 algarismos), em Dados da empresa")
    pessoas = {f.id: f for f in db.query(Funcionario).filter(Funcionario.empresa_id == empresa.id)}
    for r in p.recibos:
        f = pessoas.get(r["funcionario_id"])
        if not (r.get("niss") or (f and f.niss)):
            faltas.append(f"o NISS de {r['nome']}")
        if not (f and f.data_nascimento):
            faltas.append(f"a data de nascimento de {r['nome']}")
    if faltas:
        raise ErroDeclaracao("Para gerar o ficheiro falta: " + "; ".join(faltas) + ".")

    referencia = f"{ano:04d}{mes:02d}"
    linhas = ["R0" + MODELO_SS + "  " + VERSAO_SS + " " * 106]
    # Um bloco R1..R3 por taxa contributiva (trabalhador + entidade)
    grupos: dict[float, list[dict]] = defaultdict(list)
    for r in p.recibos:
        grupos[round(r["taxa_ss_trabalhador"] + r["taxa_ss_entidade"], 2)].append(r)
    resumo_grupos = []
    for taxa in sorted(grupos):
        recibos = grupos[taxa]
        linhas.append("R1" + empresa.niss + ESTABELECIMENTO + _numero(int(empresa.nif or 0), 9)
                      + _texto(empresa.nome, 66) + referencia + " " * 20)
        total_rem = 0
        registos = 0
        for r in sorted(recibos, key=lambda x: x["numero"]):
            f = pessoas.get(r["funcionario_id"])
            niss = r.get("niss") or f.niss
            for natureza, valor in parcelas(r):
                dias = _dias_trabalho(f, ano, mes) if natureza == "P" else 0
                cent = _centimos(valor)
                linhas.append(
                    "R2" + empresa.niss + ESTABELECIMENTO + niss + _texto(r["nome"], 60)
                    + f.data_nascimento.strftime("%Y%m%d") + referencia
                    + _numero(dias * 10, 3) + "0" + natureza.rjust(2) + _numero(cent, 9) + _sinal(cent))
                total_rem += cent
                registos += 1
        contribuicoes = _centimos(sum(r["ss_trabalhador"] + r["ss_entidade"] for r in recibos))
        linhas.append("R3" + empresa.niss + ESTABELECIMENTO + "9" * 11 + _numero(total_rem, 14) + _sinal(total_rem)
                      + _numero(contribuicoes, 12) + _sinal(contribuicoes) + _numero(_centimos(taxa), 4)
                      + _numero(registos, 6) + " " * 52)
        resumo_grupos.append({"taxa": taxa, "pessoas": len(recibos), "remuneracoes": round(total_rem / 100, 2),
                              "contribuicoes": round(contribuicoes / 100, 2), "registos": registos})
    for ln in linhas:
        if len(ln) != COMPRIMENTO:  # salvaguarda: nunca entregar um ficheiro mal formado
            raise ErroDeclaracao(f"Erro interno: registo {ln[:2]} com {len(ln)} caracteres.")
    conteudo = ("\r\n".join(linhas) + "\r\n").encode("ascii")
    nome = f"DRSS{referencia}.txt"
    return conteudo, nome, {"ano": ano, "mes": mes, "nome_mes": salarios.nome_mes(ano, mes), "grupos": resumo_grupos,
                            "total_contribuicoes": round(sum(g["contribuicoes"] for g in resumo_grupos), 2)}


# ---------- DMR (AT) ----------

COMPRIMENTO_DMR = 172
VERSAO_DMR = "05"
# NIPC do Instituto da Segurança Social, I.P.: entidade a favor de quem são feitas as contribuições (campo 07)
NIPC_SEGURANCA_SOCIAL = "505305500"
LOCAL_RENDIMENTO = {"continente": "C", "acores": "RA", "madeira": "RM"}
CODIGOS_DMR = {
    "A": "Trabalho dependente",
    "A3": "Subsídio de férias",
    "A4": "Subsídio de Natal",
    "A21": "Subsídio de refeição (parte não sujeita)",
}
NAO_SUJEITOS = {"A21"}


def _repartir(total: int, pesos: list[int]) -> list[int]:
    """Reparte `total` cêntimos na proporção dos pesos; o arredondamento fica na maior parcela."""
    soma = sum(pesos)
    if not soma:
        return [0] * len(pesos)
    partes = [total * p // soma for p in pesos]
    partes[pesos.index(max(pesos))] += total - sum(partes)
    return partes


def linhas_recibo(recibo: dict) -> list[dict]:
    """Linhas da DMR de um recibo (cêntimos): código, rendimento, retenção de IRS e contribuições."""
    c = _centimos
    valores = {
        "A": c(recibo["base"] - recibo["faltas"] + recibo["outros"] + recibo.get("suplementar", 0)
               + recibo["alimentacao"] - recibo["alimentacao_isenta"]),
        "A3": c(recibo["subsidio_ferias"]),
        "A4": c(recibo["subsidio_natal"]),
    }
    sujeitos = [k for k, v in valores.items() if v]
    # IRS: pela parte de cada remuneração no cálculo da retenção; sem esse detalhe, na proporção dos valores
    partes = (recibo.get("irs_detalhe") or {}).get("partes")
    if partes:
        por_parte = {"mensal": "A", "suplementar": "A", "subsidio_ferias": "A3", "subsidio_natal": "A4"}
        irs = dict.fromkeys(sujeitos, 0)
        for p in partes:
            codigo = por_parte.get(p["chave"], "A")
            if codigo in irs:
                irs[codigo] += c(p["valor"])
            elif sujeitos:
                irs[sujeitos[0]] += c(p["valor"])
    else:
        irs = dict(zip(sujeitos, _repartir(c(recibo["irs"]), [valores[k] for k in sujeitos]), strict=True))
    contribuicoes = dict(zip(sujeitos, _repartir(c(recibo["ss_trabalhador"]), [valores[k] for k in sujeitos]),
                             strict=True))
    linhas = [{"codigo": k, "rendimento": valores[k], "retencao_irs": irs[k], "contribuicoes": contribuicoes[k]}
              for k in sujeitos]
    if recibo.get("alimentacao_isenta"):
        linhas.append({"codigo": "A21", "rendimento": c(recibo["alimentacao_isenta"]), "retencao_irs": 0,
                       "contribuicoes": 0})
    return linhas


def mapa_dmr(db: Session, empresa: Empresa, ano: int, mes: int) -> dict:
    """Valores por titular e tipo de rendimento para a declaração mensal de remunerações da AT."""
    p = _processamento(db, empresa, ano, mes)
    linhas = []
    for r in sorted(p.recibos, key=lambda x: x["numero"]):
        for ln in linhas_recibo(r):
            linhas.append({
                "nif": r.get("nif"), "nome": r["nome"], "tipo": r["tipo"], "codigo": ln["codigo"],
                "descricao": CODIGOS_DMR[ln["codigo"]], "rendimento": ln["rendimento"] / 100,
                "retencao_irs": ln["retencao_irs"] / 100, "contribuicoes_obrigatorias": ln["contribuicoes"] / 100,
            })
    totais = {k: round(sum(ln[k] for ln in linhas), 2)
              for k in ("rendimento", "retencao_irs", "contribuicoes_obrigatorias")}
    avisos = [f"{r['nome']} não tem NIF válido: a DMR exige o NIF de cada titular."
              for r in p.recibos if not (r.get("nif") and validacao.nif_valido(r["nif"]))]
    return {"ano": ano, "mes": mes, "nome_mes": salarios.nome_mes(ano, mes), "linhas": linhas, "totais": totais,
            "avisos": avisos,
            "nota": "Códigos A (trabalho dependente), A3 (subsídio de férias), A4 (subsídio de Natal) e A21 "
                    "(subsídio de refeição até ao limite isento, rendimento não sujeito), com a retenção de IRS e "
                    "as contribuições do trabalhador para a Segurança Social de cada um."}


def _num(valor: int, tamanho: int) -> str:
    if valor < 0:
        raise ErroDeclaracao("A DMR gerada pelo Dobra não aceita valores negativos: corrija o processamento do mês.")
    return _numero(valor, tamanho)


def _nif_ou_zeros(nif: str | None) -> str:
    return nif if nif and nif.isdigit() and len(nif) == 9 else "0" * 9


def ficheiro_dmr(db: Session, empresa: Empresa, ano: int, mes: int, substituicao: bool = False,
                 hoje: date | None = None) -> tuple[bytes, str, dict]:
    """(conteúdo, nome do ficheiro, resumo) da DMR-AT do mês, no formato de transferência eletrónica."""
    p = _processamento(db, empresa, ano, mes)
    faltas = []
    if not (empresa.nif and validacao.nif_valido(empresa.nif)):
        faltas.append("o NIF da empresa")
    if not (empresa.servico_financas and empresa.servico_financas.isdigit() and len(empresa.servico_financas) == 4):
        faltas.append("o código do serviço de finanças (4 algarismos), em Dados da empresa")
    for r in p.recibos:
        if not (r.get("nif") and validacao.nif_valido(r["nif"])):
            faltas.append(f"o NIF de {r['nome']}")
    if faltas:
        raise ErroDeclaracao("Para gerar o ficheiro da DMR falta: " + "; ".join(faltas) + ".")

    hoje = hoje or date.today()
    local = LOCAL_RENDIMENTO.get(empresa.regiao_sede, "C")
    detalhe = []
    for r in sorted(p.recibos, key=lambda x: x["numero"]):
        for ln in linhas_recibo(r):
            detalhe.append((r["nif"], ln))

    def soma(chave: str, nao_sujeitos: bool | None = None) -> int:
        return sum(ln[chave] for _, ln in detalhe
                   if nao_sujeitos is None or (ln["codigo"] in NAO_SUJEITOS) == nao_sujeitos)

    linhas = [
        "001" + "ASCII" + VERSAO_DMR + hoje.strftime("%Y%m%d") + " " * 154,
        "002" + "DMR-AT" + empresa.nif + f"{ano:04d}{mes:02d}" + "EUR" + " " * 145,
        "003" + empresa.servico_financas + ("2" if substituicao else "1") + "0" + "0" * 8
        + _nif_ou_zeros(empresa.nif_contabilista) + _nif_ou_zeros(empresa.nif_representante or empresa.nif)
        + "  " + "0" * 8 + "0" * 8 + " " + " " * 118,
    ]
    # Quadro 4: sujeitos e isentos (004); não sujeitos e totais (005). Sem quotizações sindicais nem sobretaxa.
    sujeitos = (soma("rendimento", False), soma("retencao_irs", False), soma("contribuicoes", False))
    nao_sujeitos = (soma("rendimento", True), soma("retencao_irs", True), soma("contribuicoes", True))
    totais = tuple(a + b for a, b in zip(sujeitos, nao_sujeitos, strict=True))
    linhas.append("004" + _num(sujeitos[0], 16) + _num(sujeitos[1], 15) + _num(sujeitos[2], 15) + _num(0, 15)
                  + _num(0, 15) + _num(0, 16) + _num(0, 15) * 4 + " " * 17)
    linhas.append("005" + _num(nao_sujeitos[0], 16) + _num(nao_sujeitos[1], 15) + _num(nao_sujeitos[2], 15)
                  + _num(0, 15) + _num(0, 15) + _num(totais[0], 17) + _num(totais[1], 16) + _num(totais[2], 16)
                  + _num(0, 16) + _num(0, 16) + " " * 12)
    for numero, (nif, ln) in enumerate(detalhe, start=1):
        entidade = NIPC_SEGURANCA_SOCIAL if ln["contribuicoes"] else "0" * 9
        linhas.append("006" + _num(numero, 7) + nif + _num(0, 15) + _num(0, 4) + _num(ln["rendimento"], 15)
                      + ln["codigo"].ljust(3) + local.ljust(2) + _num(ln["retencao_irs"], 14)
                      + _num(ln["contribuicoes"], 14) + entidade + "0" * 9 + "0" * 9 + _num(0, 14) + _num(0, 14)
                      + " " * 31)
    registos_dmr = 2 + len(detalhe)   # 004, 005 e 006
    linhas.append("009" + _num(0, 16) + _num(totais[0], 17) + _num(totais[1], 16) + _num(totais[2], 16)
                  + _num(0, 16) + _num(0, 16) + _num(registos_dmr, 7) + " " * 65)
    # Os trailers não contam os próprios header e trailer: 099 conta 003 a 009; 999 conta 002 a 099
    linhas.append("099" + _num(registos_dmr + 2, 9) + " " * 160)
    linhas.append("999" + _num(registos_dmr + 4, 9) + " " * 160)
    for ln in linhas:
        if len(ln) != COMPRIMENTO_DMR:  # salvaguarda: nunca entregar um ficheiro mal formado
            raise ErroDeclaracao(f"Erro interno: registo {ln[:3]} com {len(ln)} caracteres.")
    conteudo = ("\n".join(linhas) + "\n").encode("ascii")
    nome = f"DMR-AT_{empresa.nif}_{ano:04d}{mes:02d}.txt"
    return conteudo, nome, {
        "ficheiro": nome, "linhas": len(detalhe), "substituicao": substituicao,
        "rendimentos": totais[0] / 100, "retencao_irs": totais[1] / 100, "contribuicoes": totais[2] / 100,
    }


def csv_dmr(mapa: dict) -> bytes:
    saida = io.StringIO()
    escritor = csv.writer(saida, delimiter=";")
    escritor.writerow(["NIF", "Nome", "Código", "Rendimento", "Retenção IRS", "Contribuições obrigatórias"])
    for ln in mapa["linhas"]:
        escritor.writerow([ln["nif"] or "", ln["nome"], ln["codigo"],
                           *(f"{ln[k]:.2f}".replace(".", ",")
                             for k in ("rendimento", "retencao_irs", "contribuicoes_obrigatorias"))])
    return ("﻿" + saida.getvalue()).encode("utf-8")
