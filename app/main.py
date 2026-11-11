"""
Leitor de Faturas - ponto de entrada da aplicação.

Aqui só se cria a aplicação e se ligam as rotas. A lógica está em:
- app/rotas/      -> endpoints HTTP
- app/servicos/   -> leitura do PDF, QR, IA, validações, autenticação e base de dados
- app/modelos/    -> tabelas (SQLAlchemy) e modelos de dados (Pydantic)
- app/dependencias.py -> quem está a fazer o pedido (sessão)
"""

import logging
from contextlib import asynccontextmanager
from urllib.parse import quote

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app import config
from app.db import SessaoLocal, get_db, preparar_base_dados
from app.dependencias import utilizador_opcional
from app.modelos.tabelas import PAPEL_ADMIN, Utilizador
from app.rotas import auth, dashboard, equipa, faturas, fornecedores

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def iniciar_sentry() -> None:
    """
    Envia os erros para o Sentry, se SENTRY_DSN estiver definido.
    Sem dados pessoais: nem cookies, nem IPs, nem o conteúdo dos pedidos (que tem dados de faturas).
    """
    if not config.SENTRY_DSN:
        return
    import sentry_sdk

    sentry_sdk.init(
        dsn=config.SENTRY_DSN,
        environment=config.AMBIENTE,
        send_default_pii=False,
        max_request_body_size="never",
        traces_sample_rate=0,  # só erros, sem medição de desempenho
    )


iniciar_sentry()


@asynccontextmanager
async def ciclo_de_vida(app: FastAPI):
    # Aplica as migrações em falta (cria as tabelas numa base de dados nova)
    preparar_base_dados()

    # Na versão publicada, a empresa de demonstração é criada no primeiro arranque
    if config.MODO_DEMO:
        from app import demo

        with SessaoLocal() as db:
            if demo.empresa_demo(db) is None:
                logger.info("Modo demonstração: criadas %d faturas de exemplo", demo.criar_dados_demo(db))
    yield


app = FastAPI(title="Leitor de Faturas", version="2.0.0", lifespan=ciclo_de_vida)
app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")
for modulo in (auth, faturas, fornecedores, dashboard, equipa):
    app.include_router(modulo.router)


@app.middleware("http")
async def cabecalhos_seguranca(request: Request, call_next):
    """Cabeçalhos HTTP que reduzem ataques comuns no navegador."""
    resposta = await call_next(request)
    resposta.headers["X-Content-Type-Options"] = "nosniff"        # não "adivinhar" tipos de ficheiro
    resposta.headers["X-Frame-Options"] = "SAMEORIGIN"            # só esta app pode mostrar as páginas em iframe
    resposta.headers["Referrer-Policy"] = "same-origin"           # não enviar endereços internos a outros sites
    if request.url.path.startswith("/api/"):
        resposta.headers["Cache-Control"] = "no-store"            # dados das faturas não ficam em caches
    return resposta


@app.exception_handler(SQLAlchemyError)
def erro_base_dados(request: Request, erro: SQLAlchemyError):
    """Qualquer erro da base de dados chega ao utilizador como uma mensagem clara."""
    logger.exception("Erro na base de dados em %s", request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Erro na base de dados. Tente novamente."})


# ---------- Páginas ----------

def _pagina(nome: str) -> FileResponse:
    return FileResponse(config.STATIC_DIR / nome)


def _protegida(request: Request, utilizador: Utilizador | None, nome: str, so_admin: bool = False):
    """Sem sessão, envia para o login (e volta a esta página depois de entrar)."""
    if utilizador is None:
        destino = quote(request.url.path, safe="/")
        return RedirectResponse(f"/entrar?voltar={destino}", status_code=303)
    if so_admin and (utilizador.papel != PAPEL_ADMIN or config.MODO_DEMO):
        return RedirectResponse("/", status_code=303)
    return _pagina(nome)


@app.get("/", include_in_schema=False)
def pagina_inicial(request: Request, utilizador: Utilizador | None = Depends(utilizador_opcional)):
    return _protegida(request, utilizador, "index.html")


@app.get("/faturas/{id_fatura}", include_in_schema=False)
def pagina_fatura(request: Request, utilizador: Utilizador | None = Depends(utilizador_opcional)):
    # A página é sempre a mesma; o JavaScript lê o id do endereço e pede os dados à API
    return _protegida(request, utilizador, "fatura.html")


@app.get("/dashboard", include_in_schema=False)
def pagina_dashboard(request: Request, utilizador: Utilizador | None = Depends(utilizador_opcional)):
    return _protegida(request, utilizador, "dashboard.html")


@app.get("/equipa", include_in_schema=False)
def pagina_equipa(request: Request, utilizador: Utilizador | None = Depends(utilizador_opcional)):
    return _protegida(request, utilizador, "equipa.html", so_admin=True)


@app.get("/entrar", include_in_schema=False)
def pagina_entrar(utilizador: Utilizador | None = Depends(utilizador_opcional)):
    # Quem já tem sessão (ou está na demonstração) vai direto para a aplicação
    return RedirectResponse("/", status_code=303) if utilizador else _pagina("entrar.html")


@app.get("/definir-palavra-passe", include_in_schema=False)
def pagina_definir_palavra_passe():
    return _pagina("definir-palavra-passe.html")


@app.get("/api/saude")
def saude(db: Session = Depends(get_db)):
    """
    Confirma que o servidor e a base de dados respondem (o alojamento usa esta rota
    para saber se a aplicação está viva) e se a IA está configurada.
    """
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError:
        logger.exception("A base de dados não responde")
        return JSONResponse(status_code=503, content={"estado": "erro", "detalhe": "Base de dados indisponível."})
    return {
        "estado": "ok",
        "ia_configurada": config.CHAVE_API_DEFINIDA,
        "modelo": config.MODELO_IA,
        "modo_demo": config.MODO_DEMO,
    }
