"""Site público: indexação (robots, sitemap, noindex), endereços canónicos, partilha e dados estruturados."""

import json
import re
import xml.etree.ElementTree as ET

from app import config


def test_robots_e_sitemap(cliente_anonimo):
    r = cliente_anonimo.get("/robots.txt")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    assert "Allow: /apresentacao" in r.text and "Disallow: /" in r.text
    assert f"Sitemap: {config.URL_PUBLICO}/sitemap.xml" in r.text

    r = cliente_anonimo.get("/sitemap.xml")
    assert r.status_code == 200 and "xml" in r.headers["content-type"]
    raiz = ET.fromstring(r.content)
    enderecos = [e.text for e in raiz.iter("{http://www.sitemaps.org/schemas/sitemap/0.9}loc")]
    assert f"{config.URL_PUBLICO}/apresentacao" in enderecos and all(e.startswith("http") for e in enderecos)


def test_paginas_da_aplicacao_nao_sao_indexadas(cliente_anonimo):
    assert "noindex" in cliente_anonimo.get("/entrar").headers.get("x-robots-tag", "")
    for pagina in ("/apresentacao", "/privacidade", "/termos"):
        r = cliente_anonimo.get(pagina)
        assert r.status_code == 200 and "x-robots-tag" not in r.headers, pagina


def test_apresentacao_com_canonico_partilha_e_dados_estruturados(cliente_anonimo):
    html = cliente_anonimo.get("/apresentacao").text
    assert "{{" not in html                                   # todas as marcas foram substituídas
    assert f'<link rel="canonical" href="{config.URL_PUBLICO}/apresentacao">' in html
    assert f'content="{config.URL_PUBLICO}/static/marketing/inicio.jpg"' in html
    assert html.count("<picture>") == 3 and "inicio.webp" in html

    blocos = re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
    dados = json.loads(blocos[0].replace("&amp;", "&"))
    tipos = {d["@type"] for d in dados}
    assert tipos == {"SoftwareApplication", "FAQPage"}
    faq = next(d for d in dados if d["@type"] == "FAQPage")
    assert len(faq["mainEntity"]) == 6 and faq["mainEntity"][0]["acceptedAnswer"]["text"]
