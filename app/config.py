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

# Versão da aplicação (aparece na documentação da API e no SAF-T)
VERSAO_APLICACAO = "2.1.0"

# SAF-T (PT): NIF e nome de quem produz o programa (ProductCompanyTaxID e ProductID).
# Sem valores, usam-se os da própria empresa (programa de desenvolvimento próprio).
SAFT_NIF_PRODUTOR = os.getenv("SAFT_NIF_PRODUTOR", "")
SAFT_NOME_PRODUTOR = os.getenv("SAFT_NOME_PRODUTOR", "")

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

# Páginas públicas (apresentação, privacidade e termos): quem presta o serviço e como contactar.
# Sem EMAIL_COMERCIAL, os botões "Pedir uma proposta" levam à página de entrada.
EMPRESA_RESPONSAVEL = os.getenv("EMPRESA_RESPONSAVEL", "").strip()
EMAIL_COMERCIAL = os.getenv("EMAIL_COMERCIAL", "").strip()
URL_DEMO = os.getenv("URL_DEMO", "https://leitor-faturas-vwgk.onrender.com").strip()

# Emails transacionais pelo Resend (https://resend.com). Sem estes dois valores, não se envia nada
# e os convites continuam a ser links copiados pelo administrador.
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "").strip()
EMAIL_REMETENTE = os.getenv("EMAIL_REMETENTE", "").strip()   # ex.: Dobra <nao-responder@o-seu-dominio.pt>
VALIDADE_REPOSICAO_MINUTOS = 60       # link de "Esqueci-me da palavra-passe"
DIAS_ALERTA_PRAZOS = (7, 1)           # alertas por email a 7 dias e na véspera do prazo

# Subscrições e pagamentos (Stripe). Sem STRIPE_SECRET_KEY não há cobranças nem bloqueios.
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "").strip()
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()
STRIPE_PRECO_ESSENCIAL = os.getenv("STRIPE_PRECO_ESSENCIAL", "").strip()          # price_... de 19 €/mês
STRIPE_PRECO_CONTABILIDADE = os.getenv("STRIPE_PRECO_CONTABILIDADE", "").strip()  # price_... de 49 €/mês
STRIPE_IVA_AUTOMATICO = os.getenv("STRIPE_IVA_AUTOMATICO", "").lower() in ("1", "true", "sim")  # Stripe Tax
DIAS_EXPERIMENTAIS = int(os.getenv("DIAS_EXPERIMENTAIS", "30"))
DIAS_TOLERANCIA_PAGAMENTO = 7        # dias com pagamento em atraso antes do bloqueio suave
# Registo online de empresas novas (página /registar). Desligue com REGISTO_ABERTO=0 para só convites.
REGISTO_ABERTO = os.getenv("REGISTO_ABERTO", "1").lower() in ("1", "true", "sim")

# Open banking (PSD2): ligação direta às contas bancárias pela Enable Banking (https://enablebanking.com).
# ID da aplicação e chave privada RSA (texto PEM, com "\n" no lugar das mudanças de linha, ou caminho
# do ficheiro). O endereço de regresso a registar na Enable Banking é URL_PUBLICO + /open-banking/retorno.
ENABLE_BANKING_APP_ID = os.getenv("ENABLE_BANKING_APP_ID", "").strip()
ENABLE_BANKING_CHAVE_PRIVADA = os.getenv("ENABLE_BANKING_CHAVE_PRIVADA", "").strip()
# Banco simulado (sem dinheiro real nem ligação externa): ativo na demonstração ou com OPEN_BANKING_SIMULADO=1
OPEN_BANKING_SIMULADO = os.getenv("OPEN_BANKING_SIMULADO", "").lower() in ("1", "true", "sim")

# API pública (/api/v1): pedidos por chave e por minuto
LIMITE_API = (int(os.getenv("LIMITE_API_POR_MINUTO", "300")), 60)
