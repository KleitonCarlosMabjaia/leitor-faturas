"""
Sincronização diária das ligações diretas aos bancos (open banking).

    python -m app.sincronizar_bancos

Lê os movimentos novos de todas as ligações ativas e grava-os na reconciliação bancária.
Pode correr várias vezes por dia: os movimentos repetidos são ignorados.
"""

import logging

from app.db import SessaoLocal
from app.servicos.contabilidade import open_banking

logger = logging.getLogger(__name__)


def main() -> int:
    with SessaoLocal() as db:
        r = open_banking.sincronizar_todas(db)
    print(f"{r['ligacoes']} ligação(ões), {r['importados']} movimento(s) novo(s), {r['erros']} erro(s).")
    return 1 if r["erros"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
