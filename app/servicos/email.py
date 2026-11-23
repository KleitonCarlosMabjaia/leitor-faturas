"""
Emails transacionais (convites, recuperação da palavra-passe, nova sessão e alertas de prazos).

Envio pela API do Resend (https://resend.com). Sem RESEND_API_KEY e EMAIL_REMETENTE, nada é
enviado e a aplicação funciona como antes: o administrador copia o link e envia-o à pessoa.

- Todos os textos vindos de fora (nomes, empresas) são escapados no HTML.
- Cada email tem também a versão em texto simples (leitores de email sem HTML e filtros de spam).
- Uma falha no envio nunca estraga o pedido: fica registada e devolve-se False.
"""

import html
import logging
from dataclasses import dataclass, field

import httpx

from app import config

logger = logging.getLogger(__name__)

API_RESEND = "https://api.resend.com/emails"


def configurado() -> bool:
    return bool(config.RESEND_API_KEY and config.EMAIL_REMETENTE)


@dataclass
class Mensagem:
    para: str
    assunto: str
    titulo: str
    paragrafos: list[str]
    botao: tuple[str, str] | None = None          # (texto, endereço)
    nota: str | None = None                       # texto pequeno no fim (ex.: "Se não foi você...")
    lista: list[str] = field(default_factory=list)


def _html(m: Mensagem) -> str:
    e = html.escape
    logo = f"{config.URL_PUBLICO}/static/logo-email.png"
    paragrafos = "".join(
        f'<p style="margin:0 0 16px;font-size:15px;line-height:1.6;color:#33414C">{e(p)}</p>' for p in m.paragrafos
    )
    lista = ""
    if m.lista:
        itens = "".join(
            f'<li style="margin:0 0 8px;font-size:15px;line-height:1.5;color:#0F1B24">{e(i)}</li>' for i in m.lista
        )
        lista = f'<ul style="margin:0 0 16px;padding-left:20px">{itens}</ul>'
    botao = ""
    if m.botao:
        texto, endereco = m.botao
        botao = (
            '<table role="presentation" cellspacing="0" cellpadding="0" style="margin:8px 0 24px"><tr>'
            f'<td style="border-radius:8px;background:#0B5F75"><a href="{e(endereco, quote=True)}" '
            'style="display:inline-block;padding:12px 22px;font-size:15px;font-weight:600;color:#FFFFFF;'
            f'text-decoration:none;border-radius:8px">{e(texto)}</a></td></tr></table>'
            f'<p style="margin:0 0 16px;font-size:12px;line-height:1.5;color:#5D6B78">Se o botão não funcionar, '
            f'copie este endereço para o navegador:<br><span style="word-break:break-all">{e(endereco)}</span></p>'
        )
    nota = f'<p style="margin:24px 0 0;font-size:13px;line-height:1.5;color:#5D6B78">{e(m.nota)}</p>' if m.nota else ""
    return f"""<!DOCTYPE html>
<html lang="pt-PT"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width">
<title>{e(m.assunto)}</title></head>
<body style="margin:0;padding:0;background:#F3F5F7;font-family:Inter,Segoe UI,Arial,sans-serif">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#F3F5F7;padding:32px 12px">
<tr><td align="center">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:560px">
<tr><td style="padding:0 4px 20px">
<img src="{e(logo, quote=True)}" width="32" height="32" alt="" style="vertical-align:middle">
<span style="font-size:20px;font-weight:700;color:#0F1B24;vertical-align:middle;margin-left:8px">Dobra</span></td></tr>
<tr><td style="background:#FFFFFF;border:1px solid #E1E6EB;border-radius:12px;padding:32px">
<h1 style="margin:0 0 16px;font-size:22px;line-height:1.3;color:#0F1B24">{e(m.titulo)}</h1>
{paragrafos}{lista}{botao}{nota}
</td></tr>
<tr><td style="padding:20px 4px;font-size:12px;line-height:1.5;color:#5D6B78">
Dobra · contabilidade para pequenas empresas portuguesas.<br>
Recebeu este email porque tem uma conta no Dobra.
<a href="{e(config.URL_PUBLICO)}/privacidade" style="color:#0B5F75">Privacidade</a>
</td></tr></table></td></tr></table></body></html>"""


def _texto(m: Mensagem) -> str:
    partes = [m.titulo, "", *m.paragrafos]
    partes += [f"- {i}" for i in m.lista]
    if m.botao:
        partes += ["", f"{m.botao[0]}: {m.botao[1]}"]
    if m.nota:
        partes += ["", m.nota]
    partes += ["", "-- ", "Dobra · contabilidade para pequenas empresas portuguesas"]
    return "\n".join(partes)


def _post(dados: dict) -> None:
    """Pedido à API do Resend (substituído nos testes: nunca há rede)."""
    resposta = httpx.post(
        API_RESEND, json=dados, timeout=10,
        headers={"Authorization": f"Bearer {config.RESEND_API_KEY}", "User-Agent": "Dobra"},
    )
    resposta.raise_for_status()


def enviar(m: Mensagem) -> bool:
    if not configurado():
        return False
    try:
        _post({
            "from": config.EMAIL_REMETENTE, "to": [m.para], "subject": m.assunto,
            "html": _html(m), "text": _texto(m),
        })
        return True
    except Exception:  # rede, chave errada, limite do serviço... nunca estraga o pedido
        logger.exception("Falha ao enviar o email '%s'", m.assunto)
        return False


# ---------- Mensagens ----------

def convite(para: str, nome: str, empresa: str, quem_convida: str, link: str) -> Mensagem:
    return Mensagem(
        para=para, assunto=f"{quem_convida} convidou-o para o Dobra ({empresa})",
        titulo=f"Olá, {nome}",
        paragrafos=[f"{quem_convida} convidou-o para a contabilidade de {empresa} no Dobra.",
                    "Para entrar, defina a sua palavra-passe. O link é válido durante 72 horas "
                    "e só pode ser usado uma vez."],
        botao=("Definir a palavra-passe", link),
        nota="Se não esperava este convite, pode ignorar este email.",
    )


def reposicao(para: str, nome: str, link: str, minutos: int) -> Mensagem:
    return Mensagem(
        para=para, assunto="Recuperar a palavra-passe do Dobra",
        titulo="Recuperar a palavra-passe",
        paragrafos=[f"Olá, {nome}. Recebemos um pedido para definir uma nova palavra-passe na sua conta.",
                    f"O link é válido durante {minutos} minutos e só pode ser usado uma vez. "
                    "Depois de mudar a palavra-passe, as sessões abertas noutros dispositivos terminam."],
        botao=("Definir uma nova palavra-passe", link),
        nota="Se não foi você a pedir, ignore este email: a palavra-passe atual continua a funcionar.",
    )


def nova_sessao(para: str, nome: str, dispositivo: str, ip: str | None, quando: str) -> Mensagem:
    return Mensagem(
        para=para, assunto="Nova entrada na sua conta do Dobra",
        titulo="Nova entrada na sua conta",
        paragrafos=[f"Olá, {nome}. A sua conta foi usada para entrar num dispositivo novo:"],
        lista=[f"Dispositivo: {dispositivo}", f"Endereço IP: {ip or 'desconhecido'}", f"Quando: {quando}"],
        botao=("Ver as sessões abertas", f"{config.URL_PUBLICO}/conta"),
        nota="Se foi você, não precisa de fazer nada. Se não reconhece esta entrada, termine a sessão na "
             "página da conta, mude a palavra-passe e ligue a verificação em dois passos.",
    )


def alerta_prazos(para: str, nome: str, empresa: str, obrigacoes: list[dict]) -> Mensagem:
    def dias(n: int) -> str:
        return "hoje" if n == 0 else "amanhã" if n == 1 else f"daqui a {n} dias"

    itens = [f"{o['nome']} ({o['periodo']}): até {o['prazo'].strftime('%d/%m/%Y')}, {dias(o['dias'])}"
             for o in obrigacoes]
    return Mensagem(
        para=para, assunto=f"Prazos fiscais a aproximar-se · {empresa}",
        titulo="Prazos fiscais a aproximar-se",
        paragrafos=[f"Olá, {nome}. Estas obrigações de {empresa} terminam em breve:"],
        lista=itens,
        botao=("Ver o calendário fiscal", f"{config.URL_PUBLICO}/contabilidade#obrigacoes"),
        nota="Prazos indicativos (regime geral): confirme sempre no Portal das Finanças. "
             "Pode desligar estes alertas na página A minha conta.",
    )


def boas_vindas(para: str, nome: str, empresa: str, dias: int) -> Mensagem:
    return Mensagem(
        para=para, assunto=f"Bem-vindo ao Dobra, {nome}",
        titulo="A sua conta está pronta",
        paragrafos=[f"Olá, {nome}. A conta de {empresa} foi criada, com {dias} dias para experimentar tudo sem custos.",
                    "Para começar, carregue as primeiras faturas: o Dobra lê o QR da AT e propõe os lançamentos."],
        lista=["Carregue as faturas dos fornecedores", "Confirme as contas propostas e contabilize",
               "Importe o extrato do banco e reconcilie", "Ligue a verificação em dois passos"],
        botao=("Abrir o Dobra", f"{config.URL_PUBLICO}/contabilidade#resumo"),
        nota="Antes do fim do período experimental, escolha um plano na página Subscrição. "
             "Pode cancelar quando quiser.",
    )


def acesso_adicionado(para: str, nome: str, empresa: str, quem: str) -> Mensagem:
    return Mensagem(
        para=para, assunto=f"Tem acesso a mais uma empresa no Dobra: {empresa}",
        titulo=f"Acesso a {empresa}",
        paragrafos=[f"Olá, {nome}. {quem} deu-lhe acesso à contabilidade de {empresa} no Dobra.",
                    "Use a mesma conta de sempre: a empresa nova aparece no seletor de empresas, "
                    "no topo da barra lateral."],
        botao=("Abrir o Dobra", f"{config.URL_PUBLICO}/"),
        nota="Se não reconhece esta empresa, fale com quem lhe deu o acesso.",
    )
