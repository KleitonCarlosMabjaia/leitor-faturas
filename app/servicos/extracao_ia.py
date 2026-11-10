"""
Extração dos campos da fatura com a API do Claude.

Usamos "saída estruturada": o SDK envia o esquema do modelo Pydantic à API,
a resposta vem obrigatoriamente nesse formato e o SDK valida-a por nós.
Se, mesmo assim, a resposta não for válida, tentamos uma segunda vez.
"""

import logging

import anthropic
from pydantic import ValidationError

from app import config
from app.modelos.fatura import DadosFatura

logger = logging.getLogger(__name__)

TENTATIVAS = 2

INSTRUCOES = """\
És um assistente de contabilidade que lê faturas de fornecedores portuguesas.
Recebes o texto extraído de um PDF e devolves os dados da fatura.

Regras:
- O fornecedor é quem EMITE a fatura, não o cliente (adquirente).
- nif_fornecedor: só os 9 dígitos do NIF do emitente, sem "PT" nem espaços.
- Datas no formato AAAA-MM-DD. Se não houver data de vencimento, usa null.
- Valores em euros como números com ponto decimal (1234.56), sem símbolo de moeda.
- iva: uma entrada por taxa (ex.: 23, 13, 6), com a base tributável e o valor do IVA,
  tal como no quadro-resumo da fatura. Linhas isentas entram com taxa 0.
- valor_sem_iva: soma das bases tributáveis. total: valor final a pagar.
- Copia os valores do documento; não os recalcules nem corrijas.
- confianca: "alta" se todos os campos estão claros, "media" se tiveste de deduzir
  algum, "baixa" se faltam campos importantes ou o documento não parece uma fatura.
"""


class ErroExtracao(Exception):
    """Falha ao obter os dados da fatura através da IA (mensagem já em português)."""


def _cliente() -> anthropic.Anthropic:
    if config.MODO_DEMO:
        raise ErroExtracao("Nesta demonstração a leitura por IA está desligada; só o QR é lido.")
    if not config.CHAVE_API_DEFINIDA:
        raise ErroExtracao(
            "A chave da API da Anthropic não está configurada. "
            "Defina ANTHROPIC_API_KEY no ficheiro .env e reinicie o servidor."
        )
    return anthropic.Anthropic()


def extrair_dados(texto_pdf: str) -> DadosFatura:
    """Envia o texto da fatura ao Claude e devolve os dados validados."""
    cliente = _cliente()
    ultimo_erro = ""

    for tentativa in range(1, TENTATIVAS + 1):
        try:
            resposta = cliente.messages.parse(
                model=config.MODELO_IA,
                max_tokens=16000,
                system=INSTRUCOES,
                messages=[{
                    "role": "user",
                    "content": f"<fatura>\n{texto_pdf}\n</fatura>",
                }],
                output_format=DadosFatura,
                output_config={"effort": config.ESFORCO_IA},
            )
        # Erros de validação: a resposta chegou mas não respeita o modelo -> nova tentativa
        except ValidationError as erro:
            ultimo_erro = "a resposta da IA não tinha o formato esperado"
            logger.warning("Tentativa %d: resposta inválida: %s", tentativa, erro)
            continue
        # Erros da API: não vale a pena repetir (o SDK já repete os 429 e 5xx sozinho)
        except anthropic.AuthenticationError:
            raise ErroExtracao("A chave da API da Anthropic é inválida. Verifique o ficheiro .env.")
        except anthropic.PermissionDeniedError:
            raise ErroExtracao("A chave da API não tem permissão para usar este modelo.")
        except anthropic.NotFoundError:
            raise ErroExtracao(f"O modelo '{config.MODELO_IA}' não existe. Verifique MODELO_IA no .env.")
        except anthropic.RateLimitError:
            raise ErroExtracao("Foram feitos demasiados pedidos à IA. Tente novamente daqui a um minuto.")
        except anthropic.BadRequestError as erro:
            logger.error("Pedido rejeitado pela API: %s", erro.message)
            # A falta de saldo não tem uma classe de erro própria: só se distingue pela mensagem
            if "credit balance" in str(erro.message).lower():
                raise ErroExtracao(
                    "A conta da Anthropic não tem créditos. Adicione créditos em "
                    "console.anthropic.com (Plans & Billing) e carregue em \"Ler\" outra vez."
                )
            raise ErroExtracao("O pedido à IA foi rejeitado. Consulte os registos do servidor.")
        except anthropic.APIStatusError as erro:
            logger.error("Erro %s da API: %s", erro.status_code, erro.message)
            raise ErroExtracao("O serviço de IA está com problemas. Tente novamente mais tarde.")
        except anthropic.APIConnectionError:
            raise ErroExtracao("Sem ligação ao serviço de IA. Verifique a ligação à Internet.")

        if resposta.stop_reason == "refusal":
            raise ErroExtracao("A IA recusou-se a processar este documento.")
        if resposta.stop_reason == "max_tokens":
            ultimo_erro = "a resposta da IA ficou cortada"
            logger.warning("Tentativa %d: resposta cortada (max_tokens)", tentativa)
            continue
        if resposta.parsed_output is None:
            ultimo_erro = "a IA não devolveu dados"
            continue

        return resposta.parsed_output

    raise ErroExtracao(f"Não foi possível ler a fatura: {ultimo_erro} ({TENTATIVAS} tentativas).")
