"""Declaração de remunerações da Segurança Social (ficheiro RC4008) e mapa da DMR da AT."""

from app.modelos.tabelas import Empresa
from tests.conftest import criar_empresa_com_admin, entrar

ANO, MES = 2026, 6   # junho: com subsídio de férias


def _preparar(cliente, fabrica_sessoes, empresa_id: int, com_dados: bool = True) -> None:
    with fabrica_sessoes() as db:
        e = db.get(Empresa, empresa_id)
        e.nif = "507842545"
        e.niss = "20012345678" if com_dados else None
        db.commit()
    pessoas = [
        {"nome": "Ana Simões", "niss": "12345678901", "data_nascimento": "1990-02-01", "salario_base": 1000,
         "subsidio_alimentacao": 12, "alimentacao_cartao": False, "taxa_irs": 10, "data_admissao": "2026-01-01"},
        {"nome": "Rui Gerente", "tipo": "orgaos_sociais", "niss": "12345678902", "data_nascimento": "1980-05-05",
         "salario_base": 2000, "taxa_irs": 15, "data_admissao": "2026-06-16"},
    ]
    for p in pessoas:
        if not com_dados:
            p.pop("data_nascimento")
        r = cliente.post("/api/contabilidade/salarios/funcionarios", json=p)
        assert r.status_code == 201, r.text
    r = cliente.post("/api/contabilidade/salarios/processar", json={"ano": ANO, "mes": MES, "variaveis": []})
    assert r.status_code == 201, r.text


def test_ficheiro_seguranca_social(cliente, fabrica_sessoes, empresa_teste):
    _preparar(cliente, fabrica_sessoes, empresa_teste)
    r = cliente.get(f"/api/contabilidade/salarios/{ANO}/{MES}/seguranca-social")
    assert r.status_code == 200, r.text
    assert 'filename="DRSS202606.txt"' in r.headers["content-disposition"]
    texto = r.content.decode("ascii")
    assert texto.endswith("\r\n")
    linhas = texto.split("\r\n")[:-1]
    assert all(len(ln) == 118 for ln in linhas)
    assert linhas[0].startswith("R0RC4008  01")

    # Dois blocos (taxas 34,75% e 31,30%), cada um com R1 ... R3
    tipos = [ln[:2] for ln in linhas]
    assert tipos.count("R1") == 2 and tipos.count("R3") == 2
    r1 = linhas[1]
    assert r1[2:13] == "20012345678" and r1[13:17] == "0000" and r1[17:26] == "507842545"
    assert r1[26:92].rstrip() == "EMPRESA DE TESTE" and r1[92:98] == "202606"

    ana = [ln for ln in linhas if ln[:2] == "R2" and ln[17:28] == "12345678901"]
    assert {ln[28:88].rstrip() for ln in ana} == {"ANA SIMOES"}        # sem acentos, em maiúsculas
    por_natureza = {ln[106:108].strip(): ln for ln in ana}
    assert set(por_natureza) == {"P", "F", "R"}                         # base, férias, refeição acima do limite
    base = por_natureza["P"]
    assert base[88:96] == "19900201" and base[96:102] == "202606"
    assert base[102:105] == "300" and base[105] == "0"                  # 30,0 dias
    assert base[108:117] == "000100000" and base[117] == "0"            # 1000,00 € em cêntimos
    assert por_natureza["F"][102:105] == "000"
    # Refeição em numerário: 22 dias x (12 - 6,15) = 128,70 € sujeitos
    assert por_natureza["R"][108:117] == "000012870"

    # Admitido a 16 de junho: 15 dias
    rui = next(ln for ln in linhas if ln[:2] == "R2" and ln[17:28] == "12345678902" and ln[106:108] == " P")
    assert rui[102:105] == "150"

    # Blocos por ordem de taxa: primeiro 31,30% (órgãos sociais), depois 34,75% (pessoal)
    fins = [i for i, t in enumerate(tipos) if t == "R3"]
    assert [linhas[i][56:60] for i in fins] == ["3130", "3475"]
    inicio = 1
    for i, taxa in zip(fins, (0.313, 0.3475), strict=True):
        r3 = linhas[i]
        bloco = [ln for ln in linhas[inicio:i] if ln[:2] == "R2"]
        total = sum(int(ln[108:117]) for ln in bloco)
        assert int(r3[28:42]) == total and r3[17:28] == "9" * 11
        assert abs(int(r3[43:55]) - round(total * taxa)) <= 1   # cada recibo é arredondado ao cêntimo
        assert int(r3[60:66]) == len(bloco)
        inicio = i + 1
    assert any(ln[17:28] == "12345678901" for ln in linhas[fins[0]:fins[1]])


def test_faltam_dados(cliente, fabrica_sessoes, empresa_teste):
    _preparar(cliente, fabrica_sessoes, empresa_teste, com_dados=False)
    r = cliente.get(f"/api/contabilidade/salarios/{ANO}/{MES}/seguranca-social")
    assert r.status_code == 422
    assert "NISS da empresa" in r.json()["detail"] and "data de nascimento de Ana" in r.json()["detail"]
    resumo = cliente.get(f"/api/contabilidade/salarios/{ANO}/{MES}/declaracoes").json()
    assert resumo["seguranca_social"]["pronto"] is False
    assert len(resumo["dmr"]["linhas"]) == 2


def test_mapa_dmr(cliente, fabrica_sessoes, empresa_teste):
    _preparar(cliente, fabrica_sessoes, empresa_teste)
    d = cliente.get(f"/api/contabilidade/salarios/{ANO}/{MES}/declaracoes").json()
    assert d["seguranca_social"]["pronto"] is True
    ana = next(ln for ln in d["dmr"]["linhas"] if ln["nome"] == "Ana Simões")
    assert ana["codigo"] == "A" and ana["rendimento"] == 2128.7 and ana["retencao_irs"] == 212.87
    assert ana["contribuicoes_obrigatorias"] == 234.16
    r = cliente.get(f"/api/contabilidade/salarios/{ANO}/{MES}/dmr.csv")
    assert r.status_code == 200 and "2128,70" in r.content.decode("utf-8-sig")


def test_mes_por_processar_e_outra_empresa(cliente, novo_cliente, fabrica_sessoes, empresa_teste):
    assert cliente.get("/api/contabilidade/salarios/2026/1/seguranca-social").status_code == 422
    _preparar(cliente, fabrica_sessoes, empresa_teste)
    criar_empresa_com_admin(fabrica_sessoes, "Outra", "outra@teste.pt")
    outra = entrar(novo_cliente(), "outra@teste.pt")
    assert outra.get(f"/api/contabilidade/salarios/{ANO}/{MES}/seguranca-social").status_code == 422


def test_niss_da_empresa_na_configuracao(cliente):
    r = cliente.put("/api/contabilidade/configuracao", json={"regime_iva": "trimestral", "niss": "123"})
    assert r.status_code == 422
    r = cliente.put("/api/contabilidade/configuracao", json={"regime_iva": "trimestral", "niss": "20012345678"})
    assert r.status_code == 200 and r.json()["niss"] == "20012345678"
