"""
Leitura das faturas em segundo plano, para lotes grandes.

Ao carregar várias faturas de uma vez, cada PDF é guardado logo (estado de extração "pendente")
e a leitura (QR, texto e IA) faz-se numa thread do servidor, uma fatura de cada vez, por ordem
de chegada. A página vai perguntando o estado; a pessoa pode sair e voltar mais tarde.

- As pendentes sobrevivem a um reinício: ao arrancar, a aplicação retoma-as.
- Uma fatura repetida (o mesmo NIF e número ou ATCUD de outra) é apagada, como no envio
  normal; a mensagem fica em memória durante uma hora para a página a mostrar.
- Um erro inesperado deixa a fatura em "erro" (pode voltar a ser lida); nunca pára a fila.
"""

import logging
import threading
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modelos.tabelas import Fatura
from app.servicos import armazenamento, processamento, webhooks

logger = logging.getLogger(__name__)

ATIVA = True   # desligada nos testes (que chamam processar_pendentes diretamente)
ESTADO_PENDENTE = "pendente"
MEMORIA_REPETIDAS = 3600   # segundos

_repetidas: dict[str, tuple[str, float]] = {}
_trinco = threading.Lock()
_a_correr = threading.Lock()


def _lembrar_repetida(fatura_id: str, mensagem: str) -> None:
    agora = time.time()
    with _trinco:
        for chave in [c for c, (_, quando) in _repetidas.items() if agora - quando > MEMORIA_REPETIDAS]:
            del _repetidas[chave]
        _repetidas[fatura_id] = (mensagem, agora)


def repetida(fatura_id: str) -> str | None:
    with _trinco:
        registo = _repetidas.get(fatura_id)
    return registo[0] if registo else None


def processar_uma(db: Session, fatura: Fatura) -> str:
    """Lê uma fatura pendente e grava. Devolve o estado final ("concluida", "erro", "repetida"...)."""
    conteudo = armazenamento.ler_pdf(fatura.ficheiro)
    if conteudo is None:
        fatura.estado_extracao, fatura.erro = "erro", "O ficheiro desta fatura não foi encontrado no servidor."
        db.commit()
        return fatura.estado_extracao
    try:
        processamento.processar(db, fatura, conteudo)
        if fatura.estado_extracao == ESTADO_PENDENTE:   # nunca deixar uma fatura presa na fila
            fatura.estado_extracao = "erro"
        webhooks.emitir(db, fatura.empresa_id, "fatura.carregada",
                        {"id": fatura.id, "url": f"/api/v1/faturas/{fatura.id}"})
        db.commit()
        return fatura.estado_extracao
    except processamento.FaturaDuplicada as erro:
        db.rollback()
        fatura = db.get(Fatura, fatura.id)
        _lembrar_repetida(fatura.id, str(erro))
        chave = fatura.ficheiro
        db.delete(fatura)
        db.commit()
        armazenamento.apagar_pdf(chave)
        return "repetida"
    except Exception:  # noqa: BLE001 - uma fatura com problemas não pode parar as outras
        logger.exception("Erro ao ler a fatura %s em segundo plano", fatura.id)
        db.rollback()
        fatura = db.get(Fatura, fatura.id)
        fatura.estado_extracao = "erro"
        fatura.erro = "Não foi possível ler esta fatura. Tente «Ler novamente»."
        db.commit()
        return "erro"


def processar_pendentes(db: Session, limite: int | None = None) -> dict[str, int]:
    """Lê as pendentes por ordem de chegada (todas, ou até `limite`)."""
    contagem: dict[str, int] = {}
    feitas = 0
    while limite is None or feitas < limite:
        fatura = db.scalar(select(Fatura).where(Fatura.estado_extracao == ESTADO_PENDENTE)
                           .order_by(Fatura.carregada_em, Fatura.id).limit(1))
        if fatura is None:
            break
        estado = processar_uma(db, fatura)
        contagem[estado] = contagem.get(estado, 0) + 1
        feitas += 1
    return contagem


def despachar() -> None:
    """Começa a ler as pendentes numa thread (se ainda não houver uma a correr)."""
    if not ATIVA or not _a_correr.acquire(blocking=False):
        return

    def correr():
        try:
            from app.db import SessaoLocal
            while True:
                with SessaoLocal() as db:
                    if not processar_pendentes(db, limite=20):
                        break
        except Exception:  # noqa: BLE001
            logger.exception("Erro na leitura em segundo plano")
        finally:
            _a_correr.release()

    threading.Thread(target=correr, name="leitura-faturas", daemon=True).start()


def estados(db: Session, empresa_id: int, ids: list[str]) -> list[dict]:
    """Estado de cada fatura pedida (só desta empresa): para a página acompanhar o lote."""
    faturas = {f.id: f for f in db.scalars(select(Fatura).where(Fatura.empresa_id == empresa_id,
                                                                Fatura.id.in_(ids)))}
    resultado = []
    for fatura_id in ids:
        f = faturas.get(fatura_id)
        if f is not None:
            resultado.append({"id": f.id, "estado_extracao": f.estado_extracao, "fornecedor": f.nome_fornecedor,
                              "total": f.total, "moeda": f.moeda, "erro": f.erro})
        else:
            mensagem = repetida(fatura_id)
            resultado.append({"id": fatura_id, "estado_extracao": "repetida" if mensagem else "desconhecida",
                              "erro": mensagem})
    return resultado
