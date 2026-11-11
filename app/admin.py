"""
Administração da plataforma (para ti, o operador; não fica exposto na web).

Uso:
    python -m app.admin criar-empresa --nome "Restaurante X, Lda." --nif 500000000 \\
                                      --admin-nome "Ana Silva" --admin-email ana@restaurantex.pt
    python -m app.admin listar
    python -m app.admin novo-link --email ana@restaurantex.pt
    python -m app.admin desativar-empresa --id 3      (ou ativar-empresa)

Os comandos imprimem o link de convite: envia-o ao cliente por um canal seguro.
"""

import argparse
import sys

from sqlalchemy import func, select, update

from app.db import SessaoLocal, criar_tabelas
from app.modelos.tabelas import PAPEL_ADMIN, Empresa, Fatura, Fornecedor, Utilizador
from app.servicos import autenticacao, validacao


def criar_empresa(nome: str, nif: str | None, admin_nome: str, admin_email: str) -> int:
    email = autenticacao.normalizar_email(admin_email)
    if nif and not validacao.nif_valido(nif):
        print(f"O NIF {nif} não é válido.")
        return 1

    with SessaoLocal() as db:
        if db.scalar(select(Utilizador).where(Utilizador.email == email)):
            print(f"Já existe uma conta com o email {email}.")
            return 1

        empresa = Empresa(nome=nome.strip(), nif=nif)
        db.add(empresa)
        db.flush()
        admin = Utilizador(empresa_id=empresa.id, nome=admin_nome.strip(), email=email, papel=PAPEL_ADMIN)
        db.add(admin)
        db.flush()

        # Dados criados antes da Fase 5 (sem empresa) passam para a primeira empresa criada
        orfas = db.scalar(select(func.count()).select_from(Fatura).where(Fatura.empresa_id.is_(None)))
        if orfas:
            db.execute(update(Fatura).where(Fatura.empresa_id.is_(None)).values(empresa_id=empresa.id))
            db.execute(update(Fornecedor).where(Fornecedor.empresa_id.is_(None)).values(empresa_id=empresa.id))

        link = autenticacao.criar_link_acesso(db, admin)
        db.commit()

        print(f"Empresa criada: {empresa.nome} (id {empresa.id})")
        if orfas:
            print(f"{orfas} fatura(s) já existente(s) passaram para esta empresa.")
        print(f"\nLink para {email} definir a palavra-passe (válido 72 horas, uso único):\n{link}\n")
    return 0


def listar() -> int:
    with SessaoLocal() as db:
        empresas = db.scalars(select(Empresa).order_by(Empresa.id)).all()
        if not empresas:
            print("Ainda não há empresas. Use: python -m app.admin criar-empresa ...")
        for empresa in empresas:
            faturas = db.scalar(select(func.count()).select_from(Fatura).where(Fatura.empresa_id == empresa.id))
            estado = "ativa" if empresa.ativa else "DESATIVADA"
            print(f"[{empresa.id}] {empresa.nome} ({estado}) - {faturas} faturas")
            for u in db.scalars(select(Utilizador).where(Utilizador.empresa_id == empresa.id).order_by(Utilizador.nome)):
                pendente = " (convite por aceitar)" if u.hash_palavra_passe is None else ""
                inativo = " (desativado)" if not u.ativo else ""
                print(f"     {u.papel:<6} {u.nome} <{u.email}>{pendente}{inativo}")
    return 0


def novo_link(email: str) -> int:
    """Útil quando o administrador de uma empresa perde o acesso."""
    with SessaoLocal() as db:
        utilizador = db.scalar(select(Utilizador).where(Utilizador.email == autenticacao.normalizar_email(email)))
        if utilizador is None:
            print("Não existe nenhuma conta com esse email.")
            return 1
        link = autenticacao.criar_link_acesso(db, utilizador)
        db.commit()
        print(f"Novo link para {utilizador.email} (válido 72 horas, uso único):\n{link}")
    return 0


def mudar_estado_empresa(id_empresa: int, ativa: bool) -> int:
    """Desativar corta o acesso a todos os utilizadores da empresa (os dados ficam guardados)."""
    with SessaoLocal() as db:
        empresa = db.get(Empresa, id_empresa)
        if empresa is None:
            print("Empresa não encontrada.")
            return 1
        empresa.ativa = ativa
        db.commit()
        print(f"{empresa.nome}: {'ativada' if ativa else 'desativada'}.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Administração do Leitor de Faturas")
    comandos = parser.add_subparsers(dest="comando", required=True)

    criar = comandos.add_parser("criar-empresa", help="cria uma empresa cliente e o convite do administrador")
    criar.add_argument("--nome", required=True)
    criar.add_argument("--nif")
    criar.add_argument("--admin-nome", required=True)
    criar.add_argument("--admin-email", required=True)

    comandos.add_parser("listar", help="lista empresas e utilizadores")

    link = comandos.add_parser("novo-link", help="gera um novo link para definir a palavra-passe")
    link.add_argument("--email", required=True)

    for nome, ajuda in (("desativar-empresa", "corta o acesso a uma empresa"), ("ativar-empresa", "repõe o acesso")):
        sub = comandos.add_parser(nome, help=ajuda)
        sub.add_argument("--id", type=int, required=True)

    args = parser.parse_args()
    criar_tabelas()

    if args.comando == "criar-empresa":
        return criar_empresa(args.nome, args.nif, args.admin_nome, args.admin_email)
    if args.comando == "listar":
        return listar()
    if args.comando == "novo-link":
        return novo_link(args.email)
    return mudar_estado_empresa(args.id, ativa=args.comando == "ativar-empresa")


if __name__ == "__main__":
    sys.exit(main())
