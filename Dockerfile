# Imagem do Leitor de Faturas
# Construir:  docker build -t leitor-faturas .
# Correr:     docker run -p 8000:8000 -e MODO_DEMO=1 leitor-faturas

FROM python:3.11-slim

# Sem ficheiros .pyc e com os registos a aparecer logo (útil nos serviços de alojamento)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependências primeiro: o Docker reaproveita esta camada enquanto o requirements.txt não mudar
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY static ./static

# Não correr como root: se alguém explorar uma falha, fica com menos permissões
RUN useradd --create-home leitor \
    && mkdir -p uploads \
    && chown -R leitor:leitor /app
USER leitor

EXPOSE 8000

# Os serviços de alojamento (Render, Railway...) indicam a porta na variável PORT.
# --proxy-headers: o HTTPS termina no proxy do alojamento; assim a aplicação sabe que o
# pedido original veio por HTTPS e marca o cookie de sessão como "Secure".
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
