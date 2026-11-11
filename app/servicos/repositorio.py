"""
Acesso à base de dados (padrão "repositório").

As rotas e os serviços não escrevem SQL: chamam estas funções. Assim, mudar
a forma de guardar os dados só obriga a mexer neste ficheiro.

Isolamento entre empresas: TODAS as funções que leem ou procuram dados recebem
`empresa_id` e filtram por ele. Uma fatura de outra empresa é tratada como se
não existisse (404), para não revelar sequer que o id existe.

As funções não fazem commit: quem as chama decide quando gravar.
"""

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.modelos.fatura import CamposFatura
from app.modelos.tabelas import ESTADO_POR_REVER, Fatura, Fornecedor
from app.servicos import validacao


# ---------- Fornecedores ----------

def fornecedor_por_nif(db: Session, empresa_id: int, nif: str) -> Fornecedor | None:
    return db.scalar(select(Fornecedor).where(Fornecedor.empresa_id == empresa_id, Fornecedor.nif == nif))


def registar_fornecedor(db: Session, empresa_id: int, nif: str, nome: str) -> Fornecedor | None:
    """
    Devolve o fornecedor deste NIF nesta empresa, criando-o se não existir.
    Se o nome guardado estiver vazio e agora soubermos o nome, atualiza-o.
    NIFs inválidos não são guardados (evita "fornecedores" com dados errados).
    """
    if not validacao.nif_valido(nif):
        return None
    fornecedor = fornecedor_por_nif(db, empresa_id, nif)
    if fornecedor is None:
        fornecedor = Fornecedor(empresa_id=empresa_id, nif=nif, nome=nome)
        db.add(fornecedor)
        db.flush()  # atribui o id sem fazer commit
    elif nome and not fornecedor.nome:
        fornecedor.nome = nome
    return fornecedor


def listar_fornecedores(db: Session, empresa_id: int) -> list[Fornecedor]:
    return list(db.scalars(
        select(Fornecedor).where(Fornecedor.empresa_id == empresa_id).order_by(Fornecedor.nome, Fornecedor.nif)
    ))


# ---------- Faturas ----------

def obter_fatura(db: Session, empresa_id: int, id_fatura: str) -> Fatura | None:
    return db.scalar(
        select(Fatura)
        .options(joinedload(Fatura.fornecedor))
        .where(Fatura.id == id_fatura, Fatura.empresa_id == empresa_id)
    )


def listar_faturas(
    db: Session,
    empresa_id: int,
    fornecedor_id: int | None = None,
    mes: str | None = None,
    estado: str | None = None,
) -> list[Fatura]:
    """Lista as faturas da empresa, mais recentes primeiro, com filtros opcionais."""
    consulta = (
        select(Fatura)
        .options(joinedload(Fatura.fornecedor))
        .where(Fatura.empresa_id == empresa_id)
        .order_by(Fatura.carregada_em.desc())
    )
    if fornecedor_id is not None:
        consulta = consulta.where(Fatura.fornecedor_id == fornecedor_id)
    if mes:
        inicio, fim = intervalo_do_mes(mes)
        consulta = consulta.where(Fatura.data_emissao >= inicio, Fatura.data_emissao < fim)
    if estado:
        consulta = consulta.where(Fatura.estado == estado)

    return list(db.scalars(consulta))


def intervalo_do_mes(mes: str) -> tuple[date, date]:
    """'2026-10' -> (1 de outubro, 1 de novembro). Lança ValueError se o formato for inválido."""
    ano, numero_mes = (int(parte) for parte in mes.split("-"))
    inicio = date(ano, numero_mes, 1)
    fim = date(ano + 1, 1, 1) if numero_mes == 12 else date(ano, numero_mes + 1, 1)
    return inicio, fim


def encontrar_duplicado(
    db: Session, empresa_id: int, nif: str, numero_fatura: str, atcud: str | None, excluir_id: str | None = None
) -> Fatura | None:
    """
    Procura uma fatura igual NESTA empresa: mesmo NIF e número, ou mesmo ATCUD.
    (Duas empresas diferentes podem ter recebido cópias da mesma fatura.)
    O ATCUD é único por documento em Portugal; "0" significa "sem ATCUD".
    """
    condicoes = []
    if nif and numero_fatura:
        condicoes.append((Fatura.nif_fornecedor == nif) & (Fatura.numero_fatura == numero_fatura))
    if atcud and atcud != "0":
        condicoes.append(Fatura.atcud == atcud)
    if not condicoes:
        return None

    consulta = select(Fatura).where(
        Fatura.empresa_id == empresa_id,
        condicoes[0] if len(condicoes) == 1 else condicoes[0] | condicoes[1],
    )
    if excluir_id:
        consulta = consulta.where(Fatura.id != excluir_id)
    return db.scalar(consulta.limit(1))


def aplicar_dados(db: Session, fatura: Fatura, dados: CamposFatura, alertas_extra: list[str] | None = None) -> None:
    """Copia os dados para a fatura, liga-a ao fornecedor da mesma empresa e recalcula as verificações."""
    verificacoes = validacao.verificar(dados)
    linhas = [linha.model_dump() for linha in dados.iva]

    fatura.fornecedor = registar_fornecedor(db, fatura.empresa_id, dados.nif_fornecedor, dados.fornecedor)
    fatura.nome_fornecedor = dados.fornecedor
    fatura.nif_fornecedor = dados.nif_fornecedor
    fatura.numero_fatura = dados.numero_fatura
    fatura.data_emissao = date.fromisoformat(dados.data_emissao) if dados.data_emissao else None
    fatura.data_vencimento = date.fromisoformat(dados.data_vencimento) if dados.data_vencimento else None
    fatura.valor_sem_iva = round(dados.valor_sem_iva, 2)
    fatura.total_iva = round(sum(linha["valor"] for linha in linhas), 2)
    fatura.total = round(dados.total, 2)
    fatura.moeda = dados.moeda
    fatura.linhas_iva = linhas
    fatura.confianca = getattr(dados, "confianca", fatura.confianca)
    fatura.nif_valido = verificacoes["nif_valido"]
    fatura.totais_batem = verificacoes["totais_batem"]
    fatura.alertas = (alertas_extra or []) + verificacoes["alertas"]


def limpar_dados(fatura: Fatura) -> None:
    """Apaga os dados extraídos (usado quando a leitura falha)."""
    for campo in (
        "fornecedor", "nome_fornecedor", "nif_fornecedor", "numero_fatura", "data_emissao",
        "data_vencimento", "valor_sem_iva", "total_iva", "total", "moeda", "linhas_iva",
        "confianca", "origem", "atcud", "tipo_documento", "alertas", "nif_valido", "totais_batem",
    ):
        setattr(fatura, campo, None)
    fatura.estado = ESTADO_POR_REVER
