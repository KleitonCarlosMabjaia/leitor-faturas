"""
Faturas certificadas das subscrições, emitidas no Facturalusa (programa certificado pela AT, n.º 2652).

O Dobra não é um programa de faturação certificado, por isso não emite faturas: quando o Stripe
confirma um pagamento (evento invoice.paid), pede ao Facturalusa uma fatura-recibo com o valor
pago, já finalizada (com ATCUD, QR e comunicação à AT feitos pelo Facturalusa), e envia-a por
email ao cliente. API: https://facturalusa.pt/documentacao/api (v2, token Bearer).

- Cada pagamento do Stripe dá no máximo uma fatura: o id da fatura do Stripe vai como
  external_reference (o Facturalusa devolve a existente em vez de criar outra) e fica numa
  tabela com restrição de unicidade.
- Os dados para emitir ficam guardados: se o Facturalusa falhar, a fatura fica "erro" e volta
  a ser tentada (python -m app.faturas_subscricao, ou no próximo pagamento recebido).
- Clientes no Facturalusa com o código DOBRA-<id da empresa>; um único artigo de serviço
  (config.FACTURALUSA_ARTIGO), criado na primeira emissão.
- IVA: com imposto no Stripe, a taxa e o total vêm do Stripe (preço com IVA incluído); sem
  imposto num cliente estrangeiro com o Stripe Tax ativo, a operação é isenta com o motivo M40
  (artigo 6.º, n.º 6, alínea a) do CIVA: autoliquidação pelo adquirente).
"""

import logging
from datetime import date, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import config
from app.modelos.tabelas import Empresa, FaturaSubscricao

logger = logging.getLogger(__name__)

URL_API = "https://facturalusa.pt/api/v2"
TIPO_DOCUMENTO = "Factura Recibo"
TAXA_NORMAL = 23
MOTIVO_AUTOLIQUIDACAO = "M40"
NOMES_PLANOS = {"essencial": "Essencial", "contabilidade": "Contabilidade"}
PAISES = {
    "PT": "Portugal", "ES": "Espanha", "FR": "França", "DE": "Alemanha", "IT": "Itália", "NL": "Países Baixos",
    "BE": "Bélgica", "LU": "Luxemburgo", "IE": "Irlanda", "AT": "Áustria", "PL": "Polónia", "SE": "Suécia",
    "DK": "Dinamarca", "FI": "Finlândia", "GB": "Reino Unido", "CH": "Suíça", "BR": "Brasil", "US": "Estados Unidos",
}

ESTADO_EMITIDA, ESTADO_ERRO, ESTADO_PENDENTE = "emitida", "erro", "pendente"


class ErroFacturalusa(Exception):
    """Resposta de erro do Facturalusa (mensagem pronta a guardar e mostrar)."""


def ativa() -> bool:
    return bool(config.FACTURALUSA_API_KEY) and not config.MODO_DEMO


def _pedido(metodo: str, caminho: str, dados: dict | None = None) -> tuple[int, dict]:
    """Pedido à API do Facturalusa (substituído nos testes: nunca há rede)."""
    resposta = httpx.request(
        metodo, f"{URL_API}/{caminho}", json=dados, timeout=30,
        headers={"Authorization": f"Bearer {config.FACTURALUSA_API_KEY}", "Accept": "application/json"},
    )
    try:
        corpo = resposta.json()
    except ValueError:
        corpo = {}
    return resposta.status_code, corpo if isinstance(corpo, dict) else {"dados": corpo}


def _mensagem(corpo: dict, estado: int) -> str:
    """Texto do erro, nos formatos habituais ({"message"}, {"errors": {...}} ou lista)."""
    partes = []
    if isinstance(corpo.get("message"), str):
        partes.append(corpo["message"])
    erros = corpo.get("errors") or corpo.get("error")
    if isinstance(erros, dict):
        for valor in erros.values():
            partes.extend(valor if isinstance(valor, list) else [str(valor)])
    elif isinstance(erros, list):
        partes.extend(str(e) for e in erros)
    elif isinstance(erros, str):
        partes.append(erros)
    texto = " ".join(str(p) for p in partes if p) or f"o Facturalusa respondeu com o código {estado}"
    return texto[:300]


def _exigir(estado: int, corpo: dict, *aceites: int) -> dict:
    if estado not in aceites:
        raise ErroFacturalusa(_mensagem(corpo, estado))
    return corpo


# ---------- Dados da fatura a partir do pagamento do Stripe ----------

def _iva_do_stripe(fatura: dict) -> int:
    if fatura.get("tax") is not None:
        return int(fatura["tax"] or 0)
    return sum(int(t.get("amount") or 0) for t in fatura.get("total_tax_amounts") or [])


def dados_da_fatura(empresa: Empresa, fatura: dict) -> dict:
    """O que é preciso para emitir, tirado da fatura paga do Stripe e da ficha da empresa."""
    pago = int(fatura.get("amount_paid") or 0)
    iva = _iva_do_stripe(fatura)
    endereco = fatura.get("customer_address") or {}
    pais = (endereco.get("country") or "PT").upper()
    ids_fiscais = [t.get("value") for t in fatura.get("customer_tax_ids") or [] if t.get("value")]
    nif = empresa.nif if pais == "PT" and empresa.nif else (ids_fiscais[0] if ids_fiscais else empresa.nif)
    if iva:
        taxa, isencao = round(iva / max(pago - iva, 1) * 100), None
    elif config.STRIPE_IVA_AUTOMATICO and pais != "PT":
        taxa, isencao = 0, MOTIVO_AUTOLIQUIDACAO
    else:
        taxa, isencao = TAXA_NORMAL, None
    linhas = (fatura.get("lines") or {}).get("data") or []
    periodo = linhas[0].get("period", {}) if linhas else {}
    inicio, fim = periodo.get("start"), periodo.get("end")
    plano = NOMES_PLANOS.get(empresa.plano, empresa.plano)
    descricao = f"Subscrição Dobra - plano {plano}"
    if inicio and fim:
        descricao += f" ({datetime.fromtimestamp(inicio):%d/%m/%Y} a {datetime.fromtimestamp(fim):%d/%m/%Y})"
    return {
        "total": round(pago / 100, 2), "iva": round(iva / 100, 2), "taxa": taxa, "isencao": isencao,
        "descricao": descricao[:200], "moeda": (fatura.get("currency") or "eur").upper(),
        "cliente": {
            "codigo": f"DOBRA-{empresa.id}", "nome": (empresa.nome or fatura.get("customer_name") or "Cliente")[:200],
            "nif": nif, "email": fatura.get("customer_email"), "pais": PAISES.get(pais, pais),
            "morada": " ".join(x for x in (endereco.get("line1"), endereco.get("line2")) if x) or empresa.morada,
            "localidade": endereco.get("city") or empresa.localidade,
            "codigo_postal": endereco.get("postal_code") or empresa.codigo_postal,
        },
    }


def registar_pagamento(db: Session, empresa: Empresa, fatura: dict) -> FaturaSubscricao | None:
    """Guarda o pagamento para faturar (uma vez por fatura do Stripe). Pagamentos de 0 € não se faturam."""
    stripe_id = fatura.get("id")
    if not stripe_id or not int(fatura.get("amount_paid") or 0):
        return None
    existente = db.scalar(select(FaturaSubscricao).where(FaturaSubscricao.stripe_fatura_id == stripe_id))
    if existente:
        return existente
    dados = dados_da_fatura(empresa, fatura)
    registo = FaturaSubscricao(
        empresa_id=empresa.id, stripe_fatura_id=stripe_id, data=date.today(), descricao=dados["descricao"],
        total=dados["total"], iva=dados["iva"], estado=ESTADO_PENDENTE, dados=dados, criada_em=datetime.now(),
    )
    try:
        with db.begin_nested():
            db.add(registo)
    except IntegrityError:   # o mesmo evento chegou duas vezes ao mesmo tempo
        return db.scalar(select(FaturaSubscricao).where(FaturaSubscricao.stripe_fatura_id == stripe_id))
    return registo


# ---------- Emissão no Facturalusa ----------

_artigo_garantido = False


def _garantir_artigo() -> None:
    """Cria o artigo de serviço da subscrição (uma vez; se já existir, o Facturalusa recusa e segue-se)."""
    global _artigo_garantido
    if _artigo_garantido:
        return
    estado, corpo = _pedido("POST", "items", {
        "reference": config.FACTURALUSA_ARTIGO, "description": "Subscrição Dobra (software de gestão e contabilidade)",
        "unit": "uni", "vat": TAXA_NORMAL, "type": "Serviços",
    })
    if estado not in (200, 201):
        logger.info("Artigo %s não criado (provavelmente já existe): %s", config.FACTURALUSA_ARTIGO,
                    _mensagem(corpo, estado))
    _artigo_garantido = True


def _cliente(dados: dict) -> int | str:
    """Id do cliente no Facturalusa: procura pelo código DOBRA-<id>; se não existir, cria."""
    c = dados["cliente"]
    estado, corpo = _pedido("POST", "customers/find", {"value": c["codigo"], "search_in": "Code"})
    if estado == 200 and corpo.get("id"):
        return corpo["id"]
    novo = {
        "name": c["nome"], "code": c["codigo"], "type": "Empresarial", "vat_type": "IVA incluído",
        "vat_number": c["nif"], "country": c["pais"], "address": c["morada"], "city": c["localidade"],
        "postal_code": c["codigo_postal"], "email": c["email"], "receive_emails": True,
    }
    corpo = _exigir(*_pedido("POST", "customers", {k: v for k, v in novo.items() if v not in (None, "")}), 200, 201)
    if not corpo.get("id"):
        raise ErroFacturalusa("O Facturalusa não devolveu o cliente criado.")
    return corpo["id"]


def emitir(db: Session, registo: FaturaSubscricao) -> FaturaSubscricao:
    """Emite (ou volta a tentar emitir) a fatura-recibo de um pagamento. Nunca lança: o erro fica no registo."""
    if registo.estado == ESTADO_EMITIDA:
        return registo
    dados = registo.dados or {}
    registo.tentativas = (registo.tentativas or 0) + 1
    try:
        _garantir_artigo()
        cliente = _cliente(dados)
        c = dados["cliente"]
        linha = {"id": config.FACTURALUSA_ARTIGO, "details": dados["descricao"], "price": dados["total"],
                 "quantity": 1, "vat": dados["taxa"]}
        if dados.get("isencao"):
            linha["vat_exemption"] = dados["isencao"]
        venda = {
            "document_type": TIPO_DOCUMENTO, "issue_date": registo.data.isoformat(), "customer": cliente,
            "vat_number": c["nif"], "address": c["morada"], "city": c["localidade"], "postal_code": c["codigo_postal"],
            "country": c["pais"], "currency": "€" if dados.get("moeda", "EUR") == "EUR" else dados["moeda"],
            "items": [linha], "vat_type": "IVA incluído", "status": "Terminado",
            "external_reference": registo.stripe_fatura_id, "language": "PT" if c["pais"] == "Portugal" else "EN",
            "force_send_email": False,
        }
        if config.FACTURALUSA_SERIE:
            venda["serie"] = config.FACTURALUSA_SERIE
        if config.FACTURALUSA_FORMA_PAGAMENTO:
            venda["payment_method"] = config.FACTURALUSA_FORMA_PAGAMENTO
        corpo = _exigir(*_pedido("POST", "sales", {k: v for k, v in venda.items() if v not in (None, "")}), 200, 201)
        registo.documento_id = str(corpo.get("id") or "")[:40] or None
        registo.numero = str(corpo.get("document_full_number") or corpo.get("number") or "")[:60] or None
        registo.atcud = str((corpo.get("documenttypeserie") or {}).get("atcud") or "")[:80] or None
        registo.url_pdf = (corpo.get("url_file") or None) and str(corpo["url_file"])[:500]
        if corpo.get("grand_total") is not None:
            registo.total = round(float(corpo["grand_total"]), 2)
        registo.estado, registo.erro, registo.emitida_em = ESTADO_EMITIDA, None, datetime.now()
    except (ErroFacturalusa, httpx.HTTPError, KeyError, ValueError) as erro:
        registo.estado, registo.erro = ESTADO_ERRO, (str(erro) or erro.__class__.__name__)[:300]
        logger.warning("Fatura da subscrição %s não emitida: %s", registo.stripe_fatura_id, registo.erro)
        return registo
    # Envio por email: a fatura já existe, por isso uma falha aqui não a torna "erro"
    email = (dados.get("cliente") or {}).get("email")
    if email and registo.documento_id:
        try:
            estado, corpo = _pedido("POST", f"sales/{registo.documento_id}/send_email",
                                    {"to": [{"name": dados["cliente"]["nome"], "email": email}]})
            registo.enviada = estado in (200, 201, 204)
            if not registo.enviada:
                logger.warning("Fatura %s emitida mas não enviada: %s", registo.numero, _mensagem(corpo, estado))
        except httpx.HTTPError as erro:
            logger.warning("Fatura %s emitida mas não enviada: %s", registo.numero, erro)
    return registo


def por_emitir(db: Session, limite: int = 50) -> list[FaturaSubscricao]:
    return list(db.scalars(select(FaturaSubscricao).where(FaturaSubscricao.estado != ESTADO_EMITIDA)
                           .order_by(FaturaSubscricao.id).limit(limite)))


def emitir_pendentes(db: Session) -> dict:
    contagem = {ESTADO_EMITIDA: 0, ESTADO_ERRO: 0}
    for registo in por_emitir(db):
        emitir(db, registo)
        db.commit()
        contagem[registo.estado] = contagem.get(registo.estado, 0) + 1
    return contagem


def fatura_dict(f: FaturaSubscricao) -> dict:
    return {
        "id": f.id, "data": f.data, "descricao": f.descricao, "total": f.total, "iva": f.iva, "estado": f.estado,
        "numero": f.numero, "atcud": f.atcud, "url_pdf": f.url_pdf, "enviada": bool(f.enviada),
        "erro": f.erro if f.estado != ESTADO_EMITIDA else None,
    }


def da_empresa(db: Session, empresa_id: int) -> list[dict]:
    return [fatura_dict(f) for f in db.scalars(
        select(FaturaSubscricao).where(FaturaSubscricao.empresa_id == empresa_id)
        .order_by(FaturaSubscricao.data.desc(), FaturaSubscricao.id.desc()).limit(36))]
