"""
Registo de atividade (auditoria): quem fez o quê e quando, por empresa.

- As ações de conta (entrar, sair, palavra-passe, verificação em dois passos, sessões)
  são registadas explicitamente nas rotas de autenticação.
- Todas as outras alterações feitas pela API (POST/PUT/PATCH/DELETE com sucesso) são
  registadas por um middleware, com a descrição da tabela ACOES abaixo. Assim nenhuma
  rota nova fica de fora: no pior caso aparece com a descrição genérica.
- Não se guardam valores nem conteúdos (só a ação e o identificador do documento).
"""

import re
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.modelos.tabelas import RegistoAtividade, Utilizador

# Rotas que não alteram dados de negócio (consultas feitas por POST) ou que já são registadas à parte
IGNORAR = (
    re.compile(r"^/api/auth/"),
    re.compile(r"^/api/contabilidade/assistente$"),
    re.compile(r"^/api/contabilidade/salarios/simular$"),
    re.compile(r"^/api/conta/"),
    re.compile(r"^/api/v1/"),   # API pública: registada pela própria rota, em nome da chave
)

ACOES: list[tuple[str, str, str]] = [
    # (método, padrão do caminho, descrição) — {id} é o identificador do documento
    ("POST", r"^/api/faturas$", "Carregou uma fatura"),
    ("PUT", r"^/api/faturas/(?P<id>[^/]+)$", "Editou ou confirmou a fatura {id}"),
    ("PATCH", r"^/api/faturas/(?P<id>[^/]+)/pagamento$", "Alterou o estado de pagamento da fatura {id}"),
    ("POST", r"^/api/faturas/(?P<id>[^/]+)/extrair$", "Voltou a ler a fatura {id}"),
    ("DELETE", r"^/api/faturas/(?P<id>[^/]+)$", "Apagou a fatura {id}"),
    ("POST", r"^/api/contabilidade/faturas/(?P<id>[^/]+)/contabilizar$", "Contabilizou a fatura {id}"),
    ("POST", r"^/api/contabilidade/faturas/(?P<id>[^/]+)/diferir$", "Diferiu a fatura {id}"),
    ("POST", r"^/api/contabilidade/faturas/(?P<id>[^/]+)/pagamento$", "Registou o pagamento da fatura {id}"),
    ("POST", r"^/api/contabilidade/contabilizar-lote$", "Contabilizou faturas em lote"),
    ("POST", r"^/api/contabilidade/lancamentos$", "Criou um lançamento manual"),
    ("POST", r"^/api/contabilidade/lancamentos/(?P<id>\d+)/estornar$", "Estornou o lançamento {id}"),
    ("POST", r"^/api/contabilidade/iva/apurar$", "Lançou o apuramento do IVA"),
    ("POST", r"^/api/contabilidade/contas$", "Criou uma conta no plano de contas"),
    ("PUT", r"^/api/contabilidade/configuracao$", "Alterou a configuração da empresa"),
    ("POST", r"^/api/contabilidade/vendas/importar-saft$", "Importou vendas do SAF-T"),
    ("POST", r"^/api/contabilidade/vendas$", "Registou uma venda"),
    ("POST", r"^/api/contabilidade/vendas/(?P<id>\d+)/contabilizar$", "Contabilizou a venda {id}"),
    ("POST", r"^/api/contabilidade/vendas/(?P<id>\d+)/receber$", "Registou o recebimento da venda {id}"),
    ("POST", r"^/api/contabilidade/inventarios(/importar)?$", "Registou uma contagem de inventário"),
    ("PUT", r"^/api/contabilidade/inventarios/(?P<id>\d+)$", "Alterou o inventário {id}"),
    ("DELETE", r"^/api/contabilidade/inventarios/(?P<id>\d+)$", "Apagou o inventário {id}"),
    ("POST", r"^/api/contabilidade/inventarios/(?P<id>\d+)/(apurar|inicial)$", "Lançou o inventário {id}"),
    ("POST", r"^/api/contabilidade/ativos$", "Registou um ativo fixo"),
    ("POST", r"^/api/contabilidade/ativos/depreciar$", "Lançou as depreciações"),
    ("POST", r"^/api/contabilidade/ativos/(?P<id>\d+)/saida$", "Registou a saída do ativo {id}"),
    ("DELETE", r"^/api/contabilidade/ativos/(?P<id>\d+)$", "Apagou o ativo {id}"),
    ("POST", r"^/api/contabilidade/fecho$", "Fechou o exercício"),
    ("POST", r"^/api/contabilidade/fecho/transferir$", "Transferiu o resultado do exercício"),
    ("POST", r"^/api/contabilidade/fecho/reabrir$", "Reabriu o exercício"),
    ("POST", r"^/api/contabilidade/banco/importar$", "Importou um extrato bancário"),
    ("POST", r"^/api/contabilidade/banco/movimentos/(?P<id>\d+)/(associar|pagar-fatura|receber-venda|lancar)$",
     "Reconciliou o movimento bancário {id}"),
    ("POST", r"^/api/contabilidade/banco/movimentos/(?P<id>\d+)/ignorar$", "Ignorou o movimento bancário {id}"),
    ("POST", r"^/api/contabilidade/banco/movimentos/(?P<id>\d+)/desfazer$", "Desfez a reconciliação do movimento {id}"),
    ("POST", r"^/api/contabilidade/banco/reconciliar-automaticamente$", "Reconciliou o banco automaticamente"),
    ("DELETE", r"^/api/contabilidade/banco/extratos/(?P<id>\d+)$", "Apagou o extrato {id}"),
    ("POST", r"^/api/contabilidade/open-banking/ligacoes$", "Pediu a ligação direta a um banco"),
    ("POST", r"^/api/contabilidade/open-banking/ligacoes/(?P<id>\d+)/conta$",
     "Escolheu a conta da ligação bancária {id}"),
    ("POST", r"^/api/contabilidade/open-banking/ligacoes/(?P<id>\d+)/sincronizar$",
     "Sincronizou os movimentos do banco (ligação {id})"),
    ("DELETE", r"^/api/contabilidade/open-banking/ligacoes/(?P<id>\d+)$", "Desligou a ligação bancária {id}"),
    ("POST", r"^/api/contabilidade/salarios/funcionarios$", "Registou uma pessoa no pessoal"),
    ("PUT", r"^/api/contabilidade/salarios/funcionarios/(?P<id>\d+)$", "Alterou a ficha da pessoa {id}"),
    ("DELETE", r"^/api/contabilidade/salarios/funcionarios/(?P<id>\d+)$", "Apagou a ficha da pessoa {id}"),
    ("POST", r"^/api/contabilidade/salarios/ferias$", "Lançou o acréscimo de férias"),
    ("POST", r"^/api/contabilidade/salarios/processar$", "Processou os salários"),
    ("POST", r"^/api/contabilidade/salarios/(?P<id>\d+/\d+)/pagar$", "Registou um pagamento de salários ({id})"),
    ("POST", r"^/api/contabilidade/periodizacao/diferimentos$", "Registou um diferimento"),
    ("POST", r"^/api/contabilidade/periodizacao/reconhecer$", "Reconheceu diferimentos do mês"),
    ("POST", r"^/api/contabilidade/periodizacao/acrescimos$", "Registou um acréscimo"),
    ("POST", r"^/api/contabilidade/abertura(/importar)?$", "Lançou os saldos de abertura"),
    ("POST", r"^/api/contabilidade/importar-saft$", "Analisou ou importou um SAF-T de outro programa"),
    ("POST", r"^/api/contabilidade/centros$", "Criou um centro de custo"),
    ("PATCH", r"^/api/contabilidade/centros/(?P<id>\d+)$", "Alterou o centro de custo {id}"),
    ("PATCH", r"^/api/contabilidade/linhas/(?P<id>\d+)/centro$", "Atribuiu um centro de custo à linha {id}"),
    ("POST", r"^/api/gestao/artigos$", "Criou um artigo"),
    ("PUT", r"^/api/gestao/artigos/(?P<id>\d+)$", "Alterou o artigo {id}"),
    ("POST", r"^/api/gestao/movimentos$", "Registou um movimento de stock"),
    ("POST", r"^/api/gestao/stock/entrada-fatura$", "Deu entrada em stock de uma fatura de compra"),
    ("POST", r"^/api/gestao/stock/saida-venda$", "Deu saída do stock de uma venda"),
    ("POST", r"^/api/gestao/stock/inventario$", "Passou o stock para os inventários da contabilidade"),
    ("PUT", r"^/api/gestao/orcamento$", "Guardou o orçamento"),
    ("POST", r"^/api/equipa$", "Convidou um colega"),
    ("POST", r"^/api/equipa/(?P<id>\d+)/link$", "Gerou um novo link de acesso para o utilizador {id}"),
    ("PATCH", r"^/api/equipa/(?P<id>\d+)$", "Alterou o acesso do utilizador {id}"),
    ("POST", r"^/api/equipa/chaves-api$", "Criou uma chave da API"),
    ("DELETE", r"^/api/equipa/chaves-api/(?P<id>\d+)$", "Revogou a chave da API {id}"),
]
_ACOES = [(m, re.compile(p), d) for m, p, d in ACOES]


def descrever(metodo: str, caminho: str) -> str | None:
    """Descrição da ação para o registo, ou None se o pedido não deve ser registado."""
    if any(p.match(caminho) for p in IGNORAR):
        return None
    for m, padrao, descricao in _ACOES:
        if m == metodo:
            encontrado = padrao.match(caminho)
            if encontrado:
                return descricao.format(id=encontrado.groupdict().get("id", ""))
    return f"Alteração ({metodo} {caminho.removeprefix('/api/')})"


def registar(db: Session, utilizador: Utilizador | None, acao: str, detalhe: str | None = None,
             ip: str | None = None, empresa_id: int | None = None) -> None:
    empresa = empresa_id or (utilizador.empresa_id if utilizador else None)
    if empresa is None:
        return
    db.add(RegistoAtividade(
        empresa_id=empresa, utilizador_id=utilizador.id if utilizador else None, quando=datetime.now(),
        acao=acao[:120], detalhe=(detalhe or None) and detalhe[:300], ip=ip,
    ))


def listar(db: Session, empresa_id: int, limite: int = 200, antes_de: int | None = None) -> list[dict]:
    consulta = (
        select(RegistoAtividade, Utilizador.nome)
        .outerjoin(Utilizador, Utilizador.id == RegistoAtividade.utilizador_id)
        .where(RegistoAtividade.empresa_id == empresa_id)
        .order_by(RegistoAtividade.id.desc())
        .limit(limite)
    )
    if antes_de:
        consulta = consulta.where(RegistoAtividade.id < antes_de)
    return [
        {"id": r.id, "quando": r.quando.isoformat(timespec="seconds"), "quem": nome or "—", "acao": r.acao,
         "detalhe": r.detalhe, "ip": r.ip}
        for r, nome in db.execute(consulta)
    ]


def apagar_da_empresa(db: Session, empresa_id: int) -> None:
    db.execute(delete(RegistoAtividade).where(RegistoAtividade.empresa_id == empresa_id))
