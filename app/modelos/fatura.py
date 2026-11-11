"""
Modelos Pydantic que descrevem os dados de uma fatura.

- CamposFatura: os campos e as regras de validação, partilhados por todos.
- DadosFatura:  o que a IA devolve (campos + grau de confiança). Também gera o
                esquema JSON enviado ao Claude (saída estruturada).
- EdicaoFatura: o que o utilizador envia na página de revisão.
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class LinhaIVA(BaseModel):
    """Uma linha do quadro-resumo do IVA (uma por taxa)."""

    taxa: float = Field(description="Taxa de IVA em percentagem, por exemplo 23, 13 ou 6.")
    base: float = Field(description="Base tributável sujeita a esta taxa, em euros.")
    valor: float = Field(description="Valor do IVA desta taxa, em euros.")


class CamposFatura(BaseModel):
    """Campos de uma fatura de fornecedor."""

    fornecedor: str = Field(description="Nome ou firma do emitente da fatura.")
    nif_fornecedor: str = Field(description="NIF do emitente, só os dígitos, sem o prefixo PT.")
    numero_fatura: str = Field(description="Número da fatura tal como aparece no documento.")
    data_emissao: str = Field(description="Data de emissão no formato AAAA-MM-DD.")
    data_vencimento: str | None = Field(
        description="Data de vencimento no formato AAAA-MM-DD, ou null se não existir."
    )
    valor_sem_iva: float = Field(description="Total sem IVA (soma das bases tributáveis).")
    iva: list[LinhaIVA] = Field(description="Uma entrada por cada taxa de IVA aplicada.")
    total: float = Field(description="Total a pagar, com IVA.")
    moeda: str = Field(description="Código ISO da moeda, normalmente EUR.")

    @field_validator("data_emissao", "data_vencimento")
    @classmethod
    def validar_data(cls, valor: str | None) -> str | None:
        """Garante que a data é real e está no formato AAAA-MM-DD."""
        if valor is None or valor == "":
            return None
        date.fromisoformat(valor)  # lança ValueError se o formato ou a data forem inválidos
        return valor

    @field_validator("nif_fornecedor")
    @classmethod
    def limpar_nif(cls, valor: str) -> str:
        """Remove o prefixo PT e espaços. A validade do NIF é verificada à parte."""
        valor = valor.strip().upper().replace(" ", "")
        return valor.removeprefix("PT")

    @field_validator("fornecedor", "numero_fatura")
    @classmethod
    def tirar_espacos(cls, valor: str) -> str:
        return valor.strip()

    @field_validator("moeda")
    @classmethod
    def normalizar_moeda(cls, valor: str) -> str:
        return valor.strip().upper() or "EUR"


class DadosFatura(CamposFatura):
    """Resposta da IA: os campos e o grau de certeza da leitura."""

    confianca: Literal["alta", "media", "baixa"] = Field(
        description="Grau de certeza na leitura: baixa se faltarem campos ou o texto for confuso."
    )


class EdicaoFatura(CamposFatura):
    """Dados corrigidos pelo utilizador na página de revisão."""

    confirmar: bool = False  # True quando se carrega em "Confirmar dados"
