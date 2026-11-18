// Leitor de Faturas - contabilidade (SNC): resumo, faturas por contabilizar, diário, balancete,
// extratos, IVA, resultados, fornecedores, obrigações fiscais, plano de contas e assistente com IA.
//
// O DOM é montado com h() e textContent (nunca innerHTML com dados): nomes de fornecedores,
// descrições e respostas da IA vêm de fora e não podem injetar HTML.

// ---------- Utilitários ----------

const euros = (v) => formatarValor(v ?? 0, "EUR");
const eurosOuVazio = (v) => (v ? euros(v) : "");
const formatoMesCurto = new Intl.DateTimeFormat("pt-PT", { month: "short" });

function h(tag, props = {}, ...filhos) {
  const el = document.createElement(tag);
  for (const [chave, valor] of Object.entries(props || {})) {
    if (valor === null || valor === undefined || valor === false) continue;
    if (chave === "class") el.className = valor;
    else if (chave === "texto") el.textContent = valor;
    else if (chave.startsWith("on")) el.addEventListener(chave.slice(2), valor);
    else if (chave === "dataset") Object.assign(el.dataset, valor);
    else el.setAttribute(chave, valor === true ? "" : valor);
  }
  for (const filho of filhos.flat()) {
    if (filho === null || filho === undefined || filho === false) continue;
    el.append(filho instanceof Node ? filho : String(filho));
  }
  return el;
}

// replaceChildren nativo escreveria "null" no ecrã: os filhos vazios (condições falsas) ficam de fora
function preencher(el, ...filhos) {
  el.replaceChildren(...filhos.flat().filter((f) => f !== null && f !== undefined && f !== false));
}

function td(texto, classe = "", rotulo = null) {
  return h("td", { class: classe || null, "data-rotulo": rotulo }, texto ?? "");
}

function iso(data) {
  const p = (n) => String(n).padStart(2, "0");
  return `${data.getFullYear()}-${p(data.getMonth() + 1)}-${p(data.getDate())}`;
}

async function obter(url) {
  const resposta = await api(url);
  if (!resposta.ok) throw new Error(await lerErro(resposta, "Não foi possível carregar os dados."));
  return resposta.json();
}

async function enviar(url, corpo, metodo = "POST") {
  const resposta = await api(url, {
    method: metodo,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(corpo ?? {}),
  });
  if (!resposta.ok) throw new Error(await lerErro(resposta, "A operação falhou."));
  return resposta.status === 204 ? null : resposta.json();
}

function carregando(secao) {
  preencher(secao, h("div", { class: "cartao" },
    h("div", { class: "esqueleto", style: "height:1.25rem;width:40%;margin-bottom:1rem" }),
    h("div", { class: "esqueleto", style: "height:8rem" })));
}

function erroNaSecao(secao, erro) {
  preencher(secao, h("div", { class: "cartao" },
    h("div", { class: "vazio" },
      h("span", { class: "vazio-icone" }, icone("erro")),
      h("strong", { texto: "Não foi possível carregar" }),
      h("span", { texto: erro.message }),
      h("button", { type: "button", class: "botao secundario pequeno", onclick: () => mostrar(atual, true) }, "Tentar de novo"))));
}

function vazio(nomeIcone, titulo, texto) {
  return h("div", { class: "vazio" },
    h("span", { class: "vazio-icone" }, icone(nomeIcone)),
    h("strong", { texto: titulo }),
    texto ? h("span", { texto }) : null);
}

function botao(texto, acao, classe = "botao secundario pequeno", nomeIcone = null) {
  return h("button", { type: "button", class: classe, onclick: acao }, nomeIcone ? icone(nomeIcone) : null, texto);
}

const ORIGENS_CLASSIFICACAO = {
  regra: { texto: "Memorizada", classe: "ok", icone: "check" },
  palavras: { texto: "Pelo nome do fornecedor", classe: "info", icone: "pesquisa" },
  ia: { texto: "Sugerida pela IA", classe: "info", icone: "faisca" },
  omissao: { texto: "Sem informação", classe: "aviso", icone: "alerta" },
};

const NOMES_ORIGEM_LANCAMENTO = {
  manual: "Manual", fatura: "Fatura", pagamento: "Pagamento", estorno: "Estorno",
  apuramento: "Apuramento", assistente: "Assistente IA", banco: "Extrato bancário", venda: "Venda", recebimento: "Recebimento", inventario: "Inventário", depreciacao: "Depreciações", ativo: "Ativo fixo", fecho: "Fecho do exercício", aplicacao: "Aplicação de resultados", salarios: "Salários", salarios_pagamento: "Pagamento de salários", acrescimo: "Acréscimo", acrescimo_reversao: "Reversão de acréscimo", diferimento: "Diferimento", diferimento_reconhecimento: "Diferimentos", abertura: "Abertura",
};

// ---------- Estado ----------

let atual = "resumo";
let contas = null;          // plano de contas (cache)
let perfis = null;          // perfis de IVA (cache)
let iaDisponivel = false;
const carregadas = new Set();
const periodo = { de: null, ate: null };

async function obterContas(forcar = false) {
  if (!contas || forcar) {
    contas = await obter("/api/contabilidade/contas");
    const lista = document.getElementById("contas-movimento");
    lista.replaceChildren(...contas.filter((c) => c.movimento).map((c) => h("option", { value: c.codigo }, `${c.codigo} ${c.nome}`)));
  }
  return contas;
}

async function obterPerfis() {
  perfis = perfis || await obter("/api/contabilidade/perfis-iva");
  return perfis;
}

function nomeConta(codigo) {
  return contas?.find((c) => c.codigo === codigo)?.nome || "";
}

// ---------- Período ----------

function calcularPeriodo(atalho) {
  const hoje = new Date();
  const a = hoje.getFullYear();
  const m = hoje.getMonth();
  const t = Math.floor(m / 3);
  switch (atalho) {
    case "este-mes": return [new Date(a, m, 1), hoje];
    case "mes-anterior": return [new Date(a, m - 1, 1), new Date(a, m, 0)];
    case "este-trimestre": return [new Date(a, t * 3, 1), hoje];
    case "trimestre-anterior": return [new Date(a, t * 3 - 3, 1), new Date(a, t * 3, 0)];
    case "ano-anterior": return [new Date(a - 1, 0, 1), new Date(a - 1, 11, 31)];
    default: return [new Date(a, 0, 1), hoje];
  }
}

function iniciarPeriodo() {
  const atalho = document.getElementById("periodo-atalho");
  const de = document.getElementById("periodo-de");
  const ate = document.getElementById("periodo-ate");
  let guardado = null;
  try {
    guardado = JSON.parse(sessionStorage.getItem("contabilidade-periodo") || "null");
  } catch {
    guardado = null;
  }
  if (guardado?.atalho) atalho.value = guardado.atalho;

  function aplicar(recarregar) {
    if (atalho.value !== "personalizado") {
      const [d, a] = calcularPeriodo(atalho.value);
      de.value = iso(d);
      ate.value = iso(a);
    }
    periodo.de = de.value;
    periodo.ate = ate.value;
    try {
      sessionStorage.setItem("contabilidade-periodo", JSON.stringify({ atalho: atalho.value, de: de.value, ate: ate.value }));
    } catch {
      // sem armazenamento: o período vale só nesta visita
    }
    if (recarregar) {
      for (const s of ["lancamentos", "balancete", "extrato", "resultados"]) carregadas.delete(s);
      mostrar(atual, true);
    }
  }

  if (guardado?.atalho === "personalizado" && guardado.de && guardado.ate) {
    de.value = guardado.de;
    ate.value = guardado.ate;
  }
  aplicar(false);
  atalho.addEventListener("change", () => aplicar(true));
  for (const campo of [de, ate]) {
    campo.addEventListener("change", () => {
      if (!de.value || !ate.value) return;
      atalho.value = "personalizado";
      aplicar(true);
    });
  }
}

const qsPeriodo = () => `de=${periodo.de}&ate=${periodo.ate}`;
const textoPeriodo = () => `${formatarData(periodo.de)} a ${formatarData(periodo.ate)}`;

// ---------- Navegação entre secções ----------

const SECOES = {
  resumo: carregarResumo,
  "por-contabilizar": carregarPorContabilizar,
  vendas: carregarVendas,
  inventarios: carregarInventarios,
  ativos: carregarAtivos,
  salarios: carregarSalarios,
  periodizacao: carregarPeriodizacao,
  balanco: carregarBalanco,
  fecho: carregarFecho,
  lancamentos: carregarLancamentos,
  balancete: carregarBalancete,
  extrato: carregarExtrato,
  iva: carregarIVA,
  resultados: carregarResultados,
  fornecedores: carregarFornecedores,
  bancos: carregarBancos,
  obrigacoes: carregarObrigacoes,
  plano: carregarPlano,
  assistente: async () => {},
};

let contaExtrato = "12";

function irPara(destino) {
  if (destino.startsWith("/")) {
    location.href = destino;
    return;
  }
  location.hash = destino;
}

async function mostrar(nome, forcar = false) {
  if (!SECOES[nome]) nome = "resumo";
  atual = nome;
  for (const botaoSep of document.querySelectorAll("#separadores [role=tab]")) {
    const sim = botaoSep.dataset.sec === nome;
    botaoSep.setAttribute("aria-selected", String(sim));
    botaoSep.tabIndex = sim ? 0 : -1;
  }
  for (const secao of document.querySelectorAll("[role=tabpanel]")) secao.hidden = secao.id !== `sec-${nome}`;
  document.getElementById("periodo").hidden = ["assistente", "obrigacoes", "plano", "por-contabilizar", "iva", "fornecedores", "bancos", "inventarios", "ativos", "fecho", "salarios", "periodizacao", "balanco"].includes(nome);

  if (carregadas.has(nome) && !forcar) return;
  const secao = document.getElementById(`sec-${nome}`);
  if (nome !== "assistente") carregando(secao);
  try {
    await SECOES[nome](secao);
    carregadas.add(nome);
  } catch (erro) {
    erroNaSecao(secao, erro);
  }
}

function lerHash() {
  const [nome, parametro] = decodeURIComponent(location.hash.slice(1)).split(":");
  if (nome === "extrato" && parametro) {
    contaExtrato = parametro;
    carregadas.delete("extrato");
  }
  if (nome === "bancos" && parametro) {
    contaBanco = parametro;
    carregadas.delete("bancos");
  }
  mostrar(nome || "resumo");
}

function iniciarSeparadores() {
  const lista = document.getElementById("separadores");
  lista.addEventListener("click", (e) => {
    const b = e.target.closest("[role=tab]");
    if (b) location.hash = b.dataset.sec;
  });
  // Setas esquerda/direita entre separadores (padrão ARIA de tabs)
  lista.addEventListener("keydown", (e) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) return;
    const botoes = [...lista.querySelectorAll("[role=tab]")];
    let i = botoes.indexOf(document.activeElement);
    if (e.key === "ArrowRight") i = (i + 1) % botoes.length;
    else if (e.key === "ArrowLeft") i = (i - 1 + botoes.length) % botoes.length;
    else if (e.key === "Home") i = 0;
    else i = botoes.length - 1;
    botoes[i].focus();
    location.hash = botoes[i].dataset.sec;
    e.preventDefault();
  });
  window.addEventListener("hashchange", lerHash);
}

function invalidar(...nomes) {
  for (const n of nomes) carregadas.delete(n);
}

// ---------- Resumo ----------

function indicador(rotulo, valor, nota, nomeIcone, destaque = false) {
  return h("div", { class: `indicador${destaque ? " destaque" : ""}` },
    h("div", { class: "indicador-topo" },
      h("span", { class: "indicador-rotulo", texto: rotulo }),
      h("span", { class: "indicador-icone" }, icone(nomeIcone))),
    h("span", { class: "indicador-valor", texto: valor }),
    h("span", { class: "indicador-nota" }, nota));
}

function listaPrazos(obrigacoes) {
  if (!obrigacoes.length) return h("p", { class: "ajuda", texto: "Sem prazos nas próximas semanas." });
  return h("ul", { class: "prazos" }, obrigacoes.map((o) => {
    const data = new Date(`${o.prazo}T00:00`);
    const quando = o.dias === 0 ? "hoje" : o.dias === 1 ? "amanhã" : `daqui a ${o.dias} dias`;
    return h("li", { class: o.dias <= 7 ? "urgente" : null },
      h("span", { class: "prazo-data", "aria-hidden": "true" },
        h("b", { texto: data.getDate() }), h("span", { texto: formatoMesCurto.format(data).replace(".", "") })),
      h("div", {},
        h("strong", { texto: o.nome }),
        h("small", { texto: `${o.periodo} · até ${formatarData(o.prazo)} (${quando})` })));
  }));
}

async function carregarResumo(secao) {
  const d = await obter("/api/contabilidade/resumo");
  iaDisponivel = d.ia_disponivel;
  atualizarContador(d.por_contabilizar.quantidade);

  const iva = d.iva_periodo;
  const notaIva = iva.saldo >= 0 ? "a pagar (estimativa, até hoje)" : "a recuperar (estimativa, até hoje)";
  const pc = d.por_contabilizar;
  const resultado = d.resultado_ano.valor;

  const alertas = d.alertas.length
    ? h("ul", { class: "lista-alertas" }, d.alertas.map((a) => {
      const nomes = { erro: "erro", aviso: "alerta", info: "relogio" };
      return h("li", { class: `alerta ${a.nivel}` },
        icone(nomes[a.nivel] || "relogio"),
        h("div", {}, h("strong", { texto: a.titulo }), h("p", { texto: a.texto })),
        a.acao ? botao(a.acao.texto, () => irPara(a.acao.destino)) : null);
    }))
    : vazio("check", "Tudo em ordem", "Não há nada a pedir a sua atenção.");

  preencher(secao, 
    h("section", { class: "indicadores", "aria-label": "Indicadores" },
      indicador("Por contabilizar", String(pc.quantidade),
        pc.quantidade ? `faturas confirmadas · ${euros(pc.total)}` : "todas as faturas confirmadas estão lançadas", "recibo", true),
      indicador(`IVA ${d.empresa.regime_iva === "mensal" ? "do mês" : "do trimestre"}`, euros(Math.abs(iva.saldo)), notaIva, "percentagem"),
      indicador(`Resultado de ${new Date().getFullYear()}`, euros(resultado),
        resultado >= 0 ? "lucro antes de impostos (aproximado)" : "prejuízo antes de impostos (aproximado)", "grafico"),
      indicador("Dívida a fornecedores", euros(d.divida_fornecedores.total),
        d.divida_fornecedores.antiguidade.mais_90 ? `${euros(d.divida_fornecedores.antiguidade.mais_90)} vencidos há mais de 90 dias` : "saldo das contas 221 e 271", "carteira")),
    h("div", { class: "resumo-grelha" },
      h("section", { class: "cartao", "aria-labelledby": "titulo-alertas" },
        h("div", { class: "cartao-cabecalho" }, h("h2", { id: "titulo-alertas", texto: "Verificações automáticas" }),
          h("span", { class: "ajuda", texto: d.empresa.fechado_ate ? `Período fechado até ${formatarData(d.empresa.fechado_ate)}` : "Nenhum período fechado" })),
        alertas),
      h("section", { class: "cartao", "aria-labelledby": "titulo-prazos" },
        h("div", { class: "cartao-cabecalho" }, h("h2", { id: "titulo-prazos", texto: "Próximos prazos" }),
          botao("Ver todos", () => irPara("obrigacoes"), "botao fantasma pequeno")),
        listaPrazos(d.obrigacoes),
        h("p", { class: "nota-rodape", texto: d.aviso_obrigacoes }))),
  );
}

function atualizarContador(n) {
  document.getElementById("n-por-contabilizar").textContent = n ? String(n) : "";
}

// ---------- Por contabilizar ----------

function tabelaLinhas(linhas, comDescricao = true) {
  const totalD = linhas.reduce((s, l) => s + l.debito, 0);
  const totalC = linhas.reduce((s, l) => s + l.credito, 0);
  return h("div", { class: "tabela-caixa" }, h("table", { class: "tabela razao" },
    h("thead", {}, h("tr", {},
      h("th", { scope: "col", texto: "Conta" }), h("th", { scope: "col", texto: "Descrição" }),
      h("th", { scope: "col", class: "num", texto: "Débito" }), h("th", { scope: "col", class: "num", texto: "Crédito" }))),
    h("tbody", {}, linhas.map((l) => h("tr", {},
      h("td", { class: "conta-codigo" }, h("a", { href: `#extrato:${l.conta}`, texto: l.conta })),
      // Nas subcontas de fornecedor a descrição é o próprio nome: não se repete
      td(comDescricao && l.descricao && l.descricao !== l.nome ? `${l.nome} · ${l.descricao}` : l.nome,
        l.credito ? "credito" : ""),
      td(eurosOuVazio(l.debito), "num"), td(eurosOuVazio(l.credito), "num")))),
    h("tfoot", {}, h("tr", {}, td(""), td("Total"), td(euros(totalD), "num"), td(euros(totalC), "num")))));
}

function etiquetaOrigem(origem, confianca) {
  const o = ORIGENS_CLASSIFICACAO[origem] || ORIGENS_CLASSIFICACAO.omissao;
  const etiqueta = criarEtiqueta(o.texto, o.classe, o.icone);
  if (confianca && origem !== "regra") etiqueta.title = `Confiança ${confianca}`;
  return etiqueta;
}

function seletorConta(id, valor) {
  const opcoes = contas.filter((c) => c.movimento && !c.nif && "3456".includes(c.codigo[0]));
  return h("select", { id }, opcoes.map((c) => {
    const o = h("option", { value: c.codigo }, `${c.codigo} · ${c.nome}`);
    if (c.codigo === valor) o.selected = true;
    return o;
  }));
}

function seletorPerfil(id, valor) {
  return h("select", { id }, perfis.map((p) => {
    const o = h("option", { value: p.chave, title: p.explicacao }, `${p.nome}`);
    if (p.chave === valor) o.selected = true;
    return o;
  }));
}

const REGIMES_IVA = {
  nacional: "Nacional (IVA do fornecedor)",
  intracomunitario_servicos: "Serviços de outro Estado-membro (autoliquidação)",
  intracomunitario_bens: "Aquisição intracomunitária de bens",
  autoliquidacao: "Inversão do sujeito passivo (ex.: construção civil)",
};

function cartaoProposta(p, aoContabilizar) {
  const f = p.fatura;
  const c = p.classificacao;
  const idConta = `conta-${f.id}`;
  const idPerfil = `perfil-${f.id}`;
  const selConta = seletorConta(idConta, c.conta);
  const selPerfil = seletorPerfil(idPerfil, c.perfil_iva);
  const idRegime = `regime-${f.id}`;
  const selRegime = h("select", { id: idRegime }, Object.entries(REGIMES_IVA).map(([v, t]) => {
    const o = h("option", { value: v }, t);
    if (v === c.regime_iva) o.selected = true;
    return o;
  }));
  // Só faz sentido escolher outro regime quando o fornecedor não liquidou IVA
  const campoRegime = h("div", { class: "campo", hidden: Boolean(f.total_iva) && c.regime_iva === "nacional" },
    h("label", { for: idRegime, texto: "Regime do IVA" }), selRegime);
  const explicacao = h("p", { class: "explicacao" }, icone("faisca"), h("span", { texto: c.explicacao }));
  const avisos = c.alertas.length ? h("ul", { class: "avisos" }, c.alertas.map((a) => h("li", { texto: a }))) : null;
  const origem = h("span", {}, etiquetaOrigem(c.origem, c.confianca));
  const resumoLinhas = h("summary", { texto: "Ver o lançamento proposto" });
  const detalhe = h("details", {}, resumoLinhas, tabelaLinhas(p.linhas));
  let alterado = false;
  const marcarAlterado = () => {
    alterado = true;
    resumoLinhas.textContent = "Ver o lançamento proposto (calculado com a sugestão inicial; ao lançar usa a sua escolha)";
  };
  selConta.addEventListener("change", marcarAlterado);
  selPerfil.addEventListener("change", marcarAlterado);
  selRegime.addEventListener("change", marcarAlterado);

  const botaoLancar = h("button", { type: "button", class: "botao" }, icone("check"), "Contabilizar");
  botaoLancar.addEventListener("click", async () => {
    ocupado(botaoLancar, true);
    try {
      const corpo = alterado || c.origem !== "regra" ? { conta_codigo: selConta.value, perfil_iva: selPerfil.value } : {};
      corpo.regime_iva = selRegime.value;
      const la = await enviar(`/api/contabilidade/faturas/${f.id}/contabilizar`, corpo);
      notificar(`Lançado: ${la.referencia} (${f.fornecedor || "fornecedor"}).`, "ok");
      aoContabilizar(cartao);
    } catch (erro) {
      notificar(erro.message, "erro");
      ocupado(botaoLancar, false);
    }
  });

  let botaoIA = null;
  if (iaDisponivel) {
    botaoIA = h("button", { type: "button", class: "botao secundario", title: "A IA lê a fatura e sugere a conta e o tratamento do IVA" }, icone("faisca"), "Sugerir com IA");
    botaoIA.addEventListener("click", async () => {
      ocupado(botaoIA, true);
      try {
        const r = await obter(`/api/contabilidade/faturas/${f.id}?ia=1`);
        if (r.erro) throw new Error(r.erro);
        const n = r.proposta.classificacao;
        selConta.value = n.conta;
        selPerfil.value = n.perfil_iva;
        explicacao.lastChild.textContent = n.explicacao;
        origem.replaceChildren(etiquetaOrigem(n.origem, n.confianca));
        detalhe.replaceChildren(resumoLinhas, tabelaLinhas(r.proposta.linhas));
        resumoLinhas.textContent = "Ver o lançamento proposto";
        alterado = true;
      } catch (erro) {
        notificar(erro.message, "erro");
      } finally {
        ocupado(botaoIA, false);
      }
    });
  }

  const cartao = h("article", { class: "proposta", dataset: { id: f.id } },
    h("div", { class: "proposta-topo" },
      h("div", {},
        h("h3", {}, h("a", { href: `/faturas/${f.id}`, texto: f.fornecedor || `NIF ${f.nif || "—"}` })),
        h("p", { class: "subtitulo", texto: `${f.documento} · ${formatarData(f.data_emissao)} · ${euros(f.total)}${f.paga ? " · paga" : ""}` })),
      origem),
    h("div", { class: "proposta-escolha" },
      h("div", { class: "campo" }, h("label", { for: idConta, texto: "Conta de gasto / compra" }), selConta),
      h("div", { class: "campo" }, h("label", { for: idPerfil, texto: "IVA (art. 21.º CIVA)" }), selPerfil),
      campoRegime,
      h("div", { class: "cabecalho-acoes" }, botaoIA, botaoLancar)),
    explicacao, avisos, detalhe);
  return cartao;
}

async function carregarPorContabilizar(secao) {
  const [propostas, saude] = await Promise.all([
    obter("/api/contabilidade/por-contabilizar"), obter("/api/saude"), obterContas(), obterPerfis(),
  ]);
  iaDisponivel = saude.ia_configurada;
  atualizarContador(propostas.length);
  if (!propostas.length) {
    preencher(secao, h("div", { class: "cartao" },
      vazio("check", "Nada por contabilizar", "Todas as faturas confirmadas já estão lançadas. As faturas por rever aparecem aqui depois de confirmadas.")));
    return;
  }

  const lista = h("div", {});
  const restantes = () => lista.querySelectorAll(".proposta").length;
  const titulo = h("h2", { texto: "" });
  const atualizarTitulo = () => {
    const n = restantes();
    titulo.textContent = `${n} fatura${n === 1 ? "" : "s"} por contabilizar`;
    atualizarContador(n);
    if (!n) lista.replaceChildren(vazio("check", "Tudo contabilizado", "Bom trabalho!"));
  };
  const aoContabilizar = (cartao) => {
    cartao.remove();
    atualizarTitulo();
    invalidar("resumo", "lancamentos", "balancete", "iva", "resultados", "fornecedores", "extrato");
  };
  for (const p of propostas) lista.append(cartaoProposta(p, aoContabilizar));

  const memorizadas = propostas.filter((p) => p.classificacao.origem === "regra").length;
  const botaoLote = h("button", { type: "button", class: "botao" }, icone("check"), "Contabilizar todas com a sugestão");
  botaoLote.addEventListener("click", async () => {
    const ids = [...lista.querySelectorAll(".proposta")].map((c) => c.dataset.id);
    const ok = await confirmar({
      titulo: "Contabilizar todas?",
      texto: `Vão ser lançadas ${ids.length} faturas com a conta e o IVA sugeridos em cada uma. Pode estornar qualquer lançamento depois.`,
      botao: "Contabilizar",
    });
    if (!ok) return;
    ocupado(botaoLote, true);
    try {
      const r = await enviar("/api/contabilidade/contabilizar-lote", { ids });
      for (const res of r.resultados) if (res.ok) lista.querySelector(`.proposta[data-id="${res.id}"]`)?.remove();
      const falhas = r.resultados.filter((x) => !x.ok);
      notificar(`${r.contabilizadas} fatura${r.contabilizadas === 1 ? "" : "s"} contabilizada${r.contabilizadas === 1 ? "" : "s"}.`
        + (falhas.length ? ` ${falhas.length} com erro: ${falhas[0].erro}` : ""), falhas.length ? "aviso" : "ok");
      atualizarTitulo();
      invalidar("resumo", "lancamentos", "balancete", "iva", "resultados", "fornecedores", "extrato");
    } catch (erro) {
      notificar(erro.message, "erro");
    } finally {
      ocupado(botaoLote, false);
    }
  });

  preencher(secao, h("section", { class: "cartao" },
    h("div", { class: "cartao-cabecalho" }, titulo, h("div", { class: "barra-lote" },
      h("span", { class: "ajuda", texto: `${memorizadas} com classificação memorizada` }), botaoLote)),
    h("p", { class: "ajuda", texto: "A conta e o IVA são sugeridos pelo que a aplicação aprendeu com cada fornecedor, pelo nome do fornecedor ou pela IA. O que escolher fica memorizado para as próximas faturas do mesmo NIF." }),
    lista));
  atualizarTitulo();
}

// ---------- Lançamentos (diário) ----------

function cartaoLancamento(la, aoMudar) {
  const estornado = la.estado === "estornado";
  const etiquetas = [criarEtiqueta(NOMES_ORIGEM_LANCAMENTO[la.origem] || la.origem, la.origem === "estorno" ? "aviso" : "neutro")];
  if (estornado) etiquetas.push(criarEtiqueta("Estornado", "erro", "desfazer"));
  const acoes = [];
  if (la.fatura_id) acoes.push(h("a", { class: "botao fantasma pequeno", href: `/faturas/${la.fatura_id}` }, icone("ficheiro"), "Fatura"));
  if (!estornado && !la.estorno_de_id) {
    acoes.push(botao("Estornar", async (e) => {
      const alvo = e.currentTarget;  // depois do await, e.currentTarget já é null
      const ok = await confirmar({
        titulo: `Estornar ${la.referencia}?`,
        texto: "É criado um lançamento de sinal contrário que anula este. O original fica no histórico, como exige a lei.",
        botao: "Estornar", perigo: true,
      });
      if (!ok) return;
      ocupado(alvo, true);
      try {
        const est = await enviar(`/api/contabilidade/lancamentos/${la.id}/estornar`);
        notificar(`Estornado com ${est.referencia}.`, "ok");
        aoMudar();
      } catch (erro) {
        notificar(erro.message, "erro");
        ocupado(alvo, false);
      }
    }, "botao fantasma pequeno", "desfazer"));
  }
  return h("article", { class: `lancamento${estornado ? " estornado" : ""}` },
    h("div", { class: "lancamento-topo" },
      h("span", { class: "ref", texto: la.referencia }),
      h("time", { datetime: la.data, texto: formatarData(la.data) }),
      h("span", { class: "desc", texto: la.descricao }),
      la.documento ? h("span", { class: "ajuda", texto: la.documento }) : null,
      ...etiquetas, ...acoes),
    tabelaLinhas(la.linhas),
    la.notas.length ? h("ul", { class: "lancamento-notas" }, la.notas.map((n) => h("li", { texto: n }))) : null);
}

async function carregarLancamentos(secao) {
  await obterContas();
  const filtroDiario = h("select", { id: "filtro-diario" },
    h("option", { value: "" }, "Todos os diários"),
    ...Object.entries({ COM: "Compras", VEN: "Vendas", BAN: "Bancos", CX: "Caixa", OD: "Operações diversas" })
      .map(([k, v]) => h("option", { value: k }, `${k} · ${v}`)));
  const pesquisa = h("input", { type: "search", id: "filtro-pesquisa", placeholder: "Descrição ou documento", autocomplete: "off" });
  const lista = h("div", { "aria-live": "polite" });
  const resumo = h("span", { class: "ajuda" });

  async function recarregar() {
    lista.replaceChildren(h("div", { class: "esqueleto", style: "height:6rem" }));
    const params = new URLSearchParams({ de: periodo.de, ate: periodo.ate });
    if (filtroDiario.value) params.set("diario", filtroDiario.value);
    if (pesquisa.value.trim()) params.set("pesquisa", pesquisa.value.trim());
    try {
      const dados = await obter(`/api/contabilidade/lancamentos?${params}`);
      resumo.textContent = `${dados.length} lançamento${dados.length === 1 ? "" : "s"} · ${textoPeriodo()}${dados.length >= 500 ? " (mostra os 500 mais recentes)" : ""}`;
      if (!dados.length) {
        lista.replaceChildren(vazio("lista", "Sem lançamentos", "Não há lançamentos com estes filtros no período escolhido."));
        return;
      }
      // Mostra 30 de cada vez: um ano de lançamentos numa só página seria ilegível
      const aoMudar = () => { invalidar("resumo", "balancete", "iva", "resultados", "fornecedores", "extrato"); recarregar(); };
      let mostrados = 0;
      const mais = h("button", { type: "button", class: "botao secundario largo" });
      const mostrarMais = () => {
        const proximos = dados.slice(mostrados, mostrados + 30);
        mais.before(...proximos.map((la) => cartaoLancamento(la, aoMudar)));
        mostrados += proximos.length;
        mais.hidden = mostrados >= dados.length;
        mais.textContent = `Mostrar mais (${dados.length - mostrados} restantes)`;
      };
      mais.addEventListener("click", mostrarMais);
      lista.replaceChildren(mais);
      mostrarMais();
    } catch (erro) {
      lista.replaceChildren(vazio("erro", "Erro", erro.message));
    }
  }

  let temporizador;
  filtroDiario.addEventListener("change", recarregar);
  pesquisa.addEventListener("input", () => {
    clearTimeout(temporizador);
    temporizador = setTimeout(recarregar, 300);
  });

  preencher(secao, h("section", { class: "cartao" },
    h("div", { class: "cartao-cabecalho" }, h("h2", { texto: "Diário" }),
      botao("Novo lançamento", () => abrirEditor({}, recarregar), "botao", "mais")),
    h("div", { class: "filtros-linha" },
      h("div", { class: "campo" }, h("label", { for: "filtro-diario", texto: "Diário" }), filtroDiario),
      h("div", { class: "campo largo" }, h("label", { for: "filtro-pesquisa", texto: "Pesquisar" }), pesquisa)),
    resumo, lista));
  await recarregar();
}

// ---------- Editor de lançamento manual ----------

function linhaEditor(dados = {}) {
  const conta = h("input", { type: "text", list: "contas-movimento", value: dados.conta || "", "aria-label": "Conta", placeholder: "Ex.: 6261", inputmode: "numeric", autocomplete: "off" });
  const debito = h("input", { type: "number", min: "0", step: "0.01", value: dados.debito || "", "aria-label": "Débito" });
  const credito = h("input", { type: "number", min: "0", step: "0.01", value: dados.credito || "", "aria-label": "Crédito" });
  // Escrever num lado limpa o outro: cada linha é só débito ou só crédito
  debito.addEventListener("input", () => { if (debito.value) credito.value = ""; atualizarTotais(); });
  credito.addEventListener("input", () => { if (credito.value) debito.value = ""; atualizarTotais(); });
  conta.addEventListener("change", () => { conta.value = conta.value.split(" ")[0]; conta.title = nomeConta(conta.value); });
  const tr = h("tr", {}, h("td", {}, conta), h("td", {}, debito), h("td", {}, credito),
    h("td", {}, botaoIcone("lixo", "Remover a linha", () => { tr.remove(); atualizarTotais(); }, "pequeno perigo")));
  return tr;
}

function lerLinhasEditor() {
  return [...document.querySelectorAll("#lanc-linhas tr")].map((tr) => {
    const [conta, debito, credito] = tr.querySelectorAll("input");
    return { conta: conta.value.trim(), debito: Number(debito.value) || 0, credito: Number(credito.value) || 0 };
  }).filter((l) => l.conta || l.debito || l.credito);
}

function atualizarTotais() {
  const linhas = lerLinhasEditor();
  const d = linhas.reduce((s, l) => s + l.debito, 0);
  const c = linhas.reduce((s, l) => s + l.credito, 0);
  document.getElementById("lanc-total-d").textContent = euros(d);
  document.getElementById("lanc-total-c").textContent = euros(c);
  const dif = document.getElementById("lanc-diferenca");
  const diferenca = Math.round((d - c) * 100) / 100;
  dif.className = `diferenca ${diferenca === 0 && d > 0 ? "ok" : "erro"}`;
  dif.textContent = diferenca === 0 ? (d > 0 ? "Equilibrado" : "") : `Diferença ${euros(Math.abs(diferenca))}`;
}

let aoGravarEditor = null;

function abrirEditor(proposta, depois) {
  obterContas().catch(() => {});  // sugestões de contas no campo "Conta"
  const janela = document.getElementById("janela-lancamento");
  document.getElementById("titulo-lancamento").textContent = proposta.origem === "assistente" ? "Rever proposta do assistente" : "Novo lançamento";
  document.getElementById("lanc-diario").value = proposta.diario || "OD";
  document.getElementById("lanc-data").value = proposta.data || iso(new Date());
  document.getElementById("lanc-documento").value = proposta.documento || "";
  document.getElementById("lanc-descricao").value = proposta.descricao || "";
  const corpo = document.getElementById("lanc-linhas");
  const linhas = proposta.linhas?.length ? proposta.linhas : [{}, {}];
  corpo.replaceChildren(...linhas.map((l) => linhaEditor(l)));
  document.getElementById("lanc-erro").hidden = true;
  janela.dataset.origem = proposta.origem || "manual";
  aoGravarEditor = depois;
  atualizarTotais();
  janela.showModal();
  document.getElementById("lanc-descricao").focus();
}

function iniciarEditor() {
  const janela = document.getElementById("janela-lancamento");
  document.getElementById("lanc-mais").addEventListener("click", () => {
    const tr = linhaEditor();
    document.getElementById("lanc-linhas").append(tr);
    tr.querySelector("input").focus();
  });
  document.getElementById("lanc-cancelar").addEventListener("click", () => janela.close());
  document.getElementById("form-lancamento").addEventListener("submit", async (e) => {
    e.preventDefault();
    const erro = document.getElementById("lanc-erro");
    const gravar = document.getElementById("lanc-gravar");
    const corpo = {
      diario: document.getElementById("lanc-diario").value,
      data: document.getElementById("lanc-data").value,
      documento: document.getElementById("lanc-documento").value.trim() || null,
      descricao: document.getElementById("lanc-descricao").value.trim(),
      linhas: lerLinhasEditor(),
      origem: janela.dataset.origem === "assistente" ? "assistente" : "manual",
    };
    if (!corpo.descricao || !corpo.data) {
      erro.hidden = false;
      erro.replaceChildren(icone("erro"), "Preencha a data e a descrição.");
      return;
    }
    ocupado(gravar, true);
    try {
      const la = await enviar("/api/contabilidade/lancamentos", corpo);
      janela.close();
      notificar(`Lançado: ${la.referencia}.`, "ok");
      invalidar("resumo", "lancamentos", "balancete", "iva", "resultados", "fornecedores", "extrato");
      aoGravarEditor?.(la);
    } catch (falha) {
      erro.hidden = false;
      erro.replaceChildren(icone("erro"), falha.message);
    } finally {
      ocupado(gravar, false);
    }
  });
}

// ---------- Balancete ----------

async function carregarBalancete(secao) {
  const d = await obter(`/api/contabilidade/balancete?${qsPeriodo()}`);
  const nivel = h("select", { id: "balancete-nivel" },
    h("option", { value: "todas" }, "Todas as contas"),
    h("option", { value: "2" }, "Classes e contas de 2 dígitos"),
    h("option", { value: "3" }, "Até 3 dígitos"),
    h("option", { value: "movimento" }, "Só contas de movimento"));
  const corpo = h("tbody", {});

  function desenhar() {
    const filtro = nivel.value;
    const linhas = d.linhas.filter((l) => filtro === "todas" || (filtro === "movimento" ? l.movimento : l.nivel <= Number(filtro)));
    corpo.replaceChildren(...linhas.map((l) => {
      const recuo = Math.min(l.nivel - 1, 3);
      const tr = h("tr", { class: l.nivel === 1 ? "agregadora classe" : l.movimento ? null : "agregadora", tabindex: "0", title: "Abrir o extrato desta conta" },
        h("td", { class: "conta-codigo" }, l.codigo),
        h("td", { class: recuo ? `recuo-${recuo}` : null, texto: l.nome }),
        td(eurosOuVazio(l.debito_periodo), "num"), td(eurosOuVazio(l.credito_periodo), "num"),
        td(eurosOuVazio(l.debito_acumulado), "num"), td(eurosOuVazio(l.credito_acumulado), "num"),
        td(eurosOuVazio(l.saldo_devedor), "num"), td(eurosOuVazio(l.saldo_credor), "num"));
      const abrir = () => irPara(`extrato:${l.codigo}`);
      tr.addEventListener("click", abrir);
      tr.addEventListener("keydown", (e) => { if (e.key === "Enter") abrir(); });
      return tr;
    }));
  }
  nivel.addEventListener("change", desenhar);
  desenhar();

  const t = d.totais;
  const estado = d.equilibrado
    ? criarEtiqueta("Débito = Crédito", "ok", "check")
    : criarEtiqueta("Desequilibrado", "erro", "erro");
  preencher(secao, h("section", { class: "cartao" },
    h("div", { class: "cartao-cabecalho" },
      h("div", {}, h("h2", { texto: "Balancete analítico" }), h("p", { class: "subtitulo", texto: textoPeriodo() })),
      h("div", { class: "cabecalho-acoes" }, estado,
        h("div", { class: "campo", style: "margin:0" }, h("label", { for: "balancete-nivel", class: "visualmente-oculto", texto: "Nível de detalhe" }), nivel),
        h("a", { class: "botao secundario", href: `/api/contabilidade/balancete/exportar?${qsPeriodo()}`, download: "" }, icone("descarregar"), "Excel"))),
    d.linhas.length ? h("div", { class: "tabela-caixa" }, h("table", { class: "tabela razao clicavel" },
      h("caption", { class: "visualmente-oculto", texto: "Balancete. Clique numa conta para ver o extrato." }),
      h("thead", {}, h("tr", {},
        h("th", { scope: "col", texto: "Conta" }), h("th", { scope: "col", texto: "Descrição" }),
        h("th", { scope: "col", class: "num", texto: "Débito período" }), h("th", { scope: "col", class: "num", texto: "Crédito período" }),
        h("th", { scope: "col", class: "num", texto: "Débito acum." }), h("th", { scope: "col", class: "num", texto: "Crédito acum." }),
        h("th", { scope: "col", class: "num", texto: "Saldo devedor" }), h("th", { scope: "col", class: "num", texto: "Saldo credor" }))),
      corpo,
      h("tfoot", {}, h("tr", {}, td(""), td("Total"),
        td(euros(t.debito_periodo), "num"), td(euros(t.credito_periodo), "num"),
        td(euros(t.debito_acumulado), "num"), td(euros(t.credito_acumulado), "num"),
        td(euros(t.saldo_devedor), "num"), td(euros(t.saldo_credor), "num")))))
      : vazio("balanca", "Sem movimentos", "Ainda não há lançamentos até à data final escolhida."),
    h("p", { class: "nota-rodape", texto: "Acumulado: nas classes 1 a 5 e 8 desde o início; nas classes 6 e 7 desde 1 de janeiro do ano da data final." })));
}

// ---------- Extratos ----------

async function carregarExtrato(secao) {
  await obterContas();
  const campo = h("input", { type: "text", id: "extrato-conta", list: "contas-todas", value: contaExtrato, inputmode: "numeric", autocomplete: "off", required: true });
  const listaTodas = h("datalist", { id: "contas-todas" }, contas.map((c) => h("option", { value: c.codigo }, `${c.codigo} ${c.nome}`)));
  const resultado = h("div", {});

  async function consultar() {
    const codigo = campo.value.split(" ")[0].trim();
    if (!/^\d+$/.test(codigo)) return;
    contaExtrato = codigo;
    resultado.replaceChildren(h("div", { class: "esqueleto", style: "height:8rem" }));
    try {
      const d = await obter(`/api/contabilidade/extrato?conta=${codigo}&${qsPeriodo()}`);
      const linhas = d.movimentos.map((m) => h("tr", { class: m.estornado ? "estornado" : null },
        td(formatarData(m.data)), h("td", { class: "conta-codigo", texto: m.referencia }),
        td(m.conta !== codigo ? `${m.conta} · ${m.descricao}` : m.descricao),
        td(eurosOuVazio(m.debito), "num"), td(eurosOuVazio(m.credito), "num"),
        td(`${euros(Math.abs(m.saldo))} ${m.saldo > 0 ? "D" : m.saldo < 0 ? "C" : ""}`, "num")));
      const sinal = (v) => `${euros(Math.abs(v))} ${v > 0 ? "D" : v < 0 ? "C" : ""}`;
      resultado.replaceChildren(
        h("h3", { class: "secao-titulo-pequeno", texto: `${d.codigo} ${d.nome || nomeConta(d.codigo)}` }),
        h("div", { class: "tabela-caixa" }, h("table", { class: "tabela razao" },
          h("thead", {}, h("tr", {},
            h("th", { scope: "col", texto: "Data" }), h("th", { scope: "col", texto: "Lançamento" }), h("th", { scope: "col", texto: "Descrição" }),
            h("th", { scope: "col", class: "num", texto: "Débito" }), h("th", { scope: "col", class: "num", texto: "Crédito" }),
            h("th", { scope: "col", class: "num", texto: "Saldo" }))),
          h("tbody", {},
            h("tr", { class: "agregadora" }, td(formatarData(periodo.de)), td(""), td("Saldo anterior"), td(""), td(""), td(sinal(d.saldo_inicial), "num")),
            ...linhas),
          h("tfoot", {}, h("tr", {}, td(""), td(""), td("Totais do período e saldo final"),
            td(euros(d.total_debito), "num"), td(euros(d.total_credito), "num"), td(sinal(d.saldo_final), "num"))))),
        d.movimentos.length ? null : h("p", { class: "ajuda", texto: "Sem movimentos no período." }));
    } catch (erro) {
      resultado.replaceChildren(vazio("erro", "Erro", erro.message));
    }
  }

  const form = h("form", { class: "filtros-linha", onsubmit: (e) => { e.preventDefault(); consultar(); } },
    h("div", { class: "campo largo" }, h("label", { for: "extrato-conta", texto: "Conta (inclui as subcontas)" }), campo),
    h("button", { type: "submit", class: "botao" }, icone("pesquisa"), "Ver extrato"));
  campo.addEventListener("change", consultar);

  preencher(secao, h("section", { class: "cartao" },
    h("div", { class: "cartao-cabecalho" }, h("div", {}, h("h2", { texto: "Extrato de conta (razão)" }), h("p", { class: "subtitulo", texto: textoPeriodo() }))),
    listaTodas, form, resultado));
  await consultar();
}

// ---------- IVA ----------

async function carregarIVA(secao, url = "/api/contabilidade/iva") {
  const d = await obter(url);
  const campos = h("div", { class: "campos-dp" }, d.dedutivel.map((c) => h("div", { class: "campo-dp" },
    h("span", { class: "numero", texto: c.campo }), h("span", { class: "descricao", texto: c.descricao }),
    h("b", { texto: euros(c.valor) }))));
  const liquidado = h("div", { class: "campos-dp" }, d.liquidado.flatMap((c) => [
    h("div", { class: "campo-dp" }, h("span", { class: "numero", texto: c.campo_base }), h("span", { class: "descricao", texto: `Base - ${c.descricao}` }), h("b", { texto: euros(c.base) })),
    h("div", { class: "campo-dp" }, h("span", { class: "numero", texto: c.campo_imposto }), h("span", { class: "descricao", texto: `Imposto - ${c.descricao}` }), h("b", { texto: euros(c.imposto) })),
  ]));

  const autoliquidado = (d.autoliquidado || []).filter((c) => c.base || c.imposto);
  const blocoAutoliquidado = autoliquidado.length || d.quadro_06a?.base ? h("div", {},
    h("h3", { class: "secao-titulo-pequeno", texto: "IVA autoliquidado pela empresa (operações intracomunitárias e inversão do sujeito passivo)" }),
    h("div", { class: "campos-dp" }, autoliquidado.flatMap((c) => [
      h("div", { class: "campo-dp" }, h("span", { class: "numero", texto: c.campo_base }), h("span", { class: "descricao", texto: `Base - ${c.descricao}` }), h("b", { texto: euros(c.base) })),
      h("div", { class: "campo-dp" }, h("span", { class: "numero", texto: c.campo_imposto }), h("span", { class: "descricao", texto: `Imposto - ${c.descricao}` }), h("b", { texto: euros(c.imposto) })),
    ]), d.quadro_06a?.base ? h("div", { class: "campo-dp" }, h("span", { class: "numero", texto: d.quadro_06a.campo }),
      h("span", { class: "descricao", texto: d.quadro_06a.descricao }), h("b", { texto: euros(d.quadro_06a.base) })) : null)) : null;

  const aPagar = d.saldo >= 0;
  const botaoApurar = h("button", { type: "button", class: "botao" }, icone("balanca"), "Gerar lançamento de apuramento");
  botaoApurar.addEventListener("click", async () => {
    const ok = await confirmar({
      titulo: "Apurar o IVA?",
      texto: `Os saldos das contas 2432, 2433 e 2434 até ${formatarData(d.ate)} passam para 2435 e o resultado fica em 2436 (a pagar) ou 2437 (a recuperar).`,
      botao: "Apurar",
    });
    if (!ok) return;
    ocupado(botaoApurar, true);
    try {
      const la = await enviar("/api/contabilidade/iva/apurar", { de: d.de, ate: d.ate });
      notificar(`Apuramento lançado: ${la.referencia}.`, "ok");
      invalidar("resumo", "lancamentos", "balancete", "extrato");
      mostrar("iva", true);
    } catch (erro) {
      notificar(erro.message, "erro");
      ocupado(botaoApurar, false);
    }
  });

  const seletor = h("div", { class: "filtros-linha" },
    botao(`Período escolhido (${textoPeriodo()})`, () => carregarIVA(secao, `/api/contabilidade/iva?${qsPeriodo()}`).catch((e) => erroNaSecao(secao, e))),
    botao("Último período de IVA terminado", () => carregarIVA(secao).catch((e) => erroNaSecao(secao, e))));

  preencher(secao, h("section", { class: "cartao" },
    h("div", { class: "cartao-cabecalho" },
      h("div", {}, h("h2", { texto: `Apuramento do IVA - ${d.descricao}` }),
        h("p", { class: "subtitulo", texto: `De ${formatarData(d.de)} a ${formatarData(d.ate)} · valores para a declaração periódica (quadro 06)` }))),
    seletor,
    h("h3", { class: "secao-titulo-pequeno", texto: "IVA dedutível (a favor da empresa)" }), campos,
    h("h3", { class: "secao-titulo-pequeno", texto: "IVA liquidado (a favor do Estado)" }), liquidado,
    blocoAutoliquidado,
    d.iva_nao_dedutivel ? h("p", { class: "ajuda", style: "margin-top:1rem" },
      `IVA não dedutível levado a gasto no período: ${euros(d.iva_nao_dedutivel)} (art. 21.º do CIVA). Não entra na declaração.`) : null,
    h("div", { class: "resultado-iva" },
      h("div", {}, h("span", { class: "indicador-rotulo", texto: aPagar ? "IVA a pagar ao Estado" : "IVA a recuperar / reportar" }),
        h("br"), h("strong", { texto: euros(Math.abs(d.saldo)) })),
      d.apuramentos.length
        ? h("div", {}, criarEtiqueta(`Apurado: ${d.apuramentos.map((a) => a.referencia).join(", ")}`, "ok", "check"))
        : botaoApurar),
    h("p", { class: "nota-rodape", texto: "Mapa de apoio: confirme os valores antes de submeter a declaração no Portal das Finanças. As regularizações (campos 40/41) não são calculadas automaticamente. Numeração do modelo em vigor até 30/06/2027." })));
}

// ---------- Resultados ----------

async function carregarResultados(secao) {
  const d = await obter(`/api/contabilidade/resultados?${qsPeriodo()}`);
  const maior = Math.max(1, ...d.detalhe_fse.map((x) => x.valor));
  preencher(secao, 
    h("section", { class: "cartao" },
      h("div", { class: "cartao-cabecalho" }, h("div", {}, h("h2", { texto: "Demonstração de resultados por naturezas" }), h("p", { class: "subtitulo", texto: textoPeriodo() }))),
      h("div", { class: "tabela-caixa" }, h("table", { class: "tabela dr" },
        h("tbody", {}, d.rubricas.map((r) => h("tr", {},
          td(`${r.tipo === "gasto" ? "−" : "+"} ${r.rubrica}`),
          td(euros(r.valor), "num")))),
        h("tfoot", {}, h("tr", { class: "total" },
          td("Resultado antes de impostos"),
          h("td", { class: `num${d.resultado < 0 ? " valor-negativo" : ""}`, texto: euros(d.resultado) }))))),
      h("p", { class: "nota-rodape", texto: "Sem inventário final, as compras do período (conta 31) são tratadas como custo das mercadorias e matérias consumidas. Não inclui IRC nem depreciações não lançadas." })),
    h("section", { class: "cartao" },
      h("h2", { texto: "Fornecimentos e serviços externos (62)" }),
      d.detalhe_fse.length
        ? h("div", { class: "tabela-caixa" }, h("table", { class: "tabela razao" },
          h("tbody", {}, d.detalhe_fse.map((x) => h("tr", { class: null },
            h("td", { class: "conta-codigo" }, h("a", { href: `#extrato:${x.conta}`, texto: x.conta })),
            td(x.nome),
            h("td", { class: "celula-barra", style: "width:35%" }, h("div", { class: "barra-antiguidade", "aria-hidden": "true" },
              h("span", { style: `width:${(x.valor / maior) * 100}%;background:var(--grafico)` }))),
            td(euros(x.valor), "num"))))))
        : vazio("euro", "Sem FSE no período"))
  );
}

// ---------- Fornecedores ----------

const ESCALOES = [
  ["a_vencer", "A vencer"], ["ate_30", "Vencido até 30 dias"], ["ate_60", "31 a 60 dias"],
  ["ate_90", "61 a 90 dias"], ["mais_90", "Mais de 90 dias"],
];

async function carregarFornecedores(secao) {
  const d = await obter("/api/contabilidade/fornecedores");
  const legenda = h("div", { class: "legenda-antiguidade" }, ESCALOES.map(([k, nome]) =>
    h("span", {}, h("i", { class: `ant-${k}` }), `${nome}: ${euros(d.antiguidade[k])}`)));

  const linhas = d.linhas.map((l) => {
    const total = ESCALOES.reduce((s, [k]) => s + l.antiguidade[k], 0);
    const barra = h("div", { class: "barra-antiguidade", role: "img",
      "aria-label": ESCALOES.filter(([k]) => l.antiguidade[k]).map(([k, n]) => `${n}: ${euros(l.antiguidade[k])}`).join("; ") || "Sem faturas por pagar" },
    total ? ESCALOES.filter(([k]) => l.antiguidade[k]).map(([k]) => h("span", { class: `ant-${k}`, style: `width:${(l.antiguidade[k] / total) * 100}%` })) : null);
    const tr = h("tr", { tabindex: "0", title: "Abrir o extrato" },
      h("td", { class: "nome" }, h("span", { class: "titulo", texto: l.nome }), h("span", { class: "ajuda", texto: ` ${l.conta}${l.nif ? ` · NIF ${l.nif}` : ""}` })),
      td(euros(l.saldo), `num${l.saldo < 0 ? " valor-negativo" : ""}`, "Saldo"),
      h("td", { style: "width:30%" }, barra));
    const abrir = () => irPara(`extrato:${l.conta}`);
    tr.addEventListener("click", abrir);
    tr.addEventListener("keydown", (e) => { if (e.key === "Enter") abrir(); });
    return tr;
  });

  preencher(secao, h("section", { class: "cartao" },
    h("div", { class: "cartao-cabecalho" },
      h("div", {}, h("h2", { texto: "Contas correntes de fornecedores" }),
        h("p", { class: "subtitulo", texto: `Saldo em dívida: ${euros(d.total)} · antiguidade pelas faturas confirmadas por pagar` }))),
    legenda,
    linhas.length ? h("div", { class: "tabela-caixa" }, h("table", { class: "tabela clicavel responsiva" },
      h("thead", {}, h("tr", {}, h("th", { scope: "col", texto: "Fornecedor" }), h("th", { scope: "col", class: "num", texto: "Saldo" }),
        h("th", { scope: "col", texto: "Antiguidade" }))),
      h("tbody", {}, linhas)))
      : vazio("loja", "Sem saldos", "Os fornecedores aparecem depois de contabilizar as faturas.")));
}

// ---------- Obrigações e configuração ----------

async function carregarObrigacoes(secao) {
  const [d, eu, resumo] = await Promise.all([obter("/api/contabilidade/obrigacoes"), obter("/api/auth/eu"), obter("/api/contabilidade/resumo")]);
  const empresa = resumo.empresa;
  const podeConfigurar = eu.papel === "admin" && !eu.modo_demo;

  // Configuração: IVA, período fechado e dados da sede (para o SAF-T)
  const regime = h("select", { id: "conf-regime" },
    h("option", { value: "trimestral" }, "Trimestral"), h("option", { value: "mensal" }, "Mensal"));
  regime.value = d.regime_iva;
  const campoTexto = (id, valor, props = {}) => h("input", { type: "text", id, value: valor || "", ...props });
  const fechado = h("input", { type: "date", id: "conf-fechado", value: empresa.fechado_ate || "" });
  const nif = campoTexto("conf-nif", empresa.nif, { inputmode: "numeric", maxlength: "9", pattern: "[0-9]{9}", placeholder: "500000000" });
  const morada = campoTexto("conf-morada", empresa.morada, { maxlength: "210", placeholder: "Rua, número, andar" });
  const codigoPostal = campoTexto("conf-cp", empresa.codigo_postal, { maxlength: "8", pattern: "[0-9]{4}-[0-9]{3}", placeholder: "0000-000" });
  const localidade = campoTexto("conf-localidade", empresa.localidade, { maxlength: "50", placeholder: "Lisboa" });
  for (const campo of [regime, fechado, nif, morada, codigoPostal, localidade]) campo.disabled = !podeConfigurar;
  const gravar = h("button", { type: "submit", class: "botao", disabled: !podeConfigurar }, icone("cadeado"), "Guardar");
  const campo = (id, rotulo, el, classe = "campo") => h("div", { class: classe }, h("label", { for: id, texto: rotulo }), el);
  const form = h("form", {},
    h("div", { class: "filtros-linha" },
      campo("conf-regime", "Periodicidade do IVA", regime),
      campo("conf-fechado", "Período fechado até", fechado),
      campo("conf-nif", "NIF da empresa", nif)),
    h("div", { class: "filtros-linha" },
      campo("conf-morada", "Morada da sede", morada, "campo largo"),
      campo("conf-cp", "Código postal", codigoPostal),
      campo("conf-localidade", "Localidade", localidade),
      gravar));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    ocupado(gravar, true);
    try {
      await enviar("/api/contabilidade/configuracao", {
        regime_iva: regime.value, fechado_ate: fechado.value || null, nif: nif.value.trim(),
        morada: morada.value.trim(), codigo_postal: codigoPostal.value.trim(), localidade: localidade.value.trim(),
      }, "PUT");
      notificar("Configuração guardada.", "ok");
      invalidar("resumo", "iva");
      mostrar("obrigacoes", true);
    } catch (erro) {
      notificar(erro.message, "erro");
      ocupado(gravar, false);
    }
  });

  const porCategoria = { iva: "IVA", saft: "Faturação", salarios: "Salários e retenções", irc: "IRC", outros: "Outras" };
  preencher(secao, 
    h("section", { class: "cartao" },
      h("div", { class: "cartao-cabecalho" }, h("div", {},
        h("h2", { texto: "Calendário fiscal - próximos 4 meses" }),
        h("p", { class: "subtitulo", texto: "Prazos já ajustados a fins de semana e feriados nacionais (incluindo Páscoa e Corpo de Deus)." }))),
      h("div", { class: "tabela-caixa" }, h("table", { class: "tabela responsiva" },
        h("thead", {}, h("tr", {}, h("th", { scope: "col", texto: "Prazo" }), h("th", { scope: "col", texto: "Obrigação" }),
          h("th", { scope: "col", texto: "Período" }), h("th", { scope: "col", texto: "Tipo" }))),
        h("tbody", {}, d.obrigacoes.map((o) => h("tr", {},
          td(`${formatarData(o.prazo)}${o.dias <= 7 ? ` (${o.dias === 0 ? "hoje" : `${o.dias} d`})` : ""}`, "", "Prazo"),
          h("td", { class: "nome" }, h("span", { class: "titulo", texto: o.nome }), h("span", { class: "ajuda", texto: o.descricao })),
          td(o.periodo, "", "Período"), td(porCategoria[o.categoria] || o.categoria, "", "Tipo")))))),
      h("p", { class: "nota-rodape", texto: d.aviso })),
    cartaoSAFT(),
    h("section", { class: "cartao" },
      h("h2", { texto: "Configuração da contabilidade" }),
      h("p", { class: "ajuda", texto: "Fechar um período impede lançamentos (e estornos) com data igual ou anterior. Use-o depois de entregar a declaração do IVA. O NIF e a morada aparecem no cabeçalho do SAF-T." }),
      form,
      podeConfigurar ? null : h("p", { class: "ajuda", texto: eu.modo_demo ? "Na demonstração a configuração está bloqueada." : "Só os administradores podem alterar a configuração." })));
}

// Exportação do SAF-T (PT) da contabilidade: verifica primeiro o que falta e só depois deixa descarregar
function cartaoSAFT() {
  const esteAno = new Date().getFullYear();
  const ano = h("select", { id: "saft-ano" }, [0, 1, 2, 3].map((i) => h("option", { value: esteAno - i }, String(esteAno - i))));
  const estado = h("div", { "aria-live": "polite" });
  const descarregar = h("a", { class: "botao", id: "saft-descarregar", download: "", "aria-disabled": "true" }, icone("descarregar"), "Descarregar SAF-T");

  async function verificar() {
    estado.replaceChildren(h("span", { class: "ajuda", texto: "A verificar…" }));
    descarregar.removeAttribute("href");
    descarregar.setAttribute("aria-disabled", "true");
    try {
      const r = await obter(`/api/contabilidade/saft/verificar?ano=${ano.value}`);
      const periodo = `${formatarData(r.de)} a ${formatarData(r.ate)} · ${r.lancamentos} lançamento${r.lancamentos === 1 ? "" : "s"}`;
      if (r.problemas.length) {
        estado.replaceChildren(
          h("p", { class: "ajuda", texto: periodo }),
          h("ul", { class: "lista-alertas" }, r.problemas.map((p) => h("li", { class: "alerta aviso" },
            icone("alerta"), h("div", {}, h("strong", { texto: "Falta corrigir" }), h("p", { texto: p }))))));
        return;
      }
      estado.replaceChildren(h("p", { class: "ajuda" }, criarEtiqueta("Pronto a exportar", "ok", "check"), ` ${periodo}`));
      descarregar.href = `/api/contabilidade/saft?ano=${ano.value}`;
      descarregar.removeAttribute("aria-disabled");
    } catch (erro) {
      estado.replaceChildren(h("p", { class: "erro-campo", texto: erro.message }));
    }
  }
  ano.addEventListener("change", verificar);
  descarregar.addEventListener("click", (e) => {
    if (descarregar.getAttribute("aria-disabled")) e.preventDefault();
  });
  verificar();

  return h("section", { class: "cartao", "aria-labelledby": "titulo-saft" },
    h("div", { class: "cartao-cabecalho" },
      h("div", {}, h("h2", { id: "titulo-saft", texto: "SAF-T (PT) da contabilidade" }),
        h("p", { class: "subtitulo", texto: "Ficheiro XML na versão 1.04_01 (Portaria n.º 302/2016), com o plano de contas, as taxonomias SNC, os fornecedores e todos os lançamentos do exercício." })),
      h("div", { class: "cabecalho-acoes" },
        h("div", { class: "campo", style: "margin:0" }, h("label", { for: "saft-ano", class: "visualmente-oculto", texto: "Exercício" }), ano),
        descarregar)),
    estado,
    h("p", { class: "nota-rodape", texto: "O SAF-T da contabilidade é entregue à AT juntamente com a IES. Confirme o ficheiro no validador do Portal das Finanças antes de o submeter." }));
}

// ---------- Plano de contas ----------

async function carregarPlano(secao) {
  const [, abertura] = await Promise.all([obterContas(true), cartaoAbertura()]);
  const filtro = h("input", { type: "search", id: "plano-filtro", placeholder: "Código ou nome", autocomplete: "off" });
  const corpo = h("tbody", {});
  function desenhar() {
    const termo = filtro.value.trim().toLowerCase();
    const lista = contas.filter((c) => !termo || c.codigo.startsWith(termo) || c.nome.toLowerCase().includes(termo));
    corpo.replaceChildren(...lista.slice(0, 600).map((c) => {
      const recuo = Math.min(c.codigo.length - 1, 3);
      return h("tr", { class: c.codigo.length === 1 ? "agregadora classe" : c.movimento ? null : "agregadora" },
        h("td", { class: "conta-codigo" }, h("a", { href: `#extrato:${c.codigo}`, texto: c.codigo })),
        h("td", { class: recuo ? `recuo-${recuo}` : null, texto: c.nome }),
        td(c.movimento ? "Movimento" : "Agregadora"),
        td(c.nif ? `NIF ${c.nif}` : ""));
    }));
  }
  filtro.addEventListener("input", desenhar);
  desenhar();

  const codigo = h("input", { type: "text", id: "nova-conta-codigo", inputmode: "numeric", pattern: "\\d+", required: true, placeholder: "Ex.: 1211" });
  const nome = h("input", { type: "text", id: "nova-conta-nome", required: true, maxlength: "200", placeholder: "Ex.: Banco A - conta à ordem" });
  const criar = h("button", { type: "submit", class: "botao" }, icone("mais"), "Criar subconta");
  const form = h("form", { class: "filtros-linha" },
    h("div", { class: "campo" }, h("label", { for: "nova-conta-codigo", texto: "Código" }), codigo),
    h("div", { class: "campo largo" }, h("label", { for: "nova-conta-nome", texto: "Nome" }), nome), criar);
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    ocupado(criar, true);
    try {
      const c = await enviar("/api/contabilidade/contas", { codigo: codigo.value.trim(), nome: nome.value.trim() });
      notificar(`Conta ${c.codigo} criada.`, "ok");
      codigo.value = nome.value = "";
      await carregarPlano(secao);
    } catch (erro) {
      notificar(erro.message, "erro");
    } finally {
      ocupado(criar, false);
    }
  });

  preencher(secao, 
    abertura,
    h("section", { class: "cartao" },
      h("div", { class: "cartao-cabecalho" }, h("div", {},
        h("h2", { texto: "Plano de contas (SNC)" }),
        h("p", { class: "subtitulo", texto: "Base do código de contas da Portaria n.º 218/2015, com as subcontas de IVA por taxa. As subcontas de cada fornecedor são criadas automaticamente." }))),
      h("div", { class: "filtros-linha" }, h("div", { class: "campo largo" }, h("label", { for: "plano-filtro", texto: "Procurar" }), filtro)),
      h("div", { class: "tabela-caixa" }, h("table", { class: "tabela razao" },
        h("thead", {}, h("tr", {}, h("th", { scope: "col", texto: "Conta" }), h("th", { scope: "col", texto: "Nome" }),
          h("th", { scope: "col", texto: "Tipo" }), h("th", { scope: "col", texto: "Terceiro" }))),
        corpo))),
    h("section", { class: "cartao" },
      h("h2", { texto: "Nova subconta" }),
      h("p", { class: "ajuda", texto: "O código tem de começar pelo de uma conta existente (ex.: 121 dentro de 12). A conta-mãe passa a agregadora, se ainda não tiver movimentos." }),
      form));
}

// ---------- Vendas (faturas de clientes) ----------

let filtroVendas = "todas";
let pesquisaVendas = "";

const TAXAS_IVA = [23, 13, 6, 0, 22, 12, 4, 16, 9];
const CONTAS_RENDIMENTO = [["711", "Vendas de mercadorias"], ["712", "Vendas de produtos acabados"], ["721", "Prestações de serviços"]];

function janelaTemporaria(titulo, conteudo, aoSubmeter, textoBotao = "Guardar") {
  document.getElementById("janela-temporaria")?.remove();
  const erro = h("p", { class: "erro-campo", role: "alert", hidden: true });
  const gravar = h("button", { type: "submit", class: "botao" }, textoBotao);
  const cancelar = h("button", { type: "button", class: "botao secundario" }, "Cancelar");
  const form = h("form", { novalidate: true }, h("h2", { id: "janela-temporaria-titulo", texto: titulo }), conteudo, erro,
    h("div", { class: "janela-botoes" }, cancelar, gravar));
  const janela = h("dialog", { class: "janela larga", id: "janela-temporaria", "aria-labelledby": "janela-temporaria-titulo" }, form);
  cancelar.addEventListener("click", () => janela.close());
  janela.addEventListener("close", () => janela.remove());
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    erro.hidden = true;
    ocupado(gravar, true);
    try {
      await aoSubmeter();
      janela.close();
    } catch (falha) {
      erro.hidden = false;
      erro.replaceChildren(icone("erro"), falha.message);
      ocupado(gravar, false);
    }
  });
  document.body.append(janela);
  janela.showModal();
  return janela;
}

function campoJanela(id, rotulo, el, classe = "campo") {
  return h("div", { class: classe }, h("label", { for: id, texto: rotulo }), el);
}

function abrirRegistoVenda() {
  const tipo = h("select", { id: "venda-tipo" }, [["FT", "Fatura"], ["FS", "Fatura simplificada"], ["FR", "Fatura-recibo"],
    ["NC", "Nota de crédito"], ["ND", "Nota de débito"]].map(([v, t]) => h("option", { value: v }, `${v} · ${t}`)));
  const numero = h("input", { type: "text", id: "venda-numero", maxlength: "60", placeholder: "Ex.: A/123", required: true });
  const data = h("input", { type: "date", id: "venda-data", value: iso(new Date()), required: true });
  const vencimento = h("input", { type: "date", id: "venda-vencimento" });
  const nif = h("input", { type: "text", id: "venda-nif", inputmode: "numeric", maxlength: "12", placeholder: "Vazio: consumidor final" });
  const nome = h("input", { type: "text", id: "venda-cliente", maxlength: "200", placeholder: "Nome do cliente" });
  const meio = h("select", { id: "venda-meio" }, [["", "—"], ["NU", "Numerário (caixa)"], ["TB", "Transferência"], ["MB", "Multibanco"],
    ["CC", "Cartão de crédito"], ["CD", "Cartão de débito"]].map(([v, t]) => h("option", { value: v }, t)));
  const corpo = h("tbody", {});
  const totais = h("p", { class: "editor-totais" });

  function linha() {
    const taxa = h("select", { "aria-label": "Taxa de IVA" }, TAXAS_IVA.map((t) => h("option", { value: t }, `${t}%`)));
    const base = h("input", { type: "number", min: "0", step: "0.01", "aria-label": "Valor sem IVA", placeholder: "0,00" });
    const conta = h("select", { "aria-label": "Conta de rendimento" }, CONTAS_RENDIMENTO.map(([c, n]) => h("option", { value: c }, `${c} · ${n}`)));
    for (const el of [taxa, base, conta]) el.addEventListener("input", recalcular);
    const tr = h("tr", {}, h("td", {}, conta), h("td", {}, taxa), h("td", {}, base),
      h("td", {}, botaoIcone("lixo", "Remover a linha", () => { tr.remove(); recalcular(); }, "pequeno perigo")));
    return tr;
  }
  function lerLinhas() {
    return [...corpo.querySelectorAll("tr")].map((tr) => {
      const [conta, taxa] = tr.querySelectorAll("select");
      return { conta: conta.value, taxa: Number(taxa.value), base: Number(tr.querySelector("input").value) || 0 };
    }).filter((l) => l.base > 0);
  }
  function recalcular() {
    const linhas = lerLinhas();
    const base = linhas.reduce((s, l) => s + l.base, 0);
    const imposto = linhas.reduce((s, l) => s + Math.round(l.base * l.taxa) / 100, 0);
    totais.textContent = `Sem IVA ${euros(base)} · IVA ${euros(imposto)} · Total ${euros(base + imposto)}`;
  }
  corpo.append(linha());
  recalcular();

  const conteudo = h("div", {},
    h("p", { class: "ajuda", texto: "Para vendas emitidas noutro programa ou no Portal das Finanças. Esta aplicação não emite faturas: isso exige um programa certificado pela AT." }),
    h("div", { class: "grelha-3" }, campoJanela("venda-tipo", "Tipo", tipo), campoJanela("venda-numero", "Número", numero),
      campoJanela("venda-data", "Data", data)),
    h("div", { class: "grelha-3" }, campoJanela("venda-nif", "NIF do cliente", nif), campoJanela("venda-cliente", "Cliente", nome),
      campoJanela("venda-vencimento", "Vencimento (opcional)", vencimento)),
    h("table", { class: "editor-linhas" },
      h("thead", {}, h("tr", {}, h("th", { texto: "Conta" }), h("th", { texto: "IVA" }), h("th", { texto: "Valor sem IVA" }),
        h("th", {}, h("span", { class: "visualmente-oculto", texto: "Remover" })))),
      corpo),
    h("div", { class: "editor-totais" }, botao("Linha", () => corpo.append(linha()), "botao secundario pequeno", "mais"), totais),
    campoJanela("venda-meio", "Forma de pagamento (faturas-recibo)", meio));

  janelaTemporaria("Registar venda", conteudo, async () => {
    const linhas = lerLinhas();
    if (!numero.value.trim() || !data.value || !linhas.length) throw new Error("Preencha o número, a data e pelo menos uma linha com valor.");
    const v = await enviar("/api/contabilidade/vendas", {
      tipo: tipo.value, numero: numero.value.trim(), data: data.value, data_vencimento: vencimento.value || null,
      cliente_nif: nif.value.trim() || null, cliente_nome: nome.value.trim() || null, meio_pagamento: meio.value || null,
      linhas,
    });
    notificar(`Venda ${v.numero} registada e contabilizada (${euros(v.total)}).`, "ok");
    invalidar("resumo", "lancamentos", "balancete", "iva", "resultados", "extrato", "bancos");
    await mostrar("vendas", true);
  }, "Registar e contabilizar");
  numero.focus();
}

async function abrirRecebimento(v) {
  const contasBanco = await obter("/api/contabilidade/banco/contas");
  const data = h("input", { type: "date", id: "receber-data", value: iso(new Date()) });
  const conta = h("select", { id: "receber-conta" }, contasBanco.map((c) => {
    const o = h("option", { value: c.codigo }, `${c.codigo} · ${c.nome}`);
    if (c.codigo === "121") o.selected = true;
    return o;
  }));
  const valor = h("input", { type: "number", id: "receber-valor", min: "0.01", step: "0.01", value: v.em_divida.toFixed(2) });
  janelaTemporaria(`Receber ${v.numero}`, h("div", {},
    h("p", { class: "ajuda", texto: `${v.cliente_nome} · em dívida ${euros(v.em_divida)}. Para um recebimento parcial, altere o valor.` }),
    h("div", { class: "grelha-3" }, campoJanela("receber-data", "Data", data), campoJanela("receber-conta", "Entrou em", conta),
      campoJanela("receber-valor", "Valor", valor))), async () => {
    const la = await enviar(`/api/contabilidade/vendas/${v.id}/receber`, { data: data.value, conta: conta.value, valor: Number(valor.value) });
    notificar(`Recebimento lançado: ${la.referencia}.`, "ok");
    invalidar("resumo", "lancamentos", "balancete", "extrato", "bancos");
    await mostrar("vendas", true);
  }, "Registar recebimento");
}

function estadoVenda(v) {
  if (v.estado === "anulada") return criarEtiqueta("Anulada", "neutro");
  if (v.estado === "faturada") return criarEtiqueta("Faturada noutro documento", "neutro");
  if (!v.contabilizada) return criarEtiqueta("Por contabilizar", "aviso", "alerta");
  if (v.tipo === "NC") return criarEtiqueta("Nota de crédito", "info");
  if (v.recebida) return criarEtiqueta("Recebida", "ok", "check");
  if (v.vencida) return criarEtiqueta(`Vencida · ${euros(v.em_divida)}`, "erro", "alerta");
  return criarEtiqueta(v.valor_recebido ? `Por receber · ${euros(v.em_divida)}` : "Por receber", "info", "relogio");
}

function acoesVenda(v) {
  if (v.estado !== "normal") return null;
  if (!v.contabilizada) {
    return botao("Contabilizar", async (e) => {
      const alvo = e.currentTarget;
      ocupado(alvo, true);
      try {
        const la = await enviar(`/api/contabilidade/vendas/${v.id}/contabilizar`);
        notificar(`Lançado: ${la.referencia}.`, "ok");
        invalidar("resumo", "lancamentos", "balancete", "iva", "resultados", "extrato");
        await mostrar("vendas", true);
      } catch (erro) {
        notificar(erro.message, "erro");
        ocupado(alvo, false);
      }
    }, "botao secundario pequeno");
  }
  if (v.tipo !== "NC" && !v.recebida) return botao("Receber", () => abrirRecebimento(v).catch((e) => notificar(e.message, "erro")), "botao secundario pequeno");
  return null;
}

async function carregarVendas(secao) {
  const params = new URLSearchParams({ estado: filtroVendas, de: periodo.de, ate: periodo.ate });
  if (pesquisaVendas) params.set("pesquisa", pesquisaVendas);
  const [d, clientes] = await Promise.all([obter(`/api/contabilidade/vendas?${params}`), obter("/api/contabilidade/vendas/clientes")]);
  const t = d.totais;

  // Importação do SAF-T de faturação
  const ficheiro = h("input", { type: "file", id: "vendas-ficheiro", class: "visualmente-oculto", accept: ".xml" });
  const nomeFicheiro = h("span", { class: "ajuda nome-ficheiro", texto: "Nenhum ficheiro escolhido" });
  ficheiro.addEventListener("change", () => { nomeFicheiro.textContent = ficheiro.files[0]?.name || "Nenhum ficheiro escolhido"; });
  const importar = h("button", { type: "submit", class: "botao" }, icone("upload"), "Importar SAF-T");
  const formImportar = h("form", { class: "filtros-linha" },
    h("div", { class: "campo largo" }, h("span", { class: "rotulo", texto: "SAF-T (PT) de faturação (.xml)" }),
      h("div", { class: "escolher-ficheiro" },
        h("label", { for: "vendas-ficheiro", class: "botao secundario" }, icone("ficheiro"), "Escolher ficheiro"), ficheiro, nomeFicheiro)),
    importar, botao("Registar venda", abrirRegistoVenda, "botao secundario", "mais"));
  formImportar.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!ficheiro.files.length) return ficheiro.click();
    const dados = new FormData();
    dados.append("ficheiro", ficheiro.files[0]);
    ocupado(importar, true);
    try {
      const resposta = await api("/api/contabilidade/vendas/importar-saft", { method: "POST", body: dados });
      if (!resposta.ok) throw new Error(await lerErro(resposta, "Não foi possível importar o SAF-T."));
      const r = await resposta.json();
      const partes = [`${r.importadas} documento${r.importadas === 1 ? "" : "s"} importado${r.importadas === 1 ? "" : "s"} e contabilizado${r.importadas === 1 ? "" : "s"}`];
      if (r.recebimentos) partes.push(`${r.recebimentos} recibo${r.recebimentos === 1 ? "" : "s"}`);
      if (r.anuladas) partes.push(`${r.anuladas} anulado${r.anuladas === 1 ? "" : "s"}`);
      if (r.duplicadas) partes.push(`${r.duplicadas} já existia${r.duplicadas === 1 ? "" : "m"}`);
      notificar(partes.join(" · ") + ".", r.avisos.length ? "aviso" : "ok");
      for (const aviso of r.avisos.slice(0, 3)) notificar(aviso, "aviso");
      invalidar("resumo", "lancamentos", "balancete", "iva", "resultados", "extrato", "bancos");
      await mostrar("vendas", true);
    } catch (erro) {
      notificar(erro.message, "erro");
      ocupado(importar, false);
    }
  });

  // Filtros e lista
  const filtros = h("div", { class: "segmentos", role: "group", "aria-label": "Mostrar vendas" },
    [["todas", "Todas"], ["por_receber", "Por receber"], ["recebidas", "Recebidas"], ["por_contabilizar", "Por contabilizar"],
      ["anuladas", "Anuladas"]].map(([v, texto]) => {
      const b = h("button", { type: "button", "aria-pressed": String(filtroVendas === v) }, texto);
      b.addEventListener("click", () => { filtroVendas = v; mostrar("vendas", true); });
      return b;
    }));
  const pesquisa = h("input", { type: "search", id: "vendas-pesquisa", value: pesquisaVendas, placeholder: "Número, cliente ou NIF", autocomplete: "off" });
  let temporizador;
  pesquisa.addEventListener("input", () => {
    clearTimeout(temporizador);
    temporizador = setTimeout(async () => {
      pesquisaVendas = pesquisa.value.trim();
      await mostrar("vendas", true);
      const campo = document.getElementById("vendas-pesquisa");
      campo?.focus();
      campo?.setSelectionRange(campo.value.length, campo.value.length);
    }, 350);
  });

  const linhas = d.vendas.map((v) => h("tr", { class: v.estado !== "normal" ? "estornado" : null },
    td(formatarData(v.data), "", "Data"),
    h("td", { class: "nome" }, h("span", { class: "titulo", texto: v.numero }), h("span", { class: "ajuda", texto: v.tipo_nome })),
    h("td", {}, h("span", { class: "titulo", texto: v.cliente_nome }),
      h("span", { class: "ajuda", texto: v.cliente_nif === "999999990" ? " Consumidor final" : ` NIF ${v.cliente_nif}` })),
    td(`${v.tipo === "NC" ? "−" : ""}${euros(v.total)}`, "num", "Total"),
    h("td", {}, estadoVenda(v)),
    h("td", { class: "acoes" }, acoesVenda(v))));

  // Contas correntes de clientes
  const linhasClientes = clientes.linhas.map((l) => {
    const total = ESCALOES.reduce((s, [k]) => s + l.antiguidade[k], 0);
    const barra = h("div", { class: "barra-antiguidade", role: "img",
      "aria-label": ESCALOES.filter(([k]) => l.antiguidade[k]).map(([k, n]) => `${n}: ${euros(l.antiguidade[k])}`).join("; ") || "Nada por receber" },
    total ? ESCALOES.filter(([k]) => l.antiguidade[k]).map(([k]) => h("span", { class: `ant-${k}`, style: `width:${(l.antiguidade[k] / total) * 100}%` })) : null);
    return h("tr", {},
      h("td", { class: "nome" }, h("a", { class: "titulo", href: `#extrato:${l.conta}`, texto: l.nome }),
        h("span", { class: "ajuda", texto: ` ${l.conta}${l.nif ? ` · NIF ${l.nif}` : ""}` })),
      td(euros(l.saldo), `num${l.saldo < 0 ? " valor-negativo" : ""}`, "Saldo"),
      h("td", { class: "celula-barra", style: "width:30%" }, barra));
  });

  preencher(secao, 
    h("section", { class: "indicadores", "aria-label": "Resumo das vendas" },
      indicador("Vendas no período", euros(t.vendas_sem_iva), `${t.documentos} documento${t.documentos === 1 ? "" : "s"} · sem IVA`, "euro", true),
      indicador("IVA liquidado", euros(t.iva_liquidado), "das vendas do período", "percentagem"),
      indicador("Por receber", euros(t.por_receber), `${t.por_receber_quantidade} fatura${t.por_receber_quantidade === 1 ? "" : "s"} (todos os períodos)`, "carteira"),
      indicador("Vencidas", euros(t.vencidas), t.vencidas_quantidade ? `${t.vencidas_quantidade} por cobrar` : "nenhuma vencida", "alerta")),
    h("section", { class: "cartao" },
      h("div", { class: "cartao-cabecalho" }, h("div", {}, h("h2", { texto: "Faturas de clientes" }),
        h("p", { class: "subtitulo", texto: "Importe o SAF-T de faturação exportado pelo seu programa certificado (todos os meses) ou registe vendas emitidas no Portal das Finanças. Ficam contabilizadas no diário de vendas, com o IVA liquidado por taxa." }))),
      formImportar),
    h("section", { class: "cartao" },
      h("div", { class: "cartao-cabecalho" }, filtros,
        h("div", { class: "campo", style: "margin:0;min-width:16rem" }, h("label", { for: "vendas-pesquisa", class: "visualmente-oculto", texto: "Pesquisar vendas" }), pesquisa)),
      linhas.length
        ? h("div", { class: "tabela-caixa" }, h("table", { class: "tabela responsiva" },
          h("thead", {}, h("tr", {}, h("th", { scope: "col", texto: "Data" }), h("th", { scope: "col", texto: "Documento" }),
            h("th", { scope: "col", texto: "Cliente" }), h("th", { scope: "col", class: "num", texto: "Total" }),
            h("th", { scope: "col", texto: "Estado" }), h("th", { scope: "col" }, h("span", { class: "visualmente-oculto", texto: "Ações" })))),
          h("tbody", {}, linhas)))
        : vazio("recibo", "Sem vendas", `Não há vendas com estes filtros entre ${textoPeriodo()}.`)),
    h("section", { class: "cartao" },
      h("div", { class: "cartao-cabecalho" }, h("div", {}, h("h2", { texto: "Contas correntes de clientes" }),
        h("p", { class: "subtitulo", texto: `Em dívida: ${euros(clientes.total)} · antiguidade pelas faturas por receber` }))),
      h("div", { class: "legenda-antiguidade" }, ESCALOES.map(([k, n]) => h("span", {}, h("i", { class: `ant-${k}` }), `${n}: ${euros(clientes.antiguidade[k])}`))),
      linhasClientes.length
        ? h("div", { class: "tabela-caixa" }, h("table", { class: "tabela responsiva" },
          h("thead", {}, h("tr", {}, h("th", { scope: "col", texto: "Cliente" }), h("th", { scope: "col", class: "num", texto: "Saldo" }),
            h("th", { scope: "col", class: "celula-barra", texto: "Antiguidade" }))),
          h("tbody", {}, linhasClientes)))
        : vazio("check", "Sem dívidas de clientes")),
  );
}

// ---------- Inventários ----------

let inventarioAberto = null;

const NOMES_CATEGORIA = { M: "Mercadorias", P: "Matérias-primas" };

function abrirContagemManual() {
  const data = h("input", { type: "date", id: "inv-data", value: iso(new Date()) });
  const descricao = h("input", { type: "text", id: "inv-descricao", maxlength: "200", value: "Contagem física" });
  const corpo = h("tbody", {});
  const total = h("span", {});
  function linha() {
    const codigo = h("input", { type: "text", maxlength: "60", "aria-label": "Código", placeholder: "Código" });
    const desc = h("input", { type: "text", maxlength: "200", "aria-label": "Descrição", placeholder: "Artigo" });
    const categoria = h("select", { "aria-label": "Categoria" }, Object.entries(NOMES_CATEGORIA).map(([v, t]) => h("option", { value: v }, t)));
    const qtd = h("input", { type: "number", min: "0", step: "0.001", "aria-label": "Quantidade", placeholder: "Qtd." });
    const custo = h("input", { type: "number", min: "0", step: "0.0001", "aria-label": "Custo unitário", placeholder: "Custo" });
    for (const el of [qtd, custo]) el.addEventListener("input", recalcular);
    const tr = h("tr", {}, h("td", {}, codigo), h("td", {}, desc), h("td", {}, categoria), h("td", {}, qtd), h("td", {}, custo),
      h("td", {}, botaoIcone("lixo", "Remover a linha", () => { tr.remove(); recalcular(); }, "pequeno perigo")));
    return tr;
  }
  function ler() {
    return [...corpo.querySelectorAll("tr")].map((tr) => {
      const [codigo, desc, qtd, custo] = tr.querySelectorAll("input");
      return { codigo: codigo.value.trim(), descricao: desc.value.trim(), categoria: tr.querySelector("select").value,
        quantidade: Number(qtd.value) || 0, custo_unitario: Number(custo.value) || 0 };
    }).filter((l) => l.descricao && l.quantidade);
  }
  function recalcular() { total.textContent = `Valor do inventário: ${euros(ler().reduce((s, l) => s + l.quantidade * l.custo_unitario, 0))}`; }
  corpo.append(linha(), linha());
  recalcular();
  janelaTemporaria("Nova contagem de inventário", h("div", {},
    h("div", { class: "grelha-3" }, campoJanela("inv-data", "Data da contagem", data), campoJanela("inv-descricao", "Descrição", descricao, "campo")),
    h("table", { class: "editor-linhas" }, h("thead", {}, h("tr", {}, ...["Código", "Artigo", "Categoria", "Quantidade", "Custo unitário"].map((t) => h("th", { texto: t })),
      h("th", {}, h("span", { class: "visualmente-oculto", texto: "Remover" })))), corpo),
    h("div", { class: "editor-totais" }, botao("Artigo", () => corpo.append(linha()), "botao secundario pequeno", "mais"), total)), async () => {
    const linhas = ler();
    if (!linhas.length) throw new Error("Indique pelo menos um artigo com quantidade.");
    const inv = await enviar("/api/contabilidade/inventarios", { data: data.value, descricao: descricao.value.trim() || "Inventário", linhas });
    notificar(`Contagem de ${formatarData(inv.data)} guardada (${euros(inv.total)}).`, "ok");
    inventarioAberto = inv.id;
    await mostrar("inventarios", true);
  }, "Guardar contagem");
}

async function detalheInventario(id) {
  const d = await obter(`/api/contabilidade/inventarios/${id}`);
  const acoes = [];
  const lancar = (url, mensagem) => async (e) => {
    const alvo = e.currentTarget;
    ocupado(alvo, true);
    try {
      const la = await enviar(url);
      notificar(`${mensagem}: ${la.referencia}.`, "ok");
      invalidar("resumo", "lancamentos", "balancete", "resultados", "extrato");
      await mostrar("inventarios", true);
    } catch (erro) {
      notificar(erro.message, "erro");
      ocupado(alvo, false);
    }
  };
  if (!d.lancamento) {
    if (d.apuramento) acoes.push(botao("Lançar apuramento do CMVMC", lancar(`/api/contabilidade/inventarios/${id}/apurar`, "Apuramento lançado"), "botao", "balanca"));
    acoes.push(botao("Usar como inventário inicial", lancar(`/api/contabilidade/inventarios/${id}/inicial`, "Inventário inicial lançado"), "botao secundario"));
    acoes.push(botao("Apagar", async () => {
      const ok = await confirmar({ titulo: "Apagar esta contagem?", texto: `A contagem de ${formatarData(d.data)} e os seus ${d.artigos} artigos serão apagados.`, botao: "Apagar", perigo: true });
      if (!ok) return;
      const r = await api(`/api/contabilidade/inventarios/${id}`, { method: "DELETE" });
      if (!r.ok) return notificar(await lerErro(r, "Não foi possível apagar."), "erro");
      inventarioAberto = null;
      mostrar("inventarios", true);
    }, "botao fantasma"));
  } else {
    acoes.push(criarEtiqueta(`${d.inicial ? "Inventário inicial" : "Apurado"}: ${d.lancamento.referencia}`, "ok", "check"));
  }
  // Ficheiro da comunicação do inventário à AT (Portaria n.º 2/2015), até 31 de janeiro
  acoes.push(h("a", { class: "botao fantasma", href: `/api/contabilidade/inventarios/${id}/at`, download: "",
    title: "CSV para submeter no Portal das Finanças (comunicação de inventários)" }, icone("descarregar"), "Ficheiro para a AT"));

  let formula = null;
  if (d.apuramento && !d.inicial) {
    const a = d.apuramento;
    formula = h("div", {},
      h("h3", { class: "secao-titulo-pequeno", texto: `CMVMC ${a.de ? `de ${formatarData(a.de)} ` : ""}até ${formatarData(a.ate)}` }),
      a.categorias.length ? h("div", { class: "tabela-caixa" }, h("table", { class: "tabela dr" },
        h("thead", {}, h("tr", {}, h("th", { scope: "col", texto: "" }), ...a.categorias.map((c) => h("th", { scope: "col", class: "num", texto: c.nome })))),
        h("tbody", {},
          ...[["Inventário inicial", "inicial", ""], ["Compras", "compras", "+ "], ["Devoluções e descontos", "devolucoes_descontos", "− "],
            ["Inventário final (esta contagem)", "final", "− "]].map(([rotulo, chave, sinal]) => h("tr", {}, td(rotulo),
            ...a.categorias.map((c) => td(`${sinal}${euros(c[chave])}`, "num")))),
          h("tr", { class: "total" }, td("Custo das vendas e consumos (CMVMC)"), ...a.categorias.map((c) => td(euros(c.cmvmc), `num${c.cmvmc < 0 ? " valor-negativo" : ""}`))))))
        : h("p", { class: "ajuda", texto: "Sem compras nem existências neste período." }),
      a.avisos.length ? h("ul", { class: "lista-alertas", style: "margin-top:0.75rem" }, a.avisos.map((x) => h("li", { class: "alerta aviso" }, icone("alerta"), h("p", { texto: x })))) : null);
  }

  return h("section", { class: "cartao", id: "detalhe-inventario" },
    h("div", { class: "cartao-cabecalho" },
      h("div", {}, h("h2", { texto: `${d.descricao} · ${formatarData(d.data)}` }),
        h("p", { class: "subtitulo", texto: `${d.artigos} artigos · mercadorias ${euros(d.mercadorias)} · matérias ${euros(d.materias)} · total ${euros(d.total)}` })),
      h("div", { class: "cabecalho-acoes" }, ...acoes)),
    formula,
    h("details", {}, h("summary", { texto: `Artigos contados (${d.linhas.length})` }),
      h("div", { class: "tabela-caixa" }, h("table", { class: "tabela razao" },
        h("thead", {}, h("tr", {}, ...["Código", "Artigo", "Categoria"].map((t) => h("th", { scope: "col", texto: t })),
          ...["Quantidade", "Custo unit.", "Valor"].map((t) => h("th", { scope: "col", class: "num", texto: t })))),
        h("tbody", {}, d.linhas.slice(0, 500).map((l) => h("tr", {}, h("td", { class: "conta-codigo", texto: l.codigo }), td(l.descricao),
          td(NOMES_CATEGORIA[l.categoria]), td(`${l.quantidade.toLocaleString("pt-PT")} ${l.unidade}`, "num"),
          td(l.custo_unitario.toLocaleString("pt-PT", { minimumFractionDigits: 2, maximumFractionDigits: 4 }), "num"), td(euros(l.valor), "num"))))))));
}

async function carregarInventarios(secao) {
  const d = await obter("/api/contabilidade/inventarios");
  if (inventarioAberto && !d.inventarios.some((i) => i.id === inventarioAberto)) inventarioAberto = null;
  inventarioAberto = inventarioAberto || d.inventarios.find((i) => !i.lancamento)?.id || d.inventarios[0]?.id || null;

  const ficheiro = h("input", { type: "file", id: "inv-ficheiro", class: "visualmente-oculto", accept: ".csv,.txt,.xlsx" });
  const nomeFicheiro = h("span", { class: "ajuda nome-ficheiro", texto: "Nenhum ficheiro escolhido" });
  ficheiro.addEventListener("change", () => { nomeFicheiro.textContent = ficheiro.files[0]?.name || "Nenhum ficheiro escolhido"; });
  const data = h("input", { type: "date", id: "inv-import-data", value: iso(new Date()) });
  const categoria = h("select", { id: "inv-import-categoria", title: "Usada quando o ficheiro não tem a coluna da categoria" },
    Object.entries(NOMES_CATEGORIA).map(([v, t]) => h("option", { value: v }, t)));
  const importar = h("button", { type: "submit", class: "botao" }, icone("upload"), "Importar contagem");
  const form = h("form", { class: "filtros-linha" },
    h("div", { class: "campo largo" }, h("span", { class: "rotulo", texto: "Contagem (CSV ou Excel)" }),
      h("div", { class: "escolher-ficheiro" }, h("label", { for: "inv-ficheiro", class: "botao secundario" }, icone("ficheiro"), "Escolher ficheiro"), ficheiro, nomeFicheiro)),
    h("div", { class: "campo" }, h("label", { for: "inv-import-data", texto: "Data da contagem" }), data),
    h("div", { class: "campo" }, h("label", { for: "inv-import-categoria", texto: "Categoria por omissão" }), categoria),
    importar, botao("Escrever à mão", abrirContagemManual, "botao secundario", "mais"));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!ficheiro.files.length) return ficheiro.click();
    const dados = new FormData();
    dados.append("ficheiro", ficheiro.files[0]);
    dados.append("data", data.value);
    dados.append("categoria", categoria.value);
    ocupado(importar, true);
    try {
      const r = await api("/api/contabilidade/inventarios/importar", { method: "POST", body: dados });
      if (!r.ok) throw new Error(await lerErro(r, "Não foi possível importar a contagem."));
      const inv = await r.json();
      notificar(`${inv.artigos} artigo${inv.artigos === 1 ? "" : "s"} importado${inv.artigos === 1 ? "" : "s"} (${euros(inv.total)}).`, "ok");
      inventarioAberto = inv.id;
      await mostrar("inventarios", true);
    } catch (erro) {
      notificar(erro.message, "erro");
      ocupado(importar, false);
    }
  });

  const linhas = d.inventarios.map((i) => {
    const tr = h("tr", { class: i.id === inventarioAberto ? "agregadora" : null, tabindex: "0", title: "Ver a contagem e o apuramento" },
      td(formatarData(i.data), "", "Data"), h("td", { class: "nome" }, h("span", { class: "titulo", texto: i.descricao }), h("span", { class: "ajuda", texto: ` ${i.artigos} artigos` })),
      td(euros(i.mercadorias), "num", "Mercadorias"), td(euros(i.materias), "num", "Matérias"), td(euros(i.total), "num", "Total"),
      h("td", {}, i.lancamento ? criarEtiqueta(i.inicial ? "Inicial" : "Apurado", "ok", "check") : criarEtiqueta("Por lançar", "aviso", "relogio")));
    const abrir = () => { inventarioAberto = i.id; mostrar("inventarios", true); };
    tr.addEventListener("click", abrir);
    tr.addEventListener("keydown", (e) => { if (e.key === "Enter") abrir(); });
    return tr;
  });

  preencher(secao, 
    h("section", { class: "indicadores", "aria-label": "Inventários" },
      indicador("Mercadorias (321)", euros(d.saldo_mercadorias), "valor na contabilidade", "venda", true),
      indicador("Matérias-primas (331)", euros(d.saldo_materias), "valor na contabilidade", "venda"),
      indicador("Compras por apurar (31)", euros(d.compras_por_apurar), d.compras_por_apurar ? "passam para inventários no próximo apuramento" : "tudo apurado", "recibo"),
      indicador("Contagens", String(d.inventarios.length), d.inventarios.length ? `última: ${formatarData(d.inventarios[0].data)}` : "nenhuma ainda", "lista")),
    h("section", { class: "cartao" },
      h("div", { class: "cartao-cabecalho" }, h("div", {}, h("h2", { texto: "Contagens de inventário" }),
        h("p", { class: "subtitulo", texto: "Sistema de inventário periódico: conte as existências no fim de cada período (pelo menos a 31 de dezembro). O custo das vendas é o inventário inicial mais as compras menos o inventário final." }))),
      form,
      linhas.length ? h("div", { class: "tabela-caixa" }, h("table", { class: "tabela clicavel responsiva" },
        h("thead", {}, h("tr", {}, ...["Data", "Contagem"].map((t) => h("th", { scope: "col", texto: t })),
          ...["Mercadorias", "Matérias", "Total"].map((t) => h("th", { scope: "col", class: "num", texto: t })), h("th", { scope: "col", texto: "Estado" }))),
        h("tbody", {}, linhas)))
        : vazio("lista", "Ainda não há contagens", "Importe a contagem de um CSV ou Excel, ou escreva-a à mão.")),
    inventarioAberto ? await detalheInventario(inventarioAberto) : null,
  );
}

// ---------- Ativos fixos e depreciações ----------

const ESTADOS_ATIVO = { em_uso: ["Em uso", "info"], abatido: ["Abatido", "neutro"], alienado: ["Vendido", "neutro"] };

function abrirRegistoAtivo(dados) {
  const referencia = h("select", { id: "ativo-referencia" }, h("option", { value: "" }, "Escolha um tipo de ativo…"),
    dados.taxas_referencia.map((t, i) => h("option", { value: i }, `${t.descricao} · ${t.taxa}%`)));
  const descricao = h("input", { type: "text", id: "ativo-descricao", maxlength: "200", placeholder: "Ex.: Forno industrial" });
  const conta = h("select", { id: "ativo-conta" }, dados.contas.map((c) => h("option", { value: c.conta }, `${c.conta} · ${c.nome}`)));
  const taxa = h("input", { type: "number", id: "ativo-taxa", min: "0.01", max: "100", step: "0.01", value: "12.5" });
  const valor = h("input", { type: "number", id: "ativo-valor", min: "0.01", step: "0.01", placeholder: "Sem IVA dedutível" });
  const residual = h("input", { type: "number", id: "ativo-residual", min: "0", step: "0.01", value: "0" });
  const aquisicao = h("input", { type: "date", id: "ativo-aquisicao", value: iso(new Date()) });
  const inicio = h("input", { type: "date", id: "ativo-inicio" });
  const mensalidade = h("p", { class: "ajuda" });
  const atualizar = () => {
    const m = ((Number(valor.value) || 0) - (Number(residual.value) || 0)) * (Number(taxa.value) || 0) / 100 / 12;
    mensalidade.textContent = m > 0 ? `Depreciação mensal: ${euros(Math.round(m * 100) / 100)} · anual: ${euros(Math.round(m * 1200) / 100)}` : "";
  };
  referencia.addEventListener("change", () => {
    const t = dados.taxas_referencia[referencia.value];
    if (!t) return;
    conta.value = t.conta;
    taxa.value = t.taxa;
    atualizar();
  });
  for (const el of [taxa, valor, residual]) el.addEventListener("input", atualizar);
  janelaTemporaria("Registar ativo fixo", h("div", {},
    h("p", { class: "ajuda", texto: "A compra já deve estar lançada (pela fatura ou à mão) na conta do ativo. Aqui fica a ficha que calcula as depreciações. As taxas propostas são as máximas de referência do Decreto Regulamentar n.º 25/2009: confirme a da sua atividade." }),
    campoJanela("ativo-referencia", "Tipo de ativo", referencia),
    h("div", { class: "grelha-3" }, campoJanela("ativo-descricao", "Descrição", descricao), campoJanela("ativo-conta", "Conta", conta),
      campoJanela("ativo-taxa", "Taxa anual (%)", taxa)),
    h("div", { class: "grelha-3" }, campoJanela("ativo-valor", "Valor de aquisição", valor), campoJanela("ativo-residual", "Valor residual", residual),
      campoJanela("ativo-aquisicao", "Data de aquisição", aquisicao)),
    h("div", { class: "grelha-3" }, campoJanela("ativo-inicio", "Entrada em funcionamento (se diferente)", inicio)),
    mensalidade), async () => {
    if (!descricao.value.trim() || !Number(valor.value)) throw new Error("Indique a descrição e o valor de aquisição.");
    const a = await enviar("/api/contabilidade/ativos", {
      descricao: descricao.value.trim(), conta_ativo: conta.value, taxa: Number(taxa.value), valor_aquisicao: Number(valor.value),
      valor_residual: Number(residual.value) || 0, data_aquisicao: aquisicao.value, data_inicio: inicio.value || null,
    });
    notificar(`Ativo ${a.codigo} registado (depreciação mensal de ${euros(a.depreciacao_mensal)}).`, "ok");
    await mostrar("ativos", true);
  }, "Registar ativo");
  descricao.focus();
}

function abrirSaidaAtivo(a) {
  const data = h("input", { type: "date", id: "saida-data", value: iso(new Date()) });
  const tipo = h("select", { id: "saida-tipo" }, h("option", { value: "abate" }, "Abate (avaria, obsolescência, roubo)"),
    h("option", { value: "venda" }, "Venda"));
  const valor = h("input", { type: "number", id: "saida-valor", min: "0", step: "0.01", placeholder: "Sem IVA" });
  const taxa = h("select", { id: "saida-taxa" }, [23, 13, 6, 0].map((t) => h("option", { value: t }, `${t}%`)));
  const venda = h("div", { class: "grelha-3", hidden: true }, campoJanela("saida-valor", "Valor de venda", valor), campoJanela("saida-taxa", "IVA", taxa));
  tipo.addEventListener("change", () => { venda.hidden = tipo.value !== "venda"; });
  janelaTemporaria(`Saída do ativo ${a.codigo}`, h("div", {},
    h("p", { class: "ajuda", texto: `${a.descricao} · valor líquido ${euros(a.valor_liquido)}. As depreciações até ao mês da saída são lançadas primeiro.` }),
    h("div", { class: "grelha-3" }, campoJanela("saida-tipo", "Tipo de saída", tipo), campoJanela("saida-data", "Data", data)),
    venda), async () => {
    const corpo = { data: data.value };
    if (tipo.value === "venda") {
      corpo.valor_venda = Number(valor.value) || 0;
      corpo.taxa_iva = Number(taxa.value);
    }
    const la = await enviar(`/api/contabilidade/ativos/${a.id}/saida`, corpo);
    notificar(`Saída lançada: ${la.referencia}.`, "ok");
    invalidar("resumo", "lancamentos", "balancete", "resultados", "extrato");
    await mostrar("ativos", true);
  }, "Lançar saída");
}

async function carregarAtivos(secao) {
  const d = await obter("/api/contabilidade/ativos");
  const t = d.totais;
  const pl = d.por_lancar;

  const lancarDep = h("button", { type: "button", class: "botao", disabled: !pl.ativos }, icone("balanca"),
    pl.ativos ? `Lançar depreciações até ${formatarData(pl.ate).slice(3)} (${euros(pl.valor)})` : "Depreciações em dia");
  lancarDep.addEventListener("click", async () => {
    ocupado(lancarDep, true);
    try {
      const la = await enviar("/api/contabilidade/ativos/depreciar", { ate: pl.ate });
      notificar(`Depreciações lançadas: ${la.referencia}.`, "ok");
      invalidar("resumo", "lancamentos", "balancete", "resultados", "extrato");
      await mostrar("ativos", true);
    } catch (erro) {
      notificar(erro.message, "erro");
      ocupado(lancarDep, false);
    }
  });

  const linhas = d.ativos.map((a) => {
    const [estado, classe] = ESTADOS_ATIVO[a.estado] || [a.estado, "neutro"];
    return h("tr", { class: a.estado !== "em_uso" ? "estornado" : null },
      h("td", { class: "nome" }, h("span", { class: "titulo", texto: `${a.codigo} · ${a.descricao}` }),
        h("span", { class: "ajuda", texto: ` ${a.conta_ativo} ${a.conta_nome} · desde ${formatarData(a.data_inicio)}` })),
      td(euros(a.valor_aquisicao), "num", "Aquisição"), td(`${a.taxa.toLocaleString("pt-PT")}%`, "num", "Taxa"),
      td(euros(a.depreciacao_ano), "num", `Depreciação ${d.ano}`), td(euros(a.acumulada), "num", "Acumulada"),
      td(euros(a.valor_liquido), "num", "Valor líquido"),
      h("td", {}, criarEtiqueta(estado, classe)),
      h("td", { class: "acoes" }, a.estado === "em_uso" ? botao("Saída", () => abrirSaidaAtivo(a), "botao secundario pequeno") : null));
  });

  preencher(secao, 
    h("section", { class: "indicadores", "aria-label": "Ativos fixos" },
      indicador("Valor de aquisição", euros(t.valor_aquisicao), "ativos em uso", "caixa", true),
      indicador("Depreciações acumuladas", euros(t.acumulada), "até à última depreciação lançada", "balanca"),
      indicador("Valor líquido", euros(t.valor_liquido), "aquisição menos depreciações", "euro"),
      indicador(`Depreciações de ${d.ano}`, euros(t.depreciacao_ano), "gasto do exercício (642x / 6433)", "grafico")),
    d.diferencas.length ? h("ul", { class: "lista-alertas", style: "margin-bottom:1.25rem" }, d.diferencas.map((x) => h("li", { class: "alerta info" },
      icone("relogio"), h("div", {}, h("strong", { texto: `Conta ${x.conta} ${x.nome}` }),
        h("p", { texto: `Contabilidade: ${euros(x.contabilidade)} · ficha de ativos: ${euros(x.registados)}. Registe na ficha os ativos comprados ou lance a aquisição que falta.` }))))) : null,
    h("section", { class: "cartao" },
      h("div", { class: "cartao-cabecalho" },
        h("div", {}, h("h2", { texto: `Mapa de depreciações de ${d.ano}` }),
          h("p", { class: "subtitulo", texto: "Quotas constantes, em duodécimos a partir do mês de entrada em funcionamento. Lança-se tudo de uma vez, sem repetir meses." })),
        h("div", { class: "cabecalho-acoes" }, botao("Registar ativo", () => abrirRegistoAtivo(d), "botao secundario", "mais"), lancarDep)),
      linhas.length ? h("div", { class: "tabela-caixa" }, h("table", { class: "tabela responsiva" },
        h("thead", {}, h("tr", {}, h("th", { scope: "col", texto: "Ativo" }),
          ...["Aquisição", "Taxa", `Deprec. ${d.ano}`, "Acumulada", "Valor líquido"].map((x) => h("th", { scope: "col", class: "num", texto: x })),
          h("th", { scope: "col", texto: "Estado" }), h("th", { scope: "col" }, h("span", { class: "visualmente-oculto", texto: "Ações" })))),
        h("tbody", {}, linhas)))
        : vazio("caixa", "Sem ativos registados", "Registe os equipamentos, viaturas e programas comprados para as depreciações serem calculadas e lançadas.")),
  );
}

// ---------- Salários ----------

const NOMES_MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"];
let anoSalarios = new Date().getFullYear();
let mesSalarios = null;  // mês aberto no processamento

function invalidarContabilidade() {
  invalidar("resumo", "lancamentos", "balancete", "resultados", "extrato", "balanco", "fecho");
}

async function carregarSalarios(secao) {
  const d = await obter(`/api/contabilidade/salarios?ano=${anoSalarios}`);
  const hoje = new Date();
  if (mesSalarios === null) {
    const pendente = d.por_processar.find((p) => p.ano === anoSalarios);
    mesSalarios = pendente ? pendente.mes : (anoSalarios === hoje.getFullYear() ? hoje.getMonth() + 1 : 12);
  }
  const t = d.mapa.totais;

  const pessoas = d.funcionarios.map((f) => h("tr", { class: f.ativo ? null : "estornado" },
    h("td", { class: "nome" }, h("span", { class: "titulo", texto: `${f.numero} · ${f.nome}` }),
      h("span", { class: "ajuda", texto: ` ${f.tipo === "orgaos_sociais" ? "Órgãos sociais" : "Pessoal"} · desde ${formatarData(f.data_admissao)}${f.data_saida ? ` · saída ${formatarData(f.data_saida)}` : ""}` })),
    td(euros(f.salario_base), "num", "Vencimento base"),
    td(f.subsidio_alimentacao ? `${euros(f.subsidio_alimentacao)}/dia ${f.alimentacao_cartao ? "(cartão)" : "(numerário)"}` : "—", "", "Alimentação"),
    td(`${f.taxa_irs.toLocaleString("pt-PT")}%`, "num", "Retenção IRS"),
    td(`${f.taxa_ss_trabalhador.toLocaleString("pt-PT")}% / ${f.taxa_ss_entidade.toLocaleString("pt-PT")}%`, "num", "Seg. Social"),
    h("td", { class: "acoes" }, botao("Editar", () => abrirFichaFuncionario(d, f), "botao fantasma pequeno"))));

  const ano = h("select", { id: "salarios-ano" }, [0, 1, 2].map((i) => {
    const a = hoje.getFullYear() - i;
    const o = h("option", { value: a }, String(a));
    if (a === anoSalarios) o.selected = true;
    return o;
  }));
  ano.addEventListener("change", () => { anoSalarios = Number(ano.value); mesSalarios = null; mostrar("salarios", true); });

  const mapa = d.mapa.meses.map((m) => {
    const pag = m.pagamentos;
    const estado = (p) => criarEtiqueta(p.pago ? "Pago" : "Por pagar", p.pago ? "ok" : "aviso", p.pago ? "check" : null);
    return h("tr", {},
      h("td", { class: "nome" }, h("button", { type: "button", class: "ligacao", onclick: () => { mesSalarios = m.mes; mostrar("salarios", true); } }, NOMES_MESES[m.mes - 1])),
      td(String(m.pessoas), "num", "Pessoas"), td(euros(m.iliquido), "num", "Ilíquido"), td(euros(m.irs), "num", "IRS"),
      td(euros(m.seguranca_social), "num", "Seg. Social"), td(euros(m.liquido), "num", "Líquido"),
      h("td", { "data-rotulo": "Salários" }, estado(pag.salarios)), h("td", { "data-rotulo": "Seg. Social" }, estado(pag.seguranca_social)),
      h("td", { "data-rotulo": "IRS" }, estado(pag.irs)));
  });

  const processamento = h("div", {});
  preencher(secao, 
    h("section", { class: "indicadores", "aria-label": "Salários" },
      indicador(`Gastos com o pessoal ${anoSalarios}`, euros(t.custo_total), "remunerações e encargos da entidade", "equipa", true),
      indicador("Remunerações ilíquidas", euros(t.iliquido), `${d.mapa.meses.length} mês(es) processado(s)`, "euro"),
      indicador("Segurança Social", euros(t.seguranca_social), "trabalhadores e entidade", "carteira"),
      indicador("IRS retido", euros(t.irs), "entregue até dia 20 do mês seguinte", "percentagem")),
    d.por_processar.length || d.pagamentos_em_atraso.length ? h("ul", { class: "lista-alertas", style: "margin-bottom:1.25rem" },
      d.por_processar.length ? h("li", { class: "alerta aviso" }, icone("alerta"), h("div", {},
        h("strong", { texto: `Por processar: ${d.por_processar.map((p) => p.nome_mes).join(", ")}` }),
        h("p", { texto: "Há pessoas ao serviço nesses meses sem salários processados." }))) : null,
      ...d.pagamentos_em_atraso.map((a) => h("li", { class: `alerta ${a.tipo === "salarios" ? "aviso" : "erro"}` }, icone("relogio"),
        h("div", {}, h("strong", { texto: `${a.nome} de ${NOMES_MESES[a.mes - 1]} de ${a.ano} por pagar` }),
          h("p", { texto: `${euros(a.valor)}. O prazo já passou.` }))))) : null,
    h("section", { class: "cartao" },
      h("div", { class: "cartao-cabecalho" },
        h("div", {}, h("h2", { texto: "Pessoal" }),
          h("p", { class: "subtitulo", texto: d.aviso })),
        h("div", { class: "cabecalho-acoes" }, botao("Adicionar pessoa", () => abrirFichaFuncionario(d, null), "botao secundario", "mais"))),
      pessoas.length ? h("div", { class: "tabela-caixa" }, h("table", { class: "tabela responsiva" },
        h("thead", {}, h("tr", {}, h("th", { scope: "col", texto: "Pessoa" }), h("th", { scope: "col", class: "num", texto: "Vencimento base" }),
          h("th", { scope: "col", texto: "Subsídio de alimentação" }), h("th", { scope: "col", class: "num", texto: "Retenção IRS" }),
          h("th", { scope: "col", class: "num", texto: "SS trab. / entidade" }), h("th", { scope: "col" }, h("span", { class: "visualmente-oculto", texto: "Ações" })))),
        h("tbody", {}, pessoas)))
        : vazio("equipa", "Sem pessoas registadas", "Registe os trabalhadores e os gerentes remunerados para processar os salários todos os meses.")),
    processamento,
    h("section", { class: "cartao" },
      h("div", { class: "cartao-cabecalho" },
        h("div", {}, h("h2", { texto: `Mapa anual de remunerações de ${anoSalarios}` }),
          h("p", { class: "subtitulo", texto: "Valores para a declaração mensal de remunerações (AT) e para a declaração de remunerações da Segurança Social." })),
        h("div", { class: "campo", style: "margin:0" }, h("label", { for: "salarios-ano", class: "visualmente-oculto", texto: "Ano" }), ano)),
      mapa.length ? h("div", { class: "tabela-caixa" }, h("table", { class: "tabela responsiva" },
        h("thead", {}, h("tr", {}, h("th", { scope: "col", texto: "Mês" }),
          ...["Pessoas", "Ilíquido", "IRS", "Seg. Social", "Líquido"].map((x) => h("th", { scope: "col", class: "num", texto: x })),
          ...["Salários", "Seg. Social", "IRS"].map((x) => h("th", { scope: "col", texto: x })))),
        h("tbody", {}, mapa),
        h("tfoot", {}, h("tr", {}, td("Total"), td(""), td(euros(t.iliquido), "num"), td(euros(t.irs), "num"),
          td(euros(t.seguranca_social), "num"), td(euros(t.liquido), "num"), td(""), td(""), td("")))))
        : vazio("calendario", "Nada processado neste ano", null)),
  );
  if (d.funcionarios.length) await mostrarProcessamento(processamento, d);
}

async function mostrarProcessamento(caixa, dadosSalarios) {
  const ano = anoSalarios;
  const p = await obter(`/api/contabilidade/salarios/${ano}/${mesSalarios}`);
  const mes = h("select", { id: "salarios-mes" }, NOMES_MESES.map((nome, i) => {
    const o = h("option", { value: i + 1 }, nome);
    if (i + 1 === mesSalarios) o.selected = true;
    return o;
  }));
  mes.addEventListener("change", () => { mesSalarios = Number(mes.value); mostrar("salarios", true); });

  const variaveis = new Map();  // funcionario_id -> {dias, outros, faltas}
  const ferias = h("input", { type: "checkbox", id: "salarios-ferias", checked: Boolean(p.subsidio_ferias) });
  const natal = h("input", { type: "checkbox", id: "salarios-natal", checked: Boolean(p.subsidio_natal) });
  const corpoTabela = h("tbody", {});
  const rodape = h("tfoot", {});

  const linhaTotais = (tt) => h("tr", {}, td("Total"), td(euros(tt.iliquido), "num"), td(euros(tt.ss_trabalhador), "num"), td(euros(tt.irs), "num"),
    td(euros(tt.liquido), "num"), td(euros(tt.ss_entidade), "num"), td(euros(tt.custo_total), "num"));

  const desenhar = (recibos, totais, editavel) => {
    corpoTabela.replaceChildren(...recibos.map((r) => {
      const detalhe = [`base ${euros(r.base)}`];
      if (r.subsidio_ferias) detalhe.push(`sub. férias ${euros(r.subsidio_ferias)}`);
      if (r.subsidio_natal) detalhe.push(`sub. Natal ${euros(r.subsidio_natal)}`);
      if (r.alimentacao) detalhe.push(`alimentação ${euros(r.alimentacao)} (${r.dias} dias)`);
      if (r.outros) detalhe.push(`outros ${euros(r.outros)}`);
      if (r.faltas) detalhe.push(`faltas -${euros(r.faltas)}`);
      const pessoa = h("td", { class: "nome" }, h("span", { class: "titulo", texto: r.nome }), h("span", { class: "ajuda", texto: ` ${detalhe.join(" · ")}` }));
      if (editavel) pessoa.append(" ", botao("Variáveis", () => abrirVariaveis(r), "botao fantasma pequeno"));
      return h("tr", {}, pessoa, td(euros(r.iliquido), "num", "Ilíquido"), td(euros(r.ss_trabalhador), "num", "SS trabalhador"),
        td(euros(r.irs), "num", "IRS"), td(euros(r.liquido), "num", "Líquido"), td(euros(r.ss_entidade), "num", "SS entidade"),
        td(euros(r.custo_total), "num", "Custo total"));
    }));
    rodape.replaceChildren(linhaTotais(totais));
  };

  const corpoPedido = () => ({
    ano, mes: mesSalarios, subsidio_ferias: ferias.checked, subsidio_natal: natal.checked,
    variaveis: [...variaveis.entries()].map(([id, v]) => ({ funcionario_id: id, ...v })),
  });
  const simular = async () => {
    const r = await enviar("/api/contabilidade/salarios/simular", corpoPedido());
    desenhar(r.recibos, r.totais, true);
  };
  function abrirVariaveis(r) {
    const atual = variaveis.get(r.funcionario_id) || { dias: r.dias, outros: r.outros, faltas: r.faltas };
    const dias = h("input", { type: "number", id: "var-dias", min: "0", max: "31", step: "1", value: atual.dias });
    const outros = h("input", { type: "number", id: "var-outros", min: "0", step: "0.01", value: atual.outros });
    const faltas = h("input", { type: "number", id: "var-faltas", min: "0", step: "0.01", value: atual.faltas });
    janelaTemporaria(`Variáveis de ${NOMES_MESES[mesSalarios - 1]}: ${r.nome}`, h("div", {},
      h("p", { class: "ajuda", texto: "Outros abonos (prémios, horas extraordinárias, comissões) estão sujeitos a IRS e a Segurança Social. As faltas não remuneradas descontam na remuneração." }),
      h("div", { class: "grelha-3" }, campoJanela("var-dias", "Dias com subsídio de alimentação", dias),
        campoJanela("var-outros", "Outros abonos (€)", outros), campoJanela("var-faltas", "Desconto por faltas (€)", faltas))), async () => {
      variaveis.set(r.funcionario_id, { dias: Number(dias.value) || 0, outros: Number(outros.value) || 0, faltas: Number(faltas.value) || 0 });
      await simular();
    }, "Aplicar");
  }

  const cabecalhoTabela = h("thead", {}, h("tr", {}, h("th", { scope: "col", texto: "Pessoa" }),
    ...["Ilíquido", "SS trabalhador", "IRS", "Líquido", "SS entidade", "Custo total"].map((x) => h("th", { scope: "col", class: "num", texto: x }))));
  const tabela = h("div", { class: "tabela-caixa" }, h("table", { class: "tabela responsiva" }, cabecalhoTabela, corpoTabela, rodape));
  desenhar(p.recibos, p.totais, !p.processado);

  const acoes = [];
  if (!p.processado) {
    for (const el of [ferias, natal]) el.addEventListener("change", () => simular().catch((e) => notificar(e.message, "erro")));
    const processar = h("button", { type: "button", class: "botao", disabled: !p.recibos.length }, icone("check"), `Processar ${NOMES_MESES[mesSalarios - 1]}`);
    processar.addEventListener("click", async () => {
      ocupado(processar, true);
      try {
        const r = await enviar("/api/contabilidade/salarios/processar", corpoPedido());
        notificar(`Salários de ${r.nome_mes} processados e lançados.`, "ok");
        invalidarContabilidade();
        await mostrar("salarios", true);
      } catch (erro) {
        notificar(erro.message, "erro");
        ocupado(processar, false);
      }
    });
    acoes.push(h("label", { class: "ajuda caixa-opcao" }, ferias, "Subsídio de férias"),
      h("label", { class: "ajuda caixa-opcao" }, natal, "Subsídio de Natal"), processar);
  } else {
    acoes.push(criarEtiqueta("Processado e lançado", "ok", "check"));
    for (const [tipo, pg] of Object.entries(p.pagamentos)) {
      if (pg.pago) {
        acoes.push(criarEtiqueta(`${pg.nome}: pago a ${formatarData(pg.data)}`, "ok", "check"));
        continue;
      }
      acoes.push(botao(`Pagar ${pg.nome.toLowerCase()} (${euros(pg.valor)})`, () => abrirPagamentoSalarios(p, tipo, pg), "botao secundario pequeno", "carteira"));
    }
  }

  caixa.replaceChildren(h("section", { class: "cartao" },
    h("div", { class: "cartao-cabecalho" },
      h("div", {}, h("h2", { texto: `Processamento de ${p.nome_mes}` }),
        h("p", { class: "subtitulo", texto: p.processado
          ? "Os gastos já estão lançados (632/631 e 635 contra 231x, 2421 e 245). Registe os pagamentos à medida que os faz."
          : `Pré-visualização com os valores das fichas. Limites isentos do subsídio de alimentação: ${euros(dadosSalarios.limites_alimentacao.numerario)} em numerário e ${euros(dadosSalarios.limites_alimentacao.cartao)} em cartão, por dia.` })),
      h("div", { class: "campo", style: "margin:0" }, h("label", { for: "salarios-mes", class: "visualmente-oculto", texto: "Mês" }), mes)),
    p.recibos.length ? tabela : vazio("equipa", "Ninguém ao serviço neste mês", null),
    h("div", { class: "barra-lote", style: "margin-top:1rem;justify-content:flex-end;flex-wrap:wrap" }, ...acoes),
    mesSalarios === 12 && p.processado ? h("p", { class: "ajuda", style: "margin-top:1rem" },
      "No fecho do ano, lance também o acréscimo das férias e do subsídio de férias que se pagam no ano seguinte: ",
      botao("Lançar acréscimo de férias", () => lancarAcrescimoFerias(ano), "botao fantasma pequeno", "relogio")) : null));
}

async function lancarAcrescimoFerias(ano) {
  const est = await obter(`/api/contabilidade/salarios/ferias?ano=${ano}`);
  const ok = await confirmar({
    titulo: `Acréscimo de férias de ${ano}`,
    texto: `Férias e subsídio de férias vencidos a 1/1/${ano + 1}: ${euros(est.remuneracoes)} mais ${euros(est.encargos)} de encargos, a 31/12/${ano} (D 63x / C 2722). A reversão fica a 1/1/${ano + 1}.`,
    botao: "Lançar acréscimo",
  });
  if (!ok) return;
  try {
    const la = await enviar("/api/contabilidade/salarios/ferias", { ano });
    notificar(`Acréscimo lançado: ${la.referencia}.`, "ok");
    invalidarContabilidade();
    invalidar("periodizacao");
  } catch (erro) {
    notificar(erro.message, "erro");
  }
}

function abrirPagamentoSalarios(p, tipo, pg) {
  const prazo = p.prazos[tipo];
  const data = h("input", { type: "date", id: "pag-sal-data", value: prazo < iso(new Date()) ? prazo : iso(new Date()) });
  const conta = h("select", { id: "pag-sal-conta" }, h("option", { value: "121" }, "121 · Banco"), h("option", { value: "111" }, "111 · Caixa"));
  janelaTemporaria(`Pagamento: ${pg.nome} de ${p.nome_mes}`, h("div", {},
    h("p", { class: "ajuda", texto: `${euros(pg.valor)} · prazo: ${formatarData(prazo)}.` }),
    h("div", { class: "grelha-3" }, campoJanela("pag-sal-data", "Data do pagamento", data), campoJanela("pag-sal-conta", "Sai de", conta))), async () => {
    const la = await enviar(`/api/contabilidade/salarios/${p.ano}/${p.mes}/pagar`, { tipo, data: data.value, conta: conta.value });
    notificar(`Pagamento lançado: ${la.referencia}.`, "ok");
    invalidarContabilidade();
    await mostrar("salarios", true);
  }, "Registar pagamento");
}

function abrirFichaFuncionario(dados, f) {
  const v = f || { tipo: "pessoal", alimentacao_cartao: true, duodecimos: false, taxa_irs: 0, subsidio_alimentacao: 0 };
  const nome = h("input", { type: "text", id: "func-nome", maxlength: "200", value: v.nome || "" });
  const tipo = h("select", { id: "func-tipo" }, h("option", { value: "pessoal" }, "Trabalhador (pessoal)"), h("option", { value: "orgaos_sociais" }, "Gerente / administrador (órgãos sociais)"));
  tipo.value = v.tipo;
  const nif = h("input", { type: "text", id: "func-nif", inputmode: "numeric", maxlength: "9", value: v.nif || "" });
  const niss = h("input", { type: "text", id: "func-niss", inputmode: "numeric", maxlength: "11", value: v.niss || "" });
  const admissao = h("input", { type: "date", id: "func-admissao", value: v.data_admissao || iso(new Date()) });
  const saida = h("input", { type: "date", id: "func-saida", value: v.data_saida || "" });
  const base = h("input", { type: "number", id: "func-base", min: "0", step: "0.01", value: v.salario_base ?? "" });
  const alimentacao = h("input", { type: "number", id: "func-alimentacao", min: "0", step: "0.01", value: v.subsidio_alimentacao });
  const cartao = h("select", { id: "func-cartao" }, h("option", { value: "1" }, "Cartão refeição"), h("option", { value: "0" }, "Numerário (no recibo)"));
  cartao.value = v.alimentacao_cartao ? "1" : "0";
  const irs = h("input", { type: "number", id: "func-irs", min: "0", max: "100", step: "0.1", value: v.taxa_irs });
  const ssT = h("input", { type: "number", id: "func-ss-t", min: "0", max: "100", step: "0.01", value: v.taxa_ss_trabalhador ?? "", placeholder: "11" });
  const ssE = h("input", { type: "number", id: "func-ss-e", min: "0", max: "100", step: "0.01", value: v.taxa_ss_entidade ?? "", placeholder: "Por omissão" });
  const duodecimos = h("input", { type: "checkbox", id: "func-duodecimos", checked: v.duodecimos });
  janelaTemporaria(f ? `Ficha de ${f.nome}` : "Adicionar pessoa", h("div", {},
    h("div", { class: "grelha-3" }, campoJanela("func-nome", "Nome", nome), campoJanela("func-tipo", "Tipo", tipo), campoJanela("func-admissao", "Admissão", admissao)),
    h("div", { class: "grelha-3" }, campoJanela("func-nif", "NIF", nif), campoJanela("func-niss", "N.º Segurança Social", niss), campoJanela("func-saida", "Saída (se saiu)", saida)),
    h("div", { class: "grelha-3" }, campoJanela("func-base", "Vencimento base mensal (€)", base), campoJanela("func-alimentacao", "Subsídio de alimentação por dia (€)", alimentacao),
      campoJanela("func-cartao", "Pago em", cartao)),
    h("div", { class: "grelha-3" }, campoJanela("func-irs", "Taxa de retenção de IRS (%)", irs), campoJanela("func-ss-t", "SS do trabalhador (%)", ssT),
      campoJanela("func-ss-e", "SS da entidade (%)", ssE)),
    h("label", { class: "ajuda caixa-opcao" }, duodecimos, "Subsídios de férias e de Natal pagos em duodécimos"),
    h("p", { class: "ajuda", texto: `Taxas da Segurança Social por omissão: ${dados.taxas_ss.pessoal.trabalhador}% e ${dados.taxas_ss.pessoal.entidade}% (pessoal), ${dados.taxas_ss.orgaos_sociais.trabalhador}% e ${dados.taxas_ss.orgaos_sociais.entidade}% (órgãos sociais). A taxa de retenção de IRS sai das tabelas da AT para o vencimento, a situação familiar e os dependentes.` })), async () => {
    if (!nome.value.trim() || base.value === "") throw new Error("Indique o nome e o vencimento base.");
    const corpo = {
      nome: nome.value.trim(), tipo: tipo.value, nif: nif.value.trim() || null, niss: niss.value.trim() || null,
      data_admissao: admissao.value, data_saida: saida.value || null, salario_base: Number(base.value),
      subsidio_alimentacao: Number(alimentacao.value) || 0, alimentacao_cartao: cartao.value === "1", duodecimos: duodecimos.checked,
      taxa_irs: Number(irs.value) || 0, taxa_ss_trabalhador: ssT.value === "" ? null : Number(ssT.value),
      taxa_ss_entidade: ssE.value === "" ? null : Number(ssE.value),
    };
    if (f) await enviar(`/api/contabilidade/salarios/funcionarios/${f.id}`, corpo, "PUT");
    else await enviar("/api/contabilidade/salarios/funcionarios", corpo);
    notificar(f ? "Ficha atualizada." : `${corpo.nome} registado.`, "ok");
    await mostrar("salarios", true);
  }, f ? "Guardar" : "Adicionar");
  nome.focus();
}

// ---------- Acréscimos e diferimentos ----------

async function carregarPeriodizacao(secao) {
  const [d] = await Promise.all([obter("/api/contabilidade/periodizacao"), obterContas()]);
  const pr = d.por_reconhecer;
  const reconhecer = h("button", { type: "button", class: "botao", disabled: !pr.quantidade }, icone("balanca"),
    pr.quantidade ? `Reconhecer até ${formatarData(pr.ate).slice(3)} (${euros(pr.valor)})` : "Diferimentos em dia");
  reconhecer.addEventListener("click", async () => {
    ocupado(reconhecer, true);
    try {
      const la = await enviar("/api/contabilidade/periodizacao/reconhecer", { ate: pr.ate });
      notificar(`Lançado: ${la.referencia}.`, "ok");
      invalidarContabilidade();
      await mostrar("periodizacao", true);
    } catch (erro) {
      notificar(erro.message, "erro");
      ocupado(reconhecer, false);
    }
  });

  const diferimentos = d.diferimentos.map((x) => h("tr", { class: x.anulado ? "estornado" : null },
    h("td", { class: "nome" }, h("span", { class: "titulo", texto: x.descricao }),
      h("span", { class: "ajuda", texto: ` ${x.tipo === "gasto" ? "Gasto" : "Rendimento"} · conta ${x.conta} ${nomeConta(x.conta)} · ${formatarData(x.inicio).slice(3)} a ${formatarData(x.fim).slice(3)}` })),
    td(euros(x.valor), "num", "Valor"), td(euros(x.mensal), "num", "Por mês"), td(`${x.meses_reconhecidos}/${x.meses}`, "num", "Meses"),
    td(euros(x.por_reconhecer), "num", "Por reconhecer"),
    h("td", {}, x.anulado ? criarEtiqueta("Estornado", "erro") : x.atrasado ? criarEtiqueta(`${x.atrasado.meses} mês(es) em atraso`, "aviso")
      : x.por_reconhecer ? criarEtiqueta("Em dia", "ok", "check") : criarEtiqueta("Concluído", "neutro"))));
  const acrescimos = d.acrescimos.map((x) => h("tr", { class: x.estado === "estornado" ? "estornado" : null },
    h("td", { class: "nome" }, h("span", { class: "titulo", texto: x.descricao }),
      h("span", { class: "ajuda", texto: ` ${x.referencia} · ${x.tipo === "gasto" ? "gasto" : "rendimento"} · contas ${x.contas.join(", ")}` })),
    td(formatarData(x.data), "", "Data"), td(euros(x.valor), "num", "Valor"),
    td(x.reversao ? formatarData(x.reversao) : "—", "", "Reversão")));

  preencher(secao, 
    h("section", { class: "indicadores", "aria-label": "Periodização" },
      indicador("Gastos a reconhecer (281)", euros(d.saldos["281"]), "pagos, de meses futuros", "relogio", true),
      indicador("Rendimentos a reconhecer (282)", euros(-d.saldos["282"] || 0), "faturados, de meses futuros", "relogio"),
      indicador("Acréscimos de gastos (2722)", euros(-d.saldos["2722"] || 0), "gastos por faturar", "recibo"),
      indicador("Acréscimos de rendimentos (2721)", euros(d.saldos["2721"]), "rendimentos por faturar", "euro")),
    h("section", { class: "cartao" },
      h("div", { class: "cartao-cabecalho" },
        h("div", {}, h("h2", { texto: "Diferimentos" }),
          h("p", { class: "subtitulo", texto: "Gastos pagos ou rendimentos faturados que respeitam a vários meses (seguros, rendas adiantadas, contratos anuais). A fatura lança-se como sempre; aqui reparte-se o valor pelos meses." })),
        h("div", { class: "cabecalho-acoes" }, botao("Novo diferimento", abrirDiferimento, "botao secundario", "mais"), reconhecer)),
      diferimentos.length ? h("div", { class: "tabela-caixa" }, h("table", { class: "tabela responsiva" },
        h("thead", {}, h("tr", {}, h("th", { scope: "col", texto: "Diferimento" }),
          ...["Valor", "Por mês", "Meses", "Por reconhecer"].map((x) => h("th", { scope: "col", class: "num", texto: x })), h("th", { scope: "col", texto: "Estado" }))),
        h("tbody", {}, diferimentos)))
        : vazio("relogio", "Sem diferimentos", "Ex.: um seguro anual de 1 200 € pago em outubro fica 300 € em gasto deste ano e 900 € em gastos a reconhecer.")),
    h("section", { class: "cartao" },
      h("div", { class: "cartao-cabecalho" },
        h("div", {}, h("h2", { texto: "Acréscimos" }),
          h("p", { class: "subtitulo", texto: "Gastos ou rendimentos deste período cujo documento só chega depois (ex.: eletricidade de dezembro faturada em janeiro). A reversão é lançada automaticamente no dia seguinte, para a fatura não duplicar o gasto." })),
        h("div", { class: "cabecalho-acoes" }, botao("Novo acréscimo", abrirAcrescimo, "botao secundario", "mais"))),
      acrescimos.length ? h("div", { class: "tabela-caixa" }, h("table", { class: "tabela responsiva" },
        h("thead", {}, h("tr", {}, h("th", { scope: "col", texto: "Acréscimo" }), h("th", { scope: "col", texto: "Data" }),
          h("th", { scope: "col", class: "num", texto: "Valor" }), h("th", { scope: "col", texto: "Reversão" }))),
        h("tbody", {}, acrescimos)))
        : vazio("recibo", "Sem acréscimos", "O acréscimo das férias e do subsídio de férias lança-se na secção Salários.")),
  );
}

function seletorContaClasse(id, classe, valor) {
  const lista = (contas || []).filter((c) => c.movimento && c.codigo.startsWith(classe));
  const sel = h("select", { id }, lista.map((c) => h("option", { value: c.codigo }, `${c.codigo} · ${c.nome}`)));
  if (valor) sel.value = valor;
  return sel;
}

function abrirDiferimento() {
  const tipo = h("select", { id: "dif-tipo" }, h("option", { value: "gasto" }, "Gasto pago adiantado (281)"), h("option", { value: "rendimento" }, "Rendimento faturado adiantado (282)"));
  const caixaConta = h("div", {});
  const desenharConta = () => caixaConta.replaceChildren(campoJanela("dif-conta", "Conta onde está lançado",
    seletorContaClasse("dif-conta", tipo.value === "gasto" ? "6" : "7", tipo.value === "gasto" ? "6263" : "721")));
  tipo.addEventListener("change", desenharConta);
  desenharConta();
  const descricao = h("input", { type: "text", id: "dif-descricao", maxlength: "200", placeholder: "Ex.: Seguro multirriscos 2026/2027" });
  const valor = h("input", { type: "number", id: "dif-valor", min: "0.01", step: "0.01" });
  const hoje = new Date();
  const inicio = h("input", { type: "month", id: "dif-inicio", value: iso(hoje).slice(0, 7) });
  const meses = h("input", { type: "number", id: "dif-meses", min: "2", max: "120", step: "1", value: "12" });
  janelaTemporaria("Novo diferimento", h("div", {},
    h("p", { class: "ajuda", texto: "O valor sai da conta de gasto (ou rendimento) para a 281 (ou 282) e volta todos os meses com o botão Reconhecer." }),
    h("div", { class: "grelha-3" }, campoJanela("dif-tipo", "Tipo", tipo), caixaConta, campoJanela("dif-descricao", "Descrição", descricao)),
    h("div", { class: "grelha-3" }, campoJanela("dif-valor", "Valor (€)", valor), campoJanela("dif-inicio", "Primeiro mês", inicio), campoJanela("dif-meses", "N.º de meses", meses))), async () => {
    if (!descricao.value.trim() || !Number(valor.value)) throw new Error("Indique a descrição e o valor.");
    await enviar("/api/contabilidade/periodizacao/diferimentos", {
      tipo: tipo.value, conta: document.getElementById("dif-conta").value, descricao: descricao.value.trim(),
      valor: Number(valor.value), inicio: `${inicio.value}-01`, meses: Number(meses.value),
    });
    notificar("Diferimento registado.", "ok");
    invalidarContabilidade();
    await mostrar("periodizacao", true);
  }, "Registar");
  descricao.focus();
}

function abrirAcrescimo() {
  const tipo = h("select", { id: "acr-tipo" }, h("option", { value: "gasto" }, "Gasto por faturar (2722)"), h("option", { value: "rendimento" }, "Rendimento por faturar (2721)"));
  const caixaConta = h("div", {});
  const desenharConta = () => caixaConta.replaceChildren(campoJanela("acr-conta", "Conta",
    seletorContaClasse("acr-conta", tipo.value === "gasto" ? "6" : "7", tipo.value === "gasto" ? "6241" : "721")));
  tipo.addEventListener("change", desenharConta);
  desenharConta();
  const descricao = h("input", { type: "text", id: "acr-descricao", maxlength: "300", placeholder: "Ex.: Eletricidade de dezembro" });
  const valor = h("input", { type: "number", id: "acr-valor", min: "0.01", step: "0.01", placeholder: "Sem IVA" });
  const hoje = new Date();
  const data = h("input", { type: "date", id: "acr-data", value: iso(new Date(hoje.getFullYear(), hoje.getMonth(), 0)) });
  janelaTemporaria("Novo acréscimo", h("div", {},
    h("p", { class: "ajuda", texto: "Lança-se no último dia do período, pelo valor estimado sem IVA. A reversão fica no dia seguinte." }),
    h("div", { class: "grelha-3" }, campoJanela("acr-tipo", "Tipo", tipo), caixaConta, campoJanela("acr-data", "Data", data)),
    h("div", { class: "grelha-3" }, campoJanela("acr-descricao", "Descrição", descricao), campoJanela("acr-valor", "Valor (€)", valor))), async () => {
    if (!descricao.value.trim() || !Number(valor.value)) throw new Error("Indique a descrição e o valor.");
    const la = await enviar("/api/contabilidade/periodizacao/acrescimos", {
      tipo: tipo.value, data: data.value, descricao: descricao.value.trim(),
      linhas: [{ conta: document.getElementById("acr-conta").value, valor: Number(valor.value) }],
    });
    notificar(`Acréscimo lançado: ${la.referencia}.`, "ok");
    invalidarContabilidade();
    await mostrar("periodizacao", true);
  }, "Lançar");
  descricao.focus();
}

// ---------- Balanço ----------

let dataBalanco = null;

async function carregarBalanco(secao) {
  const hoje = new Date();
  dataBalanco = dataBalanco || iso(hoje);
  const d = await obter(`/api/contabilidade/balanco?ate=${dataBalanco}`);
  const data = h("input", { type: "date", id: "balanco-data", value: dataBalanco });
  data.addEventListener("change", () => { if (data.value) { dataBalanco = data.value; mostrar("balanco", true); } });
  const atalhos = h("div", { class: "filtros-linha" },
    botao("Hoje", () => { dataBalanco = iso(hoje); mostrar("balanco", true); }),
    botao(`31/12/${hoje.getFullYear() - 1}`, () => { dataBalanco = `${hoje.getFullYear() - 1}-12-31`; mostrar("balanco", true); }));

  const secoes = Object.fromEntries(d.secoes.map((s) => [s.chave, s]));
  const blocoSecao = (s) => !s.rubricas.length && s.chave === "passivo_nao_corrente" ? [] : [
    h("tr", { class: "agregadora" }, td(s.nome), td("", "num")),
    ...s.rubricas.map((r) => h("tr", {}, td(r.rubrica, "recuo"), td(euros(r.valor), "num"))),
    h("tr", { class: "subtotal" }, td(`Total do ${s.nome.toLowerCase()}`), td(euros(s.total), "num")),
  ];
  const tabela = (titulo, linhas, total, rotuloTotal) => h("div", { class: "tabela-caixa" }, h("table", { class: "tabela dr balanco" },
    h("caption", { class: "visualmente-oculto", texto: titulo }),
    h("tbody", {}, linhas, h("tr", { class: "total" }, td(rotuloTotal), td(euros(total), "num")))));

  preencher(secao, h("section", { class: "cartao" },
    h("div", { class: "cartao-cabecalho" },
      h("div", {}, h("h2", { texto: `Balanço em ${formatarData(d.data)}` }),
        h("p", { class: "subtitulo", texto: "Demonstração da posição financeira (modelo do SNC, simplificado), a partir dos saldos das contas." })),
      h("div", { class: "campo", style: "margin:0" }, h("label", { for: "balanco-data", class: "visualmente-oculto", texto: "Data" }), data)),
    atalhos,
    d.diferenca ? h("p", { class: "alerta erro" }, icone("erro"), `O balanço não fecha por ${euros(d.diferenca)}: verifique contas sem rubrica.`) : null,
    h("div", { class: "grelha-balanco" },
      h("div", {}, h("h3", { class: "secao-titulo-pequeno", texto: "Ativo" }),
        tabela("Ativo", [...blocoSecao(secoes.ativo_nao_corrente), ...blocoSecao(secoes.ativo_corrente)], d.total_ativo, "Total do ativo")),
      h("div", {}, h("h3", { class: "secao-titulo-pequeno", texto: "Capital próprio e passivo" }),
        tabela("Capital próprio e passivo", [...blocoSecao(secoes.capital_proprio), ...blocoSecao(secoes.passivo_nao_corrente),
          ...blocoSecao(secoes.passivo_corrente)], d.total_capital_proprio_passivo, "Total do capital próprio e do passivo"))),
    h("p", { class: "nota-rodape", texto: d.exercicio_fechado
      ? "Exercício fechado: o resultado líquido já inclui o imposto estimado."
      : "O resultado do período é o do exercício em curso, antes do imposto sobre o rendimento (o IRC entra com o fecho do exercício). As contas de terceiros são separadas pelo saldo de cada subconta." })));
}

// ---------- Saldos de abertura ----------

async function cartaoAbertura() {
  const d = await obter("/api/contabilidade/abertura");
  if (d.lancamento) {
    return h("section", { class: "cartao" }, h("div", { class: "cartao-cabecalho" },
      h("div", {}, h("h2", { texto: "Saldos de abertura" }),
        h("p", { class: "subtitulo", texto: `Lançados em ${d.lancamento.referencia} (${formatarData(d.lancamento.data)}), ${euros(d.lancamento.total)} a débito e a crédito. Para os corrigir, estorne esse lançamento e importe de novo.` })),
      criarEtiqueta("Lançados", "ok", "check")));
  }
  const ficheiro = h("input", { type: "file", id: "abertura-ficheiro", class: "visualmente-oculto", accept: ".csv,.txt,.xlsx" });
  const nomeFicheiro = h("span", { class: "ajuda nome-ficheiro", texto: "Nenhum ficheiro escolhido" });
  ficheiro.addEventListener("change", () => { nomeFicheiro.textContent = ficheiro.files[0]?.name || "Nenhum ficheiro escolhido"; });
  const data = h("input", { type: "date", id: "abertura-data", value: `${new Date().getFullYear()}-01-01` });
  const importar = h("button", { type: "submit", class: "botao" }, icone("upload"), "Importar saldos");
  const form = h("form", { class: "filtros-linha" },
    h("div", { class: "campo" }, h("label", { for: "abertura-data", texto: "Data da abertura" }), data),
    h("div", { class: "campo" }, h("span", { class: "rotulo", texto: "Balancete do programa anterior" }),
      h("div", { class: "escolher-ficheiro" }, h("label", { for: "abertura-ficheiro", class: "botao secundario" }, icone("ficheiro"), "Escolher ficheiro"), ficheiro, nomeFicheiro)),
    importar);
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!ficheiro.files.length) return ficheiro.click();
    const dados = new FormData();
    dados.append("ficheiro", ficheiro.files[0]);
    dados.append("data", data.value);
    ocupado(importar, true);
    try {
      const resposta = await api("/api/contabilidade/abertura/importar", { method: "POST", body: dados });
      if (!resposta.ok) throw new Error(await lerErro(resposta, "Não foi possível importar os saldos."));
      const la = await resposta.json();
      notificar(`Saldos de abertura lançados: ${la.referencia}.`, "ok");
      contas = null;
      invalidarContabilidade();
      await mostrar("plano", true);
    } catch (erro) {
      notificar(erro.message, "erro");
      ocupado(importar, false);
    }
  });
  return h("section", { class: "cartao" },
    h("div", { class: "cartao-cabecalho" }, h("div", {}, h("h2", { texto: "Saldos de abertura" }),
      h("p", { class: "subtitulo", texto: "A empresa já tinha contabilidade noutro programa? Importe o balancete de fecho (CSV ou Excel com conta, nome, débito e crédito). Entram só as contas de balanço (classes 1 a 5); as contas de terceiros que faltarem são criadas." }))),
    form);
}

// ---------- Fecho do exercício ----------

// Até abril (época dos fechos) propõe-se o ano anterior; depois, o ano em curso (estimativa e verificações)
let anoFecho = new Date().getFullYear() - (new Date().getMonth() < 4 ? 1 : 0);
const parametrosFecho = { pme: true, taxa: null, taxa_pme: null, derrama: null, ajustes: 0, tributacao_autonoma: null };

async function carregarFecho(secao) {
  const params = new URLSearchParams({ ano: anoFecho, pme: parametrosFecho.pme, ajustes: parametrosFecho.ajustes || 0 });
  for (const chave of ["taxa", "taxa_pme", "derrama", "tributacao_autonoma"]) {
    if (parametrosFecho[chave] !== null && parametrosFecho[chave] !== "") params.set(chave, parametrosFecho[chave]);
  }
  const [d, eu] = await Promise.all([obter(`/api/contabilidade/fecho?${params}`), obter("/api/auth/eu")]);
  const e = d.estimativa;
  const podeAdministrar = eu.papel === "admin" && !eu.modo_demo;

  const esteAno = new Date().getFullYear();
  const ano = h("select", { id: "fecho-ano" }, [0, 1, 2, 3].map((i) => {
    const o = h("option", { value: esteAno - i }, String(esteAno - i));
    if (esteAno - i === anoFecho) o.selected = true;
    return o;
  }));
  ano.addEventListener("change", () => { anoFecho = Number(ano.value); mostrar("fecho", true); });

  // Verificações
  const lista = h("ul", { class: "lista-alertas" }, d.verificacoes.map((v) => h("li", { class: `alerta ${v.estado === "ok" ? "info" : "aviso"}` },
    icone(v.estado === "ok" ? "check" : "alerta"),
    h("div", {}, h("strong", { texto: v.nome }), h("p", { texto: v.detalhe })),
    v.estado !== "ok" && v.destino ? botao("Resolver", () => irPara(v.destino)) : null)));

  // Parâmetros da estimativa
  const numero = (id, valor, props = {}) => h("input", { type: "number", id, value: valor, step: "0.01", ...props });
  const pme = h("input", { type: "checkbox", id: "fecho-pme", checked: e.parametros.pme });
  const taxa = numero("fecho-taxa", e.parametros.taxa, { min: "0", max: "100" });
  const taxaPme = numero("fecho-taxa-pme", e.parametros.taxa_pme, { min: "0", max: "100" });
  const derrama = numero("fecho-derrama", e.parametros.derrama, { min: "0", max: "10" });
  const ajustes = numero("fecho-ajustes", e.ajustes);
  const ta = numero("fecho-ta", e.tributacao_autonoma, { min: "0" });
  let temporizador;
  const recalcular = () => {
    clearTimeout(temporizador);
    temporizador = setTimeout(() => {
      Object.assign(parametrosFecho, { pme: pme.checked, taxa: taxa.value, taxa_pme: taxaPme.value, derrama: derrama.value,
        ajustes: Number(ajustes.value) || 0, tributacao_autonoma: ta.value });
      mostrar("fecho", true);
    }, 500);
  };
  for (const el of [pme, taxa, taxaPme, derrama, ajustes, ta]) el.addEventListener("change", recalcular);

  const linhaValor = (rotulo, valor, classe = null) => h("tr", { class: classe }, td(rotulo), td(euros(valor), "num"));
  const estimativa = h("div", { class: "tabela-caixa" }, h("table", { class: "tabela dr" }, h("tbody", {},
    linhaValor("Resultado antes de impostos", e.resultado_antes_de_impostos),
    linhaValor("Ajustes fiscais (quadro 07 do Modelo 22)", e.ajustes),
    linhaValor("Matéria coletável", e.materia_coletavel, "agregadora"),
    linhaValor("IRC", e.irc), linhaValor("Derrama municipal", e.derrama), linhaValor("Tributação autónoma", e.tributacao_autonoma),
    linhaValor("Imposto estimado", e.imposto_total, "agregadora"),
    linhaValor("Resultado líquido", e.resultado_liquido, "total"))));

  // Ações
  const acoes = [];
  const acao = (texto, url, corpo, mensagem, classe = "botao", confirmacao = null) => {
    const b = h("button", { type: "button", class: classe, disabled: !podeAdministrar }, texto);
    b.addEventListener("click", async () => {
      if (confirmacao && !(await confirmar(confirmacao))) return;
      ocupado(b, true);
      try {
        await enviar(url, corpo);
        notificar(mensagem, "ok");
        invalidar("resumo", "lancamentos", "balancete", "resultados", "extrato", "obrigacoes");
        await mostrar("fecho", true);
      } catch (erro) {
        notificar(erro.message, "erro");
        ocupado(b, false);
      }
    });
    return b;
  };
  if (!d.fechado) {
    const fecharPeriodo = h("input", { type: "checkbox", id: "fecho-periodo", checked: true });
    acoes.push(h("label", { class: "ajuda", style: "display:flex;gap:0.375rem;align-items:center" }, fecharPeriodo, `Fechar também o período até 31/12/${d.ano}`));
    const fechar = h("button", { type: "button", class: "botao", disabled: !podeAdministrar || !d.pode_fechar }, icone("cadeado"), `Fechar o exercício de ${d.ano}`);
    fechar.addEventListener("click", async () => {
      const ok = await confirmar({
        titulo: `Fechar o exercício de ${d.ano}?`,
        texto: `As classes 6 e 7 passam para resultados (811), o imposto estimado de ${euros(e.imposto_total)} fica a pagar (241) e o resultado líquido de ${euros(e.resultado_liquido)} fica na 818. Pode reabrir depois.`,
        botao: "Fechar exercício",
      });
      if (!ok) return;
      ocupado(fechar, true);
      try {
        await enviar("/api/contabilidade/fecho", { ano: d.ano, imposto: e.imposto_total, fechar_periodo: fecharPeriodo.checked });
        notificar(`Exercício de ${d.ano} fechado.`, "ok");
        invalidar("resumo", "lancamentos", "balancete", "resultados", "extrato", "obrigacoes");
        await mostrar("fecho", true);
      } catch (erro) {
        notificar(erro.message, "erro");
        ocupado(fechar, false);
      }
    });
    acoes.push(fechar);
  } else {
    acoes.push(criarEtiqueta(`Fechado: ${d.lancamentos.map((l) => l.referencia).join(", ")}`, "ok", "check"));
    if (d.resultado_liquido_por_transferir) {
      acoes.push(acao(`Transferir ${euros(d.resultado_liquido_por_transferir)} para resultados transitados`, "/api/contabilidade/fecho/transferir",
        { ano: d.ano }, "Resultado transferido para a conta 561."));
    }
    acoes.push(acao("Reabrir", "/api/contabilidade/fecho/reabrir", { ano: d.ano }, `Exercício de ${d.ano} reaberto.`, "botao fantasma", {
      titulo: `Reabrir o exercício de ${d.ano}?`, texto: "Os lançamentos do fecho são estornados. O período tem de estar aberto.", botao: "Reabrir", perigo: true,
    }));
  }

  preencher(secao, 
    h("section", { class: "cartao" },
      h("div", { class: "cartao-cabecalho" },
        h("div", {}, h("h2", { texto: "Fecho do exercício" }),
          h("p", { class: "subtitulo", texto: "Antes de fechar, trate o que estiver por lançar até 31 de dezembro. O fecho salda as contas de gastos e rendimentos e apura o resultado líquido." })),
        h("div", { class: "campo", style: "margin:0" }, h("label", { for: "fecho-ano", class: "visualmente-oculto", texto: "Exercício" }), ano)),
      lista),
    h("section", { class: "cartao" },
      h("div", { class: "cartao-cabecalho" }, h("div", {}, h("h2", { texto: `Estimativa do imposto sobre o rendimento de ${d.ano}` }),
        h("p", { class: "subtitulo", texto: d.aviso }))),
      h("div", { class: "filtros-linha" },
        h("label", { class: "campo", style: "flex-direction:row;align-items:center;gap:0.5rem" }, pme, "Regime das PME (taxa reduzida nos primeiros 50 000 €)")),
      h("div", { class: "filtros-linha" },
        campoJanela("fecho-taxa", "Taxa de IRC (%)", taxa), campoJanela("fecho-taxa-pme", "Taxa das PME (%)", taxaPme),
        campoJanela("fecho-derrama", "Derrama municipal (%)", derrama), campoJanela("fecho-ajustes", "Ajustes fiscais (€)", ajustes),
        campoJanela("fecho-ta", "Tributação autónoma (€)", ta)),
      estimativa,
      h("div", { class: "barra-lote", style: "margin-top:1rem;justify-content:flex-end" }, ...acoes),
      !podeAdministrar ? h("p", { class: "ajuda", texto: eu.modo_demo ? "Na demonstração não é possível fechar o exercício." : "Só os administradores podem fechar ou reabrir o exercício." })
        : !d.pode_fechar && !d.fechado ? h("p", { class: "ajuda", texto: `O exercício de ${d.ano} só pode ser fechado depois de 31/12/${d.ano}.` }) : null),
  );
}

// ---------- Bancos: extratos e reconciliação ----------

let contaBanco = "121";
let filtroBanco = "pendente";

const NOMES_SUGESTAO = { associar: "Associar", pagar_fatura: "Pagar fatura", receber_venda: "Receber", lancar: "Lançar" };

async function acaoMovimento(botaoAcao, url, corpo, mensagem) {
  ocupado(botaoAcao, true);
  try {
    await enviar(url, corpo);
    notificar(mensagem, "ok");
    invalidar("resumo", "lancamentos", "balancete", "iva", "resultados", "fornecedores", "extrato", "por-contabilizar");
    await mostrar("bancos", true);
  } catch (erro) {
    notificar(erro.message, "erro");
    ocupado(botaoAcao, false);
  }
}

function botaoSugestao(mov, s, principal = false) {
  const base = `/api/contabilidade/banco/movimentos/${mov.id}`;
  const classe = principal ? "botao pequeno" : "botao secundario pequeno";
  const b = h("button", { type: "button", class: classe }, NOMES_SUGESTAO[s.tipo]);
  b.addEventListener("click", () => {
    if (s.tipo === "associar") acaoMovimento(b, `${base}/associar`, { linha_id: s.linha_id }, "Movimento reconciliado.");
    else if (s.tipo === "pagar_fatura") acaoMovimento(b, `${base}/pagar-fatura`, { fatura_id: s.fatura_id }, "Pagamento registado e reconciliado.");
    else if (s.tipo === "receber_venda") acaoMovimento(b, `${base}/receber-venda`, { venda_id: s.venda_id }, "Recebimento registado e reconciliado.");
    else acaoMovimento(b, `${base}/lancar`, { conta: s.conta }, `Lançado em ${s.conta} e reconciliado.`);
  });
  return b;
}

function cartaoMovimento(mov) {
  const entrada = mov.montante > 0;
  const valor = h("strong", { class: `num${entrada ? "" : " valor-negativo"}`, texto: `${entrada ? "+" : "−"} ${euros(Math.abs(mov.montante))}` });
  const topo = h("div", { class: "lancamento-topo" },
    h("time", { datetime: mov.data, texto: formatarData(mov.data) }),
    h("span", { class: "desc", texto: mov.descricao }), valor);
  const base = `/api/contabilidade/banco/movimentos/${mov.id}`;

  if (mov.estado !== "pendente") {
    const desfazer = h("button", { type: "button", class: "botao fantasma pequeno" }, icone("desfazer"), "Desfazer");
    desfazer.addEventListener("click", () => acaoMovimento(desfazer, `${base}/desfazer`, {}, "O movimento voltou a estar por reconciliar."));
    topo.append(
      mov.estado === "reconciliado" ? criarEtiqueta("Reconciliado", "ok", "check") : criarEtiqueta("Ignorado", "neutro"),
      desfazer);
    return h("article", { class: "lancamento" }, topo,
      mov.lancamento ? h("p", { class: "lancamento-notas" }, "Lançamento: ",
        h("a", { href: "#lancamentos", texto: `${mov.lancamento.referencia} · ${mov.lancamento.descricao}` })) : null);
  }

  const sugs = mov.sugestoes || [];
  const melhor = sugs[0];
  const principal = melhor
    ? h("div", { class: "sugestao-principal" },
      h("span", { class: "explicacao" }, icone(melhor.tipo === "lancar" ? "livro" : "check"), h("span", { texto: melhor.texto })),
      botaoSugestao(mov, melhor, true))
    : h("p", { class: "ajuda sugestao-principal", texto: "Sem sugestão: escolha a conta onde lançar este movimento." });

  // Lançar noutra conta (com as contas de movimento como sugestão) e ignorar
  const idConta = `conta-mov-${mov.id}`;
  const campoConta = h("input", { type: "text", id: idConta, list: "contas-movimento", placeholder: "Ex.: 6988", inputmode: "numeric", autocomplete: "off" });
  const lancar = h("button", { type: "button", class: "botao secundario pequeno" }, "Lançar");
  lancar.addEventListener("click", () => {
    const conta = campoConta.value.split(" ")[0].trim();
    if (!conta) return campoConta.focus();
    acaoMovimento(lancar, `${base}/lancar`, { conta }, `Lançado em ${conta} e reconciliado.`);
  });
  const ignorar = h("button", { type: "button", class: "botao fantasma pequeno", title: "Use quando o movimento já está na contabilidade por outra via" }, "Ignorar");
  ignorar.addEventListener("click", () => acaoMovimento(ignorar, `${base}/ignorar`, {}, "Movimento ignorado."));

  const outras = h("details", {},
    h("summary", { texto: sugs.length > 1 ? `Outras opções (${sugs.length - 1} sugest${sugs.length === 2 ? "ão" : "ões"})` : "Outras opções" }),
    sugs.slice(1).map((s) => h("div", { class: "sugestao-principal" }, h("span", { class: "ajuda", texto: s.texto }), botaoSugestao(mov, s))),
    h("div", { class: "filtros-linha", style: "margin:0.5rem 0 0" },
      h("div", { class: "campo" }, h("label", { for: idConta, texto: "Lançar na conta" }), campoConta), lancar, ignorar));

  return h("article", { class: "lancamento proposta-banco" }, topo, h("div", { class: "corpo-movimento" }, principal, outras));
}

function linhaMapa(rotulo, valor, sinal = "", destaque = false) {
  return h("tr", { class: destaque ? "agregadora" : null }, td(rotulo), td(valor === null ? "—" : `${sinal}${euros(valor)}`, "num"));
}

async function carregarBancos(secao) {
  const [contasBanco] = await Promise.all([obter("/api/contabilidade/banco/contas"), obterContas()]);
  if (!contasBanco.some((c) => c.codigo === contaBanco)) contaBanco = contasBanco[0]?.codigo || "121";
  const [dados, mapa, importacoes] = await Promise.all([
    obter(`/api/contabilidade/banco/movimentos?conta=${contaBanco}${filtroBanco ? `&estado=${filtroBanco}` : ""}`),
    obter(`/api/contabilidade/banco/reconciliacao?conta=${contaBanco}`),
    obter(`/api/contabilidade/banco/extratos?conta=${contaBanco}`),
  ]);
  const conta = contasBanco.find((c) => c.codigo === contaBanco) || { saldo: 0, por_reconciliar: 0 };

  // Escolha da conta
  const seletor = h("select", { id: "banco-conta" }, contasBanco.map((c) => {
    const o = h("option", { value: c.codigo }, `${c.codigo} · ${c.nome}${c.por_reconciliar ? ` (${c.por_reconciliar})` : ""}`);
    if (c.codigo === contaBanco) o.selected = true;
    return o;
  }));
  seletor.addEventListener("change", () => { contaBanco = seletor.value; mostrar("bancos", true); });

  // Importação
  // Campo de ficheiro próprio: o do navegador mostra "Choose File" na língua do navegador, não da página
  const ficheiro = h("input", { type: "file", id: "banco-ficheiro", class: "visualmente-oculto", accept: ".csv,.txt,.xlsx,.ofx,.qfx" });
  const nomeFicheiro = h("span", { class: "ajuda nome-ficheiro", texto: "Nenhum ficheiro escolhido" });
  ficheiro.addEventListener("change", () => {
    nomeFicheiro.textContent = ficheiro.files[0]?.name || "Nenhum ficheiro escolhido";
  });
  const saldo = h("input", { type: "text", id: "banco-saldo", inputmode: "decimal", placeholder: "Opcional", autocomplete: "off" });
  const importar = h("button", { type: "submit", class: "botao" }, icone("upload"), "Importar extrato");
  const formImportar = h("form", { class: "filtros-linha" },
    h("div", { class: "campo largo" },
      h("span", { class: "rotulo", id: "rotulo-banco-ficheiro", texto: "Extrato (CSV, Excel ou OFX)" }),
      h("div", { class: "escolher-ficheiro" },
        h("label", { for: "banco-ficheiro", class: "botao secundario", "aria-describedby": "rotulo-banco-ficheiro" },
          icone("ficheiro"), "Escolher ficheiro"),
        ficheiro, nomeFicheiro)),
    h("div", { class: "campo" }, h("label", { for: "banco-saldo", texto: "Saldo final do extrato" }), saldo),
    importar);
  formImportar.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!ficheiro.files.length) return ficheiro.focus();
    const dadosForm = new FormData();
    dadosForm.append("ficheiro", ficheiro.files[0]);
    dadosForm.append("conta", contaBanco);
    if (saldo.value.trim()) dadosForm.append("saldo_final", saldo.value.trim());
    ocupado(importar, true);
    try {
      const resposta = await api("/api/contabilidade/banco/importar", { method: "POST", body: dadosForm });
      if (!resposta.ok) throw new Error(await lerErro(resposta, "Não foi possível importar o extrato."));
      const r = await resposta.json();
      notificar(`${r.importados} movimento${r.importados === 1 ? "" : "s"} importado${r.importados === 1 ? "" : "s"}`
        + (r.duplicados ? ` (${r.duplicados} já existia${r.duplicados === 1 ? "" : "m"})` : "")
        + (r.mapeado_por_ia ? ". As colunas foram identificadas pela IA." : "."), "ok");
      filtroBanco = "pendente";
      invalidar("resumo");
      await mostrar("bancos", true);
    } catch (erro) {
      notificar(erro.message, "erro");
      ocupado(importar, false);
    }
  });

  const listaImportacoes = importacoes.length ? h("details", {},
    h("summary", { texto: `Extratos importados (${importacoes.length})` }),
    h("ul", { class: "lista-importacoes" }, importacoes.map((x) => {
      const apagar = botaoIcone("lixo", `Apagar a importação ${x.nome_ficheiro}`, async () => {
        const ok = await confirmar({ titulo: "Apagar esta importação?", texto: `Os ${x.movimentos} movimentos de ${x.nome_ficheiro} saem da lista. Só é possível se nenhum estiver reconciliado.`, botao: "Apagar", perigo: true });
        if (!ok) return;
        const r = await api(`/api/contabilidade/banco/extratos/${x.id}`, { method: "DELETE" });
        if (!r.ok) return notificar(await lerErro(r, "Não foi possível apagar."), "erro");
        notificar("Importação apagada.", "ok");
        mostrar("bancos", true);
      }, "pequeno perigo");
      return h("li", {}, h("span", { texto: `${x.nome_ficheiro} · ${formatarData(x.data_inicio)} a ${formatarData(x.data_fim)} · ${x.movimentos} movimentos` }), apagar);
    }))) : null;

  // Movimentos
  const filtros = h("div", { class: "segmentos", role: "group", "aria-label": "Mostrar movimentos" },
    [["pendente", "Por reconciliar"], ["reconciliado", "Reconciliados"], ["ignorado", "Ignorados"], ["", "Todos"]].map(([v, t]) => {
      const b = h("button", { type: "button", "aria-pressed": String(filtroBanco === v) }, t);
      b.addEventListener("click", () => { filtroBanco = v; mostrar("bancos", true); });
      return b;
    }));
  const automatico = h("button", { type: "button", class: "botao", disabled: !dados.sugestoes_seguras },
    icone("faisca"), `Reconciliar automaticamente (${dados.sugestoes_seguras})`);
  automatico.title = "Aplica só as sugestões sem dúvidas: um único lançamento ou fatura com o mesmo valor e data próxima, ou uma regra que já ensinou";
  automatico.addEventListener("click", async () => {
    ocupado(automatico, true);
    try {
      const r = await enviar("/api/contabilidade/banco/reconciliar-automaticamente", { conta: contaBanco });
      notificar(`${r.total} movimento${r.total === 1 ? "" : "s"} reconciliado${r.total === 1 ? "" : "s"}`
        + (r.pagar_fatura ? ` (${r.pagar_fatura} fatura${r.pagar_fatura === 1 ? "" : "s"} paga${r.pagar_fatura === 1 ? "" : "s"})` : "") + ".", "ok");
      invalidar("resumo", "lancamentos", "balancete", "fornecedores", "extrato");
      await mostrar("bancos", true);
    } catch (erro) {
      notificar(erro.message, "erro");
      ocupado(automatico, false);
    }
  });
  const lista = dados.movimentos.length
    ? h("div", {}, dados.movimentos.map(cartaoMovimento))
    : vazio("check", filtroBanco === "pendente" ? "Nada por reconciliar" : "Sem movimentos",
      filtroBanco === "pendente" ? "Todos os movimentos importados estão reconciliados." : "Importe um extrato para começar.");

  // Mapa de reconciliação
  const diferenca = mapa.diferenca_por_explicar;
  const estadoMapa = mapa.saldo_banco === null
    ? criarEtiqueta("Sem saldo do banco: indique-o ao importar", "aviso", "alerta")
    : Math.abs(diferenca) < 0.005 ? criarEtiqueta("Reconciliado: a diferença está toda explicada", "ok", "check")
      : criarEtiqueta(`Diferença por explicar: ${euros(diferenca)}`, "aviso", "alerta");
  const foraDoBanco = mapa.lancamentos_fora_do_banco;

  preencher(secao, 
    h("section", { class: "indicadores", "aria-label": "Resumo da conta" },
      indicador("Saldo na contabilidade", euros(conta.saldo), `conta ${contaBanco} até hoje`, "livro", true),
      indicador("Saldo do banco", mapa.saldo_banco === null ? "—" : euros(mapa.saldo_banco),
        mapa.saldo_banco === null ? "importe um extrato com saldo" : `último extrato, ${formatarData(mapa.data)}`, "carteira"),
      indicador("Por reconciliar", String(conta.por_reconciliar), "movimentos do banco", "relogio"),
      indicador("Diferença por explicar", diferenca === null ? "—" : euros(diferenca),
        diferenca === null ? "sem saldo do banco" : Math.abs(diferenca) < 0.005 ? "tudo explicado" : "desaparece à medida que reconcilia", "balanca")),
    h("section", { class: "cartao" },
      h("div", { class: "cartao-cabecalho" },
        h("div", {}, h("h2", { texto: "Importar extrato" }),
          h("p", { class: "subtitulo", texto: "Exporte o extrato no site do banco em CSV, Excel ou OFX. As colunas são reconhecidas sozinhas e os movimentos já importados não se repetem." })),
        h("div", { class: "campo", style: "margin:0" }, h("label", { for: "banco-conta", class: "visualmente-oculto", texto: "Conta bancária" }), seletor)),
      formImportar, listaImportacoes),
    h("section", { class: "cartao" },
      h("div", { class: "cartao-cabecalho" }, filtros, automatico),
      lista),
    h("section", { class: "cartao" },
      h("div", { class: "cartao-cabecalho" },
        h("div", {}, h("h2", { texto: "Mapa de reconciliação bancária" }),
          h("p", { class: "subtitulo", texto: `Conta ${contaBanco} em ${formatarData(mapa.data)}` })),
        estadoMapa),
      h("div", { class: "tabela-caixa" }, h("table", { class: "tabela dr" }, h("tbody", {},
        linhaMapa("Saldo na contabilidade nessa data", mapa.saldo_contabilistico_na_data),
        linhaMapa("Entradas no banco ainda por lançar", mapa.entradas_banco_por_lancar, "+ "),
        linhaMapa("Saídas no banco ainda por lançar", mapa.saidas_banco_por_lancar, "− "),
        linhaMapa("Débitos na contabilidade que ainda não estão no banco", mapa.debitos_contabilidade_fora_do_banco, "− "),
        linhaMapa("Créditos na contabilidade que ainda não estão no banco (ex.: cheques por descontar)", mapa.creditos_contabilidade_fora_do_banco, "+ "),
        linhaMapa("Saldo explicado", mapa.saldo_explicado, "", true),
        linhaMapa("Saldo do banco (extrato)", mapa.saldo_banco, "", true)))),
      foraDoBanco.length ? h("details", {},
        h("summary", { texto: `Lançamentos que ainda não estão no banco (${foraDoBanco.length})` }),
        h("div", { class: "tabela-caixa" }, h("table", { class: "tabela razao" }, h("tbody", {}, foraDoBanco.map((l) => h("tr", {},
          td(formatarData(l.data)), h("td", { class: "conta-codigo", texto: l.referencia }), td(l.descricao),
          td(eurosOuVazio(l.debito), "num"), td(eurosOuVazio(l.credito), "num")))))))
        : null,
      h("p", { class: "nota-rodape", texto: "Só contam os lançamentos a partir do primeiro movimento importado. Diferenças de datas (um pagamento lançado num dia e debitado no seguinte) desaparecem quando o movimento é reconciliado." })),
  );
}

// ---------- Assistente ----------

const historico = [];

// Markdown mínimo e seguro: parágrafos, listas e **negrito** (sem HTML)
function textoFormatado(texto) {
  const frag = document.createDocumentFragment();
  const inline = (linha) => {
    const el = document.createDocumentFragment();
    linha.split(/(\*\*[^*]+\*\*)/g).forEach((parte) => {
      if (parte.startsWith("**") && parte.endsWith("**") && parte.length > 4) el.append(h("strong", { texto: parte.slice(2, -2) }));
      else if (parte) el.append(parte);
    });
    return el;
  };
  for (const bloco of texto.split(/\n{2,}/)) {
    const linhas = bloco.split("\n").filter((l) => l.trim());
    if (!linhas.length) continue;
    if (linhas.every((l) => /^\s*([-*•]|\d+\.)\s+/.test(l))) {
      frag.append(h("ul", {}, linhas.map((l) => h("li", {}, inline(l.replace(/^\s*([-*•]|\d+\.)\s+/, ""))))));
    } else {
      const p = h("p", {});
      linhas.forEach((l, i) => {
        if (i) p.append(h("br"));
        p.append(inline(l.replace(/^#+\s*/, "")));
      });
      frag.append(p);
    }
  }
  return frag;
}

function cartaoPropostaAssistente(p) {
  const lancar = h("button", { type: "button", class: "botao pequeno" }, icone("check"), "Lançar");
  const rever = botao("Rever e editar", () => abrirEditor({ ...p, origem: "assistente" }, () => {
    lancar.replaceWith(criarEtiqueta("Lançado", "ok", "check"));
    rever.remove();
  }), "botao secundario pequeno");
  lancar.addEventListener("click", async () => {
    ocupado(lancar, true);
    try {
      const la = await enviar("/api/contabilidade/lancamentos", {
        diario: p.diario, data: p.data, descricao: p.descricao, documento: p.documento, origem: "assistente",
        linhas: p.linhas.map((l) => ({ conta: l.conta, debito: l.debito, credito: l.credito, descricao: l.descricao })),
      });
      notificar(`Lançado: ${la.referencia}.`, "ok");
      invalidar("resumo", "lancamentos", "balancete", "iva", "resultados", "fornecedores", "extrato");
      lancar.replaceWith(criarEtiqueta(`Lançado: ${la.referencia}`, "ok", "check"));
      rever.remove();
    } catch (erro) {
      notificar(erro.message, "erro");
      ocupado(lancar, false);
    }
  });
  return h("div", { class: "proposta" },
    h("div", { class: "proposta-topo" },
      h("div", {}, h("h3", { texto: `Proposta: ${p.descricao}` }),
        h("p", { class: "subtitulo", texto: `Diário ${p.diario} · ${formatarData(p.data)}${p.documento ? ` · ${p.documento}` : ""}` })),
      h("div", { class: "cabecalho-acoes" }, rever, lancar)),
    tabelaLinhas(p.linhas, true));
}

const NOMES_FERRAMENTAS = {
  balancete: "balancete", extrato_conta: "extrato", pesquisar_lancamentos: "diário", mapa_iva: "mapa do IVA",
  demonstracao_resultados: "resultados", contas_correntes_fornecedores: "contas correntes", plano_de_contas: "plano de contas",
  obrigacoes_fiscais: "calendário fiscal", alertas: "verificações", propor_lancamento: "proposta de lançamento",
};

function iniciarAssistente() {
  const conversa = document.getElementById("conversa");
  const form = document.getElementById("compositor");
  const campo = document.getElementById("pergunta");
  const botaoEnviar = document.getElementById("enviar");

  const rolar = () => { conversa.scrollTop = conversa.scrollHeight; };

  async function perguntar(texto) {
    texto = texto.trim();
    if (!texto) return;
    document.getElementById("sugestoes")?.remove();
    conversa.append(h("div", { class: "mensagem utilizador", texto }));
    const aPensar = h("div", { class: "mensagem assistente", "aria-label": "O assistente está a responder" },
      h("span", { class: "a-pensar" }, h("i"), h("i"), h("i")), " A consultar a contabilidade…");
    conversa.append(aPensar);
    rolar();
    campo.value = "";
    ocupado(botaoEnviar, true);
    try {
      const r = await enviar("/api/contabilidade/assistente", { pergunta: texto, historico: historico.slice(-12) });
      historico.push({ papel: "utilizador", texto }, { papel: "assistente", texto: r.resposta });
      const usadas = [...new Set(r.ferramentas.map((f) => NOMES_FERRAMENTAS[f] || f))];
      aPensar.replaceWith(h("div", { class: "mensagem assistente" },
        textoFormatado(r.resposta),
        ...r.propostas.map(cartaoPropostaAssistente),
        usadas.length ? h("span", { class: "ferramentas-usadas", texto: `Consultou: ${usadas.join(", ")}` }) : null));
    } catch (erro) {
      aPensar.replaceWith(h("div", { class: "mensagem assistente erro", texto: erro.message }));
    } finally {
      ocupado(botaoEnviar, false);
      rolar();
      campo.focus();
    }
  }

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    perguntar(campo.value);
  });
  campo.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (!botaoEnviar.disabled) perguntar(campo.value);
    }
  });
  document.getElementById("sugestoes").addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (b) perguntar(b.textContent);
  });
}

// ---------- Arranque ----------

document.addEventListener("DOMContentLoaded", () => {
  iniciarPeriodo();
  iniciarSeparadores();
  iniciarEditor();
  iniciarAssistente();
  lerHash();
  // O contador "Por contabilizar" aparece logo, mesmo noutra secção
  if ((location.hash || "#resumo") !== "#resumo") {
    obter("/api/contabilidade/resumo").then((d) => {
      iaDisponivel = d.ia_disponivel;
      atualizarContador(d.por_contabilizar.quantidade);
    }).catch(() => {});
  }
});
