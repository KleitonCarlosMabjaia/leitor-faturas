"""
Artigos e stock (inventário permanente de gestão), ligado às compras, às vendas e à contabilidade.

- Entradas: a partir das faturas de fornecedores já lidas (ou à mão). O custo de cada entrada
  atualiza o custo médio ponderado do artigo.
- Saídas: a partir das vendas registadas (SAF-T de faturação ou à mão), ao custo médio; o preço
  de venda fica no movimento para calcular a margem de cada artigo.
- Acertos: a contagem física corrige a quantidade (quebras, ofertas, erros).
- Inventário: o stock numa data gera a contagem da contabilidade (Inventários), de onde sai o
  apuramento do custo das mercadorias vendidas (CMVMC) no SNC.

Os serviços (tipo S) não têm stock. As funções não fazem commit.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modelos.tabelas import (
    ARTIGO_MATERIA,
    ARTIGO_MERCADORIA,
    ARTIGO_SERVICO,
    ESTADO_CONFIRMADA,
    MOVIMENTO_AJUSTE,
    MOVIMENTO_ENTRADA,
    MOVIMENTO_SAIDA,
    VENDA_NORMAL,
    Artigo,
    Empresa,
    Fatura,
    FaturaVenda,
    MovimentoStock,
)
from app.servicos.contabilidade import inventarios

TIPOS = {ARTIGO_MERCADORIA: "Mercadoria", ARTIGO_MATERIA: "Matéria-prima ou consumível", ARTIGO_SERVICO: "Serviço"}
TAXAS_IVA = (0.0, 4.0, 5.0, 6.0, 9.0, 12.0, 13.0, 16.0, 22.0, 23.0)
TOLERANCIA = 0.01


class ErroStock(Exception):
    """Mensagem pronta a mostrar."""


def _q(v: float) -> float:
    return round(v + 0.0, 3)


def _r(v: float) -> float:
    return round(v + 0.0, 2)


# ---------- Artigos ----------

def obter(db: Session, empresa_id: int, artigo_id: int) -> Artigo | None:
    return db.scalar(select(Artigo).where(Artigo.id == artigo_id, Artigo.empresa_id == empresa_id))


def listar(db: Session, empresa_id: int, incluir_inativos: bool = True) -> list[Artigo]:
    consulta = select(Artigo).where(Artigo.empresa_id == empresa_id).order_by(Artigo.codigo)
    if not incluir_inativos:
        consulta = consulta.where(Artigo.ativo.is_(True))
    return list(db.scalars(consulta))


def guardar(db: Session, empresa_id: int, artigo: Artigo | None, *, codigo: str, nome: str, tipo: str,
            unidade: str = "UN", preco_venda: float = 0.0, taxa_iva: float = 23.0, stock_minimo: float = 0.0,
            ativo: bool = True) -> Artigo:
    codigo = " ".join((codigo or "").split()).upper()[:60]
    nome = (nome or "").strip()[:200]
    if not codigo or not nome:
        raise ErroStock("Indique o código e o nome do artigo.")
    if tipo not in TIPOS:
        raise ErroStock("Tipo desconhecido: mercadoria, matéria-prima ou serviço.")
    if preco_venda < 0 or stock_minimo < 0:
        raise ErroStock("O preço e o stock mínimo não podem ser negativos.")
    if round(taxa_iva, 1) not in TAXAS_IVA:
        raise ErroStock("Taxa de IVA desconhecida.")
    repetido = db.scalar(select(Artigo.id).where(Artigo.empresa_id == empresa_id, Artigo.codigo == codigo))
    if repetido and (artigo is None or repetido != artigo.id):
        raise ErroStock(f"Já existe um artigo com o código {codigo}.")
    if artigo is None:
        artigo = Artigo(empresa_id=empresa_id, quantidade=0.0, custo_medio=0.0)
        db.add(artigo)
    elif artigo.tipo != tipo and tipo == ARTIGO_SERVICO and artigo.quantidade:
        raise ErroStock("Este artigo tem stock: não pode passar a serviço.")
    artigo.codigo, artigo.nome, artigo.tipo = codigo, nome, tipo
    artigo.unidade = (unidade or "UN").strip().upper()[:10] or "UN"
    artigo.preco_venda, artigo.taxa_iva = _r(preco_venda), round(taxa_iva, 1)
    artigo.stock_minimo, artigo.ativo = _q(stock_minimo), ativo
    db.flush()
    return artigo


def artigo_dict(a: Artigo) -> dict:
    valor = _r(a.quantidade * a.custo_medio)
    margem = _r(a.preco_venda - a.custo_medio) if a.preco_venda and a.custo_medio else None
    return {
        "id": a.id, "codigo": a.codigo, "nome": a.nome, "tipo": a.tipo, "tipo_nome": TIPOS.get(a.tipo, a.tipo),
        "unidade": a.unidade, "preco_venda": a.preco_venda, "taxa_iva": a.taxa_iva,
        "stock_minimo": a.stock_minimo, "quantidade": _q(a.quantidade), "custo_medio": round(a.custo_medio, 4),
        "valor": valor, "margem_unitaria": margem,
        "margem_percentual": round(margem / a.preco_venda * 100, 1) if margem is not None else None,
        "abaixo_minimo": a.tipo != ARTIGO_SERVICO and a.ativo and a.stock_minimo > 0 and a.quantidade < a.stock_minimo,
        "ativo": a.ativo,
    }


# ---------- Movimentos ----------

def movimentar(db: Session, empresa_id: int, artigo: Artigo, tipo: str, quantidade: float, data: date, *,
               custo_unitario: float | None = None, preco_unitario: float | None = None,
               documento: str | None = None, fatura_id: str | None = None, venda_id: int | None = None,
               utilizador_id: int | None = None) -> MovimentoStock:
    """
    Entrada ou saída de `quantidade` (sempre positiva) ou acerto para a quantidade contada.
    Atualiza a quantidade e o custo médio do artigo.
    """
    if artigo.tipo == ARTIGO_SERVICO:
        raise ErroStock(f"{artigo.codigo} é um serviço: não tem stock.")
    if not artigo.ativo:
        raise ErroStock(f"O artigo {artigo.codigo} está desativado.")
    if tipo == MOVIMENTO_AJUSTE:
        if quantidade < 0:
            raise ErroStock("A quantidade contada não pode ser negativa.")
        delta = _q(quantidade - artigo.quantidade)
        if not delta:
            raise ErroStock(f"A contagem de {artigo.codigo} é igual ao stock atual: não há nada a acertar.")
        custo = artigo.custo_medio if delta < 0 or custo_unitario is None else custo_unitario
    elif quantidade <= 0:
        raise ErroStock("A quantidade tem de ser maior do que zero.")
    elif tipo == MOVIMENTO_ENTRADA:
        if custo_unitario is None or custo_unitario < 0:
            raise ErroStock("Indique o custo unitário da entrada.")
        delta, custo = _q(quantidade), custo_unitario
    elif tipo == MOVIMENTO_SAIDA:
        if quantidade > artigo.quantidade + 1e-9:
            raise ErroStock(f"Stock insuficiente de {artigo.codigo}: há {_q(artigo.quantidade):g} {artigo.unidade}.")
        delta, custo = _q(-quantidade), artigo.custo_medio
    else:
        raise ErroStock("Movimento desconhecido.")

    if delta > 0:
        existente = max(artigo.quantidade, 0.0)
        total = existente + delta
        artigo.custo_medio = round((existente * artigo.custo_medio + delta * custo) / total, 4) if total else custo
    artigo.quantidade = _q(artigo.quantidade + delta)
    mov = MovimentoStock(
        empresa_id=empresa_id, artigo_id=artigo.id, data=data, tipo=tipo, quantidade=delta,
        custo_unitario=round(custo, 4), valor=_r(delta * custo), preco_unitario=preco_unitario,
        documento=(documento or "").strip()[:80] or None, fatura_id=fatura_id, venda_id=venda_id,
        criado_por_id=utilizador_id,
    )
    db.add(mov)
    db.flush()
    return mov


@dataclass
class LinhaMovimento:
    artigo_id: int
    quantidade: float
    valor_unitario: float | None = None   # custo nas entradas, preço sem IVA nas saídas


def _artigos(db: Session, empresa_id: int, linhas: list[LinhaMovimento]) -> list[Artigo]:
    if not linhas:
        raise ErroStock("Indique pelo menos um artigo.")
    artigos = []
    for ln in linhas:
        a = obter(db, empresa_id, ln.artigo_id)
        if a is None:
            raise ErroStock("Artigo não encontrado.")
        artigos.append(a)
    return artigos


def valor_entrado(db: Session, empresa_id: int, fatura_id: str) -> float:
    return _r(db.scalar(select(func.coalesce(func.sum(MovimentoStock.valor), 0.0)).where(
        MovimentoStock.empresa_id == empresa_id, MovimentoStock.fatura_id == fatura_id)) or 0.0)


def entrada_por_fatura(db: Session, empresa: Empresa, fatura: Fatura, linhas: list[LinhaMovimento],
                       utilizador_id: int | None = None) -> list[MovimentoStock]:
    """Dá entrada em stock dos artigos de uma fatura de fornecedor confirmada."""
    if fatura.estado != ESTADO_CONFIRMADA:
        raise ErroStock("Confirme primeiro os dados da fatura.")
    artigos = _artigos(db, empresa.id, linhas)
    novo = sum(ln.quantidade * (ln.valor_unitario or 0) for ln in linhas)
    disponivel = _r((fatura.valor_sem_iva or 0) - valor_entrado(db, empresa.id, fatura.id))
    if novo > disponivel + TOLERANCIA:
        euros = lambda v: f"{v:.2f} €".replace(".", ",")  # noqa: E731
        raise ErroStock(f"O valor das entradas ({euros(novo)}) é maior do que o valor da fatura sem IVA ainda "
                        f"por dar entrada ({euros(disponivel)}).")
    documento = f"{fatura.numero_fatura or fatura.id} · {fatura.nome_fornecedor or fatura.nif_fornecedor or ''}"
    return [movimentar(db, empresa.id, a, MOVIMENTO_ENTRADA, ln.quantidade, fatura.data_emissao or date.today(),
                       custo_unitario=ln.valor_unitario, documento=documento, fatura_id=fatura.id,
                       utilizador_id=utilizador_id)
            for a, ln in zip(artigos, linhas, strict=True)]


def saida_por_venda(db: Session, empresa: Empresa, venda: FaturaVenda, linhas: list[LinhaMovimento],
                    utilizador_id: int | None = None) -> list[MovimentoStock]:
    """Dá saída do stock dos artigos vendidos numa fatura emitida (ao custo médio)."""
    if venda.estado != VENDA_NORMAL or venda.tipo == "NC":
        raise ErroStock("Só se dá saída de stock em faturas válidas (não anuladas nem notas de crédito).")
    artigos = _artigos(db, empresa.id, linhas)
    ja = db.scalar(select(func.count(MovimentoStock.id)).where(MovimentoStock.venda_id == venda.id))
    if ja:
        raise ErroStock(f"Já foi dada saída do stock da fatura {venda.numero}.")
    return [movimentar(db, empresa.id, a, MOVIMENTO_SAIDA, ln.quantidade, venda.data,
                       preco_unitario=a.preco_venda if ln.valor_unitario is None else ln.valor_unitario,
                       documento=f"{venda.numero} · {venda.cliente_nome}", venda_id=venda.id,
                       utilizador_id=utilizador_id)
            for a, ln in zip(artigos, linhas, strict=True)]


def movimento_dict(m: MovimentoStock) -> dict:
    return {
        "id": m.id, "data": m.data, "tipo": m.tipo, "artigo_id": m.artigo_id, "artigo": m.artigo.codigo,
        "artigo_nome": m.artigo.nome, "unidade": m.artigo.unidade, "quantidade": m.quantidade,
        "custo_unitario": m.custo_unitario, "valor": m.valor, "preco_unitario": m.preco_unitario,
        "documento": m.documento, "fatura_id": m.fatura_id, "venda_id": m.venda_id,
    }


def movimentos(db: Session, empresa_id: int, artigo_id: int | None = None, limite: int = 200) -> list[MovimentoStock]:
    consulta = (select(MovimentoStock).where(MovimentoStock.empresa_id == empresa_id)
                .order_by(MovimentoStock.data.desc(), MovimentoStock.id.desc()).limit(limite))
    if artigo_id:
        consulta = consulta.where(MovimentoStock.artigo_id == artigo_id)
    return list(db.scalars(consulta))


# ---------- Ligação às compras e às vendas ----------

def faturas_para_entrada(db: Session, empresa_id: int, limite: int = 40) -> list[dict]:
    """Faturas de fornecedores confirmadas com valor ainda sem entrada em stock (as mais recentes)."""
    entradas = dict(db.execute(select(MovimentoStock.fatura_id, func.sum(MovimentoStock.valor)).where(
        MovimentoStock.empresa_id == empresa_id, MovimentoStock.fatura_id.is_not(None))
        .group_by(MovimentoStock.fatura_id)).all())
    resultado = []
    for f in db.scalars(select(Fatura).where(Fatura.empresa_id == empresa_id, Fatura.estado == ESTADO_CONFIRMADA)
                        .order_by(Fatura.data_emissao.desc().nulls_last()).limit(200)):
        falta = _r((f.valor_sem_iva or 0) - (entradas.get(f.id) or 0))
        if falta > TOLERANCIA:
            resultado.append({"id": f.id, "numero": f.numero_fatura,
                              "fornecedor": f.nome_fornecedor or f.nif_fornecedor,
                              "data": f.data_emissao, "valor_sem_iva": f.valor_sem_iva, "por_dar_entrada": falta})
        if len(resultado) >= limite:
            break
    return resultado


def vendas_para_saida(db: Session, empresa_id: int, limite: int = 40) -> list[dict]:
    com_saida = select(MovimentoStock.venda_id).where(MovimentoStock.empresa_id == empresa_id,
                                                      MovimentoStock.venda_id.is_not(None))
    return [{"id": v.id, "numero": v.numero, "cliente": v.cliente_nome, "data": v.data,
             "valor_sem_iva": v.valor_sem_iva}
            for v in db.scalars(select(FaturaVenda).where(
                FaturaVenda.empresa_id == empresa_id, FaturaVenda.estado == VENDA_NORMAL, FaturaVenda.tipo != "NC",
                FaturaVenda.id.not_in(com_saida)).order_by(FaturaVenda.data.desc()).limit(limite))]


# ---------- Stock numa data, inventário e margens ----------

def stock_em(db: Session, empresa_id: int, data: date) -> list[dict]:
    """Quantidade e valor de cada artigo com stock na data (soma dos movimentos até ao fim desse dia)."""
    linhas = db.execute(
        select(MovimentoStock.artigo_id, func.sum(MovimentoStock.quantidade), func.sum(MovimentoStock.valor))
        .where(MovimentoStock.empresa_id == empresa_id, MovimentoStock.data <= data)
        .group_by(MovimentoStock.artigo_id)).all()
    artigos = {a.id: a for a in listar(db, empresa_id)}
    resultado = []
    for artigo_id, qtd, valor in linhas:
        qtd, valor = _q(qtd or 0), _r(valor or 0)
        if qtd > 0 and artigo_id in artigos:
            a = artigos[artigo_id]
            resultado.append({"artigo": a, "quantidade": qtd, "valor": max(valor, 0.0),
                              "custo_unitario": round(max(valor, 0.0) / qtd, 4)})
    return sorted(resultado, key=lambda x: x["artigo"].codigo)


def criar_inventario(db: Session, empresa: Empresa, data: date, utilizador_id: int | None = None):
    """Contagem da contabilidade (Inventários) com o stock de gestão na data."""
    existencias = stock_em(db, empresa.id, data)
    if not existencias:
        raise ErroStock(f"Não há stock a {data:%d/%m/%Y}.")
    linhas = [inventarios.LinhaNovaInventario(
        codigo=e["artigo"].codigo, descricao=e["artigo"].nome, quantidade=e["quantidade"],
        custo_unitario=e["custo_unitario"], unidade=e["artigo"].unidade,
        categoria=inventarios.CATEGORIA_MATERIAS if e["artigo"].tipo == ARTIGO_MATERIA
        else inventarios.CATEGORIA_MERCADORIAS) for e in existencias]
    try:
        return inventarios.criar(db, empresa, data, "Inventário a partir do stock (Gestão)", linhas, utilizador_id)
    except inventarios.ErroInventario as erro:
        raise ErroStock(str(erro)) from erro


def margens(db: Session, empresa_id: int, de: date, ate: date) -> dict:
    """Vendas, custo e margem por artigo, pelas saídas de stock ligadas a vendas."""
    por_artigo: dict[int, dict] = defaultdict(lambda: {"quantidade": 0.0, "vendas": 0.0, "custo": 0.0})
    for m in db.scalars(select(MovimentoStock).where(
            MovimentoStock.empresa_id == empresa_id, MovimentoStock.tipo == MOVIMENTO_SAIDA,
            MovimentoStock.data >= de, MovimentoStock.data <= ate)):
        if m.preco_unitario is None:
            continue   # consumo interno, quebra ou oferta: não é venda
        x = por_artigo[m.artigo_id]
        x["quantidade"] += -m.quantidade
        x["vendas"] += -m.quantidade * (m.preco_unitario or 0)
        x["custo"] += -m.valor
    artigos = {a.id: a for a in listar(db, empresa_id)}
    linhas = []
    for artigo_id, x in por_artigo.items():
        a = artigos.get(artigo_id)
        if not a:
            continue
        margem = _r(x["vendas"] - x["custo"])
        linhas.append({"artigo_id": a.id, "codigo": a.codigo, "nome": a.nome, "quantidade": _q(x["quantidade"]),
                       "vendas": _r(x["vendas"]), "custo": _r(x["custo"]), "margem": margem,
                       "margem_percentual": round(margem / x["vendas"] * 100, 1) if x["vendas"] else None})
    linhas.sort(key=lambda x: x["margem"], reverse=True)
    vendas, custo = _r(sum(x["vendas"] for x in linhas)), _r(sum(x["custo"] for x in linhas))
    return {"de": de, "ate": ate, "artigos": linhas, "vendas": vendas, "custo": custo, "margem": _r(vendas - custo),
            "margem_percentual": round((vendas - custo) / vendas * 100, 1) if vendas else None}


def resumo(db: Session, empresa_id: int) -> dict:
    artigos = [artigo_dict(a) for a in listar(db, empresa_id, incluir_inativos=False)]
    return {"artigos": len(artigos), "valor": _r(sum(a["valor"] for a in artigos)),
            "abaixo_minimo": [a for a in artigos if a["abaixo_minimo"]]}
