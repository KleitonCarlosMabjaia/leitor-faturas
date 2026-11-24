// Dobra - equipa: convidar colegas, gerar links e desativar contas

const formulario = document.getElementById("convite");
const corpo = document.getElementById("membros");
const formatoAcesso = new Intl.DateTimeFormat("pt-PT", { dateStyle: "short", timeStyle: "short" });

function mostrarLink(link, para) {
  document.getElementById("texto-link").textContent = `Link para ${para}:`;
  document.getElementById("link").value = link;
  document.getElementById("caixa-link").hidden = false;
  document.getElementById("link").select();
}

const botaoCopiar = document.getElementById("copiar");
botaoCopiar.addEventListener("click", async () => {
  const campo = document.getElementById("link");
  try {
    await navigator.clipboard.writeText(campo.value);
    // Confirmação no próprio botão, durante 2 segundos
    botaoCopiar.replaceChildren(icone("check"), "Copiado");
    setTimeout(() => botaoCopiar.replaceChildren(icone("copiar"), "Copiar"), 2000);
  } catch {
    campo.select(); // sem acesso à área de transferência: fica selecionado para Ctrl+C
    notificar("Selecione o link e copie com Ctrl+C.", "info");
  }
});

function botaoTexto(texto, rotulo, acao, classe = "secundario") {
  const b = document.createElement("button");
  b.type = "button";
  b.className = `botao ${classe} pequeno`;
  b.textContent = texto;
  b.setAttribute("aria-label", rotulo);
  b.addEventListener("click", () => acao(b));
  return b;
}

function desenhar(membros) {
  document.getElementById("n-membros").textContent = membros.length;
  corpo.replaceChildren();
  for (const m of membros) {
    const tr = corpo.insertRow();

    const pessoa = tr.insertCell();
    pessoa.className = "nome";
    const caixa = document.createElement("div");
    caixa.className = "pessoa";
    const avatar = document.createElement("span");
    avatar.className = "avatar";
    avatar.setAttribute("aria-hidden", "true");
    avatar.textContent = iniciais(m.nome);
    const textos = document.createElement("div");
    const titulo = document.createElement("span");
    titulo.className = "titulo";
    titulo.textContent = m.nome;
    const email = document.createElement("span");
    email.className = "detalhe";
    email.textContent = m.email;
    textos.append(titulo, email);
    caixa.append(avatar, textos);
    pessoa.appendChild(caixa);

    const papel = tr.insertCell();
    papel.dataset.rotulo = "Papel:";
    papel.textContent = m.papel === "admin" ? "Administrador" : "Membro";

    const estado = tr.insertCell();
    if (!m.ativo) estado.appendChild(criarEtiqueta("Desativada", "erro", "erro"));
    else if (m.pendente) estado.appendChild(criarEtiqueta("Convite pendente", "info", "relogio"));
    else estado.appendChild(criarEtiqueta("Ativa", "ok", "check"));
    if (m.dois_passos) {
      estado.append(" ");
      estado.appendChild(criarEtiqueta("2 passos", "info", "cadeado"));
    }
    if (m.ultimo_acesso) {
      const acesso = document.createElement("span");
      acesso.className = "detalhe";
      acesso.textContent = `Último acesso: ${formatoAcesso.format(new Date(m.ultimo_acesso))}`;
      estado.appendChild(acesso);
    }

    const acoes = tr.insertCell();
    acoes.className = "num acoes";
    if (m.ativo) {
      const textoLink = m.pendente ? "Novo convite" : "Repor palavra-passe";
      acoes.appendChild(botaoTexto(textoLink, `${textoLink} para ${m.nome}`, (b) => gerarLink(m, b)));
      acoes.append(" ");
    }
    if (m.dois_passos) {
      acoes.appendChild(botaoTexto("Desligar 2 passos", `Desligar a verificação em dois passos de ${m.nome}`,
        (b) => alterar(m, { desligar_dois_passos: true }, b)));
      acoes.append(" ");
    }
    acoes.appendChild(
      botaoTexto(m.ativo ? "Desativar" : "Ativar", `${m.ativo ? "Desativar" : "Ativar"} ${m.nome}`,
        (b) => alterar(m, { ativo: !m.ativo }, b), m.ativo ? "fantasma" : "secundario")
    );
  }
}

async function carregar() {
  try {
    const resposta = await api("/api/equipa");
    if (!resposta.ok) throw new Error(await lerErro(resposta, "Não foi possível obter a equipa."));
    desenhar(await resposta.json());
  } catch (erro) {
    notificar(erro.message, "erro");
  }
}

async function gerarLink(m, botao) {
  ocupado(botao, true);
  try {
    const resposta = await api(`/api/equipa/${m.id}/link`, { method: "POST" });
    if (!resposta.ok) throw new Error(await lerErro(resposta, "Não foi possível gerar o link."));
    const dados = await resposta.json();
    mostrarLink(dados.link, m.nome);
    notificar(dados.email_enviado ? `Link enviado por email para ${m.email}. Os links anteriores deixaram de funcionar.`
      : "Link gerado. Os links anteriores desta pessoa deixaram de funcionar.", "ok");
  } catch (erro) {
    notificar(erro.message, "erro");
  } finally {
    ocupado(botao, false);
  }
}

async function alterar(m, alteracao, botao) {
  if (alteracao.desligar_dois_passos) {
    const sim = await confirmar({
      titulo: `Desligar a verificação em dois passos de ${m.nome}?`,
      texto: "Use isto só se a pessoa perdeu o telemóvel e os códigos de recuperação. As sessões dela terminam e poderá voltar a ligar a verificação na sua conta.",
      botao: "Desligar",
      perigo: true,
    });
    if (!sim) return;
  }
  if (alteracao.ativo === false) {
    const sim = await confirmar({
      titulo: `Desativar ${m.nome}?`,
      texto: "A pessoa perde o acesso imediatamente e as sessões abertas terminam. Pode reativar a conta mais tarde.",
      botao: "Desativar",
      perigo: true,
    });
    if (!sim) return;
  }
  ocupado(botao, true);
  try {
    const resposta = await api(`/api/equipa/${m.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(alteracao),
    });
    if (!resposta.ok) throw new Error(await lerErro(resposta, "Não foi possível alterar a conta."));
    notificar(alteracao.desligar_dois_passos ? `Verificação em dois passos de ${m.nome} desligada.`
      : alteracao.ativo ? `Conta de ${m.nome} reativada.` : `Conta de ${m.nome} desativada.`, "ok");
    await carregar();
    await carregarAtividade(true);
  } catch (erro) {
    ocupado(botao, false);
    notificar(erro.message, "erro");
  }
}

formulario.addEventListener("submit", async (e) => {
  e.preventDefault();
  const dados = Object.fromEntries(new FormData(formulario));
  if (!dados.nome.trim() || !dados.email.trim()) {
    notificar("Preencha o nome e o email.", "erro");
    (dados.nome.trim() ? formulario.email : formulario.nome).focus();
    return;
  }
  const botao = formulario.querySelector('button[type="submit"]');
  ocupado(botao, true);
  try {
    const resposta = await api("/api/equipa", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(dados),
    });
    if (!resposta.ok) throw new Error(await lerErro(resposta, "Não foi possível criar o convite."));
    const { membro, link, email_enviado: enviado, conta_existente: existente } = await resposta.json();
    if (existente) {
      document.getElementById("caixa-link").hidden = true;
      notificar(`${membro.email} já tinha conta no Dobra: passou a ter acesso a esta empresa${enviado ? " e foi avisado por email" : ""}.`, "ok");
    } else {
      mostrarLink(link, membro.nome);
      notificar(enviado ? `Convite enviado por email para ${membro.email}.` : `Convite criado para ${membro.nome}.`, "ok");
    }
    formulario.reset();
    await carregar();
  } catch (erro) {
    notificar(erro.message, "erro");
  } finally {
    ocupado(botao, false);
  }
});

// ---------- Registo de atividade ----------

let registos = [];
let haMais = false;

async function carregarAtividade(recomecar = false) {
  try {
    const antes = !recomecar && registos.length ? `?antes_de=${registos.at(-1).id}` : "";
    const novos = await obter(`/api/equipa/atividade${antes}`);
    registos = recomecar ? novos : registos.concat(novos);
    haMais = novos.length === 200;
    desenharAtividade();
  } catch (erro) {
    notificar(erro.message, "erro");
  }
}

function desenharAtividade() {
  const filtro = document.getElementById("filtro-atividade").value.trim().toLowerCase();
  const visiveis = registos.filter((r) => !filtro || `${r.quem} ${r.acao} ${r.detalhe || ""}`.toLowerCase().includes(filtro));
  const lista = document.getElementById("atividade");
  preencher(lista, visiveis.length ? visiveis.map((r) => h("li", { class: "registo" },
    h("span", { class: "avatar pequeno", "aria-hidden": "true", texto: iniciais(r.quem) }),
    h("div", { class: "registo-texto" },
      h("span", {}, h("strong", { texto: r.quem }), ` ${r.acao.charAt(0).toLowerCase()}${r.acao.slice(1)}`),
      h("span", { class: "detalhe", texto: [formatoAcesso.format(new Date(r.quando)), r.detalhe, r.ip].filter(Boolean).join(" · ") }))))
    : [h("li", { class: "ajuda", texto: filtro ? "Nada corresponde à pesquisa." : "Ainda não há atividade registada." })]);
  document.getElementById("mais-atividade").hidden = !haMais;
}

document.getElementById("filtro-atividade").addEventListener("input", desenharAtividade);
document.getElementById("mais-atividade").addEventListener("click", () => carregarAtividade());

// ---------- Chaves da API pública ----------

async function carregarChaves() {
  const corpoChaves = document.getElementById("chaves");
  let chaves;
  try {
    chaves = await obter("/api/equipa/chaves-api");
  } catch (erro) {
    return notificar(erro.message, "erro");
  }
  preencher(corpoChaves, ...(chaves.length ? chaves.map((c) => {
    const revogar = botaoTexto("Revogar", `Revogar a chave ${c.nome}`, async (b) => {
      const sim = await confirmar({
        titulo: `Revogar a chave ${c.nome}?`,
        texto: "O programa que a usa deixa de ter acesso imediatamente. Não é possível desfazer.",
        botao: "Revogar", perigo: true,
      });
      if (!sim) return;
      ocupado(b, true);
      const r = await api(`/api/equipa/chaves-api/${c.id}`, { method: "DELETE" });
      if (!r.ok) {
        ocupado(b, false);
        return notificar(await lerErro(r, "Não foi possível revogar a chave."), "erro");
      }
      notificar(`Chave ${c.nome} revogada.`, "ok");
      carregarChaves();
    }, "perigo");
    return h("tr", {},
      h("td", { class: "nome" },
        h("div", {}, h("span", { class: "titulo", texto: c.nome }),
          h("span", { class: "detalhe" }, h("code", { texto: `${c.prefixo}…` }), ` · criada a ${formatarData(c.criada_em.slice(0, 10))}`))),
      h("td", { "data-rotulo": "Permissão:" }, c.permissao === "escrita"
        ? criarEtiqueta("Leitura e escrita", "aviso", "enviar") : criarEtiqueta("Só leitura", "ok", "olho")),
      h("td", { "data-rotulo": "Último uso:", texto: c.ultimo_uso ? formatoAcesso.format(new Date(c.ultimo_uso)) : "Nunca" }),
      h("td", { class: "num acoes" }, revogar));
  }) : [h("tr", {}, h("td", { colspan: "4", class: "ajuda", texto: "Ainda não há chaves. Crie uma para ligar outro programa." }))]));
}

document.getElementById("nova-chave").addEventListener("click", () => {
  const nome = h("input", { id: "chave-nome", maxlength: "80", autocomplete: "off", placeholder: "Ex.: Power BI, ERP da loja", required: true });
  const permissao = h("select", { id: "chave-permissao" },
    h("option", { value: "leitura" }, "Só leitura (consultar dados)"),
    h("option", { value: "escrita" }, "Leitura e escrita (também enviar movimentos bancários)"));
  janelaTemporaria("Criar chave da API", h("div", {},
    campoJanela("chave-nome", "Nome (o programa que a vai usar)", nome),
    campoJanela("chave-permissao", "Permissão", permissao),
    h("p", { class: "ajuda", texto: "A chave dá acesso aos dados desta empresa. Dê-a só a programas em que confia e revogue-a quando deixar de ser precisa." })),
  async () => {
    if (!nome.value.trim()) throw new Error("Dê um nome à chave.");
    const criada = await enviar("/api/equipa/chaves-api", { nome: nome.value.trim(), permissao: permissao.value });
    const campo = document.getElementById("chave-criada");
    campo.value = criada.chave;
    document.getElementById("caixa-chave").hidden = false;
    campo.select();
    notificar("Chave criada. Copie-a agora: não volta a ser mostrada.", "ok");
    carregarChaves();
  }, "Criar chave");
  nome.focus();
});

document.getElementById("copiar-chave").addEventListener("click", async (e) => {
  const b = e.currentTarget;
  const campo = document.getElementById("chave-criada");
  try {
    await navigator.clipboard.writeText(campo.value);
    b.replaceChildren(icone("check"), "Copiado");
    setTimeout(() => b.replaceChildren(icone("copiar"), "Copiar"), 2000);
  } catch {
    campo.select();
    notificar("Selecione a chave e copie com Ctrl+C.", "info");
  }
});

// ---------- Webhooks ----------

let eventosWebhook = {};

function estadoWebhook(w) {
  if (!w.ativo) return criarEtiqueta("Desativado (falhas)", "erro", "alerta");
  if (w.ultimo_estado === "falhou") return criarEtiqueta(`A falhar (${w.falhas_seguidas})`, "aviso", "alerta");
  if (w.ultimo_estado === "entregue") return criarEtiqueta("A funcionar", "ok", "check");
  return criarEtiqueta("Sem avisos ainda", "neutro", "relogio");
}

function verEntregas(w) {
  const lista = h("div", { class: "lista-entregas" }, h("p", { class: "ajuda", texto: "A carregar…" }));
  const janela = janelaTemporaria(`Últimos avisos para ${new URL(w.url).host}`, lista, async () => {}, "Fechar");
  janela.querySelector(".janela-botoes .secundario").hidden = true;
  obter(`/api/equipa/webhooks/${w.id}/entregas`).then((entregas) => {
    preencher(lista, ...(entregas.length ? [h("div", { class: "tabela-caixa" }, h("table", { class: "tabela responsiva" },
      h("thead", {}, h("tr", {}, ...["Quando", "Evento", "Resultado"].map((x) => h("th", { scope: "col", texto: x })))),
      h("tbody", {}, entregas.map((e) => h("tr", {},
        h("td", { "data-rotulo": "Quando:", texto: formatoAcesso.format(new Date(e.criada_em)) }),
        h("td", { "data-rotulo": "Evento:" }, h("code", { texto: e.evento })),
        h("td", { "data-rotulo": "Resultado:" },
          e.estado === "entregue" ? criarEtiqueta(`Entregue (${e.codigo_resposta})`, "ok", "check")
            : e.estado === "falhou" ? criarEtiqueta(e.erro || "Falhou", "erro", "alerta")
              : criarEtiqueta(`Nova tentativa (${e.tentativas})`, "aviso", "relogio")))))))]
      : [h("p", { class: "ajuda", texto: "Ainda não houve avisos para este endereço." })]));
  }).catch((erro) => preencher(lista, h("p", { class: "erro-campo", texto: erro.message })));
}

async function carregarWebhooks() {
  const corpo = document.getElementById("webhooks");
  let d;
  try {
    d = await obter("/api/equipa/webhooks");
  } catch (erro) {
    return notificar(erro.message, "erro");
  }
  eventosWebhook = d.eventos;
  preencher(corpo, ...(d.webhooks.length ? d.webhooks.map((w) => {
    const testar = botaoTexto("Testar", `Enviar um aviso de teste para ${w.url}`, async (b) => {
      ocupado(b, true);
      try {
        const r = await enviar(`/api/equipa/webhooks/${w.id}/testar`, {});
        notificar(r.estado === "entregue" ? `Aviso de teste entregue (resposta ${r.codigo_resposta}).`
          : `O aviso de teste falhou: ${r.erro || "sem resposta"}.`, r.estado === "entregue" ? "ok" : "erro");
      } catch (erro) {
        notificar(erro.message, "erro");
      }
      ocupado(b, false);
      carregarWebhooks();
    });
    const historico = botaoTexto("Avisos", `Ver os últimos avisos enviados para ${w.url}`, () => verEntregas(w), "fantasma");
    const apagar = botaoTexto("Remover", `Remover o webhook ${w.url}`, async (b) => {
      const sim = await confirmar({ titulo: "Remover este webhook?", texto: `${w.url} deixa de receber avisos.`, botao: "Remover", perigo: true });
      if (!sim) return;
      ocupado(b, true);
      const r = await api(`/api/equipa/webhooks/${w.id}`, { method: "DELETE" });
      if (!r.ok) {
        ocupado(b, false);
        return notificar(await lerErro(r, "Não foi possível remover o webhook."), "erro");
      }
      notificar("Webhook removido.", "ok");
      carregarWebhooks();
    }, "perigo");
    return h("tr", {},
      h("td", { class: "nome" }, h("div", {}, h("span", { class: "titulo quebrar", texto: w.url }),
        h("span", { class: "detalhe", texto: w.ultima_entrega_em ? `Último aviso: ${formatoAcesso.format(new Date(w.ultima_entrega_em))}` : `Criado a ${formatarData(w.criado_em.slice(0, 10))}` }))),
      h("td", { "data-rotulo": "Eventos:" }, h("div", { class: "etiquetas" }, w.eventos.map((e) => h("code", { texto: e })))),
      h("td", { "data-rotulo": "Estado:" }, estadoWebhook(w)),
      h("td", { class: "num acoes" }, testar, historico, apagar));
  }) : [h("tr", {}, h("td", { colspan: "4", class: "ajuda", texto: "Ainda não há webhooks. Adicione um para avisar outro programa das novidades." }))]));
}

document.getElementById("novo-webhook").addEventListener("click", () => {
  const url = h("input", { id: "webhook-url", type: "url", maxlength: "500", autocomplete: "off", placeholder: "https://o-seu-programa.pt/webhooks/dobra", required: true });
  const caixas = Object.entries(eventosWebhook).map(([chave, descricao]) =>
    h("label", { class: "opcao-evento" }, h("input", { type: "checkbox", value: chave, checked: chave !== "fatura.carregada" }),
      h("span", {}, h("code", { texto: chave }), h("span", { class: "ajuda", texto: descricao }))));
  janelaTemporaria("Adicionar webhook", h("div", {},
    campoJanela("webhook-url", "Endereço (https) que recebe os avisos", url),
    h("fieldset", { class: "eventos-webhook" }, h("legend", { texto: "Eventos" }), ...caixas),
    h("p", { class: "ajuda", texto: "Cada aviso diz o que aconteceu e o identificador; os detalhes obtêm-se pela API com uma chave. Se o endereço falhar, o Dobra volta a tentar durante cerca de 15 horas." })),
  async () => {
    const eventos = caixas.map((c) => c.querySelector("input")).filter((i) => i.checked).map((i) => i.value);
    if (!url.value.trim()) throw new Error("Indique o endereço.");
    if (!eventos.length) throw new Error("Escolha pelo menos um evento.");
    const criado = await enviar("/api/equipa/webhooks", { url: url.value.trim(), eventos });
    const campo = document.getElementById("segredo-criado");
    campo.value = criado.segredo;
    document.getElementById("caixa-segredo").hidden = false;
    campo.select();
    notificar("Webhook criado. Copie o segredo agora: não volta a ser mostrado.", "ok");
    carregarWebhooks();
  }, "Adicionar");
  url.focus();
});

document.getElementById("copiar-segredo").addEventListener("click", async (e) => {
  const b = e.currentTarget;
  const campo = document.getElementById("segredo-criado");
  try {
    await navigator.clipboard.writeText(campo.value);
    b.replaceChildren(icone("check"), "Copiado");
    setTimeout(() => b.replaceChildren(icone("copiar"), "Copiar"), 2000);
  } catch {
    campo.select();
    notificar("Selecione o segredo e copie com Ctrl+C.", "info");
  }
});

carregar();
carregarAtividade(true);
carregarChaves();
carregarWebhooks();
