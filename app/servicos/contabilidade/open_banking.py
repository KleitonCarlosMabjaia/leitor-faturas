"""
Open banking (PSD2): os movimentos das contas bancárias chegam sozinhos à reconciliação bancária,
sem exportar extratos.

Como funciona
1. O utilizador escolhe o banco e a conta SNC (12x). A aplicação pede ao fornecedor um endereço
   de autorização e o utilizador entra no site do próprio banco para dar o consentimento (só de
   leitura: saldos e movimentos; nunca pagamentos).
2. O banco devolve-o a /open-banking/retorno com um código; trocamo-lo por uma sessão e pelas
   contas autorizadas. Com várias contas, o utilizador escolhe qual liga à conta SNC.
3. "Sincronizar" lê os movimentos contabilizados (BOOK) desde a última vez (com 3 dias de margem;
   os repetidos são ignorados) e grava-os como uma importação de formato "banco".
4. O consentimento PSD2 expira (até 180 dias, conforme o banco): a ligação passa a "expirada" e é
   preciso renovar a autorização.

Fornecedores
- Enable Banking (https://enablebanking.com): agrega os bancos portugueses (e europeus) por uma só
  API. Autenticação por JWT RS256 assinado com a chave privada da aplicação (kid = ID da aplicação).
- Simulado: um "Banco de Demonstração" com movimentos fictícios e determinísticos, para a
  demonstração pública e para os testes (nunca há rede).

As funções não fazem commit: quem as chama decide quando gravar.
"""

import base64
import json
import logging
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import config
from app.modelos.tabelas import (
    LIGACAO_ATIVA,
    LIGACAO_ERRO,
    LIGACAO_ESCOLHER_CONTA,
    LIGACAO_EXPIRADA,
    LIGACAO_PENDENTE,
    LIGACAO_REVOGADA,
    Empresa,
    ExtratoBancario,
    LigacaoBancaria,
    MovimentoBancario,
)
from app.servicos.contabilidade import banco, extratos

logger = logging.getLogger(__name__)

DIAS_CONSENTIMENTO = 180          # pedido ao banco (limitado pelo máximo que cada banco aceita)
DIAS_PRIMEIRA_LEITURA = 90        # na primeira sincronização
DIAS_MARGEM = 3                   # movimentos que o banco contabiliza com atraso
MINUTOS_PARA_AUTORIZAR = 60       # depois disto, o regresso do banco já não é aceite
HORAS_DESATUALIZADA = 12          # a página dos bancos sincroniza sozinha quando passa disto
ESTADOS_ATIVOS = (LIGACAO_PENDENTE, LIGACAO_ESCOLHER_CONTA, LIGACAO_ATIVA, LIGACAO_EXPIRADA, LIGACAO_ERRO)


class ErroOpenBanking(Exception):
    """Mensagem pronta a mostrar."""


class ConsentimentoExpirado(ErroOpenBanking):
    """O banco já não aceita a sessão: é preciso autorizar de novo."""


@dataclass
class ContaExterna:
    uid: str
    iban: str | None
    nome: str | None
    moeda: str | None


@dataclass
class SessaoExterna:
    sessao: str
    contas: list[ContaExterna]
    valido_ate: datetime | None


# ---------- Fornecedor: Enable Banking ----------

def _b64(dados: bytes) -> str:
    return base64.urlsafe_b64encode(dados).rstrip(b"=").decode()


def _chave_privada() -> bytes:
    valor = config.ENABLE_BANKING_CHAVE_PRIVADA
    if "BEGIN" in valor:
        return valor.replace("\\n", "\n").encode()
    return Path(valor).read_bytes()


def jwt_enable_banking(agora: int | None = None) -> str:
    """JWT RS256 que autentica a aplicação (válido 1 hora; a Enable Banking aceita até 24 horas)."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    agora = int(agora if agora is not None else time.time())
    cabecalho = {"typ": "JWT", "alg": "RS256", "kid": config.ENABLE_BANKING_APP_ID}
    corpo = {"iss": "enablebanking.com", "aud": "api.enablebanking.com", "iat": agora, "exp": agora + 3600}
    assinado = f"{_b64(json.dumps(cabecalho).encode())}.{_b64(json.dumps(corpo).encode())}"
    chave = serialization.load_pem_private_key(_chave_privada(), password=None)
    assinatura = chave.sign(assinado.encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{assinado}.{_b64(assinatura)}"


def _data_hora(texto: str | None) -> datetime | None:
    """'2027-05-20T12:00:00.000000+00:00' -> datetime local sem fuso (como o resto da base de dados)."""
    if not texto:
        return None
    try:
        valor = datetime.fromisoformat(texto.replace("Z", "+00:00"))
    except ValueError:
        return None
    return valor.astimezone().replace(tzinfo=None) if valor.tzinfo else valor


class EnableBanking:
    nome = "enablebanking"
    URL = "https://api.enablebanking.com"

    def _pedido(self, metodo: str, caminho: str, *, params: dict | None = None, corpo: dict | None = None,
                ip: str | None = None) -> dict:
        """Pedido à API (substituído nos testes: nunca há rede)."""
        cabecalhos = {"Authorization": f"Bearer {jwt_enable_banking()}"}
        if ip:
            cabecalhos["Psu-Ip-Address"] = ip
        try:
            resposta = httpx.request(metodo, f"{self.URL}{caminho}", params=params, json=corpo,
                                     headers=cabecalhos, timeout=30)
        except httpx.HTTPError as erro:
            logger.warning("Enable Banking sem resposta: %s", erro)
            raise ErroOpenBanking("O serviço de ligação aos bancos não respondeu. Tente daqui a pouco.") from erro
        if resposta.status_code in (401, 403) and caminho.startswith(("/accounts/", "/sessions/")):
            raise ConsentimentoExpirado("O banco já não aceita esta autorização: volte a ligar a conta.")
        if resposta.status_code >= 400:
            logger.warning("Enable Banking %s %s -> %s %s", metodo, caminho, resposta.status_code,
                           resposta.text[:300])
            raise ErroOpenBanking("O banco não respondeu como esperado. Tente daqui a pouco.")
        return resposta.json() if resposta.content else {}

    def bancos(self, pais: str) -> list[dict]:
        dados = self._pedido("GET", "/aspsps", params={"country": pais, "service": "AIS", "psu_type": "business"})
        return [{"nome": a["name"], "pais": a.get("country", pais), "logo": a.get("logo"),
                 "max_dias": int(a.get("maximum_consent_validity") or 0) // 86400 or None}
                for a in dados.get("aspsps", [])]

    def iniciar(self, banco_nome: str, pais: str, estado: str, valido_ate: datetime) -> str:
        dados = self._pedido("POST", "/auth", corpo={
            "access": {"valid_until": valido_ate.astimezone(UTC).isoformat()},
            "aspsp": {"name": banco_nome, "country": pais},
            "state": estado,
            "redirect_url": f"{config.URL_PUBLICO}/open-banking/retorno",
            "psu_type": "business",
            "language": "pt",
        })
        return dados["url"]

    def criar_sessao(self, codigo: str) -> SessaoExterna:
        dados = self._pedido("POST", "/sessions", corpo={"code": codigo})
        contas = [ContaExterna(uid=c["uid"], iban=(c.get("account_id") or {}).get("iban"),
                               nome=c.get("name") or c.get("product"), moeda=c.get("currency"))
                  for c in dados.get("accounts", []) if c.get("uid")]
        return SessaoExterna(dados["session_id"], contas, _data_hora((dados.get("access") or {}).get("valid_until")))

    def transacoes(self, conta_uid: str, desde: date, ate: date, ip: str | None = None) -> list[extratos.MovimentoLido]:
        movimentos: list[extratos.MovimentoLido] = []
        params = {"date_from": desde.isoformat(), "date_to": ate.isoformat(), "transaction_status": "BOOK"}
        for _ in range(100):   # páginas (continuation_key)
            dados = self._pedido("GET", f"/accounts/{conta_uid}/transactions", params=params, ip=ip)
            movimentos += [m for m in map(movimento_enable_banking, dados.get("transactions") or []) if m]
            chave = dados.get("continuation_key")
            if not chave:
                break
            params = {**params, "continuation_key": chave}
        return movimentos

    def terminar(self, sessao: str) -> None:
        self._pedido("DELETE", f"/sessions/{sessao}")


def movimento_enable_banking(tx: dict) -> extratos.MovimentoLido | None:
    """Uma transação da Enable Banking no formato dos extratos (montante com sinal)."""
    if tx.get("status") not in (None, "BOOK"):
        return None
    valor = extratos.ler_numero((tx.get("transaction_amount") or {}).get("amount"))
    data = extratos.ler_data(tx.get("booking_date") or tx.get("value_date") or tx.get("transaction_date"))
    if valor is None or data is None:
        return None
    saida = tx.get("credit_debit_indicator") == "DBIT"
    montante = -abs(valor) if saida else abs(valor)
    contraparte = ((tx.get("creditor") if saida else tx.get("debtor")) or {}).get("name")
    textos = [t for t in (tx.get("remittance_information") or []) if t]
    partes = [contraparte, *textos] if contraparte and not any(contraparte in t for t in textos) else textos
    descricao = " ".join(" ".join(str(p) for p in partes if p).split()) \
        or (tx.get("bank_transaction_code") or {}).get("description") or "Movimento bancário"
    saldo = extratos.ler_numero((tx.get("balance_after_transaction") or {}).get("amount"))
    referencia = tx.get("entry_reference") or tx.get("transaction_id")
    return extratos.MovimentoLido(data=data, descricao=descricao[:300], montante=round(montante, 2), saldo=saldo,
                                  referencia=f"eb:{referencia}" if referencia else None)


# ---------- Fornecedor: banco simulado ----------

BANCO_SIMULADO = "Banco de Demonstração"


def _movimentos_do_dia(d: date) -> list[tuple[str, float]]:
    """Movimentos fictícios, sempre iguais para o mesmo dia (sincronizações repetidas não duplicam)."""
    n = d.toordinal()
    dia: list[tuple[str, float]] = []
    if d.weekday() == 0:
        dia.append(("TPA VENDAS POS SEMANA", round(380 + (n % 7) * 23.5, 2)))
    if d.weekday() == 4:
        dia.append(("COMPRA CARTAO POSTO COMBUSTIVEL", -round(42 + (n % 5) * 3.1, 2)))
    if d.day == 1:
        dia.append(("COMISSAO MANUTENCAO CONTA", -5.2))
        dia.append(("IMPOSTO DO SELO S/COMISSAO", -0.21))
    if d.day == 10:
        dia.append(("DD FORNECIMENTO ELECTRICIDADE", -round(78 + (d.month % 4) * 6.37, 2)))
    if d.day == 15:
        dia.append(("TRF RECEBIDA CLIENTE EVENTO", round(615 + (d.month % 3) * 41.2, 2)))
    return dia


class Simulado:
    nome = "simulado"

    def bancos(self, pais: str) -> list[dict]:
        return [{"nome": BANCO_SIMULADO, "pais": pais, "logo": None, "max_dias": DIAS_CONSENTIMENTO}]

    def iniciar(self, banco_nome: str, pais: str, estado: str, valido_ate: datetime) -> str:
        # Sem site do banco: volta logo como se o utilizador tivesse autorizado
        return f"/open-banking/retorno?state={estado}&code=simulado-{secrets.token_hex(4)}"

    def criar_sessao(self, codigo: str) -> SessaoExterna:
        return SessaoExterna(f"sim-{secrets.token_hex(8)}",
                             [ContaExterna("sim-ordem", "PT50000000000000000000017", "Conta à ordem", "EUR")],
                             datetime.now() + timedelta(days=DIAS_CONSENTIMENTO))

    def transacoes(self, conta_uid: str, desde: date, ate: date, ip: str | None = None) -> list[extratos.MovimentoLido]:
        # Sem saldo: o banco simulado não sabe o saldo real da conta da empresa (é deduzido do último extrato)
        resultado, d = [], desde
        while d <= ate:
            for i, (descricao, montante) in enumerate(_movimentos_do_dia(d)):
                resultado.append(extratos.MovimentoLido(d, descricao, montante, None, f"sim:{d.isoformat()}:{i}"))
            d += timedelta(days=1)
        return resultado

    def terminar(self, sessao: str) -> None:
        return None


# ---------- Escolha do fornecedor ----------

def _real_configurado() -> bool:
    return bool(config.ENABLE_BANKING_APP_ID and config.ENABLE_BANKING_CHAVE_PRIVADA)


def fornecedor(nome: str | None = None):
    """O fornecedor de uma ligação existente (pelo nome) ou o ativo para ligações novas (None = indisponível)."""
    if nome == Simulado.nome or (nome is None and not _real_configurado()
                                 and (config.MODO_DEMO or config.OPEN_BANKING_SIMULADO)):
        return Simulado()
    if _real_configurado() and nome in (None, EnableBanking.nome):
        return EnableBanking()
    return None


def disponivel() -> bool:
    return fornecedor() is not None


_cache_bancos: dict[str, tuple[float, list[dict]]] = {}


def bancos(pais: str = "PT") -> list[dict]:
    f = fornecedor()
    if f is None:
        raise ErroOpenBanking("A ligação direta aos bancos não está ativa neste servidor.")
    chave = f"{f.nome}:{pais}"
    guardado = _cache_bancos.get(chave)
    if guardado and time.time() - guardado[0] < 3600:
        return guardado[1]
    lista = sorted(f.bancos(pais), key=lambda b: b["nome"].lower())
    _cache_bancos[chave] = (time.time(), lista)
    return lista


# ---------- Ligações ----------

def listar(db: Session, empresa_id: int, conta_codigo: str | None = None) -> list[LigacaoBancaria]:
    consulta = select(LigacaoBancaria).where(LigacaoBancaria.empresa_id == empresa_id,
                                             LigacaoBancaria.estado.in_(ESTADOS_ATIVOS))
    if conta_codigo:
        consulta = consulta.where(LigacaoBancaria.conta_codigo == conta_codigo)
    return list(db.scalars(consulta.order_by(LigacaoBancaria.criada_em.desc(), LigacaoBancaria.id.desc())))


def obter(db: Session, empresa_id: int, ligacao_id: int) -> LigacaoBancaria:
    lig = db.scalar(select(LigacaoBancaria).where(LigacaoBancaria.id == ligacao_id,
                                                  LigacaoBancaria.empresa_id == empresa_id,
                                                  LigacaoBancaria.estado.in_(ESTADOS_ATIVOS)))
    if lig is None:
        raise ErroOpenBanking("Ligação bancária não encontrada.")
    return lig


def iniciar(db: Session, empresa: Empresa, conta_codigo: str, banco_nome: str, pais: str = "PT",
            utilizador_id: int | None = None) -> tuple[LigacaoBancaria, str]:
    """Cria a ligação pendente e devolve o endereço de autorização no banco."""
    banco.verificar_conta(db, empresa.id, conta_codigo)
    if not conta_codigo.startswith(("12", "13")):
        raise ErroOpenBanking("Ligue o banco a uma conta de depósitos (12x) ou outros depósitos (13x).")
    f = fornecedor()
    if f is None:
        raise ErroOpenBanking("A ligação direta aos bancos não está ativa neste servidor.")
    escolhido = next((b for b in bancos(pais) if b["nome"] == banco_nome), None)
    if escolhido is None:
        raise ErroOpenBanking("Banco desconhecido: escolha-o na lista.")
    dias = min(DIAS_CONSENTIMENTO, escolhido.get("max_dias") or DIAS_CONSENTIMENTO)
    # Um pedido novo para a mesma conta substitui o anterior (renovar o consentimento, mudar de banco)
    for antiga in listar(db, empresa.id, conta_codigo):
        if antiga.estado == LIGACAO_PENDENTE:
            antiga.estado, antiga.estado_oauth = LIGACAO_REVOGADA, None
    estado = secrets.token_urlsafe(32)
    lig = LigacaoBancaria(empresa_id=empresa.id, conta_codigo=conta_codigo, fornecedor=f.nome, banco=banco_nome[:120],
                          pais=pais, estado=LIGACAO_PENDENTE, estado_oauth=estado, criada_por_id=utilizador_id,
                          criada_em=datetime.now())
    db.add(lig)
    db.flush()
    url = f.iniciar(banco_nome, pais, estado, datetime.now().astimezone() + timedelta(days=dias))
    return lig, url


def _ocultar_iban(iban: str | None) -> str | None:
    iban = (iban or "").replace(" ", "")
    return f"{iban[:4]} •••• {iban[-4:]}" if len(iban) > 8 else (iban or None)


def concluir(db: Session, empresa_id: int, estado: str, codigo: str | None, erro: str | None = None) -> LigacaoBancaria:
    """Regresso do banco: troca o código pela sessão e pelas contas autorizadas."""
    lig = db.scalar(select(LigacaoBancaria).where(LigacaoBancaria.estado_oauth == estado,
                                                  LigacaoBancaria.empresa_id == empresa_id,
                                                  LigacaoBancaria.estado == LIGACAO_PENDENTE)) if estado else None
    if lig is None:
        raise ErroOpenBanking("Este pedido de ligação já não é válido. Comece de novo na página dos bancos.")
    lig.estado_oauth = None   # uso único
    if erro or not codigo:
        lig.estado = LIGACAO_REVOGADA
        raise ErroOpenBanking("A autorização foi cancelada no banco. Nada foi ligado.")
    if datetime.now() - lig.criada_em > timedelta(minutes=MINUTOS_PARA_AUTORIZAR):
        lig.estado = LIGACAO_REVOGADA
        raise ErroOpenBanking("A autorização demorou demasiado. Comece de novo na página dos bancos.")
    sessao = fornecedor(lig.fornecedor).criar_sessao(codigo)
    if not sessao.contas:
        lig.estado = LIGACAO_REVOGADA
        raise ErroOpenBanking("O banco não autorizou nenhuma conta.")
    lig.sessao_externa = sessao.sessao
    lig.valido_ate = sessao.valido_ate
    lig.contas_disponiveis = [{"uid": c.uid, "iban": _ocultar_iban(c.iban), "nome": c.nome, "moeda": c.moeda}
                              for c in sessao.contas]
    if len(sessao.contas) == 1:
        _ativar(db, lig, sessao.contas[0].uid)
    else:
        lig.estado = LIGACAO_ESCOLHER_CONTA
    return lig


def _ativar(db: Session, lig: LigacaoBancaria, uid: str) -> None:
    conta = next((c for c in lig.contas_disponiveis or [] if c["uid"] == uid), None)
    if conta is None:
        raise ErroOpenBanking("Essa conta não foi autorizada no banco.")
    # A ligação anterior desta conta SNC (expirada, por exemplo) dá lugar à nova
    for antiga in listar(db, lig.empresa_id, lig.conta_codigo):
        if antiga.id != lig.id:
            antiga.estado = LIGACAO_REVOGADA
            if antiga.conta_externa == uid and antiga.ultima_sincronizacao:
                lig.ultima_sincronizacao = antiga.ultima_sincronizacao   # continua de onde ficou
    lig.conta_externa, lig.iban, lig.estado, lig.ultimo_erro = uid, conta["iban"], LIGACAO_ATIVA, None


def escolher_conta(db: Session, lig: LigacaoBancaria, uid: str) -> None:
    if lig.estado != LIGACAO_ESCOLHER_CONTA:
        raise ErroOpenBanking("Esta ligação já tem a conta escolhida.")
    _ativar(db, lig, uid)


@dataclass
class ResultadoSincronizacao:
    importados: int
    lidos: int
    desde: date
    ate: date


def sincronizar(db: Session, empresa: Empresa, lig: LigacaoBancaria, utilizador_id: int | None = None,
                ip: str | None = None, hoje: date | None = None) -> ResultadoSincronizacao:
    """Lê os movimentos novos do banco e grava-os na reconciliação bancária."""
    if lig.estado not in (LIGACAO_ATIVA, LIGACAO_ERRO):
        raise ErroOpenBanking("Renove a autorização no banco para voltar a sincronizar."
                              if lig.estado == LIGACAO_EXPIRADA else "Esta ligação ainda não está ativa.")
    agora = datetime.now()
    if lig.valido_ate and lig.valido_ate < agora:
        lig.estado = LIGACAO_EXPIRADA
        raise ConsentimentoExpirado("A autorização no banco terminou: volte a ligar a conta para continuar.")
    hoje = hoje or agora.date()
    if lig.ultima_sincronizacao:
        desde = lig.ultima_sincronizacao.date() - timedelta(days=DIAS_MARGEM)
    else:
        # Primeira leitura: se a conta já tem movimentos importados por ficheiro, continua a partir daí
        # (as referências do banco são outras: os mesmos movimentos ficariam em duplicado)
        desde = hoje - timedelta(days=DIAS_PRIMEIRA_LEITURA)
        ultimo = db.scalar(select(func.max(MovimentoBancario.data)).where(
            MovimentoBancario.empresa_id == empresa.id, MovimentoBancario.conta_codigo == lig.conta_codigo))
        if ultimo and ultimo >= desde:
            desde = min(ultimo + timedelta(days=1), hoje)
    try:
        lidos = fornecedor(lig.fornecedor).transacoes(lig.conta_externa, desde, hoje, ip)
    except ConsentimentoExpirado:
        lig.estado = LIGACAO_EXPIRADA
        raise
    except ErroOpenBanking as erro:
        lig.estado, lig.ultimo_erro = LIGACAO_ERRO, str(erro)[:300]
        raise
    lidos = sorted((m for m in lidos if m.data >= desde), key=lambda m: m.data)
    posicoes = banco.novos(db, empresa.id, lig.conta_codigo, lidos)
    importados = 0
    if posicoes:
        novos = [lidos[i] for i in posicoes]
        com_saldo = [m for m in lidos if m.saldo is not None]
        saldo = com_saldo[-1].saldo if com_saldo else _saldo_deduzido(db, empresa.id, lig.conta_codigo, novos)
        extrato = extratos.ExtratoLido(formato="banco", movimentos=novos, saldo_final=saldo)
        nome = f"{lig.banco} · sincronização de {agora:%d-%m-%Y %H:%M}"
        importados = banco.gravar(db, empresa, lig.conta_codigo, extrato, nome, extrato.saldo_final,
                                  utilizador_id).importados
    lig.ultima_sincronizacao, lig.estado, lig.ultimo_erro = agora, LIGACAO_ATIVA, None
    return ResultadoSincronizacao(importados, len(lidos), desde, hoje)


def _saldo_deduzido(db: Session, empresa_id: int, conta_codigo: str,
                    novos: list[extratos.MovimentoLido]) -> float | None:
    """
    Quando o banco não indica o saldo: o último saldo conhecido (de um extrato anterior) mais os
    movimentos novos posteriores a ele. Sem saldo anterior, fica por indicar.
    """
    anterior = db.scalar(select(ExtratoBancario).where(
        ExtratoBancario.empresa_id == empresa_id, ExtratoBancario.conta_codigo == conta_codigo,
        ExtratoBancario.saldo_final.is_not(None),
    ).order_by(ExtratoBancario.data_fim.desc(), ExtratoBancario.id.desc()).limit(1))
    if anterior is None or anterior.data_fim is None:
        return None
    if any(m.data <= anterior.data_fim for m in novos):
        return None   # movimentos antes do saldo conhecido: a conta não fecha, melhor não inventar
    return round(anterior.saldo_final + sum(m.montante for m in novos), 2)


def desligar(lig: LigacaoBancaria) -> None:
    """Termina a sessão no banco (se possível) e desliga. Os movimentos já importados ficam."""
    if lig.sessao_externa:
        try:
            f = fornecedor(lig.fornecedor)
            if f is not None:
                f.terminar(lig.sessao_externa)
        except ErroOpenBanking:
            logger.info("Não foi possível terminar a sessão %s no banco (fica a expirar sozinha)", lig.id)
    lig.estado, lig.estado_oauth = LIGACAO_REVOGADA, None


def ligacao_dict(lig: LigacaoBancaria, agora: datetime | None = None) -> dict:
    agora = agora or datetime.now()
    desatualizada = lig.estado == LIGACAO_ATIVA and (
        lig.ultima_sincronizacao is None or agora - lig.ultima_sincronizacao > timedelta(hours=HORAS_DESATUALIZADA))
    return {
        "id": lig.id, "conta": lig.conta_codigo, "banco": lig.banco, "fornecedor": lig.fornecedor,
        "simulado": lig.fornecedor == Simulado.nome, "estado": lig.estado, "iban": lig.iban,
        "valido_ate": lig.valido_ate, "ultima_sincronizacao": lig.ultima_sincronizacao,
        "ultimo_erro": lig.ultimo_erro, "desatualizada": desatualizada,
        "contas_disponiveis": lig.contas_disponiveis if lig.estado == LIGACAO_ESCOLHER_CONTA else None,
    }


def sincronizar_todas(db: Session) -> dict:
    """Para correr uma vez por dia (python -m app.sincronizar_bancos): todas as ligações ativas."""
    contagem = {"ligacoes": 0, "importados": 0, "erros": 0}
    for lig in list(db.scalars(select(LigacaoBancaria).where(LigacaoBancaria.estado.in_((LIGACAO_ATIVA,
                                                                                            LIGACAO_ERRO))))):
        empresa = db.get(Empresa, lig.empresa_id)
        if empresa is None or not empresa.ativa:
            continue
        contagem["ligacoes"] += 1
        try:
            contagem["importados"] += sincronizar(db, empresa, lig).importados
        except ErroOpenBanking:
            contagem["erros"] += 1
        db.commit()
    return contagem
