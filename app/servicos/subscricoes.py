"""
Planos, período experimental e subscrições (Stripe).

Planos
- Essencial (19 €/mês): compras (leitura de faturas, análise, exportação), até 2 utilizadores.
- Contabilidade (49 €/mês): tudo, até 5 utilizadores.
- Gabinete (sob consulta): tudo, utilizadores ilimitados; ativado pelo administrador do serviço.

Os limites do plano aplicam-se sempre. A faturação (período experimental, pagamentos em atraso)
só se aplica quando o Stripe está configurado (STRIPE_SECRET_KEY): sem ele, a aplicação funciona
como até aqui, sem cobranças nem bloqueios.

Bloqueio suave: com o período experimental terminado sem subscrição, ou com um pagamento em
atraso há mais de DIAS_TOLERANCIA dias, a empresa continua a ver e a exportar tudo, mas não pode
alterar dados até regularizar.

Integração com o Stripe pela API REST (httpx), sem SDK:
- Checkout para subscrever (com client_reference_id = id da empresa);
- Portal do cliente para mudar de cartão, ver faturas ou cancelar;
- Webhooks assinados (HMAC SHA-256) para saber o estado da subscrição.
"""

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import httpx

from app import config
from app.modelos.tabelas import Empresa

ESTADO_EXPERIMENTAL = "experimental"
ESTADO_ATIVA = "ativa"
ESTADO_EM_ATRASO = "em_atraso"
ESTADO_CANCELADA = "cancelada"

FUNC_COMPRAS = "compras"
FUNC_CONTABILIDADE = "contabilidade"
FUNC_GESTAO = "gestao"


@dataclass(frozen=True)
class Plano:
    chave: str
    nome: str
    preco_mensal: float | None      # sem IVA; None = sob consulta
    max_utilizadores: int | None    # None = ilimitados
    funcionalidades: frozenset[str]
    descricao: str


PLANOS = {
    "essencial": Plano("essencial", "Essencial", 19.0, 2, frozenset({FUNC_COMPRAS, FUNC_GESTAO}),
                       "Leitura de faturas, gestão, stock e tesouraria"),
    "contabilidade": Plano("contabilidade", "Contabilidade", 49.0, 5,
                           frozenset({FUNC_COMPRAS, FUNC_GESTAO, FUNC_CONTABILIDADE}),
                           "Gestão e contabilidade completa da empresa"),
    "gabinete": Plano("gabinete", "Gabinete", None, None,
                      frozenset({FUNC_COMPRAS, FUNC_GESTAO, FUNC_CONTABILIDADE}),
                      "Para contabilistas com vários clientes"),
}
PLANOS_COM_CHECKOUT = ("essencial", "contabilidade")


class ErroSubscricao(Exception):
    """Mensagem pronta para mostrar."""


def faturacao_ativa() -> bool:
    return bool(config.STRIPE_SECRET_KEY) and not config.MODO_DEMO


def plano(empresa: Empresa) -> Plano:
    return PLANOS.get(empresa.plano or "contabilidade", PLANOS["contabilidade"])


def tem_funcionalidade(empresa: Empresa, funcionalidade: str) -> bool:
    return funcionalidade in plano(empresa).funcionalidades


def dias_experimentais(empresa: Empresa, hoje: date | None = None) -> int | None:
    if empresa.estado_subscricao != ESTADO_EXPERIMENTAL or not empresa.experimental_ate:
        return None
    # Negativo quando o período já terminou (a página mostra "terminado")
    return (empresa.experimental_ate - (hoje or date.today())).days


def so_leitura(empresa: Empresa, hoje: date | None = None) -> str | None:
    """Motivo do bloqueio suave, ou None se a empresa pode alterar dados."""
    if not faturacao_ativa():
        return None
    hoje = hoje or date.today()
    estado = empresa.estado_subscricao
    if estado == ESTADO_EXPERIMENTAL and empresa.experimental_ate and hoje > empresa.experimental_ate:
        return "O período experimental terminou. Escolha um plano para continuar a registar dados."
    if estado == ESTADO_EM_ATRASO:
        desde = empresa.em_atraso_desde or hoje
        if (hoje - desde).days > config.DIAS_TOLERANCIA_PAGAMENTO:
            return "Há um pagamento em atraso. Atualize o meio de pagamento para continuar a registar dados."
    if estado == ESTADO_CANCELADA:
        return "A subscrição foi cancelada. Escolha um plano para continuar a registar dados."
    return None


def descrever(empresa: Empresa, utilizadores_ativos: int) -> dict:
    p = plano(empresa)
    return {
        "faturacao_ativa": faturacao_ativa(),
        "plano": p.chave,
        "plano_nome": p.nome,
        "preco_mensal": p.preco_mensal,
        "max_utilizadores": p.max_utilizadores,
        "utilizadores_ativos": utilizadores_ativos,
        "funcionalidades": sorted(p.funcionalidades),
        "estado": empresa.estado_subscricao,
        "experimental_ate": empresa.experimental_ate.isoformat() if empresa.experimental_ate else None,
        "dias_experimentais": dias_experimentais(empresa),
        "fim_periodo": empresa.fim_periodo.isoformat() if empresa.fim_periodo else None,
        "cancela_no_fim": bool(empresa.cancela_no_fim),
        "so_leitura": so_leitura(empresa),
        "tem_cliente_stripe": bool(empresa.stripe_cliente_id),
        "planos": [
            {"chave": x.chave, "nome": x.nome, "preco_mensal": x.preco_mensal, "max_utilizadores": x.max_utilizadores,
             "descricao": x.descricao, "checkout": x.chave in PLANOS_COM_CHECKOUT}
            for x in PLANOS.values()
        ],
    }


def nova_empresa_experimental(empresa: Empresa, chave_plano: str, hoje: date | None = None) -> None:
    empresa.plano = chave_plano if chave_plano in PLANOS_COM_CHECKOUT else "contabilidade"
    empresa.estado_subscricao = ESTADO_EXPERIMENTAL
    empresa.experimental_ate = (hoje or date.today()) + timedelta(days=config.DIAS_EXPERIMENTAIS)


# ---------- Stripe: pedidos ----------

def _preco_do_plano(chave: str) -> str:
    preco = {"essencial": config.STRIPE_PRECO_ESSENCIAL, "contabilidade": config.STRIPE_PRECO_CONTABILIDADE}.get(chave)
    if not preco:
        raise ErroSubscricao("Este plano não tem pagamento online. Fale connosco.")
    return preco


def _stripe(metodo: str, caminho: str, dados: dict | None = None) -> dict:
    """Pedido à API do Stripe (substituído nos testes: nunca há rede)."""
    resposta = httpx.request(
        metodo, f"https://api.stripe.com/v1/{caminho}", data=dados, timeout=20,
        auth=(config.STRIPE_SECRET_KEY, ""), headers={"Stripe-Version": "2024-06-20"},
    )
    if resposta.status_code >= 400:
        raise ErroSubscricao("O serviço de pagamentos não respondeu como esperado. Tente daqui a pouco.")
    return resposta.json()


def sessao_checkout(empresa: Empresa, chave_plano: str, email_cliente: str) -> str:
    """Endereço da página de pagamento do Stripe para subscrever o plano."""
    if not faturacao_ativa():
        raise ErroSubscricao("Os pagamentos online ainda não estão configurados.")
    dados = {
        "mode": "subscription",
        "line_items[0][price]": _preco_do_plano(chave_plano),
        "line_items[0][quantity]": "1",
        "client_reference_id": str(empresa.id),
        "success_url": f"{config.URL_PUBLICO}/subscricao?pagamento=concluido",
        "cancel_url": f"{config.URL_PUBLICO}/subscricao",
        "locale": "pt",
        "allow_promotion_codes": "true",
        "billing_address_collection": "required",
        "tax_id_collection[enabled]": "true",
        "automatic_tax[enabled]": "true" if config.STRIPE_IVA_AUTOMATICO else "false",
        "subscription_data[metadata][empresa_id]": str(empresa.id),
        "metadata[empresa_id]": str(empresa.id),
    }
    if empresa.stripe_cliente_id:
        dados["customer"] = empresa.stripe_cliente_id
        if config.STRIPE_IVA_AUTOMATICO:
            dados["customer_update[address]"] = "auto"
            dados["customer_update[name]"] = "auto"
    else:
        dados["customer_email"] = email_cliente
    return _stripe("POST", "checkout/sessions", dados)["url"]


def sessao_portal(empresa: Empresa) -> str:
    """Portal do Stripe: mudar o cartão, ver e descarregar faturas, mudar de plano ou cancelar."""
    if not faturacao_ativa() or not empresa.stripe_cliente_id:
        raise ErroSubscricao("Ainda não há uma subscrição associada a esta empresa.")
    dados = {"customer": empresa.stripe_cliente_id, "return_url": f"{config.URL_PUBLICO}/subscricao", "locale": "pt"}
    return _stripe("POST", "billing_portal/sessions", dados)["url"]


# ---------- Stripe: webhooks ----------

def verificar_assinatura(corpo: bytes, cabecalho: str | None, agora: float | None = None) -> dict:
    """Confirma que o evento veio do Stripe (assinatura HMAC com o segredo do webhook) e é recente."""
    if not config.STRIPE_WEBHOOK_SECRET or not cabecalho:
        raise ErroSubscricao("Assinatura em falta.")
    partes = {}
    for par in cabecalho.split(","):
        chave, _, valor = par.partition("=")
        partes.setdefault(chave.strip(), []).append(valor.strip())
    try:
        instante = int(partes["t"][0])
    except (KeyError, ValueError):
        raise ErroSubscricao("Assinatura inválida.")
    if abs((agora or time.time()) - instante) > 300:
        raise ErroSubscricao("Evento demasiado antigo.")
    assinado = f"{instante}.".encode() + corpo
    esperada = hmac.new(config.STRIPE_WEBHOOK_SECRET.encode(), assinado, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(esperada, v) for v in partes.get("v1", [])):
        raise ErroSubscricao("Assinatura inválida.")
    return json.loads(corpo)


def _plano_do_preco(preco: str | None) -> str | None:
    if not preco:
        return None
    if preco == config.STRIPE_PRECO_ESSENCIAL:
        return "essencial"
    if preco == config.STRIPE_PRECO_CONTABILIDADE:
        return "contabilidade"
    return None


def _data(segundos: int | None) -> date | None:
    return datetime.fromtimestamp(segundos).date() if segundos else None


def aplicar_evento(evento: dict, obter_empresa, empresa_por_cliente) -> Empresa | None:
    """
    Atualiza a empresa a partir de um evento do Stripe. Devolve a empresa alterada (ou None se o
    evento não interessa ou não corresponde a nenhuma empresa). Os eventos podem chegar repetidos
    ou fora de ordem: cada um leva o estado completo da subscrição, por isso aplicar duas vezes dá o mesmo.
    """
    tipo = evento.get("type", "")
    obj = evento.get("data", {}).get("object", {})

    if tipo == "checkout.session.completed":
        empresa = obter_empresa(obj.get("client_reference_id") or obj.get("metadata", {}).get("empresa_id"))
        if empresa is None:
            return None
        empresa.stripe_cliente_id = obj.get("customer") or empresa.stripe_cliente_id
        empresa.stripe_subscricao_id = obj.get("subscription") or empresa.stripe_subscricao_id
        empresa.estado_subscricao = ESTADO_ATIVA
        empresa.em_atraso_desde = None
        return empresa

    if tipo.startswith("customer.subscription."):
        empresa = obter_empresa(obj.get("metadata", {}).get("empresa_id")) or empresa_por_cliente(obj.get("customer"))
        if empresa is None:
            return None
        empresa.stripe_cliente_id = obj.get("customer") or empresa.stripe_cliente_id
        empresa.stripe_subscricao_id = obj.get("id") or empresa.stripe_subscricao_id
        itens = obj.get("items", {}).get("data", [])
        novo_plano = _plano_do_preco(itens[0].get("price", {}).get("id") if itens else None)
        if novo_plano:
            empresa.plano = novo_plano
        estado = obj.get("status")
        if tipo == "customer.subscription.deleted" or estado in ("canceled", "incomplete_expired"):
            empresa.estado_subscricao = ESTADO_CANCELADA
        elif estado in ("active", "trialing"):
            empresa.estado_subscricao = ESTADO_ATIVA
            empresa.em_atraso_desde = None
        elif estado in ("past_due", "unpaid", "incomplete"):
            if empresa.estado_subscricao != ESTADO_EM_ATRASO:
                empresa.em_atraso_desde = date.today()
            empresa.estado_subscricao = ESTADO_EM_ATRASO
        # Até à API 2025-03-31.basil o fim do período vinha na subscrição; desde então vem em cada item
        fim = obj.get("current_period_end") or (itens[0].get("current_period_end") if itens else None)
        empresa.fim_periodo = _data(fim) or empresa.fim_periodo
        empresa.cancela_no_fim = bool(obj.get("cancel_at_period_end"))
        return empresa

    if tipo in ("invoice.payment_failed", "invoice.paid"):
        empresa = empresa_por_cliente(obj.get("customer"))
        if empresa is None:
            return None
        if tipo == "invoice.payment_failed":
            if empresa.estado_subscricao != ESTADO_EM_ATRASO:
                empresa.em_atraso_desde = date.today()
            empresa.estado_subscricao = ESTADO_EM_ATRASO
        else:
            empresa.estado_subscricao = ESTADO_ATIVA
            empresa.em_atraso_desde = None
        return empresa
    return None
