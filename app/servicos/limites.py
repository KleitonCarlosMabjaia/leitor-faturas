"""
Limite de pedidos (rate limiting) com janela deslizante, em memória.

- No login e nos convites, por endereço IP: trava quem tenta muitas palavras-passe
  ou muitos emails diferentes (o bloqueio da conta só protege uma conta de cada vez).
- No envio de faturas, por utilizador: trava abusos e gastos inesperados com a IA.

Fica em memória porque a aplicação corre numa só instância. Com várias instâncias,
o contador teria de passar para um serviço partilhado (por exemplo Redis).
"""

import math
import threading
import time
from collections import deque

from fastapi import HTTPException

from app import config


class LimitePedidos:
    def __init__(self, maximo: int, janela_segundos: int):
        self.maximo = maximo
        self.janela = janela_segundos
        self._pedidos: dict[str, deque[float]] = {}
        self._trinco = threading.Lock()  # as rotas síncronas correm em várias threads

    def segundos_ate_poder(self, chave: str, agora: float | None = None) -> int:
        """Regista o pedido e devolve 0 se é permitido; senão, quantos segundos faltam."""
        agora = time.monotonic() if agora is None else agora
        with self._trinco:
            pedidos = self._pedidos.setdefault(chave, deque())
            while pedidos and pedidos[0] <= agora - self.janela:
                pedidos.popleft()
            if len(pedidos) >= self.maximo:
                return max(1, math.ceil(pedidos[0] + self.janela - agora))
            pedidos.append(agora)
            if len(self._pedidos) > 10_000:  # não deixar a memória crescer sem fim
                self._limpar_vazios(agora)
            return 0

    def _limpar_vazios(self, agora: float) -> None:
        for chave in [c for c, p in self._pedidos.items() if not p or p[-1] <= agora - self.janela]:
            del self._pedidos[chave]

    def limpar(self) -> None:
        with self._trinco:
            self._pedidos.clear()


login = LimitePedidos(*config.LIMITE_LOGIN)
envios = LimitePedidos(*config.LIMITE_ENVIOS)


def verificar(limite: LimitePedidos, chave: str) -> None:
    """Responde 429 (Too Many Requests) quando o limite é ultrapassado."""
    espera = limite.segundos_ate_poder(chave)
    if espera:
        minutos = max(1, round(espera / 60))
        raise HTTPException(
            status_code=429,
            detail=f"Demasiados pedidos. Tente novamente daqui a {minutos} minuto(s).",
            headers={"Retry-After": str(espera)},
        )


def limpar_todos() -> None:
    login.limpar()
    envios.limpar()
