"""
Leitor de Faturas - ponto de entrada da aplicação.

Aqui só se cria a aplicação e se ligam as rotas. A lógica está em:
- app/rotas/     -> endpoints HTTP
- app/servicos/  -> leitura do PDF, QR, IA, validações e acesso à base de dados
- app/modelos/   -> tabelas (SQLAlchemy) e modelos de dados (Pydantic)
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import SQLAlchemyError

from app import config
from app.db import SessaoLocal, criar_tabelas
from app.rotas import dashboard, faturas, fornecedores

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def ciclo_de_vida(app: FastAPI):
    # Cria as tabelas no arranque, se ainda não existirem
    criar_tabelas()

    # Na versão publicada, a base de dados começa com as faturas de demonstração
    if config.MODO_DEMO:
        from app import demo

        with SessaoLocal() as db:
            if demo.base_vazia(db):
                logger.info("Modo demonstração: criadas %d faturas de exemplo", demo.criar_dados_demo(db))
    yield


app = FastAPI(title="Leitor de Faturas", version="1.0.0", lifespan=ciclo_de_vida)
app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")
app.include_router(faturas.router)
app.include_router(fornecedores.router)
app.include_router(dashboard.router)


@app.exception_handler(SQLAlchemyError)
def erro_base_dados(request: Request, erro: SQLAlchemyError):
    """Qualquer erro da base de dados chega ao utilizador como uma mensagem clara."""
    logger.exception("Erro na base de dados em %s", request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Erro na base de dados. Tente novamente."})


@app.get("/", include_in_schema=False)
def pagina_inicial():
    return FileResponse(config.STATIC_DIR / "index.html")


@app.get("/faturas/{id_fatura}", include_in_schema=False)
def pagina_fatura(id_fatura: str):
    # A página é sempre a mesma; o JavaScript lê o id do endereço e pede os dados à API
    return FileResponse(config.STATIC_DIR / "fatura.html")


@app.get("/dashboard", include_in_schema=False)
def pagina_dashboard():
    return FileResponse(config.STATIC_DIR / "dashboard.html")


@app.get("/api/saude")
def saude():
    """Confirma que o servidor está a correr e se a IA está configurada."""
    return {
        "estado": "ok",
        "ia_configurada": config.CHAVE_API_DEFINIDA,
        "modelo": config.MODELO_IA,
        "modo_demo": config.MODO_DEMO,
    }
