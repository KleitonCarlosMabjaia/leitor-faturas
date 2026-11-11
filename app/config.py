"""
Configuração central da aplicação.

Lê as variáveis do ficheiro .env (se existir) e expõe caminhos e definições
num único sítio. Nenhuma chave fica escrita no código.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Pastas do projeto (calculadas a partir deste ficheiro, funcionam em qualquer PC)
BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"

load_dotenv(BASE_DIR / ".env")

# Pasta dos PDFs quando não há bucket S3 (os testes da interface usam uma pasta temporária)
UPLOAD_DIR = Path(os.getenv("PASTA_UPLOADS") or BASE_DIR / "uploads")

TAMANHO_MAXIMO = 10 * 1024 * 1024  # 10 MB

def normalizar_url(url: str) -> str:
    """
    Os alojamentos (Render, Neon...) dão o endereço como "postgres://..." ou
    "postgresql://...". O SQLAlchemy precisa de saber o driver: psycopg (versão 3).
    """
    for prefixo in ("postgres://", "postgresql://"):
        if url.startswith(prefixo):
            return "postgresql+psycopg://" + url[len(prefixo):]
    return url


# Base de dados: em desenvolvimento, um ficheiro SQLite na raiz do projeto (ignorado
# pelo Git); em produção, PostgreSQL. DATABASE_URL é o nome que os alojamentos usam.
URL_BASE_DADOS = normalizar_url(
    os.getenv("URL_BASE_DADOS") or os.getenv("DATABASE_URL") or f"sqlite:///{BASE_DIR / 'dados.db'}"
)

# Armazenamento dos PDFs: pasta uploads/ em desenvolvimento; em produção, um bucket
# compatível com S3 (Cloudflare R2, Amazon S3, MinIO). Basta definir S3_BUCKET.
S3_BUCKET = os.getenv("S3_BUCKET", "")
S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL") or None  # no R2: https://<conta>.r2.cloudflarestorage.com
S3_REGIAO = os.getenv("S3_REGIAO", "auto")               # o R2 usa "auto"
# As chaves do bucket são lidas pelo boto3 de S3_ACCESS_KEY_ID / S3_SECRET_ACCESS_KEY (ver armazenamento.py)

# Cópias de segurança da base de dados: quantas se guardam (as mais antigas são apagadas)
COPIAS_A_MANTER = int(os.getenv("COPIAS_A_MANTER", "30"))

# Limite de pedidos (por endereço IP no login; por utilizador no envio de faturas)
LIMITE_LOGIN = (20, 10 * 60)      # 20 tentativas em 10 minutos
LIMITE_ENVIOS = (120, 60 * 60)    # 120 faturas por hora

# Registo de erros no Sentry (opcional): sem SENTRY_DSN, fica desligado
SENTRY_DSN = os.getenv("SENTRY_DSN", "")
AMBIENTE = os.getenv("AMBIENTE", "desenvolvimento")

# A API da Anthropic lê ANTHROPIC_API_KEY diretamente do ambiente.
# Aqui só confirmamos se existe, para dar uma mensagem clara quando falta.
CHAVE_API_DEFINIDA = bool(os.getenv("ANTHROPIC_API_KEY"))

# O modelo fica configurável: mudar de modelo não obriga a alterar código.
MODELO_IA = os.getenv("MODELO_IA", "claude-opus-5-5")

# Endereço público da aplicação: usado para montar os links de convite
URL_PUBLICO = os.getenv("URL_PUBLICO", "http://127.0.0.1:8000").rstrip("/")

# Contas e sessões
DURACAO_SESSAO_DIAS = int(os.getenv("DURACAO_SESSAO_DIAS", "7"))
VALIDADE_CONVITE_HORAS = 72
MAX_TENTATIVAS_LOGIN = 5
BLOQUEIO_MINUTOS = 15
MIN_PALAVRA_PASSE = 10

# Modo de demonstração (para publicar o portefólio):
# - cria as faturas de exemplo no arranque, se a base de dados estiver vazia;
# - desliga a IA, para que visitantes não gastem créditos (o QR continua a funcionar).
MODO_DEMO = os.getenv("MODO_DEMO", "").lower() in ("1", "true", "sim")
if MODO_DEMO:
    CHAVE_API_DEFINIDA = False

# Esforço de raciocínio do modelo. Extrair campos de uma fatura é uma tarefa
# simples, por isso "low" chega e torna cada leitura mais rápida e barata.
ESFORCO_IA = os.getenv("ESFORCO_IA", "low")
