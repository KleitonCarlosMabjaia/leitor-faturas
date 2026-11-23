// Funções partilhadas pelas páginas: formatação, estados, sessão, cabeçalho,
// tema, ícones, notificações e janela de confirmação.

// ---------- Formatação ----------

const formatoData = new Intl.DateTimeFormat("pt-PT", { day: "2-digit", month: "2-digit", year: "numeric" });

function formatarData(iso) {
  // "T00:00" força a hora local; sem isso a data é lida em UTC e pode recuar um dia
  return iso ? formatoData.format(new Date(`${iso}T00:00`)) : "—";
}

function formatarValor(valor, moeda) {
  if (valor === null || valor === undefined) return "—";
  try {
    return new Intl.NumberFormat("pt-PT", { style: "currency", currency: moeda || "EUR" }).format(valor);
  } catch {
    // Código de moeda desconhecido: mostra o número e o código tal como vieram
    return `${valor.toLocaleString("pt-PT", { minimumFractionDigits: 2 })} ${moeda}`;
  }
}

const ORIGENS = {
  qr: "lida do QR",
  ia: "lida por IA",
  "qr+ia": "lida do QR e por IA",
  "qr+cache": "lida do QR, fornecedor conhecido",
};

// Traduz o estado da fatura numa etiqueta (texto, cor e ícone) e nas notas a mostrar.
// A cor nunca aparece sozinha: há sempre texto e ícone.
function descreverEstado(f) {
  switch (f.estado_extracao) {
    case "sem_texto":
      return { etiqueta: "Digitalizada", classe: "erro", icone: "erro", notas: [f.erro], podeLer: false };
    case "erro":
      return { etiqueta: "Erro na leitura", classe: "erro", icone: "erro", notas: [f.erro], podeLer: true };
    case "pendente":
      return { etiqueta: "Por ler", classe: "neutro", icone: "relogio", notas: [], podeLer: true };
  }
  const alertas = f.alertas || [];
  // Lida só pelo QR: a IA falhou, vale a pena tentar outra vez
  const podeLer = f.origem === "qr";
  if (f.estado === "confirmada") {
    return { etiqueta: "Confirmada", classe: "ok", icone: "check", notas: alertas, podeLer: false };
  }
  return alertas.length
    ? { etiqueta: "Por rever", classe: "aviso", icone: "alerta", notas: alertas, podeLer }
    : { etiqueta: "Por rever", classe: "info", icone: "relogio", notas: alertas, podeLer };
}

// Lê a mensagem de erro do FastAPI (texto simples ou lista de erros de validação)
async function lerErro(resposta, predefinida) {
  const corpo = await resposta.json().catch(() => ({}));
  if (typeof corpo.detail === "string") return corpo.detail;
  if (Array.isArray(corpo.detail)) return "Há campos com valores inválidos. Verifique as datas e os números.";
  return predefinida;
}

// ---------- Ícones (SVG desenhados à mão, sem bibliotecas) ----------

const ICONES = {
  upload: "M12 15V4M7.5 8.5 12 4l4.5 4.5M4 15v3.5A1.5 1.5 0 0 0 5.5 20h13a1.5 1.5 0 0 0 1.5-1.5V15",
  ficheiro: "M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8zM14 3v5h5",
  check: "M5 12.5 9.5 17 19 7",
  alerta: "M10.3 4.3 2.6 18a2 2 0 0 0 1.7 3h15.4a2 2 0 0 0 1.7-3L13.7 4.3a2 2 0 0 0-3.4 0zM12 9.5v4M12 17h.01",
  erro: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM9 9l6 6M15 9l-6 6",
  relogio: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM12 7.5V12l3 2",
  x: "M6 6l12 12M18 6 6 18",
  lixo: "M4 7h16M10 11v6M14 11v6M6 7l1 12.5A1.5 1.5 0 0 0 8.5 21h7a1.5 1.5 0 0 0 1.5-1.5L18 7M9 7V4.5A.5.5 0 0 1 9.5 4h5a.5.5 0 0 1 .5.5V7",
  atualizar: "M20 12a8 8 0 1 1-2.34-5.66M20 4v5h-5",
  sol: "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8zM12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41",
  lua: "M20 14.5A8.5 8.5 0 1 1 9.5 4a7 7 0 0 0 10.5 10.5z",
  pesquisa: "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM20 20l-4-4",
  sair: "M9 4H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h3M14 8l4 4-4 4M18 12H9",
  equipa: "M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM2.5 20.5v-.5a6.5 6.5 0 0 1 13 0v.5M16 3.6a4 4 0 0 1 0 7M21.5 20.5v-.5a6.5 6.5 0 0 0-3.5-5.8",
  euro: "M17.5 6.5a7 7 0 1 0 0 11M4 10h9.5M4 14h9.5",
  recibo: "M6 3h12v18l-3-2-3 2-3-2-3 2zM9 8h6M9 12h6M9 16h3",
  carteira: "M19 7V5.5A1.5 1.5 0 0 0 17.5 4H5a2 2 0 0 0 0 4h14a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H5a2 2 0 0 1-2-2V6M16 14h.01",
  percentagem: "M19 5 5 19M7 5a2 2 0 1 0 0 4 2 2 0 0 0 0-4zM17 15a2 2 0 1 0 0 4 2 2 0 0 0 0-4z",
  calendario: "M4 6.5A1.5 1.5 0 0 1 5.5 5h13A1.5 1.5 0 0 1 20 6.5V19a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1zM4 10h16M8 3v4M16 3v4",
  "seta-cima": "M12 19V5M6 11l6-6 6 6",
  "seta-baixo": "M12 5v14M6 13l6 6 6-6",
  "seta-direita": "M5 12h14M13 6l6 6-6 6",
  "seta-esquerda": "M19 12H5M11 6l-6 6 6 6",
  olho: "M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12zM12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z",
  "olho-fechado": "M3 3l18 18M10.6 5.1Q11.3 5 12 5c6.5 0 10 7 10 7a17 17 0 0 1-3.2 4.2M6.6 6.6C3.9 8.4 2 12 2 12s3.5 7 10 7c1.8 0 3.4-.5 4.8-1.3M9.9 9.9a3 3 0 0 0 4.2 4.2",
  copiar: "M9 9h10.5a.5.5 0 0 1 .5.5V20a.5.5 0 0 1-.5.5H9a.5.5 0 0 1-.5-.5V9.5A.5.5 0 0 1 9 9zM5 15h-.5a.5.5 0 0 1-.5-.5V4a.5.5 0 0 1 .5-.5H15a.5.5 0 0 1 .5.5v.5",
  descarregar: "M12 4v11M7.5 10.5 12 15l4.5-4.5M4 20h16",
  ordenar: "M8 18V6M5 9l3-3 3 3M16 6v12M13 15l3 3 3-3",
  mais: "M12 5v14M5 12h14",
  "chevron-baixo": "M6 9l6 6 6-6",
  teclado: "M3 6.5A1.5 1.5 0 0 1 4.5 5h15A1.5 1.5 0 0 1 21 6.5v11a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 17.5zM7 9h.01M11 9h.01M15 9h.01M7 13h.01M17 13h.01M8 16h8",
  livro: "M4 5.5A1.5 1.5 0 0 1 5.5 4H11v16H5.5A1.5 1.5 0 0 1 4 18.5zM20 5.5A1.5 1.5 0 0 0 18.5 4H13v16h5.5a1.5 1.5 0 0 0 1.5-1.5z",
  balanca: "M12 4v16M7 20h10M5 7h14M5 7l-3 7a3 3 0 0 0 6 0zM19 7l-3 7a3 3 0 0 0 6 0z",
  faisca: "M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8zM19 15l.7 1.8 1.8.7-1.8.7L19 20l-.7-1.8-1.8-.7 1.8-.7z",
  lista: "M9 6h11M9 12h11M9 18h11M4.5 6h.01M4.5 12h.01M4.5 18h.01",
  grafico: "M4 20V11M10 20V5M16 20v-6M2 20h20",
  enviar: "M4 12 20 4l-6 16-2.5-6.5z",
  desfazer: "M9 14 4 9l5-5M4 9h10.5a5.5 5.5 0 0 1 0 11H11",
  loja: "M4 10v10h16V10M3 10l2-6h14l2 6M3 10h18M9 20v-6h6v6",
  cadeado: "M6 11h12v9H6zM8 11V8a4 4 0 0 1 8 0v3",
  arvore: "M5 4v15a1 1 0 0 0 1 1h3M5 9h4M9 7h11v4H9zM9 16h11v4H9z",
  banco: "M3 9.5 12 4l9 5.5M4.5 10v8M9.5 10v8M14.5 10v8M19.5 10v8M3 20h18",
  venda: "M3 12.5V4h8.5L21 13.5 13.5 21zM7.5 8h.01",
  caixa: "M3 7.5 12 3l9 4.5v9L12 21l-9-4.5zM3 7.5l9 4.5 9-4.5M12 12v9",
  menu: "M4 6h16M4 12h16M4 18h16",
  inicio: "M3 10.5 12 4l9 6.5M5.5 9v11h13V9M10 20v-5h4v5",
  edificio: "M4 21V5a1 1 0 0 1 1-1h9a1 1 0 0 1 1 1v16M15 10h4a1 1 0 0 1 1 1v10M3 21h18M8 8h3M8 12h3M8 16h3",
};

function icone(nome, classe = "") {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("class", `icone ${classe}`.trim());
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  const caminho = document.createElementNS("http://www.w3.org/2000/svg", "path");
  caminho.setAttribute("d", ICONES[nome] || ICONES.ficheiro);
  svg.appendChild(caminho);
  return svg;
}

// Substitui <span data-icone="nome"></span> pelo ícone correspondente
function preencherIcones(raiz = document) {
  for (const marcador of raiz.querySelectorAll("[data-icone]")) {
    marcador.replaceWith(icone(marcador.dataset.icone, marcador.className));
  }
}

// Etiqueta de estado (ícone + texto)
function criarEtiqueta(texto, classe, nomeIcone) {
  const etiqueta = document.createElement("span");
  etiqueta.className = `etiqueta ${classe}`;
  if (nomeIcone) etiqueta.appendChild(icone(nomeIcone));
  etiqueta.append(texto);
  return etiqueta;
}

// ---------- Botões ----------

// Botão só com ícone (o texto fica para leitores de ecrã e no tooltip)
function botaoIcone(nomeIcone, rotulo, acao, classe = "") {
  const botao = document.createElement("button");
  botao.type = "button";
  botao.className = `botao-icone ${classe}`.trim();
  botao.setAttribute("aria-label", rotulo);
  botao.title = rotulo;
  botao.appendChild(icone(nomeIcone));
  botao.addEventListener("click", acao);
  return botao;
}

// Mostra um indicador de carregamento no botão enquanto o pedido decorre
function ocupado(botao, sim) {
  botao.disabled = sim;
  botao.classList.toggle("a-carregar", sim);
  if (sim) botao.setAttribute("aria-busy", "true");
  else botao.removeAttribute("aria-busy");
}

// ---------- Notificações ----------

function zonaNotificacoes() {
  let zona = document.getElementById("notificacoes");
  if (!zona) {
    zona = document.createElement("div");
    zona.id = "notificacoes";
    zona.className = "notificacoes";
    document.body.appendChild(zona);
  }
  return zona;
}

/**
 * Mostra uma notificação no canto do ecrã.
 * tipo: "ok" | "erro" | "aviso" | "info". acao: { texto, href } ou { texto, aoClicar }.
 */
function notificar(texto, tipo = "info", { acao = null, duracao = null } = {}) {
  const nomesIcone = { ok: "check", erro: "erro", aviso: "alerta", info: "relogio" };
  const aviso = document.createElement("div");
  aviso.className = `notificacao ${tipo}`;
  // Erros são anunciados de imediato pelos leitores de ecrã; o resto, quando houver uma pausa
  aviso.setAttribute("role", tipo === "erro" ? "alert" : "status");
  aviso.appendChild(icone(nomesIcone[tipo] || "relogio"));

  const corpo = document.createElement("div");
  corpo.className = "notificacao-texto";
  corpo.textContent = texto;
  if (acao) {
    const ligacao = document.createElement(acao.href ? "a" : "button");
    ligacao.className = "notificacao-acao";
    ligacao.textContent = acao.texto;
    if (acao.href) ligacao.href = acao.href;
    else {
      ligacao.type = "button";
      ligacao.addEventListener("click", () => {
        acao.aoClicar();
        fechar();
      });
    }
    corpo.appendChild(ligacao);
  }
  aviso.appendChild(corpo);

  const fecharBotao = botaoIcone("x", "Fechar notificação", () => fechar(), "pequeno");
  aviso.appendChild(fecharBotao);

  function fechar() {
    aviso.classList.add("a-sair");
    setTimeout(() => aviso.remove(), 200);
  }

  zonaNotificacoes().appendChild(aviso);
  // Os erros ficam mais tempo; com o rato em cima, a notificação não desaparece
  let temporizador = setTimeout(fechar, duracao ?? (tipo === "erro" ? 9000 : 5000));
  aviso.addEventListener("mouseenter", () => clearTimeout(temporizador));
  aviso.addEventListener("mouseleave", () => (temporizador = setTimeout(fechar, 3000)));
  return fechar;
}

// ---------- Janela de confirmação ----------

/** Pergunta antes de uma ação. Devolve uma Promise com true (confirmou) ou false. */
function confirmar({ titulo, texto, botao = "Confirmar", perigo = false }) {
  let janela = document.getElementById("janela-confirmar");
  if (!janela) {
    janela = document.createElement("dialog");
    janela.id = "janela-confirmar";
    janela.className = "janela";
    janela.setAttribute("aria-labelledby", "janela-titulo");
    janela.setAttribute("aria-describedby", "janela-texto");
    janela.innerHTML = `
      <form method="dialog">
        <h2 id="janela-titulo"></h2>
        <p id="janela-texto"></p>
        <div class="janela-botoes">
          <button value="nao" class="botao secundario">Cancelar</button>
          <button value="sim" class="botao" id="janela-sim"></button>
        </div>
      </form>`;
    document.body.appendChild(janela);
    // Clicar fora da janela também cancela
    janela.addEventListener("click", (e) => {
      if (e.target === janela) janela.close("nao");
    });
  }
  janela.querySelector("#janela-titulo").textContent = titulo;
  janela.querySelector("#janela-texto").textContent = texto;
  const sim = janela.querySelector("#janela-sim");
  sim.textContent = botao;
  sim.classList.toggle("perigo", perigo);

  return new Promise((resolver) => {
    janela.addEventListener("close", () => resolver(janela.returnValue === "sim"), { once: true });
    janela.returnValue = "nao";
    janela.showModal();
    // O foco começa em "Cancelar" nas ações perigosas, para um Enter distraído não apagar nada
    janela.querySelector(perigo ? 'button[value="nao"]' : "#janela-sim").focus();
  });
}

// ---------- Tema claro / escuro ----------

function temaEscuro() {
  const escolhido = document.documentElement.dataset.tema;
  if (escolhido) return escolhido === "escuro";
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

function alternarTema() {
  const novo = temaEscuro() ? "claro" : "escuro";
  document.documentElement.dataset.tema = novo;
  try {
    localStorage.setItem("tema", novo);
  } catch {
    // Sem armazenamento: o tema vale só para esta página
  }
  atualizarBotaoTema();
  window.dispatchEvent(new Event("temamudou"));
}

function atualizarBotaoTema() {
  const botao = document.getElementById("botao-tema");
  if (!botao) return;
  const escuro = temaEscuro();
  botao.replaceChildren(icone(escuro ? "sol" : "lua"));
  const rotulo = escuro ? "Mudar para o modo claro" : "Mudar para o modo escuro";
  botao.setAttribute("aria-label", rotulo);
  botao.title = rotulo;
}

window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
  atualizarBotaoTema();
  window.dispatchEvent(new Event("temamudou"));
});

// ---------- Sessão ----------

// Igual ao fetch, mas se a sessão tiver terminado (401) envia para a página de login
async function api(url, opcoes) {
  const resposta = await fetch(url, opcoes);
  if (resposta.status === 401) {
    location.href = `/entrar?voltar=${encodeURIComponent(location.pathname)}`;
    throw new Error("A sua sessão terminou.");
  }
  return resposta;
}

// ---------- Barra lateral (navegação da aplicação) ----------

const NOME_PRODUTO = "Dobra";

// Logótipo: um "D" partido em duas metades, como as partidas dobradas (débito e crédito)
const LOGO_SVG = `<svg viewBox="0 0 32 32" aria-hidden="true" focusable="false">
  <rect width="32" height="32" rx="8" fill="var(--logo-fundo)"/>
  <path d="M9 8h7.5a8 8 0 0 1 7.96 7.25H9z" fill="#FFFFFF"/>
  <path d="M9 16.75h15.46A8 8 0 0 1 16.5 24H9z" fill="var(--logo-metade)"/></svg>`;

const CONTAB = "/contabilidade#";
const NAVEGACAO = [
  { itens: [{ chave: "resumo", href: `${CONTAB}resumo`, texto: "Início", icone: "inicio" }] },
  { titulo: "Compras", itens: [
    { chave: "faturas", href: "/", texto: "Faturas", titulo: "Faturas de fornecedores", icone: "recibo", contador: "por_rever" },
    { chave: "por-contabilizar", href: `${CONTAB}por-contabilizar`, texto: "Por contabilizar", icone: "lista", contador: "por_contabilizar" },
    { chave: "fornecedores", href: `${CONTAB}fornecedores`, texto: "Fornecedores", icone: "loja" },
    { chave: "dashboard", href: "/dashboard", texto: "Análise de compras", icone: "grafico" },
  ] },
  { titulo: "Vendas e tesouraria", itens: [
    { chave: "vendas", href: `${CONTAB}vendas`, texto: "Vendas", icone: "venda" },
    { chave: "bancos", href: `${CONTAB}bancos`, texto: "Bancos", icone: "banco" },
  ] },
  { titulo: "Pessoal e património", itens: [
    { chave: "salarios", href: `${CONTAB}salarios`, texto: "Salários", icone: "equipa" },
    { chave: "ativos", href: `${CONTAB}ativos`, texto: "Ativos fixos", icone: "edificio" },
    { chave: "inventarios", href: `${CONTAB}inventarios`, texto: "Inventários", icone: "caixa" },
  ] },
  { titulo: "Contabilidade", itens: [
    { chave: "lancamentos", href: `${CONTAB}lancamentos`, texto: "Lançamentos", icone: "livro" },
    { chave: "balancete", href: `${CONTAB}balancete`, texto: "Balancete", icone: "balanca" },
    { chave: "extrato", href: `${CONTAB}extrato`, texto: "Extratos de conta", icone: "ficheiro" },
    { chave: "periodizacao", href: `${CONTAB}periodizacao`, texto: "Acréscimos e diferimentos", icone: "relogio" },
    { chave: "plano", href: `${CONTAB}plano`, texto: "Plano de contas", icone: "arvore" },
  ] },
  { titulo: "Relatórios e fiscal", itens: [
    { chave: "resultados", href: `${CONTAB}resultados`, texto: "Resultados", titulo: "Demonstração de resultados", icone: "euro" },
    { chave: "balanco", href: `${CONTAB}balanco`, texto: "Balanço", icone: "carteira" },
    { chave: "iva", href: `${CONTAB}iva`, texto: "IVA", icone: "percentagem" },
    { chave: "obrigacoes", href: `${CONTAB}obrigacoes`, texto: "Obrigações fiscais", icone: "calendario" },
    { chave: "fecho", href: `${CONTAB}fecho`, texto: "Fecho do exercício", icone: "cadeado" },
  ] },
];
const NAV_ASSISTENTE = { chave: "assistente", href: `${CONTAB}assistente`, texto: "Assistente IA", icone: "faisca" };
const NAV_EQUIPA = { chave: "equipa", href: "/equipa", texto: "Equipa", icone: "equipa" };

// Nome de cada área (para o título da página e do separador do navegador)
function tituloDaArea(chave) {
  for (const g of NAVEGACAO) for (const i of g.itens) if (i.chave === chave) return i.titulo || i.texto;
  return chave === "assistente" ? NAV_ASSISTENTE.texto : null;
}

// Grupo da barra lateral a que a área pertence (para as migalhas por cima do título)
function grupoDaArea(chave) {
  for (const g of NAVEGACAO) if (g.itens.some((i) => i.chave === chave)) return g.titulo || null;
  return chave === "assistente" ? "Ferramentas" : null;
}

function iniciais(nome) {
  const partes = nome.trim().split(/\s+/);
  return ((partes[0]?.[0] || "") + (partes.length > 1 ? partes.at(-1)[0] : "")).toUpperCase() || "?";
}

// Área atual: a página (body data-pagina) e, na contabilidade, a secção do endereço (#secao)
function areaAtual() {
  const pagina = document.body.dataset.pagina;
  if (pagina === "contabilidade") return decodeURIComponent(location.hash.slice(1)).split(":")[0] || "resumo";
  return pagina;
}

function marcarAreaAtual() {
  const atual = areaAtual();
  for (const a of document.querySelectorAll(".lateral-nav a[data-chave]")) {
    if (a.dataset.chave === atual) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  }
}

// Monta a barra lateral: marca, áreas agrupadas, assistente, tema e conta.
// Em ecrãs estreitos fica escondida e abre-se pelo botão de menu da barra do topo.
function montarBarra() {
  const barra = document.getElementById("barra");
  if (!barra) return;
  document.body.classList.add("com-lateral");
  barra.className = "lateral";
  barra.setAttribute("aria-label", "Navegação");
  barra.innerHTML = `
    <div class="lateral-topo">
      <a class="marca" href="/contabilidade#resumo" aria-label="${NOME_PRODUTO}, início">
        <span class="marca-logo">${LOGO_SVG}</span>
        <span class="marca-nome">${NOME_PRODUTO}</span>
      </a>
      <button type="button" class="botao-icone lateral-fechar" id="lateral-fechar" aria-label="Fechar o menu"></button>
    </div>
    <div class="lateral-empresa" id="lateral-empresa" hidden></div>
    <nav class="lateral-nav" aria-label="Principal"></nav>
    <div class="lateral-fundo">
      <nav class="lateral-nav lateral-extra" aria-label="Ferramentas"></nav>
      <div class="lateral-conta">
        <div class="conta" id="conta"></div>
        <button type="button" class="botao-icone barra-botao" id="botao-tema"></button>
      </div>
    </div>`;
  const nav = barra.querySelector(".lateral-nav");
  for (const grupo of NAVEGACAO) {
    const bloco = document.createElement("div");
    bloco.className = "lateral-grupo";
    if (grupo.titulo) {
      const t = document.createElement("p");
      t.className = "lateral-titulo";
      t.textContent = grupo.titulo;
      bloco.appendChild(t);
    }
    for (const item of grupo.itens) bloco.appendChild(ligacaoMenu(item));
    nav.appendChild(bloco);
  }
  barra.querySelector(".lateral-extra").appendChild(ligacaoMenu(NAV_ASSISTENTE));
  barra.querySelector("#lateral-fechar").appendChild(icone("x"));

  // Barra do topo, só em ecrãs estreitos: botão de menu e marca
  const topo = document.createElement("div");
  topo.className = "barra-movel";
  topo.innerHTML = `
    <button type="button" class="botao-icone" id="lateral-abrir" aria-label="Abrir o menu" aria-controls="barra" aria-expanded="false"></button>
    <a class="marca" href="/contabilidade#resumo"><span class="marca-logo">${LOGO_SVG}</span><span class="marca-nome">${NOME_PRODUTO}</span></a>`;
  topo.querySelector("#lateral-abrir").appendChild(icone("menu"));
  barra.after(topo);
  const veu = document.createElement("div");
  veu.className = "lateral-veu";
  topo.after(veu);
  const abrir = (sim) => {
    document.body.classList.toggle("menu-aberto", sim);
    topo.querySelector("#lateral-abrir").setAttribute("aria-expanded", String(sim));
    if (sim) barra.querySelector("a[aria-current]")?.focus();
  };
  topo.querySelector("#lateral-abrir").addEventListener("click", () => abrir(true));
  barra.querySelector("#lateral-fechar").addEventListener("click", () => abrir(false));
  veu.addEventListener("click", () => abrir(false));
  barra.addEventListener("click", (e) => {
    if (e.target.closest("a[href]")) abrir(false);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && document.body.classList.contains("menu-aberto")) abrir(false);
  });

  preencherIcones(barra);
  marcarAreaAtual();
  window.addEventListener("hashchange", marcarAreaAtual);
  atualizarBotaoTema();
  barra.querySelector("#botao-tema").addEventListener("click", alternarTema);
  atualizarContadores();
}

function ligacaoMenu(p) {
  const a = document.createElement("a");
  a.href = p.href;
  a.dataset.chave = p.chave;
  a.appendChild(icone(p.icone));
  const texto = document.createElement("span");
  texto.className = "lateral-texto";
  texto.textContent = p.texto;
  a.appendChild(texto);
  if (p.contador) {
    const c = document.createElement("span");
    c.className = "lateral-contador";
    c.dataset.contador = p.contador;
    a.appendChild(c);
  }
  return a;
}

// Números ao lado de "Faturas de fornecedores" (por rever) e "Por contabilizar"
async function atualizarContadores() {
  try {
    const resposta = await api("/api/faturas/contadores");
    if (!resposta.ok) return;
    const d = await resposta.json();
    for (const el of document.querySelectorAll("[data-contador]")) {
      const n = d[el.dataset.contador] || 0;
      el.textContent = n ? String(n) : "";
      el.closest("a")?.setAttribute("aria-label", `${el.closest("a").querySelector(".lateral-texto").textContent}${n ? ` (${n})` : ""}`);
    }
  } catch {
    // sem contadores a navegação continua a funcionar
  }
}

// Menu da conta: iniciais → nome, empresa, Equipa (admins) e Sair
async function iniciarConta() {
  const zona = document.getElementById("conta");
  if (!zona) return;
  let eu;
  try {
    const resposta = await api("/api/auth/eu");
    if (!resposta.ok) return;
    eu = await resposta.json();
  } catch {
    return; // sem menu de conta a página continua a funcionar
  }

  if (eu.papel === "admin" && !eu.modo_demo) {
    document.querySelector(".lateral-extra")?.appendChild(ligacaoMenu(NAV_EQUIPA));
    marcarAreaAtual();
    preencherIcones();
  }

  const empresa = document.getElementById("lateral-empresa");
  if (empresa && eu.empresa) {
    empresa.replaceChildren(Object.assign(document.createElement("span"), { className: "lateral-empresa-nome", textContent: eu.empresa }));
    empresa.title = eu.empresa;
    empresa.hidden = false;
  }

  if (eu.modo_demo) {
    zona.innerHTML = '<span class="selo-demo">Modo de demonstração</span>';
    return;
  }

  zona.innerHTML = `
    <button type="button" class="conta-botao" id="conta-botao" aria-expanded="false" aria-controls="conta-painel">
      <span class="avatar" aria-hidden="true"></span>
      <span class="conta-texto"><span class="conta-nome"></span><span class="conta-papel"></span></span>
      <span data-icone="chevron-baixo"></span>
    </button>
    <div class="conta-painel" id="conta-painel" hidden>
      <div class="conta-quem">
        <strong></strong>
        <span class="conta-email"></span>
        <span class="conta-empresa"></span>
      </div>
      <button type="button" class="conta-sair"><span data-icone="sair"></span>Terminar sessão</button>
    </div>`;
  zona.querySelector(".avatar").textContent = iniciais(eu.nome);
  zona.querySelector(".conta-nome").textContent = eu.nome;
  zona.querySelector(".conta-papel").textContent = eu.papel === "admin" ? "Administrador" : "Utilizador";
  zona.querySelector(".conta-quem strong").textContent = eu.nome;
  zona.querySelector(".conta-email").textContent = eu.email;
  zona.querySelector(".conta-empresa").textContent = eu.empresa;
  preencherIcones(zona);

  const botao = zona.querySelector("#conta-botao");
  const painel = zona.querySelector("#conta-painel");
  botao.setAttribute("aria-label", `Conta de ${eu.nome}`);
  const abrir = (sim) => {
    painel.hidden = !sim;
    botao.setAttribute("aria-expanded", String(sim));
  };
  botao.addEventListener("click", () => abrir(painel.hidden));
  document.addEventListener("click", (e) => {
    if (!zona.contains(e.target)) abrir(false);
  });
  zona.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !painel.hidden) {
      abrir(false);
      botao.focus();
    }
  });
  zona.querySelector(".conta-sair").addEventListener("click", async () => {
    await fetch("/api/auth/sair", { method: "POST" });
    location.href = "/entrar";
  });
}

// ---------- Palavras-passe (páginas de acesso) ----------

// Acrescenta um botão "mostrar/esconder" a um campo de palavra-passe
function botaoVerPalavraPasse(campo) {
  const caixa = document.createElement("div");
  caixa.className = "campo-com-botao";
  campo.replaceWith(caixa);
  caixa.appendChild(campo);
  const botao = botaoIcone("olho", "Mostrar a palavra-passe", () => {
    const visivel = campo.type === "text";
    campo.type = visivel ? "password" : "text";
    botao.replaceChildren(icone(visivel ? "olho" : "olho-fechado"));
    const rotulo = visivel ? "Mostrar a palavra-passe" : "Esconder a palavra-passe";
    botao.setAttribute("aria-label", rotulo);
    botao.title = rotulo;
    botao.setAttribute("aria-pressed", String(!visivel));
  });
  botao.setAttribute("aria-pressed", "false");
  caixa.appendChild(botao);
}

// O Live Server do VS Code (porta 5500) só serve os ficheiros, sem a API: avisa onde abrir a aplicação
function avisarLiveServer() {
  if (location.port !== "5500") return;
  notificar("Esta página foi aberta pelo Live Server, que não tem a API. Abra a aplicação no servidor FastAPI.", "aviso", {
    acao: { texto: "Abrir http://127.0.0.1:8000 →", href: "http://127.0.0.1:8000/" },
    duracao: 600000,
  });
}

document.addEventListener("DOMContentLoaded", () => {
  avisarLiveServer();
  montarBarra();
  preencherIcones();
  iniciarConta();
});
