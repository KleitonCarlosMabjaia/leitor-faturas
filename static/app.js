// Leitor de Faturas - página inicial: carregar, pesquisar, filtrar, ordenar e listar faturas

const zona = document.getElementById("zona");
const inputFicheiro = document.getElementById("ficheiro");
const fila = document.getElementById("fila");
const linhas = document.getElementById("linhas");
const filtros = document.getElementById("filtros");
const filtroFornecedor = document.getElementById("filtro-fornecedor");
const pesquisa = document.getElementById("pesquisa");
const segmentos = document.getElementById("segmentos");

// O servidor filtra por fornecedor e mês; a pesquisa, o estado e a ordem são feitos aqui,
// para a resposta ser imediata e os contadores dos estados estarem sempre certos.
const estadoLista = {
  faturas: [],
  estado: "",
  ordem: null, // { campo, sentido: 1 | -1 }
  idNova: null,
};

// ---------- Filtros ----------

function filtrosServidor() {
  const parametros = new URLSearchParams();
  for (const [nome, valor] of new FormData(filtros)) {
    if (valor) parametros.set(nome, valor);
  }
  return parametros;
}

const normalizar = (texto) =>
  (texto || "").toString().normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();

function correspondePesquisa(f, termo) {
  if (!termo) return true;
  return [f.fornecedor, f.nome_original, f.numero_fatura, f.nif_fornecedor].some((v) => normalizar(v).includes(termo));
}

function ordenar(lista) {
  const { ordem } = estadoLista;
  if (!ordem) return lista;
  const valor = (f) => {
    if (ordem.campo === "fornecedor") return normalizar(f.fornecedor || f.nome_original);
    return f[ordem.campo] ?? "";
  };
  return [...lista].sort((a, b) => {
    const [x, y] = [valor(a), valor(b)];
    if (x === y) return 0;
    if (x === "") return 1; // vazios sempre no fim
    if (y === "") return -1;
    return (x > y ? 1 : -1) * ordem.sentido;
  });
}

// ---------- Desenhar a lista ----------

function linhaEsqueleto() {
  const tr = document.createElement("tr");
  tr.setAttribute("aria-hidden", "true");
  for (const largura of ["70%", "60%", "50%", "60%", "30%"]) {
    const td = tr.insertCell();
    const barra = document.createElement("span");
    barra.className = "esqueleto";
    barra.style.width = largura;
    td.appendChild(barra);
  }
  return tr;
}

function mostrarEsqueleto() {
  linhas.replaceChildren(...Array.from({ length: 5 }, linhaEsqueleto));
}

function desenharLista() {
  const termo = normalizar(pesquisa.value.trim());
  const pesquisadas = estadoLista.faturas.filter((f) => correspondePesquisa(f, termo));

  // Contadores dos botões de estado
  const contar = (estado) => pesquisadas.filter((f) => !estado || f.estado === estado).length;
  document.getElementById("n-todas").textContent = contar("");
  document.getElementById("n-por-rever").textContent = contar("por rever");
  document.getElementById("n-confirmada").textContent = contar("confirmada");

  const visiveis = ordenar(pesquisadas.filter((f) => !estadoLista.estado || f.estado === estadoLista.estado));
  linhas.replaceChildren(...visiveis.map(criarLinha));
  mostrarVazio(visiveis.length === 0, termo);
}

function criarLinha(f) {
  const estado = descreverEstado(f);
  const nome = f.fornecedor || f.nome_original;
  const tr = document.createElement("tr");
  if (f.id === estadoLista.idNova) tr.classList.add("nova");
  // A linha inteira abre a fatura; a ligação no nome continua a existir para o teclado
  tr.addEventListener("click", (e) => {
    if (e.target.closest("a, button")) return;
    location.href = `/faturas/${f.id}`;
  });

  const celulaNome = tr.insertCell();
  celulaNome.className = "nome";
  const ligacao = document.createElement("a");
  ligacao.href = `/faturas/${f.id}`;
  ligacao.className = "titulo";
  ligacao.textContent = nome;
  celulaNome.appendChild(ligacao);

  const detalhe = document.createElement("span");
  detalhe.className = "detalhe";
  detalhe.textContent = f.numero_fatura
    ? [`Fatura ${f.numero_fatura}`, f.nif_fornecedor && `NIF ${f.nif_fornecedor}`, ORIGENS[f.origem]].filter(Boolean).join(" · ")
    : f.nome_original;
  celulaNome.appendChild(detalhe);

  if (estado.notas.length) {
    const lista = document.createElement("ul");
    lista.className = `notas ${estado.classe === "erro" ? "erro" : "aviso"}`;
    for (const nota of estado.notas) {
      const li = document.createElement("li");
      li.append(icone(estado.classe === "erro" ? "erro" : "alerta"), nota);
      lista.appendChild(li);
    }
    celulaNome.appendChild(lista);
  }

  const data = tr.insertCell();
  data.dataset.rotulo = "Emissão";
  data.textContent = formatarData(f.data_emissao);

  const total = tr.insertCell();
  total.className = "num valor-total";
  total.textContent = formatarValor(f.total, f.moeda);

  const celulaEstado = tr.insertCell();
  celulaEstado.appendChild(criarEtiqueta(estado.etiqueta, estado.classe, estado.icone));

  const acoes = tr.insertCell();
  acoes.className = "num acoes";
  if (estado.podeLer) {
    acoes.appendChild(botaoIcone("atualizar", `Ler novamente ${nome}`, (e) => lerNovamente(f, e.currentTarget)));
  }
  acoes.appendChild(botaoIcone("lixo", `Apagar ${nome}`, () => apagarFatura(f), "perigo"));
  return tr;
}

function mostrarVazio(vazio, termo) {
  const caixa = document.getElementById("vazio");
  caixa.hidden = !vazio;
  if (!vazio) return;
  const comFiltros = Boolean(termo || estadoLista.estado || filtrosServidor().toString());
  const nenhumaAinda = estadoLista.faturas.length === 0 && !filtrosServidor().toString();
  document.getElementById("vazio-titulo").textContent = nenhumaAinda ? "Ainda não há faturas" : "Nenhuma fatura encontrada";
  document.getElementById("vazio-texto").textContent = nenhumaAinda
    ? "Carregue a primeira fatura em PDF na zona acima."
    : "Experimente outra pesquisa ou limpe os filtros.";
  document.getElementById("vazio-limpar").hidden = nenhumaAinda || !comFiltros;
}

function atualizarOrdem() {
  for (const th of document.querySelectorAll("th[data-campo]")) {
    const ativo = estadoLista.ordem?.campo === th.dataset.campo;
    if (ativo) th.setAttribute("aria-sort", estadoLista.ordem.sentido === 1 ? "ascending" : "descending");
    else th.removeAttribute("aria-sort");
    const botao = th.querySelector(".ordenar");
    const nomeIcone = ativo ? (estadoLista.ordem.sentido === 1 ? "seta-cima" : "seta-baixo") : "ordenar";
    botao.querySelector(".icone").replaceWith(icone(nomeIcone));
  }
}

// ---------- Dados do servidor ----------

async function carregarFornecedores() {
  try {
    const resposta = await api("/api/fornecedores");
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

// Os botões de exportação levam os mesmos filtros que a lista (fornecedor, mês e estado)
function atualizarExportacao() {
  for (const formato of ["xlsx", "csv"]) {
    const parametros = filtrosServidor();
    if (estadoLista.estado) parametros.set("estado", estadoLista.estado);
    parametros.set("formato", formato);
    document.getElementById(`exportar-${formato}`).href = `/api/faturas/exportar?${parametros}`;
  }
}

async function carregarLista({ esqueleto = false } = {}) {
  atualizarExportacao();
  if (esqueleto) mostrarEsqueleto();
  try {
    const resposta = await api(`/api/faturas?${filtrosServidor()}`);
    if (!resposta.ok) throw new Error();
    estadoLista.faturas = await resposta.json();
    desenharLista();
  } catch {
    linhas.replaceChildren();
    notificar("Não foi possível obter a lista. Confirme que o servidor está a correr.", "erro");
  }
}

async function atualizarTudo(idNova = null) {
  estadoLista.idNova = idNova;
  await Promise.all([carregarFornecedores(), carregarLista()]);
}

// ---------- Carregar ficheiros ----------

function itemFila(ficheiro) {
  const li = document.createElement("li");
  li.append(icone("ficheiro"));
  const nome = document.createElement("span");
  nome.className = "nome-ficheiro";
  nome.textContent = ficheiro.name;
  const detalhe = document.createElement("span");
  detalhe.className = "detalhe-ficheiro";
  const estado = document.createElement("span");
  estado.className = "estado-ficheiro";
  const girador = document.createElement("span");
  girador.className = "girador";
  estado.append(girador, "Em espera");
  li.append(nome, detalhe, estado);
  fila.appendChild(li);

  return {
    aLer() {
      estado.lastChild.textContent = "A ler…";
    },
    terminar(classe, nomeIcone, texto, textoDetalhe = "", idFatura = null) {
      li.className = classe;
      detalhe.textContent = textoDetalhe;
      estado.replaceChildren(icone(nomeIcone), texto);
      if (idFatura) {
        const rever = document.createElement("a");
        rever.href = `/faturas/${idFatura}`;
        rever.textContent = "Rever";
        estado.append(" · ", rever);
      }
    },
  };
}

async function enviarFicheiros(ficheiros) {
  const lista = Array.from(ficheiros);
  if (lista.length === 0) return;

  fila.replaceChildren(); // cada envio começa com a fila limpa
  const itens = lista.map(itemFila);
  zona.setAttribute("aria-busy", "true");
  let lidas = 0;
  let problemas = 0;
  let ultimaId = null;

  for (const [indice, ficheiro] of lista.entries()) {
    const item = itens[indice];
    item.aLer();
    const dados = new FormData();
    dados.append("ficheiro", ficheiro);
    try {
      const resposta = await api("/api/faturas", { method: "POST", body: dados });
      if (resposta.status === 409) {
        item.terminar("aviso", "alerta", "Repetida", await lerErro(resposta, "Esta fatura já existe."));
        problemas += 1;
        continue;
      }
      if (!resposta.ok) {
        item.terminar("erro", "erro", "Erro", await lerErro(resposta, "Não foi possível carregar."));
        problemas += 1;
        continue;
      }
      const f = await resposta.json();
      ultimaId = f.id;
      if (f.estado_extracao === "concluida") {
        lidas += 1;
        const resumo = [f.fornecedor, formatarValor(f.total, f.moeda)].filter(Boolean).join(" · ");
        item.terminar("ok", "check", "Lida", resumo, f.id);
      } else {
        // A fatura ficou guardada, mas não foi possível ler os dados
        problemas += 1;
        item.terminar("aviso", "alerta", "Guardada, por ler", f.erro || "", f.id);
      }
    } catch {
      item.terminar("erro", "erro", "Sem ligação", "Não foi possível contactar o servidor.");
      problemas += 1;
    }
  }

  zona.removeAttribute("aria-busy");
  inputFicheiro.value = "";

  if (problemas === 0) {
    notificar(lidas === 1 ? "Fatura lida. Reveja-a e confirme." : `${lidas} faturas lidas.`, "ok",
      lidas === 1 && ultimaId ? { acao: { texto: "Rever agora →", href: `/faturas/${ultimaId}` } } : {});
  } else {
    notificar(`${lidas} lida(s), ${problemas} com problemas. Veja os detalhes na lista de ficheiros.`, "aviso");
  }
  await atualizarTudo(ultimaId);
}

// ---------- Ações das linhas ----------

async function lerNovamente(fatura, botao) {
  botao.disabled = true;
  botao.replaceChildren(Object.assign(document.createElement("span"), { className: "girador" }));
  try {
    const resposta = await api(`/api/faturas/${fatura.id}/extrair`, { method: "POST" });
    if (!resposta.ok) throw new Error(await lerErro(resposta, "Não foi possível ler a fatura."));
    const corpo = await resposta.json();
    if (corpo.estado_extracao === "concluida") notificar("Fatura lida.", "ok");
    else notificar(corpo.erro, "erro");
  } catch (erro) {
    notificar(erro.message || "Não foi possível ler a fatura.", "erro");
  }
  await atualizarTudo(fatura.id);
}

async function apagarFatura(fatura) {
  const nome = fatura.numero_fatura ? `a fatura ${fatura.numero_fatura}` : fatura.nome_original;
  const sim = await confirmar({
    titulo: "Apagar fatura?",
    texto: `Vai apagar ${nome}${fatura.fornecedor ? ` de ${fatura.fornecedor}` : ""} e o PDF. Esta ação não pode ser anulada.`,
    botao: "Apagar",
    perigo: true,
  });
  if (!sim) return;
  try {
    const resposta = await api(`/api/faturas/${fatura.id}`, { method: "DELETE" });
    if (!resposta.ok) throw new Error();
    notificar("Fatura apagada.", "ok");
  } catch {
    notificar("Não foi possível apagar a fatura.", "erro");
  }
  await atualizarTudo();
}

// ---------- Eventos ----------

// Filtros do servidor: atualizam a lista assim que mudam
filtros.addEventListener("change", (e) => {
  if (e.target !== pesquisa) carregarLista();
});
filtros.addEventListener("reset", () =>
  setTimeout(() => {
    estadoLista.estado = "";
    marcarSegmento();
    carregarLista();
  })
);
filtros.addEventListener("submit", (e) => e.preventDefault());

// Pesquisa: filtra enquanto se escreve
pesquisa.addEventListener("input", desenharLista);

// "/" leva o cursor para a pesquisa (como no GitHub ou no Gmail)
document.addEventListener("keydown", (e) => {
  if (e.key === "/" && !e.target.closest("input, select, textarea") && !document.querySelector("dialog[open]")) {
    e.preventDefault();
    pesquisa.focus();
  }
});

function marcarSegmento() {
  for (const botao of segmentos.querySelectorAll("button")) {
    botao.setAttribute("aria-pressed", String(botao.dataset.estado === estadoLista.estado));
  }
}

segmentos.addEventListener("click", (e) => {
  const botao = e.target.closest("button");
  if (!botao) return;
  estadoLista.estado = botao.dataset.estado;
  marcarSegmento();
  atualizarExportacao();
  desenharLista();
});

document.getElementById("vazio-limpar").addEventListener("click", () => {
  pesquisa.value = "";
  filtros.reset();
});

// Ordenar: 1.º clique ascendente, 2.º descendente, 3.º volta à ordem original
for (const th of document.querySelectorAll("th[data-campo]")) {
  th.querySelector(".ordenar").addEventListener("click", () => {
    const campo = th.dataset.campo;
    const atual = estadoLista.ordem;
    if (!atual || atual.campo !== campo) estadoLista.ordem = { campo, sentido: 1 };
    else if (atual.sentido === 1) estadoLista.ordem = { campo, sentido: -1 };
    else estadoLista.ordem = null;
    atualizarOrdem();
    desenharLista();
  });
}

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

// Os ícones dos cabeçalhos só existem depois de comum.js os preencher
document.addEventListener("DOMContentLoaded", atualizarOrdem);
carregarFornecedores();
carregarLista({ esqueleto: true });
