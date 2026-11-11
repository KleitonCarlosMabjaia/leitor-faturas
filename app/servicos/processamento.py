"""
Orquestra a leitura de uma fatura, da fonte mais barata para a mais cara:

1. QR da AT          -> gratuito e exato para os valores fiscais.
2. Cache por NIF     -> se o fornecedor já é conhecido, o nome vem da base de
                        dados e a IA nem é chamada.
3. IA                -> lê o nome e o vencimento, ou a fatura toda se não houver QR.

Se só uma das fontes funcionar, a fatura fica lida na mesma, com um alerta.
Os erros esperados não interrompem o pedido: ficam registados na fatura.
Uma fatura repetida lança FaturaDuplicada, antes de alterar qualquer dado.
"""

import logging

from sqlalchemy.orm import Session

from app.modelos.fatura import DadosFatura
from app.modelos.tabelas import ESTADO_POR_REVER, Fatura
from app.servicos import extracao_ia, pdf, qr, repositorio

logger = logging.getLogger(__name__)

# Campos em que o QR tem prioridade sobre a IA (vêm do programa de faturação certificado)
CAMPOS_DO_QR = ("nif_fornecedor", "numero_fatura", "data_emissao", "valor_sem_iva", "iva", "total")


class FaturaDuplicada(Exception):
    def __init__(self, existente: Fatura):
        self.existente = existente
        descricao = existente.numero_fatura or existente.nome_original
        nome = existente.nome_fornecedor or existente.nif_fornecedor or "fornecedor desconhecido"
        super().__init__(f"Esta fatura já foi carregada ({descricao}, {nome}).")


def combinar(dados_qr: dict, dados_ia: DadosFatura | None, nome_conhecido: str = "") -> DadosFatura:
    """Junta as fontes: valores fiscais do QR; nome e vencimento da IA (ou da cache)."""
    valores_qr = {campo: dados_qr[campo] for campo in CAMPOS_DO_QR}

    if dados_ia is None:
        return DadosFatura(
            fornecedor=nome_conhecido,
            data_vencimento=None,
            moeda="EUR",
            # Com o nome da cache a leitura está completa; sem ele falta um campo
            confianca="alta" if nome_conhecido else "media",
            **valores_qr,
        )

    # QR + IA: a IA completa, o QR manda nos números
    return dados_ia.model_copy(update={**valores_qr, "moeda": "EUR", "confianca": "alta"})


def _ler(
    db: Session, empresa_id: int, conteudo: bytes
) -> tuple[str, DadosFatura | None, str | None, list[str], dict | None]:
    """
    Faz a leitura sem tocar na fatura.
    Devolve (estado_extracao, dados, origem, alertas ou erro, dados do QR).
    """
    alertas: list[str] = []
    dados_ia = None

    # 1. QR (nunca lança exceções: devolve None se não houver)
    dados_qr = qr.ler_qr(conteudo)

    # 2. Fornecedor conhecido? Então não é preciso IA.
    if dados_qr:
        # A cache é de cada empresa: o nome que uma empresa confirmou não passa para outra
        conhecido = repositorio.fornecedor_por_nif(db, empresa_id, dados_qr["nif_fornecedor"])
        if conhecido and conhecido.nome:
            return "concluida", combinar(dados_qr, None, conhecido.nome), "qr+cache", [], dados_qr

    # 3. Texto + IA
    try:
        texto = pdf.extrair_texto(conteudo)
        dados_ia = extracao_ia.extrair_dados(texto)
    except pdf.PDFSemTexto as erro:
        if dados_qr is None:
            return "sem_texto", None, None, [str(erro)], None
        alertas.append("PDF digitalizado: os valores foram lidos do QR.")
    except (pdf.PDFIlegivel, extracao_ia.ErroExtracao) as erro:
        if dados_qr is None:
            return "erro", None, None, [str(erro)], None
        alertas.append(f"Valores lidos do QR. A IA não conseguiu ler o resto: {erro}")
    except Exception:
        # Erro inesperado: fica no registo do servidor com o traceback completo
        logger.exception("Erro inesperado ao ler a fatura")
        if dados_qr is None:
            return "erro", None, None, ["Ocorreu um erro inesperado ao ler a fatura."], None
        alertas.append("Valores lidos do QR. A IA não conseguiu ler o resto.")

    if dados_qr is not None:
        return "concluida", combinar(dados_qr, dados_ia), "qr+ia" if dados_ia else "qr", alertas, dados_qr
    return "concluida", dados_ia, "ia", alertas, None


def processar(db: Session, fatura: Fatura, conteudo: bytes) -> Fatura:
    """Lê o PDF e atualiza a fatura (sem commit). Lança FaturaDuplicada se já existir."""
    estado, dados, origem, mensagens, dados_qr = _ler(db, fatura.empresa_id, conteudo)

    if estado != "concluida":
        repositorio.limpar_dados(fatura)
        fatura.estado_extracao = estado
        fatura.erro = mensagens[0]
        return fatura

    atcud = dados_qr.get("atcud") if dados_qr else None
    duplicada = repositorio.encontrar_duplicado(
        db, fatura.empresa_id, dados.nif_fornecedor, dados.numero_fatura, atcud, excluir_id=fatura.id
    )
    if duplicada:
        raise FaturaDuplicada(duplicada)

    repositorio.aplicar_dados(db, fatura, dados, alertas_extra=mensagens)
    fatura.estado_extracao = "concluida"
    fatura.erro = None
    fatura.origem = origem
    fatura.estado = ESTADO_POR_REVER
    fatura.atcud = atcud
    fatura.tipo_documento = dados_qr.get("tipo_documento") if dados_qr else None
    return fatura
