"""App instalável (PWA): manifesto, service worker e faturas fotografadas com a câmara."""

import io
import json

from tests.conftest import QR_EXEMPLO, criar_pdf_com_qr


def _foto_da_fatura(formato="JPEG", rodada=False) -> bytes:
    """Uma "fotografia" de uma fatura com o QR da AT (a página do PDF de teste, como imagem)."""
    import pypdfium2

    pdf = pypdfium2.PdfDocument(criar_pdf_com_qr(QR_EXEMPLO))
    imagem = pdf[0].render(scale=1.4).to_pil().convert("RGB")
    if rodada:
        imagem = imagem.rotate(90, expand=True)
    saida = io.BytesIO()
    imagem.save(saida, formato)
    return saida.getvalue()


def test_manifesto_e_service_worker(cliente_anonimo):
    r = cliente_anonimo.get("/manifest.webmanifest")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/manifest+json")
    manifesto = json.loads(r.text)
    assert manifesto["display"] == "standalone" and manifesto["start_url"].startswith("/")
    assert any(i["purpose"] == "maskable" for i in manifesto["icons"])
    for icone in manifesto["icons"]:
        assert cliente_anonimo.get(icone["src"]).status_code == 200
    sw = cliente_anonimo.get("/sw.js")
    assert sw.status_code == 200 and sw.headers["service-worker-allowed"] == "/"
    assert sw.headers["cache-control"] == "no-cache"
    assert cliente_anonimo.get("/offline").status_code == 200


def test_fotografia_da_fatura_e_lida_como_um_pdf(cliente):
    r = cliente.post("/api/faturas", files={"ficheiro": ("IMG_2041.jpg", _foto_da_fatura(), "image/jpeg")})
    assert r.status_code == 201, r.text
    f = r.json()
    assert f["nome_original"] == "IMG_2041.pdf"
    assert f["nif_fornecedor"] == "500000000"   # o QR da AT foi lido da fotografia
    pdf = cliente.get(f"/api/faturas/{f['id']}/pdf")
    assert pdf.content.startswith(b"%PDF")


def test_fotografia_png_e_imagens_invalidas(cliente):
    r = cliente.post("/api/faturas", files={"ficheiro": ("foto.png", _foto_da_fatura("PNG"), "image/png")})
    assert r.status_code == 201
    r = cliente.post("/api/faturas", files={"ficheiro": ("foto.jpg", b"isto nao e uma imagem", "image/jpeg")})
    assert r.status_code == 400 and "imagem" in r.json()["detail"]
    r = cliente.post("/api/faturas", files={"ficheiro": ("foto.gif", b"GIF89a....", "image/gif")})
    assert r.status_code == 400 and "fotografias" in r.json()["detail"]
