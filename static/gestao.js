// Dobra - gestão: painel do empresário, clientes, artigos e stock, tesouraria e orçamento.
// Tudo ligado à contabilidade: as compras vêm das faturas lidas, as vendas do SAF-T de faturação,
// o stock passa para os inventários (CMVMC) e o real do orçamento vem dos lançamentos.
//
// O DOM é montado com h() e textContent (nunca innerHTML com dados).

const euros = (v) => formatarValor(v ?? 0, "EUR");
const numero = (v, casas = 2) => (v ?? 0).toLocaleString("pt-PT", { maximumFractionDigits: casas });
const MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"];

function td(texto, classe = "", rotulo = null) {
  return h("td", { class: classe || null, "data-rotulo": rotulo }, texto ?? "");
}

function th(texto, classe = "") {
  return h("th", { scope: "col", class: classe || null, texto });
}

function tabela(cabecalhos, linhas, classe = "tabela responsiva") {
  return h("div", { class: "tabela-caixa" }, h("table", { class: classe },
    h("thead", {}, h("tr", {}, cabecalhos)), h("tbody", {}, linhas)));
}

function cartao(titulo, subtitulo, ...conteudo) {
  return h("section", { class: "cartao" },
    h("div", { class: "cartao-cabecalho" }, h("div", {},
      h("h2", { texto: titulo }), subtitulo ? h("p", { class: "subtitulo", texto: subtitulo }) : null)),
    ...conteudo);
}

function indicador(rotulo, valor, nota, nomeIcone, href = null) {
  const corpo = [
    h("div", { class: "indicador-topo" },
      h("span", { class: "indicador-rotulo", texto: rotulo }),
      h("span", { class: "indicador-icone" }, icone(nomeIcone))),
    h("span", { class: "indicador-valor", texto: valor }),
    h("span", { class: "indicador-nota" }, nota)];
  return href ? h("a", { class: "indicador ligacao-indicador", href }, ...corpo) : h("div", { class: "indicador" }, ...corpo);
}

function carregando(secao) {
  preencher(secao, h("div", { class: "cartao" },
    h("div", { class: "esqueleto", style: "height:1.25rem;width:40%;margin-bottom:1rem" }),
    h("div", { class: "esqueleto", style: "height:8rem" })));
}

// ---------- Navegação entre secções ----------

const SECOES = {
  painel: carregarPainel,
  clientes: carregarClientes,
  artigos: carregarArtigos,
  tesouraria: carregarTesouraria,
  orcamento: carregarOrcamento,
};
let atual = "painel";
let eu = null;

async function mostrar(nome) {
  if (!SECOES[nome]) nome = "painel";
  atual = nome;
  const titulo = tituloDaArea(nome) || "Gestão";
  document.getElementById("titulo-secao").textContent = titulo;
  document.getElementById("migalhas").textContent = grupoDaArea(nome) || "Gestão";
  document.title = `${titulo} · ${NOME_PRODUTO}`;
  for (const secao of document.querySelectorAll("main > section[id^=sec-]")) secao.hidden = secao.id !== `sec-${nome}`;
  const secao = document.getElementById(`sec-${nome}`);
  carregando(secao);
  try {
    await SECOES[nome](secao);
  } catch (erro) {
    preencher(secao, h("div", { class: "cartao" }, h("div", { class: "vazio" },
      h("span", { class: "vazio-icone" }, icone("erro")),
      h("strong", { texto: "Não foi possível carregar" }), h("span", { texto: erro.message }),
      h("button", { type: "button", class: "botao secundario pequeno", onclick: () => mostrar(atual) }, "Tentar de novo"))));
  }
}

const temContabilidade = () => (eu?.funcionalidades || []).includes("contabilidade");

// ---------- Painel ----------

function variacao(pct) {
  if (pct === null || pct === undefined) return h("span", { texto: "sem mês anterior para comparar" });
  const sobe = pct >= 0;
  return h("span", { class: sobe ? "positivo" : "negativo" }, icone(sobe ? "seta-cima" : "seta-baixo"),
    `${sobe ? "+" : ""}${numero(pct, 1)}% face ao mês anterior`);
}

function graficoBarras(colunas, series, alturaMaxima = null) {
  const maior = alturaMaxima || Math.max(1, ...colunas.flatMap((c) => series.map((s) => Math.abs(c[s.chave] || 0))));
  return h("div", { class: "barras", role: "img", "aria-label": series.map((s) => s.nome).join(" e ") },
    colunas.map((c) => h("div", { class: "barras-coluna", title: `${c.rotulo}: ${series.map((s) => `${s.nome} ${euros(c[s.chave])}`).join(" · ")}` },
      h("div", { class: "barras-par" }, series.map((s) => h("span", {
        class: `barra-v ${s.classe}${(c[s.chave] || 0) < 0 ? " negativo" : ""}`,
        style: `height:${Math.max(1, Math.round((Math.abs(c[s.chave] || 0) / maior) * 100))}%`,
      }))),
      h("span", { class: "barras-rotulo", texto: c.rotulo }))));
}

function ranking(itens, cor) {
  if (!itens.length) return h("p", { class: "ajuda", texto: "Ainda sem dados este ano." });
  const maior = Math.max(...itens.map((i) => i.valor), 1);
  return h("ol", { class: "lista-ranking", style: `--cor-rank:${cor}` }, itens.map((i) => h("li", {},
    h("span", { class: "nome", texto: i.nome }), h("span", { class: "valor", texto: euros(i.valor) }),
    h("span", { class: "trilho" }, h("span", { style: `width:${Math.round((i.valor / maior) * 100)}%` })))));
}

async function carregarPainel(secao) {
  const d = await obter("/api/gestao/painel");
  const alertas = d.alertas.length ? h("ul", { class: "alertas-gestao", "aria-label": "Alertas" }, d.alertas.map((a) =>
    h("li", { class: a.nivel }, h("a", { href: a.ligacao }, icone(a.nivel === "erro" ? "erro" : "alerta"),
      h("span", { texto: a.texto }), icone("seta-direita"))))) : null;

  const indicadores = h("div", { class: "indicadores seis" },
    indicador("Vendas do mês", euros(d.vendas.mes), variacao(d.vendas.variacao), "venda", "/contabilidade#vendas"),
    indicador("Resultado do ano", euros(d.resultado_ano),
      d.margem.percentual !== null ? `Margem bruta dos artigos: ${numero(d.margem.percentual, 1)}%` : "Pela contabilidade",
      "euro", "/contabilidade#resultados"),
    indicador("Dinheiro disponível", euros(d.tesouraria.saldo),
      h("span", { class: d.tesouraria.saldo_30_dias < 0 ? "negativo" : null, texto: `Daqui a 30 dias: ${euros(d.tesouraria.saldo_30_dias)}` }),
      "carteira", "#tesouraria"),
    indicador("A receber de clientes", euros(d.receber.total),
      d.receber.vencido ? h("span", { class: "negativo", texto: `${euros(d.receber.vencido)} vencido` }) : "Nada vencido",
      "cliente", "#clientes"),
    indicador("A pagar a fornecedores", euros(d.pagar.total),
      d.pagar.vencido ? h("span", { class: "negativo", texto: `${euros(d.pagar.vencido)} vencido` }) : "Nada vencido",
      "recibo", "/contabilidade#fornecedores"),
    indicador("Valor do stock", euros(d.stock.valor),
      d.stock.abaixo_minimo.length ? h("span", { class: "negativo", texto: `${d.stock.abaixo_minimo.length} abaixo do mínimo` })
        : `${d.stock.artigos} artigo(s)`, "caixa", "#artigos"));

  const evolucao = cartao("Vendas e compras", "Últimos 12 meses, sem IVA",
    graficoBarras(d.evolucao, [{ chave: "vendas", nome: "Vendas", classe: "vendas" }, { chave: "compras", nome: "Compras", classe: "compras" }]),
    h("div", { class: "legenda" }, h("span", { style: "--c:var(--grafico)", texto: "Vendas" }), h("span", { style: "--c:var(--grafico-2)", texto: "Compras" })));
  const semanas = d.tesouraria.semanas.map((s) => ({ ...s, rotulo: formatarData(s.de).slice(0, 5) }));
  const tesouraria = cartao("Tesouraria prevista", "Saldo no fim de cada semana",
    graficoBarras(semanas, [{ chave: "saldo", nome: "Saldo", classe: "saldo" }]),
    h("p", { class: "ajuda" }, h("a", { href: "#tesouraria", texto: "Ver a previsão completa" })));

  const orc = d.orcamento.tem
    ? h("p", {}, "Resultado até agora: ", h("strong", { texto: euros(d.orcamento.resultado_real_ate) }),
      ` (orçado: ${euros(d.orcamento.resultado_orcado_ate)})`,
      d.orcamento.vendas_execucao !== null ? h("span", { class: "ajuda", texto: ` · vendas a ${numero(d.orcamento.vendas_execucao, 0)}% do orçado` }) : null)
    : h("p", { class: "ajuda" }, "Ainda sem orçamento para este ano. ", h("a", { href: "#orcamento", texto: "Criar o orçamento" }));

  const atalho = (href, texto, nomeIcone, area) => h("a", { href, "data-area": area }, icone(nomeIcone), h("span", { texto }));
  const atalhos = cartao("Ações rápidas", "A gestão e a contabilidade estão ligadas: o que regista aqui segue para os registos contabilísticos.",
    h("div", { class: "atalhos-gestao" },
      atalho("/", "Carregar faturas de compra", "upload", "gestao"),
      atalho("#artigos", "Dar entrada de stock", "caixa", "gestao"),
      atalho("/contabilidade#vendas", "Importar vendas (SAF-T)", "venda", "contabilidade"),
      atalho("/contabilidade#salarios", "Processar salários", "equipa", "contabilidade"),
      atalho("/contabilidade#iva", "Ver o IVA a pagar", "percentagem", "contabilidade"),
      atalho("/contabilidade#obrigacoes", "Prazos fiscais", "calendario", "contabilidade")));

  preencher(secao, alertas, indicadores,
    h("div", { class: "gestao-grelha" }, evolucao, tesouraria),
    h("div", { class: "gestao-grelha" },
      cartao("Melhores clientes", "Faturado este ano, sem IVA", ranking(d.top_clientes, "var(--cor-2)")),
      cartao("Maiores fornecedores", "Compras este ano, sem IVA", ranking(d.top_fornecedores, "var(--grafico-2)"))),
    h("div", { class: "gestao-grelha" }, atalhos, cartao("Orçamento", null, orc)));
  if (!temContabilidade()) {
    for (const a of secao.querySelectorAll('a[href^="/contabilidade#"]')) a.href = "/subscricao?recurso=contabilidade";
  }
}

// ---------- Clientes ----------

async function carregarClientes(secao) {
  const lista = await obter("/api/gestao/clientes");
  if (!lista.length) {
    preencher(secao, h("div", { class: "cartao" }, vazio("cliente", "Ainda sem clientes",
      "Os clientes aparecem com as faturas emitidas: importe o SAF-T de faturação do seu programa certificado ou registe as faturas em Vendas."),
      h("p", { class: "centrado" }, h("a", { class: "botao", href: "/contabilidade#vendas" }, icone("venda"), "Ir para Vendas"))));
    return;
  }
  const total = (k) => lista.reduce((s, c) => s + c[k], 0);
  const linhas = lista.map((c) => {
    const tr = h("tr", {},
      h("td", { "data-rotulo": "Cliente" }, h("strong", { texto: c.nome }), h("br"), h("small", { class: "ajuda", texto: `NIF ${c.nif}` })),
      td(euros(c.faturado_ano), "num", "Faturado no ano"),
      td(`${numero(c.peso, 1)}%`, "num", "Peso"),
      td(euros(c.em_divida), "num", "Em dívida"),
      h("td", { class: "num", "data-rotulo": "Vencido" }, c.vencido
        ? h("span", { class: "texto-erro", texto: `${euros(c.vencido)} · ${c.dias_atraso} dias` }) : "—"),
      td(formatarData(c.ultimo_documento), "", "Último documento"),
      h("td", {}, botao("Documentos", () => abrirCliente(c), "botao secundario pequeno")));
    return tr;
  });
  preencher(secao,
    h("div", { class: "indicadores" },
      indicador("Clientes", String(lista.length), "Com faturas registadas", "cliente"),
      indicador("Faturado este ano", euros(total("faturado_ano")), "Sem IVA, notas de crédito abatidas", "venda"),
      indicador("Em dívida", euros(total("em_divida")), "Faturas por receber", "carteira"),
      indicador("Vencido", euros(total("vencido")), `${lista.filter((c) => c.vencido > 0).length} cliente(s) em atraso`, "alerta")),
    cartao("Clientes", "Ordenados pelo que faturaram este ano. Os recebimentos registam-se em Vendas ou na reconciliação bancária.",
      tabela([th("Cliente"), th("Faturado no ano", "num"), th("Peso", "num"), th("Em dívida", "num"), th("Vencido", "num"), th("Último documento"), th("")], linhas)));
}

async function abrirCliente(c) {
  const docs = await obter(`/api/gestao/clientes/${encodeURIComponent(c.nif)}`);
  const conteudo = tabela([th("Documento"), th("Data"), th("Vencimento"), th("Total", "num"), th("Em dívida", "num"), th("Estado")],
    docs.map((d) => h("tr", {},
      td(d.numero, "", "Documento"), td(formatarData(d.data), "", "Data"), td(formatarData(d.data_vencimento), "", "Vencimento"),
      td(euros(d.tipo === "NC" ? -d.total : d.total), "num", "Total"), td(euros(d.em_divida), "num", "Em dívida"),
      h("td", { "data-rotulo": "Estado" }, d.estado !== "normal" ? criarEtiqueta("Anulada", "neutro")
        : d.recebida || d.tipo === "NC" ? criarEtiqueta("Recebida", "ok", "check")
          : d.vencida ? criarEtiqueta("Vencida", "erro", "alerta") : criarEtiqueta("Por receber", "aviso", "relogio")))));
  const janela = janelaTemporaria(`${c.nome} · NIF ${c.nif}`, conteudo, async () => {
    location.href = "/contabilidade#vendas";
  }, "Abrir em Vendas");
  if (!temContabilidade()) janela.querySelector("button[type=submit]").hidden = true;
}

// ---------- Artigos e stock ----------

let dadosArtigos = null;

async function carregarArtigos(secao) {
  const hoje = new Date();
  const [d, movs, margens] = await Promise.all([
    obter("/api/gestao/artigos"), obter("/api/gestao/movimentos"),
    obter(`/api/gestao/margens?de=${hoje.getFullYear()}-01-01&ate=${iso(hoje)}`)]);
  dadosArtigos = d;
  const ativos = d.artigos.filter((a) => a.ativo);

  const acoes = h("div", { class: "barra-acoes" },
    botao("Novo artigo", () => janelaArtigo(), "botao", "mais"),
    botao("Entrada de fatura de compra", janelaEntradaFatura, "botao secundario", "recibo"),
    botao("Saída por venda", janelaSaidaVenda, "botao secundario", "venda"),
    botao("Movimento ou contagem", () => janelaMovimento(), "botao secundario", "atualizar"),
    temContabilidade() ? botao("Passar para os inventários", janelaInventario, "botao secundario", "livro") : null);

  const linhas = d.artigos.map((a) => h("tr", { class: a.abaixo_minimo ? "abaixo-minimo" : a.ativo ? null : "inativo" },
    h("td", { "data-rotulo": "Artigo" }, h("strong", { texto: a.codigo }), ` · ${a.nome}`, a.ativo ? null : h("small", { class: "ajuda", texto: " (desativado)" })),
    h("td", { "data-rotulo": "Tipo" }, h("span", { class: `etiqueta-tipo ${a.tipo}`, texto: a.tipo_nome })),
    h("td", { class: "num", "data-rotulo": "Stock" }, a.tipo === "S" ? "—"
      : h("span", { class: a.abaixo_minimo ? "texto-erro" : null, texto: `${numero(a.quantidade, 3)} ${a.unidade}` })),
    td(a.tipo === "S" || !a.stock_minimo ? "—" : numero(a.stock_minimo, 3), "num", "Mínimo"),
    td(a.tipo === "S" ? "—" : euros(a.custo_medio), "num", "Custo médio"),
    td(a.preco_venda ? euros(a.preco_venda) : "—", "num", "Preço (sem IVA)"),
    td(a.margem_percentual !== null ? `${numero(a.margem_percentual, 1)}%` : "—", "num", "Margem"),
    td(a.tipo === "S" ? "—" : euros(a.valor), "num", "Valor em stock"),
    h("td", {}, botao("Editar", () => janelaArtigo(a), "botao fantasma pequeno"))));

  const tabelaMargens = margens.artigos.length
    ? tabela([th("Artigo"), th("Quantidade", "num"), th("Vendas", "num"), th("Custo", "num"), th("Margem", "num")],
      margens.artigos.map((m) => h("tr", {},
        td(`${m.codigo} · ${m.nome}`, "", "Artigo"), td(numero(m.quantidade, 3), "num", "Quantidade"),
        td(euros(m.vendas), "num", "Vendas"), td(euros(m.custo), "num", "Custo"),
        h("td", { class: "num", "data-rotulo": "Margem" }, h("strong", { class: m.margem < 0 ? "texto-erro" : "texto-ok", texto: euros(m.margem) }),
          m.margem_percentual !== null ? ` (${numero(m.margem_percentual, 1)}%)` : ""))))
    : h("p", { class: "ajuda", texto: "As margens aparecem quando dá saída do stock com o preço de venda (saídas por venda)." });

  const tabelaMovs = movs.length
    ? tabela([th("Data"), th("Artigo"), th("Movimento"), th("Quantidade", "num"), th("Valor", "num"), th("Documento")],
      movs.slice(0, 30).map((m) => h("tr", {},
        td(formatarData(m.data), "", "Data"), td(`${m.artigo} · ${m.artigo_nome}`, "", "Artigo"),
        h("td", { "data-rotulo": "Movimento" }, m.tipo === "entrada" ? criarEtiqueta("Entrada", "ok", "seta-baixo")
          : m.tipo === "saida" ? criarEtiqueta("Saída", "info", "seta-cima") : criarEtiqueta("Acerto", "aviso", "atualizar")),
        h("td", { class: "num", "data-rotulo": "Quantidade" }, h("span", { class: m.quantidade < 0 ? "texto-erro" : "texto-ok", texto: `${m.quantidade > 0 ? "+" : ""}${numero(m.quantidade, 3)} ${m.unidade}` })),
        td(euros(m.valor), "num", "Valor"),
        h("td", { "data-rotulo": "Documento" }, m.fatura_id ? h("a", { href: `/faturas/${m.fatura_id}`, texto: m.documento || "Fatura" }) : m.documento || "—"))))
    : h("p", { class: "ajuda", texto: "Sem movimentos." });

  preencher(secao,
    h("div", { class: "indicadores" },
      indicador("Artigos ativos", String(ativos.length), `${ativos.filter((a) => a.tipo === "S").length} serviço(s)`, "caixa"),
      indicador("Valor do stock", euros(d.valor_total), "Ao custo médio ponderado", "euro"),
      indicador("Abaixo do mínimo", String(d.abaixo_minimo), d.abaixo_minimo ? "Está na altura de encomendar" : "Stock em dia", "alerta"),
      indicador("Margem bruta do ano", euros(margens.margem),
        margens.margem_percentual !== null ? `${numero(margens.margem_percentual, 1)}% das vendas com saída de stock` : "Sem saídas por venda", "tendencia")),
    cartao("Artigos", "Mercadorias e matérias com stock e custo médio; os serviços não têm stock.", acoes,
      d.artigos.length ? tabela([th("Artigo"), th("Tipo"), th("Stock", "num"), th("Mínimo", "num"), th("Custo médio", "num"),
        th("Preço (sem IVA)", "num"), th("Margem", "num"), th("Valor em stock", "num"), th("")], linhas)
        : vazio("caixa", "Ainda sem artigos", "Crie os artigos que compra e vende para acompanhar o stock, o custo e a margem.")),
    cartao("Margem por artigo", "Este ano, pelas saídas com preço de venda", tabelaMargens),
    cartao("Últimos movimentos", "Os 30 mais recentes", tabelaMovs));
}

function seletorArtigos(id, soComStock = false) {
  return h("select", { id, required: true }, h("option", { value: "" }, "Escolha o artigo"),
    (dadosArtigos?.artigos || []).filter((a) => a.ativo && a.tipo !== "S" && (!soComStock || a.quantidade > 0))
      .map((a) => h("option", { value: String(a.id) }, `${a.codigo} · ${a.nome} (${numero(a.quantidade, 3)} ${a.unidade})`)));
}

function numeroCampo(id, props = {}) {
  return h("input", { type: "number", id, step: "any", min: "0", inputmode: "decimal", ...props });
}

function janelaArtigo(a = null) {
  const codigo = h("input", { id: "art-codigo", maxlength: "60", required: true, value: a?.codigo || "", placeholder: "Ex.: VINHO-01" });
  const nome = h("input", { id: "art-nome", maxlength: "200", required: true, value: a?.nome || "" });
  const tipo = h("select", { id: "art-tipo" }, Object.entries(dadosArtigos.tipos).map(([k, v]) => h("option", { value: k }, v)));
  tipo.value = a?.tipo || "M";
  const unidade = h("input", { id: "art-unidade", maxlength: "10", value: a?.unidade || "UN" });
  const preco = numeroCampo("art-preco", { value: a?.preco_venda ?? "" });
  const taxa = h("select", { id: "art-iva" }, dadosArtigos.taxas_iva.map((t) => h("option", { value: String(t) }, `${numero(t, 1)}%`)));
  taxa.value = String(a?.taxa_iva ?? 23);
  const minimo = numeroCampo("art-minimo", { value: a?.stock_minimo ?? "" });
  const ativo = h("input", { type: "checkbox", id: "art-ativo", checked: a ? a.ativo : true });
  janelaTemporaria(a ? `Editar ${a.codigo}` : "Novo artigo", h("div", {},
    h("div", { class: "grelha-3" }, campoJanela("art-codigo", "Código", codigo), campoJanela("art-tipo", "Tipo", tipo),
      campoJanela("art-unidade", "Unidade", unidade)),
    campoJanela("art-nome", "Nome", nome),
    h("div", { class: "grelha-3" }, campoJanela("art-preco", "Preço de venda (sem IVA)", preco),
      campoJanela("art-iva", "Taxa de IVA", taxa), campoJanela("art-minimo", "Stock mínimo", minimo)),
    a ? h("label", { class: "caixa-opcao" }, ativo, " Artigo ativo") : null), async () => {
    const corpo = {
      codigo: codigo.value, nome: nome.value, tipo: tipo.value, unidade: unidade.value,
      preco_venda: Number(preco.value || 0), taxa_iva: Number(taxa.value), stock_minimo: Number(minimo.value || 0), ativo: ativo.checked,
    };
    await enviar(a ? `/api/gestao/artigos/${a.id}` : "/api/gestao/artigos", corpo, a ? "PUT" : "POST");
    notificar(a ? "Artigo atualizado." : "Artigo criado.", "ok");
    mostrar("artigos");
  });
}

// Linhas de artigos (entrada por fatura / saída por venda)
function editorLinhas(rotuloValor, comStock, valorOmissao) {
  const caixa = h("div", { class: "linhas-stock" });
  let n = 0;
  const adicionar = () => {
    n += 1;
    const artigo = seletorArtigos(`ln-art-${n}`, comStock);
    const qtd = numeroCampo(`ln-qtd-${n}`, { required: true, placeholder: "0" });
    const valor = numeroCampo(`ln-val-${n}`, { placeholder: valorOmissao ? "preço do artigo" : "0,00" });
    artigo.addEventListener("change", () => {
      const a = dadosArtigos.artigos.find((x) => String(x.id) === artigo.value);
      if (a && valorOmissao && !valor.value) valor.value = a.preco_venda || "";
    });
    const linha = h("div", { class: "linha-stock" },
      campoJanela(`ln-art-${n}`, "Artigo", artigo), campoJanela(`ln-qtd-${n}`, "Quantidade", qtd),
      campoJanela(`ln-val-${n}`, rotuloValor, valor),
      botaoIcone("lixo", "Remover a linha", () => linha.remove()));
    linha.dados = () => ({ artigo_id: Number(artigo.value), quantidade: Number(qtd.value), valor_unitario: valor.value === "" ? null : Number(valor.value) });
    caixa.appendChild(linha);
  };
  adicionar();
  const mais = botao("Mais um artigo", adicionar, "botao secundario pequeno", "mais");
  return {
    el: h("div", {}, caixa, mais),
    linhas: () => [...caixa.children].map((l) => l.dados()).filter((l) => l.artigo_id),
  };
}

async function janelaEntradaFatura() {
  const { faturas } = await obter("/api/gestao/stock/origens");
  if (!faturas.length) {
    notificar("Não há faturas de compra confirmadas por dar entrada em stock. Carregue e confirme as faturas em Faturas de compra.", "info");
    return;
  }
  const fatura = h("select", { id: "ent-fatura", required: true }, faturas.map((f) => h("option", { value: f.id },
    `${formatarData(f.data)} · ${f.fornecedor || "Fornecedor"} · ${f.numero || "sem número"} · falta ${euros(f.por_dar_entrada)}`)));
  const editor = editorLinhas("Custo unitário (sem IVA)", false, false);
  janelaTemporaria("Entrada de stock a partir de uma fatura de compra", h("div", {},
    h("p", { class: "ajuda", texto: "O custo de cada artigo atualiza o custo médio. O total não pode passar do valor da fatura sem IVA." }),
    campoJanela("ent-fatura", "Fatura de compra", fatura), editor.el), async () => {
    await enviar("/api/gestao/stock/entrada-fatura", { fatura_id: fatura.value, linhas: editor.linhas() });
    notificar("Entrada registada no stock.", "ok");
    mostrar("artigos");
  }, "Dar entrada");
}

async function janelaSaidaVenda() {
  const { vendas } = await obter("/api/gestao/stock/origens");
  if (!vendas.length) {
    notificar("Não há faturas emitidas por dar saída do stock. Importe as vendas (SAF-T) ou registe-as em Vendas.", "info");
    return;
  }
  const venda = h("select", { id: "sai-venda", required: true }, vendas.map((v) => h("option", { value: String(v.id) },
    `${formatarData(v.data)} · ${v.numero} · ${v.cliente} · ${euros(v.valor_sem_iva)}`)));
  const editor = editorLinhas("Preço unitário (sem IVA)", true, true);
  janelaTemporaria("Saída de stock de uma venda", h("div", {},
    h("p", { class: "ajuda", texto: "Sai ao custo médio; o preço de venda serve para calcular a margem de cada artigo." }),
    campoJanela("sai-venda", "Fatura emitida", venda), editor.el), async () => {
    await enviar("/api/gestao/stock/saida-venda", { venda_id: Number(venda.value), linhas: editor.linhas() });
    notificar("Saída registada no stock.", "ok");
    mostrar("artigos");
  }, "Dar saída");
}

function janelaMovimento() {
  const artigo = seletorArtigos("mov-artigo");
  const tipo = h("select", { id: "mov-tipo" }, h("option", { value: "entrada" }, "Entrada (compra sem fatura lida, oferta recebida...)"),
    h("option", { value: "saida" }, "Saída (consumo, quebra, oferta)"), h("option", { value: "ajuste" }, "Contagem física (acerta para a quantidade contada)"));
  const qtd = numeroCampo("mov-qtd", { required: true });
  const custo = numeroCampo("mov-custo");
  const data = h("input", { type: "date", id: "mov-data", value: iso(new Date()) });
  const documento = h("input", { id: "mov-doc", maxlength: "80", placeholder: "Ex.: Guia 123" });
  const campoCusto = campoJanela("mov-custo", "Custo unitário (sem IVA)", custo);
  const rotuloQtd = h("label", { for: "mov-qtd", texto: "Quantidade" });
  tipo.addEventListener("change", () => {
    campoCusto.hidden = tipo.value !== "entrada";
    rotuloQtd.textContent = tipo.value === "ajuste" ? "Quantidade contada" : "Quantidade";
  });
  janelaTemporaria("Movimento de stock", h("div", {},
    campoJanela("mov-artigo", "Artigo", artigo), campoJanela("mov-tipo", "Movimento", tipo),
    h("div", { class: "grelha-3" }, h("div", { class: "campo" }, rotuloQtd, qtd), campoCusto, campoJanela("mov-data", "Data", data)),
    campoJanela("mov-doc", "Documento (opcional)", documento)), async () => {
    await enviar("/api/gestao/movimentos", {
      artigo_id: Number(artigo.value), tipo: tipo.value, quantidade: Number(qtd.value),
      custo_unitario: custo.value === "" ? null : Number(custo.value), data: data.value || null, documento: documento.value || null,
    });
    notificar("Movimento registado.", "ok");
    mostrar("artigos");
  }, "Registar");
}

function janelaInventario() {
  const hoje = new Date();
  const data = h("input", { type: "date", id: "inv-data", value: iso(new Date(hoje.getFullYear(), hoje.getMonth(), 0)), required: true });
  janelaTemporaria("Passar o stock para os inventários da contabilidade", h("div", {},
    h("p", { texto: "Cria a contagem de inventário da contabilidade com as quantidades e o custo médio de cada artigo nessa data. Depois, em Inventários, lança o apuramento do custo das mercadorias vendidas (CMVMC)." }),
    campoJanela("inv-data", "Data da contagem", data)), async () => {
    const r = await enviar("/api/gestao/stock/inventario", { data: data.value });
    notificar(`Inventário de ${formatarData(r.data)} criado: ${r.artigos} artigos, ${euros(r.valor)}.`, "ok",
      { acao: { texto: "Abrir em Inventários", href: "/contabilidade#inventarios" } });
  }, "Criar inventário");
}

// ---------- Tesouraria ----------

async function carregarTesouraria(secao) {
  const t = await obter("/api/gestao/tesouraria?semanas=13");
  const semanas = t.semanas.map((s) => ({ ...s, rotulo: formatarData(s.de).slice(0, 5) }));
  const NOMES = { recebimento: ["Recebimento", "ok", "seta-baixo"], pagamento: ["Fornecedor", "erro", "seta-cima"],
    salarios: ["Pessoal", "info", "equipa"], estado: ["Estado", "aviso", "percentagem"] };
  const movimentos = t.movimentos.map((m) => {
    const [nome, classe, ic] = NOMES[m.tipo] || [m.tipo, "neutro", "relogio"];
    return h("tr", {},
      h("td", { "data-rotulo": "Data" }, formatarData(m.data), m.vencido ? h("small", { class: "texto-erro", texto: " · em atraso" }) : null),
      h("td", { "data-rotulo": "Descrição" }, m.ligacao ? h("a", { href: m.ligacao, texto: m.descricao }) : m.descricao,
        m.estimado ? h("small", { class: "ajuda", texto: " (estimativa)" }) : null),
      h("td", { "data-rotulo": "Tipo" }, criarEtiqueta(nome, classe, ic)),
      h("td", { class: "num", "data-rotulo": "Valor" }, h("strong", { class: m.valor < 0 ? "texto-erro" : "texto-ok", texto: euros(m.valor) })));
  });
  preencher(secao,
    t.alerta ? h("ul", { class: "alertas-gestao" }, h("li", { class: "erro" }, h("a", { href: "#tesouraria" }, icone("erro"), h("span", { texto: t.alerta })))) : null,
    h("div", { class: "indicadores" },
      indicador("Disponível hoje", euros(t.saldo_inicial), "Caixa e bancos na contabilidade", "carteira", "/contabilidade#bancos"),
      indicador("Daqui a 30 dias", euros(t.saldo_30_dias), "Previsto", "tendencia"),
      indicador("Recebimentos em atraso", euros(t.recebimentos_em_atraso), "Faturas de clientes vencidas", "cliente", "#clientes"),
      indicador("Pagamentos em atraso", euros(t.pagamentos_em_atraso), "Faturas de fornecedores vencidas", "recibo", "/contabilidade#fornecedores")),
    cartao("Saldo previsto por semana", "Próximas 13 semanas: vendas por receber, faturas por pagar, salários, Segurança Social, IRS retido e IVA",
      graficoBarras(semanas, [{ chave: "saldo", nome: "Saldo", classe: "saldo" }]),
      tabela([th("Semana"), th("Entradas", "num"), th("Saídas", "num"), th("Saldo no fim", "num")], t.semanas.map((s) => h("tr", {},
        td(`${formatarData(s.de)} a ${formatarData(s.ate)}`, "", "Semana"), td(euros(s.entradas), "num", "Entradas"),
        td(euros(s.saidas), "num", "Saídas"),
        h("td", { class: "num", "data-rotulo": "Saldo no fim" }, h("strong", { class: s.saldo < 0 ? "texto-erro" : null, texto: euros(s.saldo) })))))),
    cartao("Movimentos previstos", "Os valores já vencidos contam na primeira semana.",
      movimentos.length ? tabela([th("Data"), th("Descrição"), th("Tipo"), th("Valor", "num")], movimentos)
        : vazio("tendencia", "Sem movimentos previstos", "Não há faturas por receber nem por pagar nas próximas semanas.")));
}

// ---------- Orçamento ----------

let anoOrcamento = new Date().getFullYear();

async function carregarOrcamento(secao) {
  const o = await obter(`/api/gestao/orcamento?ano=${anoOrcamento}`);
  const ano = h("select", { id: "orc-ano", "aria-label": "Ano do orçamento" },
    [-1, 0, 1].map((d) => h("option", { value: String(new Date().getFullYear() + d) }, String(new Date().getFullYear() + d))));
  ano.value = String(anoOrcamento);
  ano.addEventListener("change", () => {
    anoOrcamento = Number(ano.value);
    mostrar("orcamento");
  });

  const comparacao = tabela([th("Rubrica"), th(`Orçado até ${o.ate_mes ? MESES[o.ate_mes - 1] : "—"}`, "num"), th("Real", "num"), th("Desvio", "num"), th("Execução")],
    [...o.rubricas.map((r) => {
      const excedido = r.tipo === "gasto" ? r.real_ate > r.orcado_ate : false;
      return h("tr", {},
        h("td", { "data-rotulo": "Rubrica" }, h("strong", { texto: r.nome }), h("small", { class: "ajuda", texto: r.tipo === "gasto" ? " · gasto" : " · rendimento" })),
        td(euros(r.orcado_ate), "num", "Orçado"), td(euros(r.real_ate), "num", "Real"),
        h("td", { class: "num", "data-rotulo": "Desvio" }, h("span", { class: r.desvio >= 0 ? "desvio-bom" : "desvio-mau", texto: `${r.desvio >= 0 ? "+" : ""}${euros(r.desvio)}` })),
        h("td", { "data-rotulo": "Execução" }, r.execucao !== null ? `${numero(r.execucao, 0)}%` : "—",
          h("span", { class: "barra-execucao" }, h("span", { class: excedido ? "excedido" : null, style: `width:${Math.min(100, r.execucao || 0)}%` }))));
    }),
    h("tr", { class: "total" }, h("td", {}, h("strong", { texto: "Resultado" })), td(euros(o.resultado_orcado_ate), "num"),
      td(euros(o.resultado_real_ate), "num"),
      h("td", { class: "num" }, h("span", { class: o.resultado_real_ate >= o.resultado_orcado_ate ? "desvio-bom" : "desvio-mau",
        texto: euros(o.resultado_real_ate - o.resultado_orcado_ate) })), td(""))]);

  // Editor: 12 meses por rubrica
  const entradas = {};
  const linhasEditor = o.rubricas.map((r) => {
    entradas[r.chave] = r.orcado.map((v, i) => h("input", { type: "number", min: "0", step: "any", inputmode: "decimal", value: v || "",
      "aria-label": `${r.nome}, ${MESES[i]}` }));
    return h("tr", {}, h("td", { class: "rubrica" }, h("strong", { texto: r.nome })), entradas[r.chave].map((e) => h("td", {}, e)),
      td(euros(r.orcado_ano), "num"));
  });
  const crescimento = numeroCampo("orc-crescimento", { value: "5", min: "-90", max: "500", style: "max-width:6rem" });
  const sugerir = botao("Preencher com o ano anterior", async () => {
    const p = await obter(`/api/gestao/orcamento/proposta?ano=${anoOrcamento}&crescimento=${Number(crescimento.value || 0)}`);
    for (const [chave, valores] of Object.entries(p)) entradas[chave]?.forEach((e, i) => { e.value = valores[i] || ""; });
    notificar("Valores preenchidos com o real do ano anterior. Reveja e guarde.", "info");
  }, "botao secundario", "atualizar");
  const guardar = botao("Guardar orçamento", async () => {
    ocupado(guardar, true);
    try {
      const valores = Object.fromEntries(Object.entries(entradas).map(([k, es]) => [k, es.map((e) => Number(e.value || 0))]));
      await enviar("/api/gestao/orcamento", { ano: anoOrcamento, valores }, "PUT");
      notificar("Orçamento guardado.", "ok");
      mostrar("orcamento");
    } catch (erro) {
      notificar(erro.message, "erro");
      ocupado(guardar, false);
    }
  }, "botao", "check");

  preencher(secao,
    h("div", { class: "barra-acoes" }, h("div", { class: "campo" }, h("label", { for: "orc-ano", texto: "Ano" }), ano)),
    h("div", { class: "indicadores" },
      indicador("Resultado orçado (ano)", euros(o.resultado_orcado_ano), "Rendimentos menos gastos", "calculadora"),
      indicador("Resultado orçado até agora", euros(o.resultado_orcado_ate), o.ate_mes ? `Até ${MESES[o.ate_mes - 1]}` : "Ano ainda por começar", "alvo"),
      indicador("Resultado real", euros(o.resultado_real_ate), "Pela contabilidade", "euro", "/contabilidade#resultados"),
      indicador("Desvio", euros(o.resultado_real_ate - o.resultado_orcado_ate),
        h("span", { class: o.resultado_real_ate >= o.resultado_orcado_ate ? "positivo" : "negativo",
          texto: o.resultado_real_ate >= o.resultado_orcado_ate ? "Acima do orçado" : "Abaixo do orçado" }), "tendencia")),
    o.tem_orcamento ? cartao("Orçado e real", "O real vem dos lançamentos da contabilidade (o apuramento do fecho não conta).", comparacao) : null,
    cartao(o.tem_orcamento ? "Editar o orçamento" : "Criar o orçamento", "Valores mensais sem IVA. Pode partir do real do ano anterior com uma variação nas vendas.",
      h("div", { class: "barra-acoes" }, h("div", { class: "campo" }, h("label", { for: "orc-crescimento", texto: "Variação das vendas (%)" }), crescimento), sugerir),
      h("div", { class: "tabela-caixa" }, h("table", { class: "tabela tabela-orcamento" },
        h("thead", {}, h("tr", {}, th("Rubrica"), MESES.map((m) => th(m, "num")), th("Total", "num"))),
        h("tbody", {}, linhasEditor))),
      h("div", { class: "barra-acoes" }, guardar)));
}

// ---------- Arranque ----------

document.addEventListener("DOMContentLoaded", async () => {
  window.addEventListener("hashchange", () => mostrar(decodeURIComponent(location.hash.slice(1)).split(":")[0]));
  try {
    eu = await obter("/api/auth/eu");
  } catch {
    eu = null;
  }
  mostrar(decodeURIComponent(location.hash.slice(1)).split(":")[0] || "painel");
});
