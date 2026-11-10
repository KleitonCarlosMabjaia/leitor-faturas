"""
Verificações de negócio sobre os dados extraídos.

Estas funções não dependem da IA nem da base de dados, por isso são fáceis
de testar e de reutilizar.
"""

from app.modelos.fatura import CamposFatura

TOLERANCIA_TOTAIS = 0.02  # euros: absorve arredondamentos linha a linha

# Primeiros dígitos atribuídos pela AT (pessoas singulares, coletivas, entidades públicas, etc.)
PREFIXOS_UM_DIGITO = {"1", "2", "3", "5", "6", "8", "9"}
PREFIXOS_DOIS_DIGITOS = {"45", "70", "71", "72", "74", "75", "77", "78", "79"}


def nif_valido(nif: str) -> bool:
    """
    Valida um NIF português: 9 dígitos, prefixo atribuído e dígito de controlo.

    O dígito de controlo calcula-se com pesos 9, 8, ..., 2 sobre os 8 primeiros
    dígitos (módulo 11). Se o resto for 0 ou 1, o controlo é 0.
    """
    if not nif or len(nif) != 9 or not nif.isdigit():
        return False
    if nif[0] not in PREFIXOS_UM_DIGITO and nif[:2] not in PREFIXOS_DOIS_DIGITOS:
        return False

    soma = sum(int(digito) * peso for digito, peso in zip(nif[:8], range(9, 1, -1)))
    resto = soma % 11
    controlo = 0 if resto < 2 else 11 - resto
    return controlo == int(nif[8])


def diferenca_totais(dados: CamposFatura) -> float:
    """Diferença entre o total declarado e (valor sem IVA + soma do IVA)."""
    soma_iva = sum(linha.valor for linha in dados.iva)
    return round(dados.total - (dados.valor_sem_iva + soma_iva), 2)


def totais_batem(dados: CamposFatura) -> bool:
    # Arredondar antes de comparar evita erros de vírgula flutuante (0.02 vs 0.0200001)
    return abs(diferenca_totais(dados)) <= TOLERANCIA_TOTAIS


def verificar(dados: CamposFatura) -> dict:
    """
    Corre todas as verificações e devolve um resumo com alertas legíveis.
    Serve tanto para os dados lidos automaticamente como para os editados.
    """
    alertas = []

    if not dados.fornecedor:
        alertas.append("Falta o nome do fornecedor.")

    nif_ok = nif_valido(dados.nif_fornecedor)
    if not nif_ok:
        alertas.append(f"O NIF do fornecedor ({dados.nif_fornecedor or 'vazio'}) não é válido.")

    diferenca = diferenca_totais(dados)
    totais_ok = abs(diferenca) <= TOLERANCIA_TOTAIS
    if not totais_ok:
        alertas.append(
            f"Os valores não batem certo: o total difere {diferenca:+.2f} € "
            "da soma do valor sem IVA com o IVA."
        )

    # Só os dados vindos da IA têm grau de confiança
    if getattr(dados, "confianca", None) == "baixa":
        alertas.append("A leitura tem confiança baixa. Confirme os dados com o PDF.")

    return {
        "nif_valido": nif_ok,
        "totais_batem": totais_ok,
        "diferenca_totais": diferenca,
        "alertas": alertas,
    }
