"""Proteções do navegador, páginas públicas e contadores da barra lateral."""

from app import main


def test_paginas_html_tem_politica_de_seguranca(cliente_anonimo, empresa_teste):
    resposta = cliente_anonimo.get("/entrar")
    csp = resposta.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "object-src 'none'" in csp and "frame-ancestors 'self'" in csp
    assert resposta.headers["x-content-type-options"] == "nosniff"
    assert "camera=()" in resposta.headers["permissions-policy"]
    assert resposta.headers["cross-origin-opener-policy"] == "same-origin"
    # Sem HTTPS (testes e desenvolvimento) não se envia HSTS, que obrigaria o navegador a usar HTTPS
    assert "strict-transport-security" not in resposta.headers


def test_hsts_quando_o_pedido_chega_por_https(cliente_anonimo, empresa_teste):
    resposta = cliente_anonimo.get("https://testserver/entrar")
    assert resposta.headers["strict-transport-security"].startswith("max-age=31536000")


def test_pedido_de_outro_site_e_recusado(cliente, empresa_teste):
    for cabecalhos in ({"Origin": "https://site-malicioso.example"}, {"Sec-Fetch-Site": "cross-site"}):
        resposta = cliente.post("/api/auth/sair", headers=cabecalhos)
        assert resposta.status_code == 403, cabecalhos
    # Da própria aplicação continua a funcionar
    assert cliente.get("/api/auth/eu").status_code == 200
    assert cliente.post("/api/auth/sair", headers={"Origin": "http://testserver"}).status_code != 403


def test_ficheiros_com_versao_ficam_em_cache_e_sao_comprimidos(cliente_anonimo):
    resposta = cliente_anonimo.get("/static/contabilidade.js?v=1", headers={"Accept-Encoding": "gzip"})
    assert resposta.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert resposta.headers["content-encoding"] == "gzip"
    assert cliente_anonimo.get("/static/contabilidade.js").headers["cache-control"] == "no-cache"


def test_visitante_ve_a_apresentacao_e_paginas_legais(cliente_anonimo, empresa_teste, monkeypatch):
    resposta = cliente_anonimo.get("/", follow_redirects=False)
    assert resposta.status_code == 303 and resposta.headers["location"] == "/apresentacao"

    monkeypatch.setattr(main.config, "EMAIL_COMERCIAL", "vendas@exemplo.pt")
    monkeypatch.setattr(main.config, "EMPRESA_RESPONSAVEL", "Exemplo <Lda>")
    main._pagina_publica.cache_clear()
    try:
        site = cliente_anonimo.get("/apresentacao").text
        assert "mailto:vendas@exemplo.pt" in site and "{{" not in site
        privacidade = cliente_anonimo.get("/privacidade").text
        assert "Exemplo &lt;Lda&gt;" in privacidade  # valores da configuração escapados
        assert cliente_anonimo.get("/termos").status_code == 200
    finally:
        main._pagina_publica.cache_clear()


def test_contadores_da_barra_lateral(cliente, empresa_teste):
    assert cliente.get("/api/faturas/contadores").json() == {"por_rever": 0, "por_contabilizar": 0}
