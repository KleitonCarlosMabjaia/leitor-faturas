"""
Testes da interface: arrancam a aplicação a sério (uvicorn, modo de demonstração,
base de dados e pasta temporárias) e comandam um Chromium com o Playwright.

    playwright install chromium   # só da primeira vez
    pytest tests_ui
"""

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent


def _porta_livre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def base_url(tmp_path_factory):
    """Endereço do servidor de teste (o pytest-playwright usa-o em page.goto("/..."))."""
    pasta = tmp_path_factory.mktemp("servidor")
    porta = _porta_livre()
    ambiente = {
        **os.environ,
        "MODO_DEMO": "1",  # entra sem login, com 30 faturas fictícias e a IA desligada
        "URL_BASE_DADOS": f"sqlite:///{(pasta / 'demo.db').as_posix()}",
        "PASTA_UPLOADS": str(pasta / "uploads"),
        "S3_BUCKET": "",  # nunca usar um bucket a sério nos testes
    }
    servidor = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(porta)],
        cwd=RAIZ, env=ambiente, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    url = f"http://127.0.0.1:{porta}"
    for _ in range(120):  # até 60 s (criar a demonstração leva alguns segundos)
        try:
            urllib.request.urlopen(f"{url}/api/saude", timeout=1)
            break
        except OSError:
            if servidor.poll() is not None:
                raise RuntimeError(f"O servidor não arrancou:\n{servidor.stderr.read().decode()}") from None
            time.sleep(0.5)
    else:
        servidor.kill()
        raise RuntimeError("O servidor de teste não respondeu a tempo.")
    yield url
    servidor.terminate()
    servidor.wait(timeout=10)


@pytest.fixture
def api(base_url):
    """Lê dados diretamente da API (para comparar com o que a página mostra)."""

    def obter(caminho: str):
        with urllib.request.urlopen(f"{base_url}{caminho}") as resposta:
            return json.load(resposta)

    return obter


@pytest.fixture
def erros_js(page):
    """Junta os erros de JavaScript da página; o teste falha se houver algum."""
    erros: list[str] = []
    page.on("pageerror", lambda erro: erros.append(str(erro)))
    page.on("console", lambda msg: erros.append(msg.text) if msg.type == "error" else None)
    return erros
