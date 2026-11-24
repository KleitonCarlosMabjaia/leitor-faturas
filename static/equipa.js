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

carregar();
carregarAtividade(true);
carregarChaves();
