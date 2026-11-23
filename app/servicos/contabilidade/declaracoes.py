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

2. AT: mapa de apoio da declaração mensal de remunerações (DMR), por trabalhador, com os
   valores a declarar no Portal das Finanças. O Dobra não gera o ficheiro da DMR porque o
   formato técnico da AT não está publicado de forma que se possa validar aqui.
"""

import csv
import io
import unicodedata
from collections import defaultdict
from datetime import date

from sqlalchemy.orm import Session

from app.modelos.tabelas import Empresa, Funcionario, ProcessamentoSalarial
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


# ---------- DMR (AT): mapa de apoio ----------

def mapa_dmr(db: Session, empresa: Empresa, ano: int, mes: int) -> dict:
    """Valores por trabalhador para a declaração mensal de remunerações da AT."""
    p = _processamento(db, empresa, ano, mes)
    linhas = []
    for r in sorted(p.recibos, key=lambda x: x["numero"]):
        linhas.append({
            "nif": r.get("nif"), "nome": r["nome"], "tipo": r["tipo"],
            "codigo": "A",  # rendimentos do trabalho dependente (categoria A)
            "rendimento": r["remuneracao_sujeita"], "retencao_irs": r["irs"],
            "contribuicoes_obrigatorias": r["ss_trabalhador"],
        })
    totais = {k: round(sum(ln[k] for ln in linhas), 2)
              for k in ("rendimento", "retencao_irs", "contribuicoes_obrigatorias")}
    avisos = [f"{ln['nome']} não tem NIF: a DMR exige o NIF de cada trabalhador." for ln in linhas if not ln["nif"]]
    return {"ano": ano, "mes": mes, "nome_mes": salarios.nome_mes(ano, mes), "linhas": linhas, "totais": totais,
            "avisos": avisos,
            "nota": "Rendimentos sujeitos a IRS (código A, trabalho dependente), retenções e contribuições "
                    "obrigatórias do trabalhador. Confirme os códigos de rendimento nas instruções oficiais da "
                    "DMR antes de submeter no Portal das Finanças."}


def csv_dmr(mapa: dict) -> bytes:
    saida = io.StringIO()
    escritor = csv.writer(saida, delimiter=";")
    escritor.writerow(["NIF", "Nome", "Código", "Rendimento", "Retenção IRS", "Contribuições obrigatórias"])
    for ln in mapa["linhas"]:
        escritor.writerow([ln["nif"] or "", ln["nome"], ln["codigo"],
                           *(f"{ln[k]:.2f}".replace(".", ",")
                             for k in ("rendimento", "retencao_irs", "contribuicoes_obrigatorias"))])
    return ("﻿" + saida.getvalue()).encode("utf-8")
