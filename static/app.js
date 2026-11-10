// Leitor de Faturas - página inicial: carregar, filtrar e listar faturas

const zona = document.getElementById("zona");
const inputFicheiro = document.getElementById("ficheiro");
const mensagem = document.getElementById("mensagem");
const linhas = document.getElementById("linhas");
const vazio = document.getElementById("vazio");
const contagem = document.getElementById("contagem");
const filtros = document.getElementById("filtros");
const filtroFornecedor = document.getElementById("filtro-fornecedor");

function mostrarMensagem(texto, tipo) {
  mensagem.textContent = texto;
  mensagem.className = `mensagem ${tipo || ""}`;
}

function criarBotao(texto, rotulo, classe, acao) {
  const botao = document.createElement("button");
  botao.type = "button";
  botao.className = `acao ${classe}`;
  botao.textContent = texto;
  botao.setAttribute("aria-label", rotulo);
  botao.addEventListener("click", acao);
  return botao;
}

function filtrosAtivos() {
  const parametros = new URLSearchParams();
  for (const [nome, valor] of new FormData(filtros)) {
    if (valor) parametros.set(nome, valor);
  }
  return parametros;
}

function desenharLista(faturas, idNova) {
  linhas.innerHTML = "";
  for (const f of faturas) {
    const estado = descreverEstado(f);
    const tr = document.createElement("tr");
    if (f.id === idNova) tr.classList.add("nova");

    // Fornecedor: ligação para a página de revisão
    const celulaFornecedor = document.createElement("td");
    celulaFornecedor.className = "nome";
    const ligacao = document.createElement("a");
    ligacao.href = `/faturas/${f.id}`;
    ligacao.className = "titulo";
    ligacao.textContent = f.fornecedor || f.nome_original;
    celulaFornecedor.appendChild(ligacao);

    if (f.numero_fatura) {
      const detalhe = document.createElement("span");
      detalhe.className = "detalhe";
      detalhe.textContent = [`Fatura ${f.numero_fatura}`, `NIF ${f.nif_fornecedor}`, ORIGENS[f.origem]]
        .filter(Boolean)
        .join(" · ");
      celulaFornecedor.appendChild(detalhe);
    }

    if (estado.notas.length) {
      const lista = document.createElement("ul");
      lista.className = `notas ${estado.classe === "erro" ? "erro" : "aviso"}`;
      for (const nota of estado.notas) {
        const li = document.createElement("li");
        li.textContent = nota;
        lista.appendChild(li);
      }
      celulaFornecedor.appendChild(lista);
    }

    const data = document.createElement("td");
    data.textContent = formatarData(f.data_emissao);

    const total = document.createElement("td");
    total.className = "num";
    total.textContent = formatarValor(f.total, f.moeda);

    const celulaEstado = document.createElement("td");
    const etiqueta = document.createElement("span");
    etiqueta.className = `etiqueta ${estado.classe}`;
    etiqueta.textContent = estado.etiqueta;
    celulaEstado.appendChild(etiqueta);

    const acoes = document.createElement("td");
    acoes.className = "num acoes";
    const nome = f.fornecedor || f.nome_original;
    if (estado.podeLer) {
      acoes.appendChild(criarBotao("Ler", `Ler novamente ${nome}`, "repetir", () => lerNovamente(f)));
    }
    acoes.appendChild(criarBotao("Apagar", `Apagar ${nome}`, "apagar", () => apagarFatura(f)));

    tr.append(celulaFornecedor, data, total, celulaEstado, acoes);
    linhas.appendChild(tr);
  }

  const comFiltros = filtrosAtivos().toString() !== "";
  vazio.hidden = faturas.length > 0;
  vazio.textContent = comFiltros
    ? "Nenhuma fatura corresponde aos filtros."
    : "Ainda não há faturas. Carregue a primeira acima.";
  contagem.textContent = faturas.length === 1 ? "1 fatura" : `${faturas.length} faturas`;
}

async function carregarFornecedores() {
  try {
    const resposta = await fetch("/api/fornecedores");
    if (!resposta.ok) return;
    const escolhido = filtroFornecedor.value;
    filtroFornecedor.length = 1; // mantém só a opção "Todos"
    for (const f of await resposta.json()) {
      filtroFornecedor.add(new Option(f.nome || `NIF ${f.nif}`, f.id));
    }
    filtroFornecedor.value = escolhido;
  } catch {
    // O filtro é opcional: se falhar, a lista continua a funcionar
  }
}

// Os botões de exportação levam os mesmos filtros que a lista
function atualizarExportacao() {
  for (const formato of ["xlsx", "csv"]) {
    const parametros = filtrosAtivos();
    parametros.set("formato", formato);
    document.getElementById(`exportar-${formato}`).href = `/api/faturas/exportar?${parametros}`;
  }
}

async function carregarLista(idNova) {
  atualizarExportacao();
  try {
    const resposta = await fetch(`/api/faturas?${filtrosAtivos()}`);
    if (!resposta.ok) throw new Error();
    desenharLista(await resposta.json(), idNova);
  } catch {
    mostrarMensagem("Não foi possível obter a lista. Confirme que o servidor está a correr.", "erro");
  }
}

async function atualizarTudo(idNova) {
  await Promise.all([carregarFornecedores(), carregarLista(idNova)]);
}

async function enviarFicheiros(ficheiros) {
  const lista = Array.from(ficheiros);
  if (lista.length === 0) return;

  let lidas = 0;
  let ultimaId = null;
  const erros = [];
  zona.setAttribute("aria-busy", "true");

  for (const [indice, ficheiro] of lista.entries()) {
    const progresso = lista.length > 1 ? ` (${indice + 1} de ${lista.length})` : "";
    mostrarMensagem(`A ler ${ficheiro.name}${progresso}… pode demorar alguns segundos.`);
    const dados = new FormData();
    dados.append("ficheiro", ficheiro);

    try {
      const resposta = await fetch("/api/faturas", { method: "POST", body: dados });
      if (!resposta.ok) {
        erros.push(`${ficheiro.name}: ${await lerErro(resposta, "erro ao carregar")}`);
        continue;
      }
      const corpo = await resposta.json();
      ultimaId = corpo.id;
      // A fatura foi guardada, mas a leitura pode ter falhado
      if (corpo.estado_extracao === "concluida") lidas += 1;
      else erros.push(`${ficheiro.name}: ${corpo.erro}`);
    } catch {
      erros.push(`${ficheiro.name}: sem ligação ao servidor`);
    }
  }

  zona.removeAttribute("aria-busy");
  if (erros.length) {
    mostrarMensagem(erros.join(" "), "erro");
  } else {
    mostrarMensagem(lidas === 1 ? "Fatura lida. Reveja-a e confirme." : `${lidas} faturas lidas.`, "sucesso");
  }

  inputFicheiro.value = "";
  await atualizarTudo(ultimaId);
}

async function lerNovamente(fatura) {
  mostrarMensagem(`A ler ${fatura.nome_original} novamente…`);
  try {
    const resposta = await fetch(`/api/faturas/${fatura.id}/extrair`, { method: "POST" });
    if (!resposta.ok) throw new Error(await lerErro(resposta, "Não foi possível ler a fatura."));
    const corpo = await resposta.json();
    if (corpo.estado_extracao === "concluida") mostrarMensagem("Fatura lida.", "sucesso");
    else mostrarMensagem(corpo.erro, "erro");
  } catch (erro) {
    mostrarMensagem(erro.message || "Não foi possível ler a fatura.", "erro");
  }
  await atualizarTudo(fatura.id);
}

async function apagarFatura(fatura) {
  if (!confirm(`Apagar a fatura ${fatura.numero_fatura || fatura.nome_original}?`)) return;
  try {
    const resposta = await fetch(`/api/faturas/${fatura.id}`, { method: "DELETE" });
    if (!resposta.ok) throw new Error();
    mostrarMensagem("Fatura apagada.", "sucesso");
  } catch {
    mostrarMensagem("Não foi possível apagar a fatura.", "erro");
  }
  await atualizarTudo();
}

// Filtros: atualizam a lista assim que mudam
filtros.addEventListener("change", () => carregarLista());
filtros.addEventListener("reset", () => setTimeout(() => carregarLista())); // depois de o formulário limpar
filtros.addEventListener("submit", (e) => e.preventDefault());

// Escolher ficheiros pelo botão
inputFicheiro.addEventListener("change", () => enviarFicheiros(inputFicheiro.files));

// Arrastar e largar
["dragenter", "dragover"].forEach((evento) =>
  zona.addEventListener(evento, (e) => {
    e.preventDefault();
    zona.classList.add("a-arrastar");
  })
);

["dragleave", "drop"].forEach((evento) =>
  zona.addEventListener(evento, (e) => {
    e.preventDefault();
    zona.classList.remove("a-arrastar");
  })
);

zona.addEventListener("drop", (e) => enviarFicheiros(e.dataTransfer.files));

atualizarTudo();
