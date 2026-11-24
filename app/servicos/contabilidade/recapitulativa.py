"""
Mapa de apoio da declaração recapitulativa (art. 30.º do RITI): vendas isentas a sujeitos passivos
de outros Estados-membros, por cliente (número de IVA) e tipo de operação.

Vem das mesmas linhas que o campo 7 da declaração periódica (vendas sem IVA com o motivo de
isenção M16, M40 ou M41 a clientes com número de IVA de outro Estado-membro), por isso o total
tem de ser igual ao campo 7 do mesmo período.

Tipos de operação: 1 transmissões intracomunitárias de bens (M16, M41); 5 prestações de serviços
(M40). As operações triangulares (tipo 4) não se distinguem nas vendas: corrija na aplicação da AT.
O Dobra não gera o ficheiro da recapitulativa: os valores preenchem-se no Portal das Finanças.
"""

import csv
import io
import re
from collections import defaultdict
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modelos.tabelas import Conta, FaturaVenda, Lancamento, LinhaLancamento
from app.servicos.contabilidade.iva import PREFIXOS_UE

TIPO_OPERACAO = {"M16": "1", "M41": "1", "M40": "5"}
NOMES_TIPOS = {"1": "Transmissões intracomunitárias de bens", "4": "Operações triangulares",
               "5": "Prestações de serviços (art. 6.º, n.º 6, alínea a) do CIVA)"}


def _r(v: float) -> float:
    return round(v + 0.0, 2)


def _euros(v: float) -> str:
    return f"{_r(v):.2f} €".replace(".", ",")


def _numero_iva(nif: str | None) -> tuple[str, str] | None:
    """('ES', 'B12345678') para um número de IVA de outro Estado-membro; None se não for."""
    limpo = re.sub(r"[\s.\-]", "", (nif or "")).upper()
    if len(limpo) > 2 and limpo[:2] in PREFIXOS_UE:
        return limpo[:2], limpo[2:]
    return None


def mapa(db: Session, empresa_id: int, de: date, ate: date) -> dict:
    linhas = db.execute(
        select(LinhaLancamento.descricao, LinhaLancamento.debito, LinhaLancamento.credito,
               FaturaVenda.cliente_nif, FaturaVenda.cliente_nome)
        .join(LinhaLancamento.conta).join(LinhaLancamento.lancamento)
        .join(FaturaVenda, FaturaVenda.id == Lancamento.venda_id)
        .where(Lancamento.empresa_id == empresa_id, Lancamento.data >= de, Lancamento.data <= ate,
               LinhaLancamento.tipo_iva == "base", func.coalesce(LinhaLancamento.taxa_iva, 0) == 0,
               Conta.codigo.like("7%"))
    ).all()
    grupos: dict[tuple[str, str, str], float] = defaultdict(float)
    nomes: dict[tuple[str, str], str] = {}
    sem_numero: dict[str, float] = defaultdict(float)
    for descricao, debito, credito, nif, nome in linhas:
        achado = re.search(r"\b(M\d{2})\b", descricao or "")
        tipo = TIPO_OPERACAO.get(achado.group(1) if achado else "")
        if not tipo:
            continue
        numero = _numero_iva(nif)
        if numero is None:
            if achado.group(1) == "M16":   # M40/M41 fora da UE são exportações de serviços: não entram aqui
                sem_numero[nome or nif or "Cliente sem número de IVA"] += credito - debito
            continue
        grupos[(*numero, tipo)] += credito - debito
        nomes[numero] = nome or ""

    resultado = [{"pais": pais, "numero_iva": numero, "cliente": nomes.get((pais, numero), ""), "tipo": tipo,
                  "tipo_descricao": NOMES_TIPOS[tipo], "valor": _r(valor)}
                 for (pais, numero, tipo), valor in sorted(grupos.items()) if _r(valor)]
    total = _r(sum(ln["valor"] for ln in resultado))
    avisos = [f"{nome}: {_euros(v)} com o motivo M16 mas sem número de IVA de outro Estado-membro "
              "(a transmissão só é isenta com o número de IVA do adquirente)"
              for nome, v in sorted(sem_numero.items()) if _r(v)]
    if any(ln["valor"] < 0 for ln in resultado):
        avisos.append("Há clientes com valor negativo (notas de crédito sem vendas no período): na recapitulativa "
                      "as regularizações indicam-se no período da operação original.")
    return {"de": de, "ate": ate, "linhas": resultado, "total": total,
            "por_tipo": {t: _r(sum(ln["valor"] for ln in resultado if ln["tipo"] == t)) for t in ("1", "5")},
            "avisos": avisos}


def csv_mapa(dados: dict) -> bytes:
    saida = io.StringIO()
    escritor = csv.writer(saida, delimiter=";")
    escritor.writerow(["País", "Número de IVA", "Cliente", "Tipo de operação", "Valor"])
    for ln in dados["linhas"]:
        escritor.writerow([ln["pais"], ln["numero_iva"], ln["cliente"], ln["tipo"],
                           f"{ln['valor']:.2f}".replace(".", ",")])
    return ("﻿" + saida.getvalue()).encode("utf-8")
