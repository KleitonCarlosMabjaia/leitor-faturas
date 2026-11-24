"""
Webhooks da API pública: avisar outros programas (ERP, Power BI, Zapier...) quando há novidades.

Eventos "leves", como no Stripe ou no GitHub: cada aviso diz o que aconteceu e o identificador
do objeto; os detalhes vêm da API (/api/v1/...). Assim o aviso nunca leva dados a mais e o
programa recebe sempre a versão mais recente.

    POST <url do cliente>
    Content-Type: application/json
    Dobra-Evento: fatura.confirmada
    Dobra-Entrega: 123
    Dobra-Assinatura: t=1764000000,v1=<HMAC-SHA256 hexadecimal de "t.corpo" com o segredo>
    {"id": 123, "evento": "fatura.confirmada", "criado_em": "2026-11-24T10:00:00",
     "dados": {"id": "a1b2c3", "url": "/api/v1/faturas/a1b2c3"}}

- O evento fica gravado na mesma transação que a alteração (se a alteração falhar, não há aviso).
- A entrega é feita depois, fora do pedido: em caso de erro (rede, resposta que não seja 2xx)
  volta a tentar com intervalos crescentes (1 min, 5 min, 30 min, 2 h, 12 h) e desiste à 6.ª.
  Um endereço com 20 falhas seguidas é desativado (o administrador vê e pode reativar).
- Só endereços https públicos: endereços internos (localhost, redes privadas, metadados da
  nuvem) são recusados, também no momento do envio (o DNS pode mudar).
"""

import hashlib
import hmac
import ipaddress
import json
import logging
import secrets
import socket
import threading
import time
from datetime import datetime, timedelta
from urllib.parse import urlsplit

import httpx
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app import config
from app.modelos.tabelas import EntregaWebhook, WebhookEndpoint

logger = logging.getLogger(__name__)

EVENTOS = {
    "fatura.carregada": "Fatura de compra carregada (ainda por rever)",
    "fatura.confirmada": "Fatura de compra confirmada",
    "venda.registada": "Fatura emitida registada (manual ou do SAF-T de faturação)",
    "lancamento.criado": "Lançamento contabilístico criado (incluindo estornos)",
    "movimentos.importados": "Movimentos bancários importados (extrato, banco ou API)",
}
EVENTO_TESTE = "teste"
MAX_ENDPOINTS = 5
INTERVALOS = [timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=30), timedelta(hours=2),
              timedelta(hours=12)]
MAX_TENTATIVAS = len(INTERVALOS) + 1
FALHAS_PARA_DESATIVAR = 20
TEMPO_LIMITE = 10   # segundos por envio
ENTREGA_AUTOMATICA = True   # desligado nos testes

ESTADO_PENDENTE, ESTADO_ENTREGUE, ESTADO_FALHOU = "pendente", "entregue", "falhou"


class ErroWebhook(Exception):
    """Mensagem pronta a mostrar."""


# ---------- Endereços ----------

def _ip_publico(ip: str) -> bool:
    endereco = ipaddress.ip_address(ip)
    return not (endereco.is_private or endereco.is_loopback or endereco.is_link_local or endereco.is_multicast
                or endereco.is_reserved or endereco.is_unspecified)


def validar_url(url: str, resolver: bool = True) -> str:
    """Endereço https com um nome público; levanta ErroWebhook com o motivo se não servir."""
    url = (url or "").strip()
    partes = urlsplit(url)
    if partes.scheme != "https" or not partes.hostname:
        raise ErroWebhook("O endereço tem de começar por https:// (os avisos levam dados da empresa).")
    if len(url) > 500:
        raise ErroWebhook("O endereço é demasiado comprido.")
    if partes.username or partes.password:
        raise ErroWebhook("O endereço não pode ter utilizador nem palavra-passe: use o segredo da assinatura.")
    nome = partes.hostname.lower()
    if nome in ("localhost",) or nome.endswith((".local", ".internal", ".localhost")):
        raise ErroWebhook("O endereço tem de ser público na Internet.")
    if resolver:
        try:
            ips = {info[4][0] for info in socket.getaddrinfo(nome, partes.port or 443, proto=socket.IPPROTO_TCP)}
        except socket.gaierror:
            raise ErroWebhook(f"Não foi possível encontrar o servidor {nome}.")
        if not ips or not all(_ip_publico(ip) for ip in ips):
            raise ErroWebhook("O endereço tem de ser público na Internet (não pode apontar para uma rede interna).")
    return url


# ---------- Gestão dos endereços ----------

def listar(db: Session, empresa_id: int) -> list[WebhookEndpoint]:
    return list(db.scalars(select(WebhookEndpoint).where(WebhookEndpoint.empresa_id == empresa_id)
                           .order_by(WebhookEndpoint.id)))


def criar(db: Session, empresa_id: int, url: str, eventos: list[str], utilizador_id: int | None) -> WebhookEndpoint:
    url = validar_url(url)
    eventos = sorted(set(eventos))
    if not eventos or any(e not in EVENTOS for e in eventos):
        raise ErroWebhook("Escolha pelo menos um evento da lista.")
    if len(listar(db, empresa_id)) >= MAX_ENDPOINTS:
        raise ErroWebhook(f"Cada empresa pode ter até {MAX_ENDPOINTS} endereços de webhooks.")
    endpoint = WebhookEndpoint(empresa_id=empresa_id, url=url, eventos=eventos, ativo=True,
                               segredo="dobra_whsec_" + secrets.token_urlsafe(24), falhas_seguidas=0,
                               criado_por_id=utilizador_id, criado_em=datetime.now())
    db.add(endpoint)
    db.flush()
    return endpoint


def obter(db: Session, empresa_id: int, endpoint_id: int) -> WebhookEndpoint:
    endpoint = db.scalar(select(WebhookEndpoint).where(WebhookEndpoint.id == endpoint_id,
                                                       WebhookEndpoint.empresa_id == empresa_id))
    if endpoint is None:
        raise ErroWebhook("Webhook não encontrado.")
    return endpoint


def apagar(db: Session, empresa_id: int, endpoint_id: int) -> None:
    endpoint = obter(db, empresa_id, endpoint_id)
    for entrega in db.scalars(select(EntregaWebhook).where(EntregaWebhook.endpoint_id == endpoint.id)):
        db.delete(entrega)
    db.delete(endpoint)


def endpoint_dict(e: WebhookEndpoint) -> dict:
    return {"id": e.id, "url": e.url, "eventos": e.eventos, "ativo": e.ativo, "falhas_seguidas": e.falhas_seguidas,
            "ultima_entrega_em": e.ultima_entrega_em, "ultimo_estado": e.ultimo_estado, "criado_em": e.criado_em,
            "segredo_fim": e.segredo[-4:]}


def entrega_dict(e: EntregaWebhook) -> dict:
    return {"id": e.id, "evento": e.evento, "estado": e.estado, "tentativas": e.tentativas,
            "codigo_resposta": e.codigo_resposta, "erro": e.erro, "criada_em": e.criada_em,
            "entregue_em": e.entregue_em, "dados": (e.carga or {}).get("dados")}


# ---------- Emitir ----------

def emitir(db: Session, empresa_id: int, evento: str, dados: dict) -> int:
    """Regista o evento para os endereços ativos que o pediram. Envia depois de a transação gravar."""
    if config.MODO_DEMO:
        return 0
    cache = db.info.setdefault("webhooks", {})
    if empresa_id not in cache:
        cache[empresa_id] = [(e.id, set(e.eventos)) for e in db.scalars(select(WebhookEndpoint).where(
            WebhookEndpoint.empresa_id == empresa_id, WebhookEndpoint.ativo))]
    destinos = [eid for eid, eventos in cache[empresa_id] if evento in eventos or evento == EVENTO_TESTE]
    agora = datetime.now()
    for endpoint_id in destinos:
        db.add(EntregaWebhook(endpoint_id=endpoint_id, empresa_id=empresa_id, evento=evento, estado=ESTADO_PENDENTE,
                              carga={"evento": evento, "criado_em": agora.isoformat(timespec="seconds"),
                                     "dados": dados},
                              tentativas=0, criada_em=agora, proxima_tentativa=agora))
    if destinos and not db.info.get("webhooks_ouvinte"):
        db.info["webhooks_ouvinte"] = True
        event.listen(db, "after_commit", _depois_de_gravar)
    return len(destinos)


def _depois_de_gravar(db: Session) -> None:
    db.info.pop("webhooks", None)
    if ENTREGA_AUTOMATICA:
        despachar_em_fundo()


_a_despachar = threading.Lock()


def despachar_em_fundo() -> None:
    """Entrega as pendentes numa thread (uma de cada vez), sem atrasar o pedido do utilizador."""
    if not _a_despachar.acquire(blocking=False):
        return   # já há uma a correr: vai apanhar as novas

    def correr():
        try:
            time.sleep(0.2)   # deixa a transação que emitiu ficar visível
            from app.db import SessaoLocal
            with SessaoLocal() as db:
                entregar_pendentes(db)
        except Exception:  # noqa: BLE001 - nunca deixar cair o processo
            logger.exception("Erro ao entregar webhooks")
        finally:
            _a_despachar.release()

    threading.Thread(target=correr, name="webhooks", daemon=True).start()


# ---------- Entregar ----------

def assinatura(segredo: str, corpo: bytes, instante: int) -> str:
    assinado = f"{instante}.".encode() + corpo
    return f"t={instante},v1={hmac.new(segredo.encode(), assinado, hashlib.sha256).hexdigest()}"


def _enviar(url: str, corpo: bytes, cabecalhos: dict) -> int:
    """POST ao endereço do cliente (substituído nos testes: nunca há rede). Devolve o código HTTP."""
    validar_url(url)   # o DNS pode ter mudado desde que o endereço foi guardado
    resposta = httpx.post(url, content=corpo, headers=cabecalhos, timeout=TEMPO_LIMITE, follow_redirects=False)
    return resposta.status_code


def entregar(db: Session, entrega: EntregaWebhook, agora: datetime | None = None) -> EntregaWebhook:
    agora = agora or datetime.now()
    endpoint = db.get(WebhookEndpoint, entrega.endpoint_id)
    if endpoint is None:
        entrega.estado = ESTADO_FALHOU
        return entrega
    corpo = json.dumps({"id": entrega.id, **(entrega.carga or {})}, ensure_ascii=False, default=str).encode()
    cabecalhos = {
        "Content-Type": "application/json", "User-Agent": "Dobra-Webhooks/1.0",
        "Dobra-Evento": entrega.evento, "Dobra-Entrega": str(entrega.id),
        "Dobra-Assinatura": assinatura(endpoint.segredo, corpo, int(time.time())),
    }
    entrega.tentativas += 1
    try:
        codigo = _enviar(endpoint.url, corpo, cabecalhos)
        entrega.codigo_resposta = codigo
        sucesso = 200 <= codigo < 300
        entrega.erro = None if sucesso else f"Resposta {codigo}"
    except (httpx.HTTPError, ErroWebhook) as erro:
        sucesso = False
        entrega.codigo_resposta = None
        entrega.erro = (str(erro) or erro.__class__.__name__)[:300]
    endpoint.ultima_entrega_em = agora
    if sucesso:
        entrega.estado, entrega.entregue_em, entrega.proxima_tentativa = ESTADO_ENTREGUE, agora, None
        endpoint.ultimo_estado, endpoint.falhas_seguidas = ESTADO_ENTREGUE, 0
        return entrega
    endpoint.ultimo_estado = ESTADO_FALHOU
    endpoint.falhas_seguidas = (endpoint.falhas_seguidas or 0) + 1
    if endpoint.falhas_seguidas >= FALHAS_PARA_DESATIVAR:
        endpoint.ativo = False
    if entrega.tentativas >= MAX_TENTATIVAS:
        entrega.estado, entrega.proxima_tentativa = ESTADO_FALHOU, None
    else:
        entrega.proxima_tentativa = agora + INTERVALOS[entrega.tentativas - 1]
    return entrega


def entregar_pendentes(db: Session, agora: datetime | None = None, limite: int = 200) -> dict:
    """Entrega as pendentes que já chegaram à hora da próxima tentativa (grava depois de cada uma)."""
    agora = agora or datetime.now()
    contagem = {ESTADO_ENTREGUE: 0, ESTADO_FALHOU: 0, ESTADO_PENDENTE: 0}
    ids = list(db.scalars(select(EntregaWebhook.id).where(
        EntregaWebhook.estado == ESTADO_PENDENTE, EntregaWebhook.proxima_tentativa <= agora)
        .order_by(EntregaWebhook.id).limit(limite)))
    for entrega_id in ids:
        entrega = db.get(EntregaWebhook, entrega_id)
        if entrega is None or entrega.estado != ESTADO_PENDENTE:
            continue
        entregar(db, entrega, agora)
        db.commit()
        contagem[entrega.estado] += 1
    return contagem


def ultimas_entregas(db: Session, empresa_id: int, endpoint_id: int, limite: int = 20) -> list[dict]:
    obter(db, empresa_id, endpoint_id)
    return [entrega_dict(e) for e in db.scalars(select(EntregaWebhook).where(
        EntregaWebhook.endpoint_id == endpoint_id).order_by(EntregaWebhook.id.desc()).limit(limite))]


def testar(db: Session, empresa_id: int, endpoint_id: int) -> EntregaWebhook:
    """Envia já um evento "teste" a um endereço (mesmo desativado) e devolve o resultado."""
    endpoint = obter(db, empresa_id, endpoint_id)
    agora = datetime.now()
    entrega = EntregaWebhook(endpoint_id=endpoint.id, empresa_id=empresa_id, evento=EVENTO_TESTE,
                             estado=ESTADO_PENDENTE, tentativas=0, criada_em=agora, proxima_tentativa=None,
                             carga={"evento": EVENTO_TESTE, "criado_em": agora.isoformat(timespec="seconds"),
                                    "dados": {"mensagem": "Webhook do Dobra configurado corretamente."}})
    db.add(entrega)
    db.flush()
    entregar(db, entrega, agora)
    if entrega.estado == ESTADO_PENDENTE:   # o teste não fica em fila para novas tentativas
        entrega.estado, entrega.proxima_tentativa = ESTADO_FALHOU, None
    if entrega.estado == ESTADO_ENTREGUE:
        endpoint.ativo = True
    return entrega
