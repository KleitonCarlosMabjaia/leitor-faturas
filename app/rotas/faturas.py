"""
Endpoints REST das faturas.

Todas as rotas exigem sessão iniciada (Depends(utilizador_atual)) e só veem as
faturas da empresa de quem faz o pedido.

As funções são "def" (síncronas) de propósito: a leitura do PDF e a chamada
à IA são bloqueantes, e o FastAPI corre funções síncronas numa thread à parte,
sem bloquear os outros pedidos.
"""

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import config
from app.db import get_db
from app.dependencias import utilizador_atual
from app.modelos.esquemas import FaturaSaida
from app.modelos.fatura import EdicaoFatura
from app.modelos.tabelas import ESTADO_CONFIRMADA, ESTADO_POR_REVER, Fatura, Utilizador
from app.servicos import armazenamento, exportacao, processamento, repositorio

router = APIRouter(prefix="/api/faturas", tags=["faturas"])

PADRAO_MES = r"^\d{4}-(0[1-9]|1[0-2])$"


class Pagamento(BaseModel):
    paga: bool


def _obter_ou_404(db: Session, utilizador: Utilizador, id_fatura: str) -> Fatura:
    if not armazenamento.id_valido(id_fatura):
        raise HTTPException(status_code=400, detail="Identificador inválido.")
    fatura = repositorio.obter_fatura(db, utilizador.empresa_id, id_fatura)
    if fatura is None:
        # Também para faturas de outras empresas: não se revela que o id existe
        raise HTTPException(status_code=404, detail="Fatura não encontrada.")
    return fatura


@router.post("", status_code=201, response_model=FaturaSaida)
def carregar_fatura(
    ficheiro: UploadFile = File(...),
    db: Session = Depends(get_db),
    utilizador: Utilizador = Depends(utilizador_atual),
):
    """Recebe um PDF, guarda-o e extrai os dados."""
    if not ficheiro.filename or not ficheiro.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Só são aceites ficheiros PDF.")

    conteudo = ficheiro.file.read()

    if len(conteudo) == 0:
        raise HTTPException(status_code=400, detail="O ficheiro está vazio.")
    if len(conteudo) > config.TAMANHO_MAXIMO:
        raise HTTPException(status_code=413, detail="O ficheiro tem mais de 10 MB.")
    if not conteudo.startswith(b"%PDF"):
        raise HTTPException(status_code=400, detail="O ficheiro não é um PDF válido.")

    try:
        id_fatura, nome_ficheiro = armazenamento.guardar_pdf(conteudo, ficheiro.filename, utilizador.empresa_id)
    except OSError:
        raise HTTPException(status_code=500, detail="Não foi possível guardar o ficheiro no servidor.")

    fatura = Fatura(
        id=id_fatura,
        empresa_id=utilizador.empresa_id,
        ficheiro=nome_ficheiro,
        nome_original=ficheiro.filename,
        tamanho_kb=round(len(conteudo) / 1024, 1),
        estado=ESTADO_POR_REVER,
    )

    try:
        processamento.processar(db, fatura, conteudo)
        db.add(fatura)
        db.commit()
    except processamento.FaturaDuplicada as erro:
        db.rollback()
        armazenamento.apagar_pdf(nome_ficheiro)
        raise HTTPException(status_code=409, detail=str(erro))
    except Exception:
        # Se a gravação falhar, não deixar um PDF órfão em uploads/
        db.rollback()
        armazenamento.apagar_pdf(nome_ficheiro)
        raise

    # Mesmo que a leitura falhe, a fatura fica guardada (201) com o erro registado
    return fatura


@router.get("", response_model=list[FaturaSaida])
def listar_faturas(
    fornecedor_id: int | None = None,
    mes: str | None = Query(None, pattern=PADRAO_MES, description="Mês de emissão, AAAA-MM"),
    estado: Literal["por rever", "confirmada"] | None = None,
    db: Session = Depends(get_db),
    utilizador: Utilizador = Depends(utilizador_atual),
):
    return repositorio.listar_faturas(
        db, utilizador.empresa_id, fornecedor_id=fornecedor_id, mes=mes, estado=estado
    )


# Tem de vir antes de "/{id_fatura}", senão "exportar" seria lido como um id
@router.get("/exportar")
def exportar_faturas(
    formato: Literal["csv", "xlsx"] = "xlsx",
    fornecedor_id: int | None = None,
    mes: str | None = Query(None, pattern=PADRAO_MES),
    estado: Literal["por rever", "confirmada"] | None = None,
    db: Session = Depends(get_db),
    utilizador: Utilizador = Depends(utilizador_atual),
):
    """Exporta as faturas (com os mesmos filtros da lista) para CSV ou Excel."""
    faturas = [
        f for f in repositorio.listar_faturas(
            db, utilizador.empresa_id, fornecedor_id=fornecedor_id, mes=mes, estado=estado
        )
        if f.estado_extracao == "concluida"  # sem dados não há nada útil para exportar
    ]
    # Ordem cronológica, como num extrato
    faturas.sort(key=lambda f: (f.data_emissao or date.min, f.numero_fatura or ""))

    nome = f"faturas-{mes or date.today().isoformat()}"
    if formato == "csv":
        conteudo, tipo = exportacao.gerar_csv(faturas), "text/csv; charset=utf-8"
    else:
        conteudo = exportacao.gerar_xlsx(faturas)
        tipo = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

    return Response(
        content=conteudo,
        media_type=tipo,
        headers={"Content-Disposition": f'attachment; filename="{nome}.{formato}"'},
    )


@router.get("/{id_fatura}", response_model=FaturaSaida)
def obter_fatura(id_fatura: str, db: Session = Depends(get_db), utilizador: Utilizador = Depends(utilizador_atual)):
    return _obter_ou_404(db, utilizador, id_fatura)


@router.get("/{id_fatura}/pdf")
def obter_pdf(id_fatura: str, db: Session = Depends(get_db), utilizador: Utilizador = Depends(utilizador_atual)):
    fatura = _obter_ou_404(db, utilizador, id_fatura)
    caminho = armazenamento.caminho_pdf(fatura.ficheiro)
    if caminho is None:
        raise HTTPException(status_code=404, detail="O ficheiro PDF desta fatura não foi encontrado.")
    # "inline" faz o navegador mostrar o PDF em vez de o descarregar
    return FileResponse(
        caminho,
        media_type="application/pdf",
        content_disposition_type="inline",
        filename=fatura.nome_original,
    )


@router.put("/{id_fatura}", response_model=FaturaSaida)
def editar_fatura(
    id_fatura: str,
    edicao: EdicaoFatura,
    db: Session = Depends(get_db),
    utilizador: Utilizador = Depends(utilizador_atual),
):
    """Guarda as correções do utilizador. Com confirmar=true, marca a fatura como confirmada."""
    fatura = _obter_ou_404(db, utilizador, id_fatura)

    if edicao.confirmar:
        # Para confirmar, os campos essenciais têm de estar preenchidos
        em_falta = [
            nome
            for nome, valor in (
                ("fornecedor", edicao.fornecedor),
                ("NIF", edicao.nif_fornecedor),
                ("número da fatura", edicao.numero_fatura),
                ("data de emissão", edicao.data_emissao),
            )
            if not valor
        ]
        if em_falta:
            raise HTTPException(status_code=422, detail=f"Preencha antes de confirmar: {', '.join(em_falta)}.")

    duplicada = repositorio.encontrar_duplicado(
        db, utilizador.empresa_id, edicao.nif_fornecedor, edicao.numero_fatura, fatura.atcud, excluir_id=fatura.id
    )
    if duplicada:
        raise HTTPException(
            status_code=409,
            detail=f"Já existe outra fatura com este NIF e número ({duplicada.nome_original}).",
        )

    repositorio.aplicar_dados(db, fatura, edicao)
    fatura.estado_extracao = "concluida"
    fatura.erro = None
    fatura.estado = ESTADO_CONFIRMADA if edicao.confirmar else ESTADO_POR_REVER

    # O nome confirmado pelo utilizador passa a ser o nome do fornecedor na cache da empresa
    if edicao.confirmar and fatura.fornecedor and edicao.fornecedor:
        fatura.fornecedor.nome = edicao.fornecedor

    db.commit()
    return fatura


@router.patch("/{id_fatura}/pagamento", response_model=FaturaSaida)
def marcar_pagamento(
    id_fatura: str,
    pagamento: Pagamento,
    db: Session = Depends(get_db),
    utilizador: Utilizador = Depends(utilizador_atual),
):
    """Marca a fatura como paga ou por pagar (separado da edição dos dados da fatura)."""
    fatura = _obter_ou_404(db, utilizador, id_fatura)
    fatura.paga = pagamento.paga
    db.commit()
    return fatura


@router.post("/{id_fatura}/extrair", response_model=FaturaSaida)
def extrair_novamente(id_fatura: str, db: Session = Depends(get_db), utilizador: Utilizador = Depends(utilizador_atual)):
    """Volta a ler uma fatura já guardada (útil depois de um erro)."""
    fatura = _obter_ou_404(db, utilizador, id_fatura)
    caminho = armazenamento.caminho_pdf(fatura.ficheiro)
    if caminho is None:
        raise HTTPException(status_code=404, detail="O ficheiro PDF desta fatura não foi encontrado.")

    try:
        processamento.processar(db, fatura, caminho.read_bytes())
    except processamento.FaturaDuplicada as erro:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(erro))
    db.commit()
    return fatura


@router.delete("/{id_fatura}", status_code=204)
def apagar_fatura(id_fatura: str, db: Session = Depends(get_db), utilizador: Utilizador = Depends(utilizador_atual)):
    fatura = _obter_ou_404(db, utilizador, id_fatura)
    db.delete(fatura)
    db.commit()
    # O ficheiro só é apagado depois de a base de dados confirmar
    armazenamento.apagar_pdf(fatura.ficheiro)
