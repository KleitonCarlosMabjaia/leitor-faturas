"""
Webhooks da API pública: entrega os avisos pendentes (novas tentativas dos que falharam).
Os avisos novos são entregues sozinhos logo a seguir ao evento; agende este comando
(por exemplo, de 10 em 10 minutos) para as novas tentativas não dependerem de novos eventos.

    python -m app.webhooks
"""

import logging

from app.db import SessaoLocal
from app.servicos import webhooks


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    with SessaoLocal() as db:
        contagem = webhooks.entregar_pendentes(db)
    print(f"Entregues: {contagem['entregue']} · por tentar outra vez: {contagem['pendente']} · "
          f"desistidas: {contagem['falhou']}")


if __name__ == "__main__":
    main()
