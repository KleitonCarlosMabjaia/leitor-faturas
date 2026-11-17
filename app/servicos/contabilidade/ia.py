"""
Ligação partilhada à API do Claude para a contabilidade (classificação e assistente).

Usa o modelo configurado em MODELO_IA e o mecanismo de "fallback" do servidor:
se o modelo principal recusar um pedido, a API repete-o noutro modelo na mesma
chamada, em vez de devolver só uma recusa.
"""

import logging

import anthropic

from app import config

logger = logging.getLogger(__name__)

BETAS = ["server-side-fallback-2026-07-01"]
FALLBACKS = "default"


class ErroIA(Exception):
    """Falha ao falar com a IA (mensagem já em português, pronta a mostrar)."""


def cliente() -> anthropic.Anthropic:
    if config.MODO_DEMO:
        raise ErroIA("Nesta demonstração a IA está desligada, para os visitantes não gastarem créditos.")
    if not config.CHAVE_API_DEFINIDA:
        raise ErroIA("A IA não está configurada: defina ANTHROPIC_API_KEY no ficheiro .env e reinicie o servidor.")
    return anthropic.Anthropic()


def traduzir_erro(erro: Exception) -> ErroIA:
    """Converte os erros do SDK numa mensagem clara. Do mais específico para o mais geral."""
    if isinstance(erro, anthropic.AuthenticationError):
        return ErroIA("A chave da API da Anthropic é inválida. Verifique o ficheiro .env.")
    if isinstance(erro, anthropic.PermissionDeniedError):
        return ErroIA("A chave da API não tem permissão para usar este modelo.")
    if isinstance(erro, anthropic.NotFoundError):
        return ErroIA(f"O modelo '{config.MODELO_IA}' não existe. Verifique MODELO_IA no .env.")
    if isinstance(erro, anthropic.RateLimitError):
        return ErroIA("Foram feitos demasiados pedidos à IA. Tente novamente daqui a um minuto.")
    if isinstance(erro, anthropic.BadRequestError):
        logger.error("Pedido rejeitado pela API: %s", erro.message)
        if "credit balance" in str(erro.message).lower():
            return ErroIA("A conta da Anthropic não tem créditos. Adicione créditos em console.anthropic.com.")
        return ErroIA("O pedido à IA foi rejeitado. Consulte os registos do servidor.")
    if isinstance(erro, anthropic.APIStatusError):
        logger.error("Erro %s da API: %s", erro.status_code, erro.message)
        return ErroIA("O serviço de IA está com problemas. Tente novamente mais tarde.")
    if isinstance(erro, anthropic.APIConnectionError):
        return ErroIA("Sem ligação ao serviço de IA. Verifique a ligação à Internet.")
    raise erro
