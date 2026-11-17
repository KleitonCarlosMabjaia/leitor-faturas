"""
Calendário das obrigações fiscais e contributivas de uma pequena empresa (IRC, regime geral).

Os prazos que terminam a um sábado, domingo ou feriado nacional passam para o dia útil
seguinte (art. 20.º do CPPT e art. 279.º do Código Civil). Os prazos são indicativos:
leis e despachos podem prorrogá-los. Confirme sempre no Portal das Finanças.
"""

from dataclasses import dataclass
from datetime import date, timedelta

AVISO = ("Prazos indicativos (regime geral). Prorrogações por despacho não estão incluídas: "
         "confirme no Portal das Finanças.")

NOMES_MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro",
               "outubro", "novembro", "dezembro"]


def pascoa(ano: int) -> date:
    """Domingo de Páscoa (algoritmo de Meeus/Jones/Butcher, calendário gregoriano)."""
    a, b, c = ano % 19, ano // 100, ano % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    m = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 22 * m) // 451
    mes, dia = divmod(h + m - 7 * n + 114, 31)
    return date(ano, mes, dia + 1)


def feriados(ano: int) -> set[date]:
    """Feriados nacionais obrigatórios (Código do Trabalho, art. 234.º)."""
    domingo_pascoa = pascoa(ano)
    fixos = [(1, 1), (4, 25), (5, 1), (6, 10), (8, 15), (10, 5), (11, 1), (12, 1), (12, 8), (12, 25)]
    return {date(ano, m, d) for m, d in fixos} | {
        domingo_pascoa - timedelta(days=2),   # Sexta-feira Santa
        domingo_pascoa,                       # Páscoa
        domingo_pascoa + timedelta(days=60),  # Corpo de Deus
    }


def dia_util(dia: date) -> date:
    """O próprio dia, se for útil; senão, o dia útil seguinte."""
    while dia.weekday() >= 5 or dia in feriados(dia.year):
        dia += timedelta(days=1)
    return dia


def _somar_meses(ano: int, mes: int, n: int) -> tuple[int, int]:
    total = ano * 12 + (mes - 1) + n
    return total // 12, total % 12 + 1


@dataclass
class Obrigacao:
    chave: str
    nome: str
    descricao: str
    prazo: date          # já ajustado ao dia útil
    periodo: str         # a que período respeita
    categoria: str       # iva | saft | salarios | irc | outros

    def para_dict(self, hoje: date) -> dict:
        return {
            "chave": self.chave, "nome": self.nome, "descricao": self.descricao, "prazo": self.prazo,
            "periodo": self.periodo, "categoria": self.categoria, "dias": (self.prazo - hoje).days,
        }


def _mensais(ano: int, mes: int) -> list[Obrigacao]:
    """Obrigações que respeitam ao mês (ano, mes) e se cumprem no mês seguinte."""
    a, m = _somar_meses(ano, mes, 1)
    periodo = f"{NOMES_MESES[mes - 1]} de {ano}"
    return [
        Obrigacao("saft", "Comunicação do SAF-T (PT) de faturação", "Envio das faturas emitidas à AT.",
                  dia_util(date(a, m, 5)), periodo, "saft"),
        Obrigacao("dmr", "Declaração Mensal de Remunerações (AT) e declaração à Segurança Social",
                  "Remunerações pagas e retenções de IRS do mês.", dia_util(date(a, m, 10)), periodo, "salarios"),
        Obrigacao("retencoes", "Pagamento das retenções na fonte (IRS e IRC)",
                  "Retenções feitas a trabalhadores e prestadores de serviços (conta 242).",
                  dia_util(date(a, m, 20)), periodo, "salarios"),
        Obrigacao("tsu", "Pagamento das contribuições para a Segurança Social",
                  "TSU da entidade empregadora e dos trabalhadores (conta 245).",
                  dia_util(date(a, m, 20)), periodo, "salarios"),
    ]


def _iva(ano: int, mes: int, regime: str) -> list[Obrigacao]:
    """Declaração periódica e pagamento do IVA do período que termina no mês (ano, mes)."""
    if regime == "trimestral" and mes % 3:
        return []
    a, m = _somar_meses(ano, mes, 2)
    if regime == "trimestral":
        periodo = f"{mes // 3}.º trimestre de {ano}"
    else:
        periodo = f"{NOMES_MESES[mes - 1]} de {ano}"
    return [
        Obrigacao("iva_dp", "Declaração periódica do IVA", f"Regime {regime}.", dia_util(date(a, m, 20)),
                  periodo, "iva"),
        Obrigacao("iva_pagamento", "Pagamento do IVA", "Se o apuramento der imposto a pagar (conta 2436).",
                  dia_util(date(a, m, 25)), periodo, "iva"),
    ]


def _anuais(ano: int) -> list[Obrigacao]:
    anterior = f"exercício de {ano - 1}"
    return [
        Obrigacao("inventario", "Comunicação do inventário", "Inventário a 31 de dezembro (se aplicável).",
                  dia_util(date(ano, 1, 31)), anterior, "outros"),
        Obrigacao("modelo10", "Declaração Modelo 10", "Rendimentos pagos a residentes e retenções na fonte.",
                  dia_util(date(ano, 2, 10)), anterior, "outros"),
        Obrigacao("modelo22", "Declaração Modelo 22 (IRC)", "Declaração e pagamento do IRC do exercício.",
                  dia_util(date(ano, 5, 31)), anterior, "irc"),
        Obrigacao("ies", "IES / Declaração anual", "Informação Empresarial Simplificada.",
                  dia_util(date(ano, 7, 15)), anterior, "irc"),
        Obrigacao("ppc1", "1.º pagamento por conta do IRC", "Com base no IRC do exercício anterior.",
                  dia_util(date(ano, 7, 31)), f"exercício de {ano}", "irc"),
        Obrigacao("ppc2", "2.º pagamento por conta do IRC", "Com base no IRC do exercício anterior.",
                  dia_util(date(ano, 9, 30)), f"exercício de {ano}", "irc"),
        Obrigacao("ppc3", "3.º pagamento por conta do IRC", "Com base no IRC do exercício anterior.",
                  dia_util(date(ano, 12, 15)), f"exercício de {ano}", "irc"),
    ]


def proximas(hoje: date, regime_iva: str = "trimestral", dias: int = 75) -> list[dict]:
    """Obrigações com prazo entre hoje e daqui a `dias` dias, por ordem de prazo."""
    candidatas: list[Obrigacao] = []
    for desvio in range(-3, 3):
        ano, mes = _somar_meses(hoje.year, hoje.month, desvio)
        candidatas += _mensais(ano, mes) + _iva(ano, mes, regime_iva)
    for ano in (hoje.year, hoje.year + 1):
        candidatas += _anuais(ano)
    limite = hoje + timedelta(days=dias)
    escolhidas = sorted((o for o in candidatas if hoje <= o.prazo <= limite), key=lambda o: (o.prazo, o.nome))
    return [o.para_dict(hoje) for o in escolhidas]


def periodo_iva(hoje: date, regime_iva: str) -> tuple[date, date, str]:
    """Último período de IVA já terminado: (início, fim, descrição). Útil para o apuramento."""
    if regime_iva == "mensal":
        ano, mes = _somar_meses(hoje.year, hoje.month, -1)
        inicio = date(ano, mes, 1)
        fim = date(*_somar_meses(ano, mes, 1), 1) - timedelta(days=1)
        return inicio, fim, f"{NOMES_MESES[mes - 1]} de {ano}"
    trimestre_atual = (hoje.month - 1) // 3
    ano, mes = _somar_meses(hoje.year, trimestre_atual * 3 + 1, -3)
    inicio = date(ano, mes, 1)
    fim = date(*_somar_meses(ano, mes, 3), 1) - timedelta(days=1)
    return inicio, fim, f"{(mes - 1) // 3 + 1}.º trimestre de {ano}"
