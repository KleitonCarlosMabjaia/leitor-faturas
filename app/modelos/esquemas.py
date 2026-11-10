"""
Formato das respostas da API.

Separar isto das tabelas permite mudar a base de dados sem mudar o que o
frontend recebe (e o FastAPI usa estes modelos para gerar a documentação em /docs).
"""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.modelos.fatura import LinhaIVA


class FornecedorSaida(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nif: str
    nome: str


class FaturaSaida(BaseModel):
    # from_attributes: permite criar o modelo diretamente a partir de um objeto da tabela
    model_config = ConfigDict(from_attributes=True)

    id: str
    nome_original: str
    tamanho_kb: float
    carregada_em: datetime

    estado_extracao: str
    erro: str | None
    origem: str | None
    confianca: str | None
    estado: str
    paga: bool

    fornecedor_id: int | None
    fornecedor: str | None = Field(validation_alias="nome_fornecedor")
    nif_fornecedor: str | None
    numero_fatura: str | None
    data_emissao: date | None
    data_vencimento: date | None
    valor_sem_iva: float | None
    total_iva: float | None
    total: float | None
    moeda: str | None
    iva: list[LinhaIVA] | None = Field(validation_alias="linhas_iva")

    atcud: str | None
    tipo_documento: str | None

    alertas: list[str] | None
    nif_valido: bool | None
    totais_batem: bool | None
