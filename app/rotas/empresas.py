"""
Seletor de empresas (várias empresas por utilizador).

- GET  /api/empresas                       empresas a que a pessoa tem acesso (e a atual)
- POST /api/empresas/{id}/selecionar       muda a empresa desta sessão
- POST /api/empresas                       cria uma empresa cliente (administradores no plano Gabinete)

A empresa escolhida fica na sessão: cada dispositivo ou separador pode estar numa empresa
diferente. Escolher uma empresa sem acesso ativo devolve 404 (não se revela que existe).
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import config
from app.db import get_db
from app.dependencias import NOME_COOKIE, utilizador_atual
from app.modelos.tabelas import PAPEL_ADMIN, AcessoEmpresa, Empresa, Utilizador
from app.servicos import atividade, autenticacao, empresas, subscricoes, validacao

router = APIRouter(prefix="/api/empresas", tags=["empresas"])


def pode_criar_empresas(utilizador: Utilizador) -> bool:
    return (utilizador.papel == PAPEL_ADMIN and subscricoes.plano(utilizador.empresa).chave == "gabinete"
            and not config.MODO_DEMO)


@router.get("")
def listar(db: Session = Depends(get_db), utilizador: Utilizador = Depends(utilizador_atual)):
    return {
        "atual": utilizador.empresa_id,
        "pode_criar": pode_criar_empresas(utilizador),
        "empresas": [
            {"id": a.empresa_id, "nome": a.empresa.nome, "nif": a.empresa.nif, "papel": a.papel,
             "plano": subscricoes.plano(a.empresa).nome}
            for a in empresas.acessos_ativos(db, utilizador.id)
        ],
    }


@router.post("/{empresa_id}/selecionar")
def selecionar(empresa_id: int, request: Request, db: Session = Depends(get_db),
               utilizador: Utilizador = Depends(utilizador_atual)):
    if config.MODO_DEMO:
        raise HTTPException(status_code=403, detail="A demonstração tem uma só empresa.")
    if not autenticacao.selecionar_empresa(db, request.cookies.get(NOME_COOKIE), utilizador, empresa_id):
        raise HTTPException(status_code=404, detail="Empresa não encontrada.")
    atividade.registar(db, utilizador, "Abriu a empresa", ip=request.client.host if request.client else None)
    db.commit()
    return {"empresa": utilizador.empresa.nome, "contabilidade": subscricoes.tem_funcionalidade(
        utilizador.empresa, subscricoes.FUNC_CONTABILIDADE)}


class NovaEmpresa(BaseModel):
    nome: str = Field(min_length=2, max_length=200)
    nif: str = Field(max_length=12)


@router.post("", status_code=201)
def criar(dados: NovaEmpresa, request: Request, db: Session = Depends(get_db),
          utilizador: Utilizador = Depends(utilizador_atual)):
    """Um gabinete cria a empresa de um cliente: fica administrador dela e passa a trabalhar nela."""
    if not pode_criar_empresas(utilizador):
        raise HTTPException(status_code=403,
                            detail="Só os administradores no plano Gabinete podem criar empresas cliente.")
    nif = "".join(c for c in dados.nif if c.isdigit())
    if not validacao.nif_valido(nif):
        raise HTTPException(status_code=400, detail="O NIF da empresa não é válido.")
    gabinete = utilizador.empresa
    empresa = Empresa(nome=dados.nome.strip(), nif=nif, plano="gabinete", estado_subscricao=subscricoes.ESTADO_ATIVA)
    db.add(empresa)
    db.flush()
    db.add(AcessoEmpresa(utilizador_id=utilizador.id, empresa_id=empresa.id, papel=PAPEL_ADMIN))
    atividade.registar(db, utilizador, "Criou uma empresa cliente", empresa.nome)
    db.flush()
    autenticacao.selecionar_empresa(db, request.cookies.get(NOME_COOKIE), utilizador, empresa.id)
    atividade.registar(db, utilizador, "Empresa criada pelo gabinete", gabinete.nome, empresa_id=empresa.id)
    db.commit()
    return {"id": empresa.id, "nome": empresa.nome}
