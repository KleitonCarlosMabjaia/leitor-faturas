"""
Várias empresas por utilizador (gabinetes de contabilidade).

A cada pedido, a empresa da sessão só é usada depois de confirmado que o utilizador tem um
acesso ativo a ela e que a empresa está ativa. Depois de confirmado, Utilizador.empresa e
Utilizador.papel passam a ser os dessa empresa: todo o resto do código continua a filtrar por
utilizador.empresa_id, por isso o isolamento entre empresas mantém-se igual.

(Utilizador.empresa_id e .papel ficam assim gravados como "a última empresa usada", que é a
empresa por omissão da próxima sessão.)
"""

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.modelos.tabelas import AcessoEmpresa, Empresa, Utilizador


def acessos_ativos(db: Session, utilizador_id: int) -> list[AcessoEmpresa]:
    return list(db.scalars(
        select(AcessoEmpresa).options(joinedload(AcessoEmpresa.empresa))
        .join(Empresa, Empresa.id == AcessoEmpresa.empresa_id)
        .where(AcessoEmpresa.utilizador_id == utilizador_id, AcessoEmpresa.ativo, Empresa.ativa)
        .order_by(Empresa.nome)
    ))


def acesso(db: Session, utilizador_id: int, empresa_id: int | None) -> AcessoEmpresa | None:
    """O acesso ativo do utilizador a esta empresa (ativa), ou None."""
    if empresa_id is None:
        return None
    return db.scalar(
        select(AcessoEmpresa).options(joinedload(AcessoEmpresa.empresa))
        .join(Empresa, Empresa.id == AcessoEmpresa.empresa_id)
        .where(AcessoEmpresa.utilizador_id == utilizador_id, AcessoEmpresa.empresa_id == empresa_id,
               AcessoEmpresa.ativo, Empresa.ativa)
    )


def resolver(db: Session, utilizador: Utilizador, preferida: int | None) -> AcessoEmpresa | None:
    """A empresa preferida (a da sessão), senão a última usada, senão a primeira com acesso."""
    for candidata in (preferida, utilizador.empresa_id):
        encontrado = acesso(db, utilizador.id, candidata)
        if encontrado is not None:
            return encontrado
    restantes = acessos_ativos(db, utilizador.id)
    return restantes[0] if restantes else None


def aplicar(utilizador: Utilizador, escolhido: AcessoEmpresa) -> None:
    """Passa a empresa e o papel do utilizador (neste pedido) para os da empresa escolhida."""
    if utilizador.empresa_id != escolhido.empresa_id or utilizador.empresa is not escolhido.empresa:
        # As duas coisas, explicitamente: só a relação (empresa) não muda empresa_id até à gravação
        # (autoflush desligado), e todas as consultas filtram por empresa_id.
        utilizador.empresa = escolhido.empresa
        utilizador.empresa_id = escolhido.empresa_id
    if utilizador.papel != escolhido.papel:
        utilizador.papel = escolhido.papel


def membro(db: Session, empresa_id: int, utilizador_id: int) -> AcessoEmpresa | None:
    """O acesso de uma pessoa a esta empresa (ativo ou não), para a gestão da equipa."""
    return db.scalar(
        select(AcessoEmpresa).options(joinedload(AcessoEmpresa.utilizador))
        .where(AcessoEmpresa.empresa_id == empresa_id, AcessoEmpresa.utilizador_id == utilizador_id)
    )


def noutras_empresas(db: Session, utilizador_id: int, empresa_id: int) -> bool:
    """A pessoa tem acesso a outras empresas além desta? (limita o que o administrador desta pode fazer)"""
    return db.scalar(select(AcessoEmpresa.id).where(
        AcessoEmpresa.utilizador_id == utilizador_id, AcessoEmpresa.empresa_id != empresa_id,
    ).limit(1)) is not None
