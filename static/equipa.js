// Leitor de Faturas - equipa: convidar colegas, gerar links e desativar contas

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
    mostrarLink((await resposta.json()).link, m.nome);
    notificar("Link gerado. Os links anteriores desta pessoa deixaram de funcionar.", "ok");
  } catch (erro) {
    notificar(erro.message, "erro");
  } finally {
    ocupado(botao, false);
  }
}

async function alterar(m, alteracao, botao) {
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
    notificar(alteracao.ativo ? `Conta de ${m.nome} reativada.` : `Conta de ${m.nome} desativada.`, "ok");
    await carregar();
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
    const { membro, link } = await resposta.json();
    mostrarLink(link, membro.nome);
    notificar(`Convite criado para ${membro.nome}.`, "ok");
    formulario.reset();
    await carregar();
  } catch (erro) {
    notificar(erro.message, "erro");
  } finally {
    ocupado(botao, false);
  }
});

carregar();
