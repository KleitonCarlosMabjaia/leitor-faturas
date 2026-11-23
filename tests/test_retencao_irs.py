"""Retenção de IRS pelas tabelas de 2026 (Despacho 233-A/2026) e o ajustamento de novembro (12523-A/2026)."""

import pytest

from app.servicos.contabilidade import retencao_irs as r
from app.servicos.contabilidade.retencao_irs import CASADO_DOIS, CASADO_UNICO, NAO_CASADO, Perfil
from tests.test_salarios import TRABALHADORA

# Coluna "Taxa efetiva mensal de retenção no limite do escalão" do despacho, linha a linha
TAXA_EFETIVA_PUBLICADA = {
    "I": [0, 5.3, 7.2, 7.5, 8.1, 13.5, 16.0, 18.8, 23.6, 30.1, 40.9],
    "II": [0, 5.3, 7.2, 7.5, 8.1, 13.5, 16.0, 18.8, 23.6, 30.1, 40.9],
    "III": [0, 2.2, 3.8, 3.9, 5.8, 8.5, 9.8, 12.3, 14.8, 20.8, 33.2],
    "IV": [0, 3.8, 8.5, 20.2, 21.2, 26.6, 38.9],
    "V": [0, 1.3, 9.6, 18.8, 25.5, 25.9, 38.4],
    "VI": [0, 4.0, 7.4, 12.1, 19.2, 25.7, 26.1, 38.5],
    "VII": [0, 7.6, 8.9, 17.6, 34.3],
}


def test_tabelas_transcritas_batem_com_a_taxa_efetiva_publicada():
    for numero, tabela in r.TABELAS_2026.items():
        for (limite, taxa, parcela), publicada in zip(tabela.linhas, TAXA_EFETIVA_PUBLICADA[numero], strict=False):
            if isinstance(parcela, tuple):
                parcela = taxa / 100 * parcela[0] * (parcela[1] - limite)
            assert round((limite * taxa / 100 - parcela) / limite * 100, 1) == pytest.approx(publicada, abs=0.051), \
                (numero, limite)


@pytest.mark.parametrize(("perfil", "esperada"), [
    (Perfil(NAO_CASADO), "I"), (Perfil(CASADO_DOIS, 2), "I"), (Perfil(NAO_CASADO, 1), "II"),
    (Perfil(CASADO_UNICO, 0), "III"), (Perfil(NAO_CASADO, deficiencia=True), "IV"),
    (Perfil(CASADO_DOIS, 0, deficiencia=True), "IV"), (Perfil(NAO_CASADO, 1, deficiencia=True), "V"),
    (Perfil(CASADO_DOIS, 1, deficiencia=True), "VI"), (Perfil(CASADO_UNICO, 3, deficiencia=True), "VII"),
])
def test_escolha_da_tabela(perfil, esperada):
    assert r.tabela_aplicavel(perfil, 2026).numero == esperada


def test_valores_de_retencao():
    def valor(remuneracao, perfil):
        return r.calcular(remuneracao, perfil, 2026)["valor"]

    assert valor(920, Perfil(NAO_CASADO)) == 0                       # salário mínimo de 2026: sem retenção
    assert valor(1000, Perfil(NAO_CASADO)) == 36.00                   # 125 - 12,5% x 2,6 x (1273,85 - 1000)
    assert valor(2000, Perfil(NAO_CASADO)) == 301.34                  # 2000 x 31,1% - 320,66
    assert valor(2000, Perfil(CASADO_DOIS, 1)) == 279.91              # - 21,43 por dependente
    assert valor(2000, Perfil(NAO_CASADO, 3)) == 178.47               # 3 dependentes: taxa 30,1% e 3 x 34,29
    assert valor(1500, Perfil(CASADO_UNICO)) == 94.18                 # tabela III
    assert valor(1500, Perfil(CASADO_UNICO, conjuge_deficiencia=True)) == 0
    assert valor(2000, Perfil(NAO_CASADO, deficiencia=True)) == 64.87  # tabela IV
    # Dependente com incapacidade >= 60%: + 84,82 (não casado) ou + 42,41 (casado, dois titulares)
    assert valor(2000, Perfil(NAO_CASADO, 1, dependentes_deficiencia=1)) == round(301.34 - 34.29 - 84.82, 2)
    assert valor(2000, Perfil(CASADO_DOIS, 1, dependentes_deficiencia=1)) == round(301.34 - 21.43 - 42.41, 2)
    assert valor(25000, Perfil(NAO_CASADO)) == round(25000 * 0.4717 - 1272.31, 2)


def test_subsidios_duodecimos_suplementar_e_novembro():
    perfil = Perfil(NAO_CASADO)
    # Subsídio de Natal pago por inteiro: retenção autónoma, a tabela aplica-se ao subsídio
    junho = r.retencao_mes(perfil, 2026, 6, mensal=2000, subsidio_ferias=2000)
    assert [p["valor"] for p in junho["partes"]] == [301.34, 301.34] and junho["valor"] == 602.68
    # Duodécimos: cada duodécimo à taxa do subsídio inteiro (15,07% para 2 000 €)
    duo = r.retencao_mes(perfil, 2026, 3, mensal=2000, subsidio_ferias=166.67, subsidio_natal=166.67,
                         duodecimos=True, base_subsidio=2000)
    assert [p["taxa"] for p in duo["partes"]] == [15.07, 15.07, 15.07]
    assert duo["partes"][1]["valor"] == 25.11                     # 301,34 x 166,67 / 2000
    # Trabalho suplementar: metade da taxa efetiva do mês
    sup = r.retencao_mes(perfil, 2026, 3, mensal=2000, suplementar=200)
    assert sup["partes"][1]["taxa"] == 7.53 and sup["partes"][1]["valor"] == 15.07
    # Novembro de 2026: metade, também no subsídio de Natal; não em dezembro
    nov = r.retencao_mes(perfil, 2026, 11, mensal=2000, subsidio_natal=2000)
    assert nov["valor"] == 301.34 and "12523-A/2026" in nov["nota"]
    assert r.retencao_mes(perfil, 2026, 12, mensal=2000)["valor"] == 301.34
    # Acima do penúltimo escalão (20 221 €) não há redução
    alto = r.retencao_mes(perfil, 2026, 11, mensal=21000)
    assert alto["valor"] == r.calcular(21000, perfil, 2026)["valor"] and "Sem a redução" in alto["nota"]


def test_processamento_pelas_tabelas(cliente):
    ficha = {**TRABALHADORA, "salario_base": 1150, "taxa_irs": 0, "irs_situacao": "nao_casado",
             "irs_dependentes": 1}
    f = cliente.post("/api/contabilidade/salarios/funcionarios", json=ficha)
    assert f.status_code == 201, f.text
    assert f.json()["irs_tabela"] == "II"
    # Março: 1 150 € x 15,7% - 94,71 - 34,29 = 51,55 €
    marco = cliente.get("/api/contabilidade/salarios/2026/3").json()["recibos"][0]
    assert marco["irs"] == 51.55 and marco["irs_detalhe"]["tabela"] == "II"
    # Novembro: salário e subsídio de Natal, cada um com 51,55 €, a metade
    r_nov = cliente.post("/api/contabilidade/salarios/processar", json={"ano": 2026, "mes": 11})
    assert r_nov.status_code == 201, r_nov.text
    nov = r_nov.json()["recibos"][0]
    assert nov["irs"] == round(25.78 * 2, 2) and nov["irs_detalhe"]["nota"]
    # Horas extra: sujeitas a SS; IRS a metade da taxa do mês
    r_mar = cliente.post("/api/contabilidade/salarios/processar", json={"ano": 2026, "mes": 3, "variaveis": [
        {"funcionario_id": f.json()["id"], "suplementar": 100}]})
    rec = r_mar.json()["recibos"][0]
    assert rec["suplementar"] == 100 and rec["remuneracao_sujeita"] == 1250
    assert rec["irs"] == 53.79                                 # 51,55 + 100 x (51,55 / 1150) / 2

    # Validações
    erro = cliente.put(f"/api/contabilidade/salarios/funcionarios/{f.json()['id']}",
                       json={**ficha, "irs_dependentes": 1, "irs_dependentes_deficiencia": 2})
    assert erro.status_code == 422
