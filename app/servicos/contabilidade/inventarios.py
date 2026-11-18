"""
Inventários pelo sistema de inventário periódico (o das pequenas empresas).

Durante o período as compras ficam na conta 31. Na data de cada contagem física:
    CMVMC = inventário inicial + compras líquidas - inventário final
e o apuramento lança, por categoria:
    1. compras para inventários:   D 321 / C 311   (devoluções e descontos 317x/318x ao contrário)
    2. custo das vendas/consumos:  D 611 / C 321
deixando a conta 321 (ou 331) com o valor do inventário final e a 31 a zero.

Categorias: M mercadorias (311 -> 321 -> 611) e P matérias-primas, subsidiárias e de
consumo (312 -> 331 -> 612).

O valor de cada artigo é quantidade x custo unitário (custo de aquisição), indicado na contagem.

As funções não fazem commit: quem as chama decide quando gravar.
"""

import re
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.modelos.tabelas import (
    CATEGORIA_MATERIAS,
    CATEGORIA_MERCADORIAS,
    LANCAMENTO_ATIVO,
    Empresa,
    Inventario,
    Lancamento,
    LinhaInventario,
)
from app.servicos.contabilidade import extratos, lancamentos, relatorios
from app.servicos.contabilidade.lancamentos import LinhaNova

# categoria -> (conta de inventário, contas de compras (raiz), devoluções, descontos, CMVMC, nome)
CATEGORIAS = {
    CATEGORIA_MERCADORIAS: ("321", "311", "3171", "3181", "611", "Mercadorias"),
    CATEGORIA_MATERIAS: ("331", "312", "3172", "3182", "612", "Matérias-primas, subsidiárias e de consumo"),
}


class ErroInventario(Exception):
    """Dados ou ficheiro de inventário inválidos (mensagem pronta a mostrar)."""


@dataclass
class LinhaNovaInventario:
    codigo: str
    descricao: str
    quantidade: float
    custo_unitario: float
    categoria: str = CATEGORIA_MERCADORIAS
    unidade: str = "UN"


def obter(db: Session, empresa_id: int, inventario_id: int) -> Inventario | None:
    return db.scalar(select(Inventario).options(selectinload(Inventario.linhas)).where(
        Inventario.id == inventario_id, Inventario.empresa_id == empresa_id))


def lancamento_ativo(db: Session, inventario: Inventario) -> Lancamento | None:
    if not inventario.lancamento_id:
        return None
    la = db.get(Lancamento, inventario.lancamento_id)
    return la if la and la.estado == LANCAMENTO_ATIVO else None


def _preparar(linhas: list[LinhaNovaInventario]) -> list[LinhaInventario]:
    if not linhas:
        raise ErroInventario("O inventário precisa de pelo menos um artigo.")
    resultado = []
    for ln in linhas:
        if ln.categoria not in CATEGORIAS:
            raise ErroInventario(f"Categoria desconhecida no artigo {ln.codigo}: use M (mercadorias) ou P (matérias).")
        if ln.quantidade < 0 or ln.custo_unitario < 0:
            raise ErroInventario(f"O artigo {ln.codigo} tem quantidade ou custo negativos.")
        resultado.append(LinhaInventario(
            codigo=(ln.codigo or "").strip()[:60] or "-", descricao=(ln.descricao or "").strip()[:200] or "Artigo",
            categoria=ln.categoria, unidade=(ln.unidade or "UN").strip()[:10] or "UN",
            quantidade=round(ln.quantidade, 3),
            custo_unitario=round(ln.custo_unitario, 4), valor=round(ln.quantidade * ln.custo_unitario, 2),
        ))
    return resultado


def criar(db: Session, empresa: Empresa, data: date, descricao: str, linhas: list[LinhaNovaInventario],
          utilizador_id: int | None = None) -> Inventario:
    if db.scalar(select(Inventario.id).where(Inventario.empresa_id == empresa.id, Inventario.data == data)):
        raise ErroInventario(f"Já existe um inventário de {data:%d/%m/%Y}: altere-o em vez de criar outro.")
    inventario = Inventario(empresa_id=empresa.id, data=data, descricao=(descricao or "Inventário").strip()[:200],
                            inicial=False, criado_por_id=utilizador_id, linhas=_preparar(linhas))
    db.add(inventario)
    db.flush()
    return inventario


def substituir_linhas(db: Session, inventario: Inventario, linhas: list[LinhaNovaInventario]) -> None:
    if lancamento_ativo(db, inventario):
        raise ErroInventario("O inventário já foi lançado: estorne o lançamento antes de o alterar.")
    inventario.linhas = _preparar(linhas)


def totais(inventario: Inventario) -> dict[str, float]:
    return {c: round(sum(ln.valor for ln in inventario.linhas if ln.categoria == c), 2) for c in CATEGORIAS}


# ---------- Importação de contagens (CSV ou Excel) ----------

PAPEIS = [
    ("codigo", ("codigo", "referencia", "ref", "artigo", "sku", "cod")),
    ("descricao", ("descricao", "designacao", "nome", "produto")),
    ("categoria", ("categoria", "tipo", "familia", "classe")),
    ("unidade", ("unidade", "un", "und", "uni")),
    ("quantidade", ("quantidade", "qtd", "qt", "existencia", "stock", "quant")),
    ("custo", ("custo unitario", "preco unitario", "custo", "preco", "pmp", "valor unitario")),
    ("valor", ("valor total", "total", "valor")),
]


def _mapear(cabecalho: list[str]) -> dict[str, int] | None:
    nomes = [extratos.normalizar(c) for c in cabecalho]
    mapa: dict[str, int] = {}
    for papel, palavras in PAPEIS:
        for palavra in palavras:
            i = next((i for i, n in enumerate(nomes) if i not in mapa.values() and n and
                      (n == palavra or n.startswith(palavra + " ") or (len(palavra) > 4 and palavra in n))), None)
            if i is not None:
                mapa[papel] = i
                break
    tem_valor = "custo" in mapa or "valor" in mapa
    return mapa if "descricao" in mapa and "quantidade" in mapa and tem_valor else None


def _categoria(texto: str) -> str:
    t = extratos.normalizar(texto)
    if t in ("p", "mp", "materia", "materias") or re.search(r"materia|consumo|subsidiar|ingrediente", t):
        return CATEGORIA_MATERIAS
    return CATEGORIA_MERCADORIAS


def ler_ficheiro(conteudo: bytes, nome_ficheiro: str, categoria_omissao: str = CATEGORIA_MERCADORIAS
                 ) -> list[LinhaNovaInventario]:
    """Lê a contagem de um CSV ou Excel com código, descrição, quantidade e custo unitário (ou valor total)."""
    if not conteudo:
        raise ErroInventario("O ficheiro está vazio.")
    if nome_ficheiro.lower().endswith((".xlsx", ".xlsm")) or conteudo[:2] == b"PK":
        try:
            linhas = extratos._linhas_xlsx(conteudo)
        except extratos.ErroExtrato as erro:
            raise ErroInventario(str(erro))
    else:
        linhas = extratos._linhas_csv(extratos._texto(conteudo))
    for indice, linha in enumerate(linhas[:30]):
        mapa = _mapear([str(c or "") for c in linha])
        if mapa:
            cabecalho = indice
            break
    else:
        raise ErroInventario("Não reconheci as colunas: o ficheiro precisa de descrição, quantidade e custo "
                             "unitário (ou valor total).")

    def celula(linha, papel):
        j = mapa.get(papel)
        return linha[j] if j is not None and j < len(linha) else None

    resultado = []
    for linha in linhas[cabecalho + 1:]:
        quantidade = extratos.ler_numero(celula(linha, "quantidade"))
        descricao = str(celula(linha, "descricao") or "").strip()
        if quantidade is None or not descricao:
            continue
        custo = extratos.ler_numero(celula(linha, "custo"))
        if custo is None:
            valor = extratos.ler_numero(celula(linha, "valor"))
            if valor is None:
                continue
            custo = valor / quantidade if quantidade else 0.0
        categoria = _categoria(str(celula(linha, "categoria"))) if "categoria" in mapa else categoria_omissao
        resultado.append(LinhaNovaInventario(
            codigo=str(celula(linha, "codigo") or "").strip() or descricao[:20], descricao=descricao,
            quantidade=quantidade, custo_unitario=abs(custo), categoria=categoria,
            unidade=str(celula(linha, "unidade") or "UN").strip() or "UN",
        ))
    if not resultado:
        raise ErroInventario("O ficheiro não tem artigos com quantidade e custo.")
    return resultado


# ---------- Inventário inicial e apuramento do CMVMC ----------

def registar_inicial(db: Session, empresa: Empresa, inventario: Inventario,
                     utilizador_id: int | None = None) -> Lancamento:
    """
    Usa a contagem como inventário de abertura (empresa que começa a usar a aplicação com
    existências): D 321/331 contra 561 Resultados transitados. Se a abertura do balanço for
    feita de outra forma, ajuste a contrapartida.
    """
    if lancamento_ativo(db, inventario):
        raise ErroInventario("Este inventário já foi lançado.")
    t = totais(inventario)
    linhas = [LinhaNova(CATEGORIAS[c][0], debito=v, descricao=f"Inventário inicial - {CATEGORIAS[c][5]}")
              for c, v in t.items() if v]
    if not linhas:
        raise ErroInventario("O inventário não tem valor.")
    linhas.append(LinhaNova("561", credito=round(sum(t.values()), 2), descricao="Contrapartida do inventário inicial"))
    lancamento = lancamentos.criar(
        db, empresa, "OD", inventario.data, f"Inventário inicial em {inventario.data:%d/%m/%Y}", linhas,
        documento=f"Inventário {inventario.data:%d/%m/%Y}", origem="inventario", utilizador_id=utilizador_id,
        notas=["Inventário de abertura: contrapartida em resultados transitados (561). Ajuste se a abertura do "
               "balanço tiver outra origem."],
    )
    inventario.inicial = True
    inventario.lancamento_id = lancamento.id
    return lancamento


def _anterior(db: Session, inventario: Inventario) -> Inventario | None:
    return db.scalar(select(Inventario).where(
        Inventario.empresa_id == inventario.empresa_id, Inventario.data < inventario.data,
    ).order_by(Inventario.data.desc()).limit(1))


def apuramento(db: Session, empresa_id: int, inventario: Inventario) -> dict:
    """Pré-visualização do CMVMC por categoria (nada é gravado)."""
    anterior = _anterior(db, inventario)
    inicio = anterior.data + timedelta(days=1) if anterior else None
    saldos_antes = relatorios.saldos_por_conta(db, empresa_id, ate=(inicio - timedelta(days=1)) if inicio else None) \
        if inicio else {}
    movimentos = relatorios.movimentos_por_conta(db, empresa_id, inicio, inventario.data, excluir_origem="inventario")
    finais = totais(inventario)
    categorias, avisos = [], []
    for c, (conta_inv, compras, devolucoes, descontos, conta_cmv, nome) in CATEGORIAS.items():
        inicial = round(saldos_antes.get(conta_inv, 0.0), 2) if inicio else 0.0
        bruto = sum(d - cr for cod, (d, cr) in movimentos.items() if cod.startswith(compras))
        devolvido = sum(cr - d for cod, (d, cr) in movimentos.items() if cod.startswith(devolucoes))
        descontado = sum(cr - d for cod, (d, cr) in movimentos.items() if cod.startswith(descontos))
        liquidas = round(bruto - devolvido - descontado, 2)
        final = finais[c]
        cmvmc = round(inicial + liquidas - final, 2)
        if not any((inicial, liquidas, final)):
            continue
        if cmvmc < 0:
            avisos.append(f"{nome}: o inventário final é maior do que o inicial mais as compras ({cmvmc:.2f} €). "
                          "Faltam faturas de compra ou há erros na contagem?")
        categorias.append({
            "categoria": c, "nome": nome, "conta_inventario": conta_inv, "conta_cmvmc": conta_cmv,
            "inicial": inicial, "compras": round(bruto, 2), "devolucoes_descontos": round(devolvido + descontado, 2),
            "compras_liquidas": liquidas, "final": final, "cmvmc": cmvmc,
        })
    if anterior and not lancamento_ativo(db, anterior):
        avisos.append(f"O inventário de {anterior.data:%d/%m/%Y} ainda não foi lançado: lance-o primeiro, para as "
                      "compras desse período passarem para inventários.")
    if not anterior:
        avisos.append("Não há inventário anterior: o inventário inicial conta como zero. Se a empresa já tinha "
                      "existências, registe primeiro a contagem de abertura como inventário inicial.")
    return {"de": inicio, "ate": inventario.data, "anterior_id": anterior.id if anterior else None,
            "categorias": categorias, "avisos": avisos,
            "total_cmvmc": round(sum(c["cmvmc"] for c in categorias), 2)}


def apurar(db: Session, empresa: Empresa, inventario: Inventario, utilizador_id: int | None = None) -> Lancamento:
    """Lança o apuramento do CMVMC na data do inventário (diário de operações diversas)."""
    if lancamento_ativo(db, inventario):
        raise ErroInventario("Este inventário já foi lançado.")
    anterior = _anterior(db, inventario)
    if anterior and not lancamento_ativo(db, anterior):
        raise ErroInventario(f"Lance primeiro o inventário de {anterior.data:%d/%m/%Y} (os inventários lançam-se "
                             "por ordem de data).")
    preview = apuramento(db, empresa.id, inventario)
    movimentos = relatorios.movimentos_por_conta(db, empresa.id, preview["de"], inventario.data,
                                                 excluir_origem="inventario")
    linhas: list[LinhaNova] = []
    for cat in preview["categorias"]:
        conta_inv, compras, devolucoes, descontos, conta_cmv, nome = CATEGORIAS[cat["categoria"]]
        # 1. Compras (e devoluções/descontos) passam para a conta de inventário
        for codigo, (d, c) in sorted(movimentos.items()):
            saldo = round(d - c, 2)
            if not saldo or not codigo.startswith((compras, devolucoes, descontos)):
                continue
            if saldo > 0:
                linhas += [LinhaNova(conta_inv, debito=saldo, descricao=f"Compras {codigo} para inventário"),
                           LinhaNova(codigo, credito=saldo, descricao="Transferência para inventário")]
            else:
                linhas += [LinhaNova(codigo, debito=-saldo, descricao="Transferência para inventário"),
                           LinhaNova(conta_inv, credito=-saldo, descricao=f"{codigo} para inventário")]
        # 2. O consumido passa para o CMVMC
        if cat["cmvmc"] > 0:
            linhas += [LinhaNova(conta_cmv, debito=cat["cmvmc"], descricao=f"CMVMC - {nome}"),
                       LinhaNova(conta_inv, credito=cat["cmvmc"], descricao=f"Saída para CMVMC - {nome}")]
        elif cat["cmvmc"] < 0:
            linhas += [LinhaNova(conta_inv, debito=-cat["cmvmc"], descricao=f"Regularização - {nome}"),
                       LinhaNova(conta_cmv, credito=-cat["cmvmc"], descricao=f"Regularização - {nome}")]
    if not linhas:
        raise ErroInventario("Não há compras nem diferenças de inventário para apurar.")
    notas = [f"CMVMC = inventário inicial + compras líquidas - inventário final ({c['nome']}: "
             f"{c['inicial']:.2f} + {c['compras_liquidas']:.2f} - {c['final']:.2f} = {c['cmvmc']:.2f} €)"
             for c in preview["categorias"]] + preview["avisos"]
    lancamento = lancamentos.criar(
        db, empresa, "OD", inventario.data, f"Apuramento do CMVMC - inventário de {inventario.data:%d/%m/%Y}", linhas,
        documento=f"Inventário {inventario.data:%d/%m/%Y}", origem="inventario", notas=notas,
        utilizador_id=utilizador_id,
    )
    inventario.lancamento_id = lancamento.id
    return lancamento


def resumo(db: Session, inventario: Inventario) -> dict:
    t = totais(inventario)
    la = lancamento_ativo(db, inventario)
    return {
        "id": inventario.id, "data": inventario.data, "descricao": inventario.descricao, "inicial": inventario.inicial,
        "artigos": len(inventario.linhas), "mercadorias": t[CATEGORIA_MERCADORIAS], "materias": t[CATEGORIA_MATERIAS],
        "total": round(sum(t.values()), 2),
        "lancamento": {"id": la.id, "referencia": f"{la.diario} {la.ano}/{la.numero}"} if la else None,
    }


# ---------- Comunicação do inventário à AT ----------
# Portaria n.º 2/2015: ficheiro CSV separado por ponto e vírgula, vírgula decimal, com o inventário
# a 31/12 (ou no fim do período de tributação), a comunicar até 31 de janeiro do ano seguinte.
COLUNAS_AT = ("ProductCategory", "ProductCode", "ProductDescription", "ProductNumberCode",
              "ClosingStockQuantity", "UnitOfMeasure")


def _campo_csv(texto: str, tamanho: int) -> str:
    return re.sub(r"[;\s]+", " ", texto or "").strip()[:tamanho]


def ficheiro_at(inventario: Inventario) -> bytes:
    """CSV da comunicação do inventário (categoria, código, descrição, código do produto, quantidade, unidade)."""
    linhas = [";".join(COLUNAS_AT)]
    for ln in inventario.linhas:
        quantidade = f"{ln.quantidade:.4f}".rstrip("0").rstrip(".").replace(".", ",") or "0"
        linhas.append(";".join((ln.categoria, _campo_csv(ln.codigo, 60), _campo_csv(ln.descricao, 200),
                                _campo_csv(ln.codigo, 60), quantidade, _campo_csv(ln.unidade or "UN", 20))))
    return ("\r\n".join(linhas) + "\r\n").encode("utf-8")


def nome_ficheiro_at(empresa: Empresa, inventario: Inventario) -> str:
    return f"inventario_{empresa.nif or empresa.id}_{inventario.data:%Y%m%d}.csv"
