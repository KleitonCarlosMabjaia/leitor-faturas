"""
Dobra - ponto de entrada da aplicação.

Aqui só se cria a aplicação e se ligam as rotas. A lógica está em:
- app/rotas/      -> endpoints HTTP
- app/servicos/   -> leitura do PDF, QR, IA, validações, autenticação e base de dados
- app/modelos/    -> tabelas (SQLAlchemy) e modelos de dados (Pydantic)
- app/dependencias.py -> quem está a fazer o pedido (sessão)
"""

import html
import logging
from contextlib import asynccontextmanager
from functools import lru_cache
from urllib.parse import quote

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app import config
from app.db import SessaoLocal, get_db, preparar_base_dados
from app.dependencias import utilizador_opcional
from app.modelos.tabelas import PAPEL_ADMIN, Utilizador
from app.rotas import auth, contabilidade, dashboard, equipa, faturas, fornecedores

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


# Em produção a documentação automática da API não é publicada: não se expõe o mapa das rotas
_docs = config.AMBIENTE != "producao"
app = FastAPI(
    title="Dobra", version=config.VERSAO_APLICACAO, lifespan=ciclo_de_vida,
    docs_url="/docs" if _docs else None, redoc_url=None, openapi_url="/openapi.json" if _docs else None,
)
# Respostas comprimidas (JSON da contabilidade, JS e CSS): 4 a 8 vezes menos dados na rede
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")
for modulo in (auth, faturas, fornecedores, dashboard, contabilidade, equipa):
    app.include_router(modulo.router)


PAGINAS_HTML = {"/", "/dashboard", "/contabilidade", "/equipa", "/entrar", "/definir-palavra-passe",
                "/apresentacao", "/privacidade", "/termos"}

# Política de segurança de conteúdo: só scripts, estilos, tipos de letra e imagens da própria
# aplicação. Um script injetado (XSS) não corre nem consegue enviar dados para outro site.
# style-src-attr: alguns elementos têm estilos em linha (larguras de barras), sem risco de execução.
CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self'",
    "style-src-attr 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self'",
    "connect-src 'self'",
    "frame-src 'self' blob:",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'self'",
])
METODOS_QUE_ALTERAM = {"POST", "PUT", "PATCH", "DELETE"}


def _origem_estrangeira(request: Request) -> bool:
    """
    Defesa contra CSRF, além do cookie SameSite=Lax: um pedido que altera dados tem de vir
    de uma página desta aplicação. O navegador indica a origem (Origin / Sec-Fetch-Site).
    """
    origem = request.headers.get("origin")
    if origem:
        return origem.split("://", 1)[-1] != request.headers.get("host", "")
    return request.headers.get("sec-fetch-site") == "cross-site"


@app.middleware("http")
async def cabecalhos_seguranca(request: Request, call_next):
    """Cabeçalhos HTTP que reduzem ataques comuns no navegador."""
    if request.method in METODOS_QUE_ALTERAM and request.url.path.startswith("/api/") and _origem_estrangeira(request):
        return JSONResponse(status_code=403, content={"detail": "Pedido recusado: origem não autorizada."})
    resposta = await call_next(request)
    cab = resposta.headers
    cab["X-Content-Type-Options"] = "nosniff"        # não "adivinhar" tipos de ficheiro
    cab["X-Frame-Options"] = "SAMEORIGIN"            # só esta app pode mostrar as páginas em iframe
    cab["Referrer-Policy"] = "same-origin"           # não enviar endereços internos a outros sites
    cab["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
    cab["Cross-Origin-Opener-Policy"] = "same-origin"
    if cab.get("content-type", "").startswith("text/html"):
        cab["Content-Security-Policy"] = CSP
    if request.url.scheme == "https":
        # O navegador passa a usar sempre HTTPS neste domínio durante um ano
        cab["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    caminho = request.url.path
    if caminho.startswith("/api/"):
        cab["Cache-Control"] = "no-store"            # dados das faturas não ficam em caches
    elif caminho.startswith("/static/") and request.query_params.get("v"):
        # Ficheiros com versão no endereço (?v=...) nunca mudam: o navegador guarda-os um ano
        # e não volta a pedi-los. Uma atualização muda a versão e, com ela, o endereço.
        cab["Cache-Control"] = "public, max-age=31536000, immutable"
    elif caminho.startswith("/static/") or caminho in PAGINAS_HTML:
        # O navegador pode guardar as páginas, mas confirma sempre se mudaram (ETag -> 304)
        cab["Cache-Control"] = "no-cache"
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
    # Visitantes sem sessão veem a apresentação do produto; clientes entram direto na aplicação
    if utilizador is None:
        return RedirectResponse("/apresentacao", status_code=303)
    return _protegida(request, utilizador, "index.html")


# ---------- Páginas públicas ----------

@lru_cache(maxsize=8)
def _pagina_publica(nome: str) -> str:
    """Páginas públicas com os dados comerciais da configuração (escapados) no lugar das marcas {{...}}."""
    email = config.EMAIL_COMERCIAL
    valores = {
        "URL_DEMO": config.URL_DEMO or "/entrar",
        "CONTACTO": f"mailto:{email}?subject=Dobra" if email else "/entrar",
        "CONTACTO_TEXTO": "Pedir uma proposta" if email else "Entrar na minha conta",
        "RESPONSAVEL": config.EMPRESA_RESPONSAVEL or "Dobra",
        "EMAIL_CONTACTO": email or "o endereço de contacto indicado no contrato",
    }
    texto = (config.STATIC_DIR / nome).read_text(encoding="utf-8")
    for chave, valor in valores.items():
        texto = texto.replace("{{" + chave + "}}", html.escape(valor))
    return texto


@app.get("/apresentacao", include_in_schema=False)
def pagina_apresentacao():
    return HTMLResponse(_pagina_publica("apresentacao.html"))


@app.get("/privacidade", include_in_schema=False)
def pagina_privacidade():
    return HTMLResponse(_pagina_publica("privacidade.html"))


@app.get("/termos", include_in_schema=False)
def pagina_termos():
    return HTMLResponse(_pagina_publica("termos.html"))


@app.get("/faturas/{id_fatura}", include_in_schema=False)
def pagina_fatura(request: Request, utilizador: Utilizador | None = Depends(utilizador_opcional)):
    # A página é sempre a mesma; o JavaScript lê o id do endereço e pede os dados à API
    return _protegida(request, utilizador, "fatura.html")


@app.get("/dashboard", include_in_schema=False)
def pagina_dashboard(request: Request, utilizador: Utilizador | None = Depends(utilizador_opcional)):
    return _protegida(request, utilizador, "dashboard.html")


@app.get("/contabilidade", include_in_schema=False)
def pagina_contabilidade(request: Request, utilizador: Utilizador | None = Depends(utilizador_opcional)):
    return _protegida(request, utilizador, "contabilidade.html")


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
