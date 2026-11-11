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
UPLOAD_DIR = BASE_DIR / "uploads"

load_dotenv(BASE_DIR / ".env")

TAMANHO_MAXIMO = 10 * 1024 * 1024  # 10 MB

# Base de dados: um ficheiro SQLite na raiz do projeto (ignorado pelo Git).
# Trocar por PostgreSQL no futuro é só mudar esta variável.
URL_BASE_DADOS = os.getenv("URL_BASE_DADOS", f"sqlite:///{BASE_DIR / 'dados.db'}")

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
