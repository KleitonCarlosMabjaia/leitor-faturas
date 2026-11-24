"""
Faturas certificadas das subscrições: volta a emitir as que ficaram por emitir (Facturalusa em baixo,
dados em falta...). As faturas novas são emitidas sozinhas quando o Stripe confirma o pagamento.

    python -m app.faturas_subscricao
"""

import logging

from app.db import SessaoLocal
from app.servicos import faturas_certificadas


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if not faturas_certificadas.ativa():
        print("A faturação certificada não está configurada (FACTURALUSA_API_KEY).")
        return
    with SessaoLocal() as db:
        contagem = faturas_certificadas.emitir_pendentes(db)
    print(f"Emitidas: {contagem.get('emitida', 0)} · com erro: {contagem.get('erro', 0)}")


if __name__ == "__main__":
    main()
