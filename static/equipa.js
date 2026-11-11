// Leitor de Faturas - equipa: convidar colegas, gerar links e desativar contas

const formulario = document.getElementById("convite");
const mensagem = document.getElementById("mensagem");
const corpo = document.getElementById("membros");
const formatoAcesso = new Intl.DateTimeFormat("pt-PT", { dateStyle: "short", timeStyle: "short" });

function mostrarMensagem(texto, tipo) {
  mensagem.textContent = texto;
  mensagem.className = `mensagem ${tipo || ""}`;
}

function mostrarLink(link, para) {
  document.getElementById("texto-link").textContent = `Link para ${para}:`;
  document.getElementById("link").value = link;
  document.getElementById("caixa-link").hidden = false;
  document.getElementById("link").select();
}

document.getElementById("copiar").addEventListener("click", async () => {
  const campo = document.getElementById("link");
  try {
    await navigator.clipboard.writeText(campo.value);
    mostrarMensagem("Link copiado.", "sucesso");
  } catch {
    campo.select(); // sem acesso à área de transferência: fica selecionado para Ctrl+C
    mostrarMensagem("Selecione o link e copie com Ctrl+C.");
  }
});

function botao(texto, rotulo, acao, classe = "") {
  const b = document.createElement("button");
  b.type = "button";
  b.className = `acao ${classe}`;
  b.textContent = texto;
  b.setAttribute("aria-label", rotulo);
  b.addEventListener("click", acao);
  return b;
}

function desenhar(membros) {
  corpo.innerHTML = "";
  for (const m of membros) {
    const tr = corpo.insertRow();

    const nome = tr.insertCell();
    nome.className = "nome";
    const titulo = document.createElement("span");
    titulo.className = "titulo";
    titulo.textContent = m.nome;
    const email = document.createElement("span");
    email.className = "detalhe";
    email.textContent = m.email;
    nome.append(titulo, email);

    tr.insertCell().textContent = m.papel === "admin" ? "Administrador" : "Membro";

    const estado = tr.insertCell();
    const etiqueta = document.createElement("span");
    if (!m.ativo) {
      etiqueta.className = "etiqueta erro";
      etiqueta.textContent = "Desativada";
    } else if (m.pendente) {
      etiqueta.className = "etiqueta aviso";
      etiqueta.textContent = "Convite pendente";
    } else {
      etiqueta.className = "etiqueta ok";
      etiqueta.textContent = "Ativa";
    }
    estado.appendChild(etiqueta);
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
      acoes.appendChild(botao(textoLink, `${textoLink} para ${m.nome}`, () => gerarLink(m), "repetir"));
    }
    acoes.appendChild(
      botao(m.ativo ? "Desativar" : "Ativar", `${m.ativo ? "Desativar" : "Ativar"} ${m.nome}`, () => alterar(m, { ativo: !m.ativo }),
            m.ativo ? "apagar" : "repetir")
    );
  }
}

async function carregar() {
  try {
    const resposta = await api("/api/equipa");
    if (!resposta.ok) throw new Error(await lerErro(resposta, "Não foi possível obter a equipa."));
    desenhar(await resposta.json());
  } catch (erro) {
    mostrarMensagem(erro.message, "erro");
  }
}

async function gerarLink(m) {
  try {
    const resposta = await api(`/api/equipa/${m.id}/link`, { method: "POST" });
    if (!resposta.ok) throw new Error(await lerErro(resposta, "Não foi possível gerar o link."));
    mostrarLink((await resposta.json()).link, m.nome);
    mostrarMensagem("Link gerado. Os links anteriores desta pessoa deixaram de funcionar.", "sucesso");
  } catch (erro) {
    mostrarMensagem(erro.message, "erro");
  }
}

async function alterar(m, alteracao) {
  if (alteracao.ativo === false && !confirm(`Desativar a conta de ${m.nome}? Perde o acesso imediatamente.`)) return;
  try {
    const resposta = await api(`/api/equipa/${m.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(alteracao),
    });
    if (!resposta.ok) throw new Error(await lerErro(resposta, "Não foi possível alterar a conta."));
    mostrarMensagem("Conta atualizada.", "sucesso");
    await carregar();
  } catch (erro) {
    mostrarMensagem(erro.message, "erro");
  }
}

formulario.addEventListener("submit", async (e) => {
  e.preventDefault();
  const dados = Object.fromEntries(new FormData(formulario));
  if (!dados.nome.trim() || !dados.email.trim()) {
    mostrarMensagem("Preencha o nome e o email.", "erro");
    return;
  }
  try {
    const resposta = await api("/api/equipa", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(dados),
    });
    if (!resposta.ok) throw new Error(await lerErro(resposta, "Não foi possível criar o convite."));
    const { membro, link } = await resposta.json();
    mostrarLink(link, membro.nome);
    mostrarMensagem(`Convite criado para ${membro.nome}.`, "sucesso");
    formulario.reset();
    await carregar();
  } catch (erro) {
    mostrarMensagem(erro.message, "erro");
  }
});

carregar();
