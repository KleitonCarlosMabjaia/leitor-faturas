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
  secao.replaceChildren(h("div", { class: "cartao" },
    h("div", { class: "esqueleto", style: "height:1.25rem;width:40%;margin-bottom:1rem" }),
    h("div", { class: "esqueleto", style: "height:8rem" })));
}

function erroNaSecao(secao, erro) {
  secao.replaceChildren(h("div", { class: "cartao" },
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
  apuramento: "Apuramento", assistente: "Assistente IA", banco: "Extrato bancário", venda: "Venda", recebimento: "Recebimento", inventario: "Inventário",
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
  document.getElementById("periodo").hidden = ["assistente", "obrigacoes", "plano", "por-contabilizar", "iva", "fornecedores", "bancos", "inventarios"].includes(nome);

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

  secao.replaceChildren(
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

function cartaoProposta(p, aoContabilizar) {
  const f = p.fatura;
  const c = p.classificacao;
  const idConta = `conta-${f.id}`;
  const idPerfil = `perfil-${f.id}`;
  const selConta = seletorConta(idConta, c.conta);
  const selPerfil = seletorPerfil(idPerfil, c.perfil_iva);
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

  const botaoLancar = h("button", { type: "button", class: "botao" }, icone("check"), "Contabilizar");
  botaoLancar.addEventListener("click", async () => {
    ocupado(botaoLancar, true);
    try {
      const corpo = alterado || c.origem !== "regra" ? { conta_codigo: selConta.value, perfil_iva: selPerfil.value } : {};
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
    secao.replaceChildren(h("div", { class: "cartao" },
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

  secao.replaceChildren(h("section", { class: "cartao" },
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

  secao.replaceChildren(h("section", { class: "cartao" },
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
  secao.replaceChildren(h("section", { class: "cartao" },
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
    h("p", { class: "nota-rodape", texto: "Acumulado: nas classes 1 a 5 desde o início; nas classes 6 a 8 desde 1 de janeiro do ano da data final." })));
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

  secao.replaceChildren(h("section", { class: "cartao" },
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

  secao.replaceChildren(h("section", { class: "cartao" },
    h("div", { class: "cartao-cabecalho" },
      h("div", {}, h("h2", { texto: `Apuramento do IVA - ${d.descricao}` }),
        h("p", { class: "subtitulo", texto: `De ${formatarData(d.de)} a ${formatarData(d.ate)} · valores para a declaração periódica (quadro 06)` }))),
    seletor,
    h("h3", { class: "secao-titulo-pequeno", texto: "IVA dedutível (a favor da empresa)" }), campos,
    h("h3", { class: "secao-titulo-pequeno", texto: "IVA liquidado (a favor do Estado)" }), liquidado,
    d.iva_nao_dedutivel ? h("p", { class: "ajuda", style: "margin-top:1rem" },
      `IVA não dedutível levado a gasto no período: ${euros(d.iva_nao_dedutivel)} (art. 21.º do CIVA). Não entra na declaração.`) : null,
    h("div", { class: "resultado-iva" },
      h("div", {}, h("span", { class: "indicador-rotulo", texto: aPagar ? "IVA a pagar ao Estado" : "IVA a recuperar / reportar" }),
        h("br"), h("strong", { texto: euros(Math.abs(d.saldo)) })),
      d.apuramentos.length
        ? h("div", {}, criarEtiqueta(`Apurado: ${d.apuramentos.map((a) => a.referencia).join(", ")}`, "ok", "check"))
        : botaoApurar),
    h("p", { class: "nota-rodape", texto: "Mapa de apoio: confirme os valores antes de submeter a declaração no Portal das Finanças. Regularizações (campos 40/41) e operações intracomunitárias não são calculadas automaticamente." })));
}

// ---------- Resultados ----------

async function carregarResultados(secao) {
  const d = await obter(`/api/contabilidade/resultados?${qsPeriodo()}`);
  const maior = Math.max(1, ...d.detalhe_fse.map((x) => x.valor));
  secao.replaceChildren(
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

  secao.replaceChildren(h("section", { class: "cartao" },
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
  secao.replaceChildren(
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
  await obterContas(true);
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

  secao.replaceChildren(
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

  secao.replaceChildren(
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

  secao.replaceChildren(
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

  secao.replaceChildren(
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
