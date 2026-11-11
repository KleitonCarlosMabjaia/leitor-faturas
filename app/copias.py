"""
Cópias de segurança da base de dados.

    python -m app.copias criar                       # guarda em copias/ (no bucket, em produção)
    python -m app.copias criar --ficheiro copia.json.gz
    python -m app.copias listar
    python -m app.copias restaurar --chave copias/2026-11-11_030000.json.gz [--substituir]
    python -m app.copias restaurar --ficheiro copia.json.gz [--substituir]

A cópia é um JSON comprimido com todas as linhas de todas as tabelas. Não depende
do motor da base de dados: uma cópia feita em SQLite pode ser restaurada em
PostgreSQL e vice-versa (útil para passar dos testes para produção).

Os PDFs não entram na cópia: ficam no bucket (R2/S3), que já guarda os ficheiros
de forma redundante. As sessões também não: depois de restaurar, entra-se de novo.

Em produção corre todos os dias como tarefa agendada (ver render.yaml).
"""

import argparse
import gzip
import json
import sys
from datetime import date, datetime
from pathlib import Path

from alembic.runtime.migration import MigrationContext
from sqlalchemy import Date, DateTime, Engine, Integer, delete, func, insert, select, text

from app import config
from app.db import Base, motor, preparar_base_dados
from app.modelos import tabelas  # noqa: F401  (regista as tabelas em Base.metadata)
from app.servicos import armazenamento

VERSAO_FORMATO = 1
PREFIXO = "copias/"
TABELAS_EXCLUIDAS = {"sessoes"}  # temporárias; restaurá-las reabriria sessões antigas


class ErroCopia(Exception):
    """Problema que impede restaurar uma cópia (mensagem para o operador)."""


def _tabelas():
    return [t for t in Base.metadata.sorted_tables if t.name not in TABELAS_EXCLUIDAS]


def _para_json(valor):
    return valor.isoformat() if isinstance(valor, (date, datetime)) else valor


def _do_json(coluna, valor):
    if valor is None:
        return None
    if isinstance(coluna.type, DateTime):
        return datetime.fromisoformat(valor)
    if isinstance(coluna.type, Date):
        return date.fromisoformat(valor)
    return valor


def _revisao(ligacao) -> str | None:
    return MigrationContext.configure(ligacao).get_current_revision()


def exportar(motor_alvo: Engine) -> dict:
    """Lê todas as tabelas para um dicionário pronto a converter em JSON."""
    with motor_alvo.connect() as ligacao:
        return {
            "formato": VERSAO_FORMATO,
            "criada_em": datetime.now().isoformat(timespec="seconds"),
            "revisao": _revisao(ligacao),
            "tabelas": {
                tabela.name: [
                    {chave: _para_json(valor) for chave, valor in linha.items()}
                    for linha in ligacao.execute(select(tabela)).mappings()
                ]
                for tabela in _tabelas()
            },
        }


def comprimir(dados: dict) -> bytes:
    return gzip.compress(json.dumps(dados, ensure_ascii=False).encode("utf-8"))


def descomprimir(conteudo: bytes) -> dict:
    try:
        return json.loads(gzip.decompress(conteudo))
    except (OSError, ValueError) as erro:
        raise ErroCopia(f"O ficheiro não é uma cópia válida ({erro}).")


def restaurar(motor_alvo: Engine, dados: dict, substituir: bool = False) -> dict[str, int]:
    """
    Repõe as linhas da cópia numa só transação: ou entra tudo, ou nada.
    Sem substituir=True, recusa uma base de dados que já tenha dados.
    """
    if dados.get("formato") != VERSAO_FORMATO:
        raise ErroCopia("Formato de cópia desconhecido.")

    preparar_base_dados(motor_alvo)
    contagens = {}
    with motor_alvo.begin() as ligacao:
        revisao = _revisao(ligacao)
        if dados.get("revisao") != revisao:
            raise ErroCopia(
                f"A cópia é do esquema {dados.get('revisao')} e a base de dados está no {revisao}. "
                "Restaure com a versão da aplicação que fez a cópia e atualize depois."
            )

        com_dados = [t.name for t in _tabelas() if ligacao.scalar(select(func.count()).select_from(t))]
        if com_dados and not substituir:
            raise ErroCopia(f"A base de dados já tem dados ({', '.join(com_dados)}). Use --substituir para os apagar.")

        # Apagar pela ordem inversa das dependências (faturas antes de empresas, etc.)
        for tabela in reversed(Base.metadata.sorted_tables):
            ligacao.execute(delete(tabela))

        for tabela in _tabelas():
            linhas = [
                {coluna.name: _do_json(coluna, linha.get(coluna.name)) for coluna in tabela.columns}
                for linha in dados["tabelas"].get(tabela.name, [])
            ]
            if linhas:
                ligacao.execute(insert(tabela), linhas)
            contagens[tabela.name] = len(linhas)

        # No PostgreSQL, os ids automáticos vêm de uma sequência que não sabe das linhas
        # inseridas com id: sem isto, a próxima empresa criada teria um id repetido.
        if ligacao.dialect.name == "postgresql":
            for tabela in _tabelas():
                id_coluna = tabela.columns.get("id")
                if id_coluna is not None and isinstance(id_coluna.type, Integer):
                    ligacao.execute(text(
                        f"SELECT setval(pg_get_serial_sequence('{tabela.name}', 'id'), "
                        f"COALESCE((SELECT MAX(id) FROM {tabela.name}), 0) + 1, false)"
                    ))
    return contagens


def criar_no_armazenamento(motor_alvo: Engine) -> str:
    """Guarda uma cópia nova em copias/ e apaga as mais antigas além de COPIAS_A_MANTER."""
    dados = exportar(motor_alvo)
    chave = f"{PREFIXO}{datetime.now():%Y-%m-%d_%H%M%S}.json.gz"
    loja = armazenamento.atual()
    loja.guardar(chave, comprimir(dados), "application/gzip")

    copias = loja.listar(PREFIXO)  # os nomes têm a data, por isso a ordem alfabética é a cronológica
    for antiga in copias[: max(0, len(copias) - config.COPIAS_A_MANTER)]:
        loja.apagar(antiga)
    return chave


def main() -> int:
    parser = argparse.ArgumentParser(description="Cópias de segurança da base de dados")
    comandos = parser.add_subparsers(dest="comando", required=True)

    criar = comandos.add_parser("criar", help="faz uma cópia agora")
    criar.add_argument("--ficheiro", help="guardar neste ficheiro local em vez do armazenamento")

    comandos.add_parser("listar", help="lista as cópias guardadas")

    repor = comandos.add_parser("restaurar", help="repõe uma cópia")
    origem = repor.add_mutually_exclusive_group(required=True)
    origem.add_argument("--chave", help="cópia guardada no armazenamento (ver 'listar')")
    origem.add_argument("--ficheiro", help="cópia num ficheiro local")
    repor.add_argument("--substituir", action="store_true", help="apaga os dados atuais antes de restaurar")

    args = parser.parse_args()

    if args.comando == "criar":
        preparar_base_dados()
        if args.ficheiro:
            Path(args.ficheiro).write_bytes(comprimir(exportar(motor)))
            print(f"Cópia guardada em {args.ficheiro}")
        else:
            print(f"Cópia guardada: {criar_no_armazenamento(motor)}")
        return 0

    if args.comando == "listar":
        copias = armazenamento.atual().listar(PREFIXO)
        print("\n".join(copias) if copias else "Ainda não há cópias.")
        return 0

    if args.ficheiro:
        conteudo = Path(args.ficheiro).read_bytes()
    else:
        conteudo = armazenamento.atual().ler(args.chave)
        if conteudo is None:
            print(f"Não existe a cópia {args.chave}.")
            return 1

    if args.substituir:
        resposta = input("Isto APAGA todos os dados atuais e repõe os da cópia. Escreva 'sim' para continuar: ")
        if resposta.strip().lower() != "sim":
            print("Cancelado.")
            return 1
    try:
        contagens = restaurar(motor, descomprimir(conteudo), substituir=args.substituir)
    except ErroCopia as erro:
        print(f"Não foi possível restaurar: {erro}")
        return 1
    print("Cópia restaurada: " + ", ".join(f"{nome} {n}" for nome, n in contagens.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
