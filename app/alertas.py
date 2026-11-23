"""
Alertas de prazos fiscais por email (tarefa diária).

    python -m app.alertas              # envia os alertas de hoje
    python -m app.alertas --simular    # mostra o que seria enviado, sem enviar

Para cada empresa ativa, junta as obrigações cujo prazo termina daqui a 7 dias ou amanhã
(config.DIAS_ALERTA_PRAZOS) e envia um único email a cada utilizador ativo que não desligou
os alertas. Correr mais de uma vez no mesmo dia repete os emails: agende-a uma vez por dia.
"""

import argparse
import logging
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.db import SessaoLocal
from app.modelos.tabelas import AcessoEmpresa, Empresa, Utilizador
from app.servicos import email
from app.servicos.contabilidade import obrigacoes

logger = logging.getLogger(__name__)


def alertas_do_dia(db: Session, hoje: date) -> list[email.Mensagem]:
    mensagens = []
    for empresa in db.scalars(select(Empresa).where(Empresa.ativa)):
        perto = [o for o in obrigacoes.proximas(hoje, empresa.regime_iva, dias=max(config.DIAS_ALERTA_PRAZOS))
                 if o["dias"] in config.DIAS_ALERTA_PRAZOS]
        if not perto:
            continue
        destinatarios = db.scalars(
            select(Utilizador).join(AcessoEmpresa, AcessoEmpresa.utilizador_id == Utilizador.id).where(
                AcessoEmpresa.empresa_id == empresa.id, AcessoEmpresa.ativo, Utilizador.ativo, Utilizador.alertas_email,
                Utilizador.hash_palavra_passe.is_not(None),
            ))
        for u in destinatarios:
            mensagens.append(email.alerta_prazos(u.email, u.nome, empresa.nome, perto))
    return mensagens


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Alertas de prazos fiscais por email")
    parser.add_argument("--simular", action="store_true", help="mostra os emails sem os enviar")
    args = parser.parse_args(argv)
    with SessaoLocal() as db:
        mensagens = alertas_do_dia(db, date.today())
    if args.simular or not email.configurado():
        for m in mensagens:
            print(f"{m.para}: {m.assunto} ({len(m.lista)} prazo(s))")
        if not email.configurado():
            print("Envio de emails não configurado (RESEND_API_KEY e EMAIL_REMETENTE): nada foi enviado.")
        return 0
    falhas = sum(not email.enviar(m) for m in mensagens)
    print(f"{len(mensagens) - falhas} alerta(s) enviados, {falhas} falha(s).")
    return 1 if falhas else 0


if __name__ == "__main__":
    raise SystemExit(main())
